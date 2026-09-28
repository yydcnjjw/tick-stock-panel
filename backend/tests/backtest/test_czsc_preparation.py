import asyncio
import json
from datetime import date
from threading import Event
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.indicators import chan_signals
from app.strategy.engine import StrategyDef


def test_panel_preparation_forwards_progress_cancellation_and_worker_limit(monkeypatch):
    panel = pl.DataFrame({"symbol": ["600000.SH"], "date": [date(2024, 1, 2)]})
    plan = SimpleNamespace(
        base_columns=frozenset(panel.columns), indicator_columns=frozenset(),
        signal_columns=frozenset({"signal_chan_bi_1_buy"}),
        instrument_columns=frozenset(), execution_backend="polars_expr",
    )
    engine = BacktestEngine(None)
    monkeypatch.setattr(engine, "load_panel", lambda *args, **kwargs: panel)
    monkeypatch.setattr("app.indicators.pipeline.compute_indicators", lambda df, needed: df)
    events = []
    cancelled = Event()

    def compute_signals(df, needed, *, progress_cb, cancel_event, czsc_max_workers):
        assert needed == {"signal_chan_bi_1_buy"}
        assert cancel_event is cancelled
        assert czsc_max_workers == 4
        progress_cb({"phase": "chan_signals", "completed": 1, "total": 1})
        return df.with_columns(pl.lit(False).alias("signal_chan_bi_1_buy"))

    monkeypatch.setattr("app.indicators.pipeline.compute_signals", compute_signals)
    result = engine.load_panel_for_backtest(
        None, date(2024, 1, 1), date(2024, 1, 2), plan,
        progress_cb=events.append, cancel_event=cancelled, czsc_max_workers=4,
    )
    assert result["signal_chan_bi_1_buy"].to_list() == [False]
    assert events == [{"phase": "chan_signals", "completed": 1, "total": 1}]


@pytest.mark.parametrize("cancel_before_load", [True, False])
def test_cancelled_preparation_does_not_start_feature_computation(monkeypatch, cancel_before_load):
    cancelled = Event()
    panel = pl.DataFrame({"symbol": ["600000.SH"]})
    engine = BacktestEngine(None)
    if cancel_before_load:
        cancelled.set()

    def load_panel(*args, **kwargs):
        assert not cancel_before_load, "cancelled preparation still loaded data"
        cancelled.set()
        return panel

    monkeypatch.setattr(engine, "load_panel", load_panel)
    monkeypatch.setattr(
        "app.indicators.pipeline.compute_indicators",
        lambda *args, **kwargs: pytest.fail("cancelled preparation computed features"),
    )
    with pytest.raises(chan_signals.ChanReplayCancelledError):
        engine.load_panel_for_backtest(
            None, date(2024, 1, 1), date(2024, 1, 2),
            SimpleNamespace(base_columns=frozenset(panel.columns)), cancel_event=cancelled,
        )


def test_strategy_preparation_reports_progress_and_returns_cancellation(monkeypatch):
    spec = StrategyDef(
        meta={"id": "czsc", "name": "CZSC", "scoring": {}, "params": []},
        basic_filter={"enabled": False}, entry_signals=["signal_chan_bi_1_buy"],
        exit_signals=[], stop_loss=None, trailing_stop=None,
        trailing_take_profit_activate=None, trailing_take_profit_drawdown=None,
        max_hold_days=None, filter_fn=None, filter_history_fn=None,
        lookback_days=1, source="custom",
    )
    monkeypatch.setattr(chan_signals, "_load_runtime", lambda: None)
    cancelled = Event()
    events = []

    def load_panel(*args, progress_cb, cancel_event, czsc_max_workers, **kwargs):
        assert czsc_max_workers == 4
        assert cancel_event is cancelled
        progress_cb({"phase": "chan_signals", "completed": 0, "total": 10})
        cancelled.set()
        raise chan_signals.ChanReplayCancelledError("回测已取消")

    service = StrategyBacktestService(
        SimpleNamespace(load_panel_for_backtest=load_panel),
        SimpleNamespace(get=lambda sid: spec),
    )
    result = service.run(
        StrategyBacktestConfig(
            strategy_id="czsc", symbols=None, start=date(2024, 1, 1), end=date(2024, 1, 2),
        ),
        events.append, cancelled,
    )
    assert result.error == "cancelled"
    assert not result.trades
    assert events == [{"phase": "chan_signals", "completed": 0, "total": 10}]


def test_sse_preserves_preparation_phase_then_reports_cancelled(monkeypatch):
    from app.api import backtest

    job = backtest._BacktestJob("czsc-progress")
    progress = {"phase": "chan_signals", "completed": 12, "total": 100}
    job.progress.append(progress)
    monkeypatch.setattr(backtest, "_running_jobs", {job.key: job})
    monkeypatch.setattr(backtest, "_make_job_key", lambda *args, **kwargs: job.key)

    async def read_stream():
        response = await backtest.strategy_stream(
            SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
            "czsc", start="2024-01-01", end="2024-01-02",
        )
        stream = response.body_iterator
        first = await anext(stream)
        assert first.startswith("event: progress\n")
        assert json.loads(first.split("data: ")[1]) == progress
        job.result, job.done = {"error": "cancelled"}, True
        cancelled = await anext(stream)
        assert cancelled.startswith("event: error\n")
        assert json.loads(cancelled.split("data: ")[1]) == {"message": "回测已取消"}
        await stream.aclose()

    asyncio.run(read_stream())
