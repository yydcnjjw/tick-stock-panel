from datetime import date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.indicators import czsc_signals as cs
from app.market_time import CN_TZ


def bars(n=12):
    return pl.DataFrame({
        "symbol": ["600000.SH"] * n,
        "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
        "open": [10.0] * n, "close": [10.0] * n,
        "high": [11.0] * n, "low": [9.0] * n,
        "volume": [123.0] * n, "amount": [123000.0] * n,
    })


def fake_runtime(monkeypatch, values):
    captured = []

    class Analysis:
        def __init__(self, initial, **kwargs):
            self.last = initial[-1]
            captured.extend(initial)

        def update(self, bar):
            self.last = bar
            captured.append(bar)

    runtime = SimpleNamespace(
        CZSC=Analysis, RawBar=lambda **kw: SimpleNamespace(**kw),
        Freq=SimpleNamespace(D="日线"),
        call_signal=lambda name, analysis, params: [SimpleNamespace(v1=values[analysis.last.id])],
    )
    monkeypatch.setattr(cs, "_load_runtime", lambda: runtime)
    monkeypatch.setattr(cs, "_ready", lambda analysis, spec: True)
    return captured


def test_edges_are_per_signal_and_initial_true_only_establishes_baseline(monkeypatch):
    fake_runtime(monkeypatch, ["一买", "一买", "其他", "一买", "一买", "其他", "一买"])
    sig = "signal_czsc_first_buy"
    result = cs.compute(bars(7), {sig})
    assert result[sig].to_list() == [None, False, False, True, False, False, True]


def test_units_order_and_isolation_between_symbols(monkeypatch):
    captured = fake_runtime(monkeypatch, ["其他", "一买", "一买"])
    sig = "signal_czsc_first_buy"
    frame = pl.concat([bars(3), bars(3).with_columns(pl.lit("600001.SH").alias("symbol"))]).reverse()
    result = cs.compute(frame, {sig})
    assert result.select("symbol", "date").equals(frame.select("symbol", "date"))
    assert result.filter(pl.col("date") == date(2024, 1, 2))[sig].to_list() == [True, True]
    assert captured[0].vol == 12300.0  # enriched volume is lots; RawBar uses shares.
    assert captured[0].amount == 123000.0


def test_missing_bar_breaks_the_baseline_instead_of_fabricating_an_edge(monkeypatch):
    fake_runtime(monkeypatch, ["其他", "一买", "一买", "一买"])
    sig = "signal_czsc_first_buy"
    frame = bars(4).with_columns(
        pl.when(pl.col("date") == date(2024, 1, 2)).then(None).otherwise(pl.col("close")).alias("close")
    )
    result = cs.compute(frame, {sig})
    assert result[sig].to_list() == [None, None, None, False]
    assert cs.coverage(result, {sig})["signals"][0]["reasons"]["missing_data"] == 1


def test_unclosed_daily_bar_never_fires_and_timezone_is_explicit(monkeypatch):
    fake_runtime(monkeypatch, ["其他", "一买"])
    sig = "signal_czsc_first_buy"
    result = cs.compute(bars(2), {sig}, now=datetime(2024, 1, 2, 14, 59, tzinfo=CN_TZ))
    assert result[sig].to_list() == [None, None]
    assert cs.coverage(result, {sig})["signals"][0]["reasons"]["unclosed_bar"] == 1


def test_duplicate_daily_bars_are_rejected(monkeypatch):
    fake_runtime(monkeypatch, ["其他"])
    with pytest.raises(ValueError, match="重复"):
        cs.compute(pl.concat([bars(1), bars(1)]), {"signal_czsc_first_buy"})


def test_no_selected_signals_never_loads_optional_dependency(monkeypatch):
    monkeypatch.setattr(cs, "_load_runtime", lambda: pytest.fail("unused CZSC was loaded"))
    frame = bars()
    assert cs.compute(frame, set()).equals(frame)


def test_selected_signals_fail_clearly_without_optional_dependency(monkeypatch):
    def unavailable():
        raise ValueError("CZSC 信号组件未安装")
    monkeypatch.setattr(cs, "_load_runtime", unavailable)
    with pytest.raises(ValueError, match="CZSC"):
        cs.compute(bars(), {"signal_czsc_first_buy"})


@pytest.mark.parametrize("failure", ["missing", "version", "binary"])
def test_dependency_failures_disable_only_czsc(monkeypatch, failure):
    def installed_version(name):
        if failure == "missing":
            raise cs.PackageNotFoundError(name)
        return "0.0.0" if failure == "version" else cs.VERSION

    def import_native(name):
        raise RuntimeError("native module cannot initialize")

    monkeypatch.setattr(cs, "version", installed_version)
    monkeypatch.setattr(cs.importlib, "import_module", import_native)
    status = cs.availability()
    assert status["available"] is False
    assert status["reason"]
    frame = bars()
    assert cs.compute(frame, set()).equals(frame)
    with pytest.raises(ValueError, match="CZSC"):
        cs.compute(frame, {"signal_czsc_first_buy"})


@pytest.fixture(scope="module")
def native_history():
    pytest.importorskip("czsc")
    import numpy as np
    rng = np.random.default_rng(2019)
    n = 900
    prices = 20 * np.exp(np.cumsum(rng.normal(0, .025, n)))
    return pl.DataFrame({
        "symbol": ["600000.SH"] * n,
        "date": [date(2018, 1, 1) + timedelta(days=i) for i in range(n)],
        "open": prices, "close": prices, "high": prices * 1.015, "low": prices * .985,
        "volume": rng.uniform(1000, 3000, n), "amount": prices * 200000,
    })


def test_all_six_native_results_and_future_prefix_invariance(native_history):
    import czsc
    from czsc._native.signals import call_signal

    wanted = set(cs.SIGNALS)
    result = cs.compute(native_history, wanted)
    prefix = cs.compute(native_history.head(600), wanted)
    assert result.head(600).equals(prefix)
    # Both historical directions and all three algorithm families are exercised.
    assert all(result[name].sum() > 0 for name in wanted)
    analysis = None
    previous = dict.fromkeys(wanted)
    for i, row in enumerate(native_history.iter_rows(named=True)):
        raw = czsc.RawBar(
            symbol=row["symbol"], id=i, dt=datetime.combine(row["date"], datetime.min.time().replace(hour=15)),
            freq=czsc.Freq.D, open=row["open"], high=row["high"], low=row["low"],
            close=row["close"], vol=row["volume"] * 100, amount=row["amount"],
        )
        if analysis is None:
            analysis = czsc.CZSC([raw], max_bi_num=50, min_bi_len=6)
        else:
            analysis.update(raw)
        for name, spec in cs.SIGNALS.items():
            raw_hit = call_signal(spec.function, analysis, spec.params)[0].v1 == spec.value
            if result[name][i] is not None:
                assert result[name][i] == (raw_hit and previous[name] is False)
            previous[name] = raw_hit


def test_structure_shortage_is_reported_separately_from_no_signal():
    pytest.importorskip("czsc")
    sig = "signal_czsc_third_buy"
    result = cs.compute(bars(), {sig})
    assert result[sig].null_count() == len(result)
    assert cs.coverage(result, {sig})["signals"][0]["reasons"] == {"insufficient_structure": len(result)}


def test_pipeline_is_opt_in_even_when_czsc_is_installed(monkeypatch):
    from app.indicators.pipeline import compute_indicators, compute_signals
    monkeypatch.setattr(cs, "_load_runtime", lambda: pytest.fail("eager optional dependency"))
    result = compute_signals(compute_indicators(bars(70)))
    assert not set(result.columns) & cs.SIGNALS.keys()


def test_options_reports_missing_optional_dependency_without_breaking_api(monkeypatch):
    from app.api.signals import get_options
    monkeypatch.setattr(cs, "availability", lambda: {"available": False, "reason": "组件未安装", "version": None})
    options = get_options()
    assert options["czsc"]["available"] is False
    assert options["fields"]


def strategy(**kwargs):
    from app.strategy.engine import StrategyDef
    defaults = dict(
        meta={"id": "czsc_test", "name": "CZSC测试", "scoring": {}, "params": [], "limit": 100, "asset_types": ["stock", "etf"]},
        basic_filter={"enabled": False}, entry_signals=["signal_czsc_first_buy"],
        exit_signals=["signal_czsc_first_sell"], stop_loss=None, trailing_stop=None,
        trailing_take_profit_activate=None, trailing_take_profit_drawdown=None,
        max_hold_days=None, filter_fn=None, filter_history_fn=None, lookback_days=1, source="custom",
    )
    return StrategyDef(**(defaults | kwargs))


@pytest.mark.parametrize("field,value,error", [
    ("entry_fill", "close_t", "入场成交"),
    ("exit_fill", "close_t", "出场成交"),
    ("exit_fill", "signal_next_minute", "出场成交"),
    ("asset_type", "etf", "仅支持 A 股"),
])
def test_invalid_backtest_usage_is_rejected_before_data_loading(monkeypatch, field, value, error):
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    monkeypatch.setattr(cs, "_load_runtime", lambda: None)
    service = StrategyBacktestService(engine=SimpleNamespace(), strategy_engine=SimpleNamespace(get=lambda sid: strategy()))
    config = StrategyBacktestConfig(
        strategy_id="czsc_test", symbols=None, start=date(2024, 1, 1), end=date(2024, 3, 1),
        **{field: value},
    )
    assert error in service.run(config).error


def test_resolver_requests_raw_inputs_and_structural_history_only_when_selected(monkeypatch):
    from app.backtest.strategy import StrategyDependencyResolver
    monkeypatch.setattr(cs, "_load_runtime", lambda: None)
    resolver = StrategyDependencyResolver()
    plan = resolver.resolve(strategy(), params={}, basic_filter={}, entry_signals=["signal_czsc_first_buy"], exit_signals=[])
    assert plan.base_columns >= cs.INPUT_COLUMNS
    assert plan.warmup_bars >= cs.WARMUP_BARS
    # A dynamic Python filter's legacy full-feature fallback must remain optional-dependency-free.
    legacy = strategy(entry_signals=[], exit_signals=[], filter_history_fn=lambda df, params: df)
    plain = resolver.resolve(legacy, params={}, basic_filter={}, entry_signals=[], exit_signals=[])
    assert not cs.selected(plain.signal_columns)


def test_strategy_run_uses_history_and_ignores_future_rows(native_history):
    from app.strategy.engine import StrategyDataContext, StrategyEngine
    engine = StrategyEngine([])
    spec = strategy()
    engine._strategies["czsc_test"] = spec
    computed = cs.compute(native_history.head(600), set(spec.entry_signals + spec.exit_signals))
    target = computed.filter(pl.col("signal_czsc_first_buy").fill_null(False))["date"][-1]
    result = engine.run("czsc_test", StrategyDataContext(
        asset_type="stock", timeframe="1d", as_of=target,
        current=native_history.filter(pl.col("date") == target), history=native_history,
    ))
    assert result.entry_signal_hits == [{"symbol": "600000.SH", "signals": ["signal_czsc_first_buy"]}]
    assert result.czsc_coverage["signals"][0]["ready_rows"] == 1
    assert engine.required_history_bars(["czsc_test"]) >= cs.WARMUP_BARS


def test_empty_selection_keeps_coverage_for_only_the_requested_pool(monkeypatch):
    from app.strategy.engine import StrategyDataContext, StrategyEngine

    fake_runtime(monkeypatch, ["其他", "一买", "一买"])
    frame = pl.concat([bars(3), bars(3).with_columns(pl.lit("600001.SH").alias("symbol"))])
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = strategy(filter_history_fn=lambda df, params: df.head(0))
    target = date(2024, 1, 3)
    result = engine.run("czsc_test", StrategyDataContext(
        asset_type="stock", timeframe="1d", as_of=target,
        current=frame.filter(pl.col("date") == target), history=frame,
    ), pool=["600000.SH"])
    assert result.rows == []
    report = result.czsc_coverage["signals"][0]
    assert report["ready_rows"] == 1
    assert report["unavailable_rows"] == 0


@pytest.mark.parametrize("example", [None, "first", "second", "third"])
def test_backtest_native_signal_confirmed_before_fill(native_history, monkeypatch, tmp_path, example):
    from pathlib import Path

    from app.api.strategy import StrategyCodeSaveRequest, _save_strategy_code
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.strategy.engine import StrategyEngine
    frame = native_history.with_columns(
        pl.col("close").alias("raw_close"), pl.col("high").alias("raw_high"), pl.col("low").alias("raw_low"),
    )
    repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        get_instruments_asset=lambda at: pl.DataFrame({"symbol": ["600000.SH"], "name": ["测试股票"]}),
        get_historical_shares=lambda: pl.DataFrame(), get_index_daily=lambda *a, **k: pl.DataFrame(),
    )
    engine = BacktestEngine(repo)
    monkeypatch.setattr(engine, "load_panel", lambda symbols, start, end, **kw: frame.filter(pl.col("date").is_between(start, end)).select(kw["columns"]))
    strategies = StrategyEngine([tmp_path / "strategies" / "custom"])
    sid = "czsc_test"
    if example:
        sid = f"custom_czsc_{example}_bs"
        path = Path(__file__).resolve().parents[2] / "docs" / "examples" / "czsc-strategies" / f"{sid}.py"
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=strategies)))
        saved = _save_strategy_code(StrategyCodeSaveRequest(
            strategy_id=sid, code=path.read_text(), target_source="custom", mode="create",
        ), request)
        assert saved["ok"] is True
        assert sid in {meta["id"] for meta in strategies.list_strategies()}
        assert strategies.get(sid).entry_signals == [f"signal_czsc_{example}_buy"]
    else:
        strategies._strategies[sid] = strategy()
    service = StrategyBacktestService(engine, strategies)
    config = StrategyBacktestConfig(strategy_id=sid, symbols=["600000.SH"], start=frame["date"][300], end=frame["date"][-1])
    result = service.run(config)
    assert result.error is None, result.error
    assert result.trades
    assert result.stats["czsc_coverage"]["signals"]
    for trade in result.trades:
        assert str(trade["entry_date"]) > str(trade["entry_signal_date"])
        if trade["exit_reason"] == "signal":
            assert str(trade["exit_date"]) > str(trade["exit_signal_date"])
    insufficient = service.run(StrategyBacktestConfig(
        strategy_id=sid, symbols=["600000.SH"], start=frame["date"][0], end=frame["date"][29],
    ))
    assert insufficient.error
    assert insufficient.stats["czsc_coverage"]["signals"][0]["reasons"]["insufficient_structure"] == 30


@pytest.mark.parametrize("method", ["save", "patch", "reset", "reset_unknown"])
def test_czsc_config_changes_invalidate_only_affected_strategy(tmp_path, method):
    from app.api.strategy import SaveConfigRequest, patch_config, reset_config, save_config
    from app.services import strategy_cache
    from app.strategy import config as strategy_config
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine([])
    spec = strategy(entry_signals=[], exit_signals=[])
    engine._strategies["czsc_test"] = spec
    prior = {"entry_signals": ["signal_czsc_first_buy"]} if method.startswith("reset") else {}
    if method == "reset_unknown":
        prior = {"entry_signals": ["signal_czsc_retired"]}
    strategy_config.save_override(tmp_path, "czsc_test", prior)
    strategy_cache.write_cache(tmp_path, "2024-01-01", {
        "czsc_test": {"rows": [{"symbol": "600000.SH"}], "total": 1},
        "unrelated": {"rows": [{"symbol": "600001.SH"}], "total": 1},
    })
    calls = []
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        strategy_engine=engine, repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        monitor_engine=SimpleNamespace(invalidate_strategy_state=lambda **kw: calls.append(kw)),
    )))
    if method.startswith("reset"):
        reset_config("czsc_test", request)
    else:
        handler = save_config if method == "save" else patch_config
        handler(SaveConfigRequest(strategy_id="czsc_test", overrides={"entry_signals": ["signal_czsc_first_buy"]}), request)
    cached = strategy_cache.read_cache(tmp_path)
    assert set(cached["results"]) == {"unrelated"}
    assert "czsc_test" not in cached["today_ever_rows"]
    assert calls == [{"strategy_id": "czsc_test"}]


@pytest.mark.parametrize("as_object", [False, True])
def test_sse_error_preserves_czsc_coverage(monkeypatch, as_object):
    import json
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import backtest
    from app.backtest.strategy import StrategyBacktestResult

    report = {"version": cs.VERSION, "signals": [{"signal_id": "signal_czsc_first_buy", "ready_rows": 0}]}
    result = {"error": "没有买入信号", "stats": {"czsc_coverage": report}}
    if as_object:
        result = StrategyBacktestResult(run_id="test", config={}, **result)
    job = backtest._BacktestJob("czsc-test")
    job.result, job.done, job.finish_ts = result, True, time.time()
    monkeypatch.setattr(backtest, "_make_job_key", lambda *args, **kwargs: "czsc-test")
    monkeypatch.setattr(backtest, "_running_jobs", {"czsc-test": job})
    app = FastAPI()
    app.include_router(backtest.router)
    response = TestClient(app).get("/api/backtest/strategy/stream", params={
        "strategy_id": "czsc_test", "start": "2024-01-01", "end": "2024-01-02",
    })
    assert response.status_code == 200
    assert "event: error" in response.text
    payload = json.loads(response.text.split("data: ")[1].strip())
    assert payload == {"message": "没有买入信号", "stats": {"czsc_coverage": report}}
