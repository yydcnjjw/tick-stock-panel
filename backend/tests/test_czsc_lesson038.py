from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.api.strategy import StrategyCodeSaveRequest, _save_strategy_code
from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.strategy import StrategyBacktestService
from app.strategy.engine import StrategyEngine

SID = "custom_chan_lesson038"


@pytest.fixture
def preset(tmp_path):
    engine = StrategyEngine([tmp_path / "strategies" / "custom"])
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo, strategy_engine=engine,
    )))
    path = Path(__file__).resolve().parents[2] / "docs/examples/chan-strategies" / f"{SID}.py"
    _save_strategy_code(StrategyCodeSaveRequest(
        strategy_id=SID, code=path.read_text(), target_source="custom", mode="create",
    ), request)
    return engine.get(SID)


def test_main_board_pool_includes_st_without_extra_thresholds(preset):
    frame = pl.DataFrame({
        "symbol": ["600000.SH", "601899.SH", "603000.SH", "605001.SH", "000001.SZ",
                   "001001.SZ", "002001.SZ", "003001.SZ", "300001.SZ", "688001.SH",
                   "920001.BJ", "000001.SH", "600000.SZ"],
        "name": ["*ST测试"] * 13,
        "close": [1.0] * 13,
        "amount": [100.0] * 13,
        "signal_chan_bi_1_sell": [False] * 13,
        "signal_chan_bi_2_sell": [False] * 13,
    })
    assert preset.basic_filter["enabled"] is False
    assert frame.filter(preset.filter_fn(frame, {}))["symbol"].to_list() == [
        "600000.SH", "601899.SH", "603000.SH", "605001.SH",
        "000001.SZ", "001001.SZ", "002001.SZ", "003001.SZ",
    ]


def test_each_sell_event_blocks_entry_without_masking_exit(preset):
    frame = pl.DataFrame({
        "symbol": ["600000.SH"] * 5,
        "signal_chan_bi_1_buy": [True, True, False, False, None],
        "signal_chan_bi_2_buy": [False, False, True, True, None],
        "signal_chan_bi_1_sell": [False, True, False, False, None],
        "signal_chan_bi_2_sell": [False, False, False, True, None],
    })
    service = StrategyBacktestService(None, None)
    entries = service._build_entry_mask(frame, preset, {}, preset.entry_signals)
    exits = service._build_signal_mask(frame, preset.exit_signals, "_exit")
    assert entries.to_list() == [True, False, True, False, False]
    assert exits.to_list() == [False, True, False, True, False]


def test_position_replay_waits_for_new_signal_and_fills_next_session(preset):
    days = [date(2024, 1, 2) + timedelta(days=i) for i in range(20)]
    days = [day for day in days if day.weekday() < 5][:13]
    n = len(days)
    frame = pl.DataFrame({
        "symbol": ["600000.SH"] * n,
        "date": days,
        "open": [10.0] * n, "close": [10.0] * n,
        "high": [10.1] * n, "low": [9.9] * n,
        "raw_close": [10.0] * n, "raw_high": [10.1] * n, "raw_low": [9.9] * n,
        "volume": [1000.0] * n, "amount": [1_000_000.0] * n,
        "signal_chan_bi_1_buy": [i == 1 for i in range(n)],
        "signal_chan_bi_2_buy": [i in {3, 6, 8} for i in range(n)],
        "signal_chan_bi_1_sell": [i == 4 for i in range(n)],
        "signal_chan_bi_2_sell": [i in {6, 10} for i in range(n)],
    })
    service = StrategyBacktestService(None, None)
    entries = service._build_entry_mask(frame, preset, {}, preset.entry_signals)
    exits = service._build_signal_mask(frame, preset.exit_signals, "_exit")
    engine = BacktestEngine(SimpleNamespace())
    result = engine.simulate_portfolio(
        frame, entries, exits,
        MatcherConfig(
            matching="open_t+1", initial_capital=100_000, max_positions=1,
            stop_loss_pct=preset.stop_loss, max_hold_days=preset.max_hold_days,
            commission_pct=0.0002, stamp_tax_pct=0.0005, slippage_bps=5,
        ),
        entry_signal_ids=preset.entry_signals, exit_signal_ids=preset.exit_signals,
    )
    assert len(result.trades) == 2
    first, second = result.trades
    assert (str(first.entry_signal_date), str(first.entry_date), str(first.exit_signal_date), str(first.exit_date)) == tuple(
        str(days[i]) for i in (1, 2, 4, 5)
    )
    assert (str(second.entry_signal_date), str(second.entry_date), str(second.exit_signal_date), str(second.exit_date)) == tuple(
        str(days[i]) for i in (8, 9, 10, 11)
    )
    assert all(trade.exit_reason == "signal" and trade.pnl_pct < 0 for trade in result.trades)
