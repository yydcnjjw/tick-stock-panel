"""Registration, public data gate, and isolation from ordinary screening."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.strategy import router
from app.backtest.chan_center import FORMAL_BLOCK_REASON
from app.backtest.engine import BacktestEngine
from app.backtest.matrix import build_market_data_matrix
from app.backtest.strategy import (
    BacktestResultPolicy,
    StrategyBacktestConfig,
    StrategyBacktestService,
    StrategyDependencyResolver,
)
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.strategy.monitor import czsc_strategy_monitor_warning
from tests.test_chan_runtime import history


def test_builtin_detail_api_and_dependency_plan(tmp_path):
    engine = StrategyEngine(strategy_dirs=[Path(__file__).parents[1] / "app/strategy/builtin"])
    strategy = engine.get("chan_center_range")
    assert strategy.stop_loss is None
    assert strategy.max_hold_days is None
    assert strategy.execution_backend == "matrix_native"
    app = FastAPI()
    app.state.strategy_engine = engine
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(router)
    with TestClient(app) as client:
        listing = client.get("/api/strategies").json()
        assert any(s["id"] == "chan_center_range" for s in listing["strategies"])
        detail = client.get("/api/strategies/chan_center_range")
        assert detail.status_code == 200
        assert detail.json()["backtest_only"]
        assert detail.json()["backtest_block_reason"] == FORMAL_BLOCK_REASON
        assert detail.json()["backtest_exploratory_param"] == "exploratory"
        assert "历史" in detail.json()["backtest_exploratory_warning"]
        assert detail.json()["params_defaults"]["exploratory"] is False
        assert detail.json()["params_defaults"]["rule_set"] == "完整新版"
        rule_param = next(p for p in detail.json()["params"] if p["id"] == "rule_set")
        assert rule_param["option_details"]["中枢结构实验保守版"]["risk_fraction"] == .005
        assert rule_param["option_details"]["中枢结构实验版"]["risk_sizing"] is True
        assert rule_param["option_details"]["第24课止损实验版"]["risk_fraction"] == .01
    plan = StrategyDependencyResolver().resolve(
        strategy, params={}, basic_filter={"enabled": False}, entry_signals=[],
        exit_signals=[], overrides={}, minute_fill=False, asset_type="stock")
    assert plan.warmup_bars == 1000
    assert "amount" in plan.base_columns
    engine.reload()
    assert engine.get("chan_center_range").meta["backtest_block_reason"] == FORMAL_BLOCK_REASON


def test_formal_backtest_and_optimizer_fail_before_loading_current_universe():
    engine = StrategyEngine(strategy_dirs=[Path(__file__).parents[1] / "app/strategy/builtin"])
    service = StrategyBacktestService(BacktestEngine(repo=None), engine)
    config = StrategyBacktestConfig("chan_center_range", None, date(2024, 1, 1), date(2024, 12, 31))
    result = service.run(config)
    assert result.error == FORMAL_BLOCK_REASON
    assert result.stats == {}
    assert result.trades == []
    assert result.equity_curve == []
    with pytest.raises(ValueError, match="历史股票池"):
        service.prepare_matrix_optimization([config])


def test_cannot_run_as_live_screen_or_monitor():
    engine = StrategyEngine(strategy_dirs=[Path(__file__).parents[1] / "app/strategy/builtin"])
    context = StrategyDataContext(asset_type="stock", timeframe="1d", as_of=date(2024, 1, 1),
                                  current=pl.DataFrame({"symbol": ["600000.SH"]}))
    with pytest.raises(ValueError, match="仅支持回测"):
        engine.run("chan_center_range", context)
    with pytest.raises(ValueError, match="仅支持回测"):
        engine.run_all(context, strategy_ids=["chan_center_range"])
    assert "仅支持回测" in czsc_strategy_monitor_warning(engine.get("chan_center_range"))


def test_exploratory_run_uses_native_signals_and_persists_scope(monkeypatch):
    rows = history(1200)
    market = build_market_data_matrix(pl.DataFrame(rows), field_columns={"amount"})
    engine = StrategyEngine(strategy_dirs=[Path(__file__).parents[1] / "app/strategy/builtin"])
    matcher = BacktestEngine(repo=None)
    monkeypatch.setattr(matcher, "load_market_data_matrix_for_backtest", lambda *a, **kw: market)
    service = StrategyBacktestService(matcher, engine)
    monkeypatch.setattr(service, "_build_benchmark_curve", lambda *a: [])
    config = StrategyBacktestConfig(
        "chan_center_range", None, rows[100]["date"], rows[-1]["date"],
        params={"exploratory": True}, initial_capital=100_000, max_positions=2,
    )
    result = service.run(config)
    assert result.error is None
    assert result.trades
    assert result.equity_curve
    assert all(t["entry_signal_date"] < t["entry_date"] for t in result.trades)
    assert all(t["center_reference"] for t in result.trades)
    assert max(row["positions"] for row in result.equity_curve) <= 2
    scope = result.stats["research_scope"]
    assert scope["mode"] == "exploratory"
    assert scope["historical_eligibility_verified"] is False
    assert scope["st_filter"] == "none"
    assert scope["symbols_with_valid_bars"] == 1
    assert scope["market_days"] == 1100
    assert scope["warning"]
    assert result.config["params"]["exploratory"] is True
    assert result.config["params"]["rule_set"] == "原版"  # old request remains reproducible
    assert result.stats["center_rules"]["rule_set"] == "原版"
    # An exploratory backtest does not authorize parameter optimization.
    with pytest.raises(ValueError, match="历史股票池"):
        service.prepare_matrix_optimization([config])


@pytest.mark.parametrize("params", [{}, {"exploratory": False}, {"exploratory": "false"}, {"exploratory": 1}])
def test_exploratory_requires_explicit_true(params):
    engine = StrategyEngine(strategy_dirs=[Path(__file__).parents[1] / "app/strategy/builtin"])
    service = StrategyBacktestService(BacktestEngine(repo=None), engine)
    config = StrategyBacktestConfig(
        "chan_center_range", None, date(2024, 1, 1), date(2024, 12, 31), params=params,
    )
    assert service.run(config).error == FORMAL_BLOCK_REASON


def test_exploratory_data_error_preserves_scope_and_current_name_filter(monkeypatch):
    engine = StrategyEngine(strategy_dirs=[Path(__file__).parents[1] / "app/strategy/builtin"])
    matcher = BacktestEngine(repo=None)
    def no_data(*args, **kwargs):
        raise ValueError("sample unavailable")
    monkeypatch.setattr(matcher, "load_market_data_matrix_for_backtest", no_data)
    service = StrategyBacktestService(matcher, engine)
    config = StrategyBacktestConfig(
        "chan_center_range", None, date(2024, 1, 1), date(2024, 12, 31),
        params={"exploratory": True},
        overrides={"basic_filter": {"enabled": True, "exclude_st": True}},
    )
    result = service.run(config)
    assert "sample unavailable" in result.error
    assert result.stats["research_scope"]["st_filter"] == "current_name"
    assert "额外启用了当前名称过滤" in result.stats["research_scope"]["warning"]
    assert BacktestResultPolicy(required_stats=frozenset()).select_stats(result.stats) == result.stats


@pytest.mark.parametrize(("rule_set", "risk", "gate"), [
    ("完整新版", .01, True), ("中枢结构实验版", .01, False), ("中枢结构实验保守版", .005, False),
    ("第24课止损实验版", .01, False),
])
def test_selected_center_rules_reach_matcher_and_saved_result(monkeypatch, rule_set, risk, gate):
    import numpy as np

    from app.backtest.chan_center import CENTER_DTYPE, CenterFeatures
    from app.backtest.matrix import make_signal_matrix

    rows = history(8)
    market = build_market_data_matrix(pl.DataFrame(rows), field_columns={"amount"})
    values = np.zeros(market.shape, dtype=CENTER_DTYPE)
    values["valid"] = True
    values["eligible"] = True
    values["center"] = 1
    values["low"] = 10
    values["high"] = 12
    values["bottom"] = np.nan
    values["top"] = np.nan
    values["bottom"][0] = 10.1
    values["bottom_at"][0] = rows[0]["date"].toordinal() - 1
    from app.backtest.chan_center_lesson24 import Lesson24Features
    features = CenterFeatures(values, np.array([r["date"].toordinal() for r in rows]),
                              lesson24=Lesson24Features() if rule_set == "第24课止损实验版" else None).readonly()
    engine = StrategyEngine(strategy_dirs=[Path(__file__).parents[1] / "app/strategy/builtin"])
    monkeypatch.setattr(engine.get("chan_center_range").matrix_strategy, "compute_signals",
                        lambda *a: make_signal_matrix(market.shape, entry=np.ones(market.shape),
                                                      center_features=features))
    matcher = BacktestEngine(repo=None)
    monkeypatch.setattr(matcher, "load_market_data_matrix_for_backtest", lambda *a, **kw: market)
    original = matcher.simulate_market_matrix
    observed = []
    def capture(matrix, cfg, *a):
        observed.append(cfg.center_policy)
        return original(matrix, cfg, *a)
    monkeypatch.setattr(matcher, "simulate_market_matrix", capture)
    service = StrategyBacktestService(matcher, engine)
    monkeypatch.setattr(service, "_build_benchmark_curve", lambda *a: [])
    result = service.run(StrategyBacktestConfig(
        "chan_center_range", None, rows[0]["date"], rows[-1]["date"],
        params={"exploratory": True, "rule_set": rule_set}, initial_capital=100000, max_positions=2,
    ))
    assert result.error is None
    assert observed[0].entry_gate == gate
    assert observed[0].extended_exit and observed[0].risk_sizing
    assert result.config["params"]["rule_set"] == rule_set
    assert result.stats["center_rules"]["risk_fraction"] == risk
    if rule_set == "第24课止损实验版":
        assert observed[0].lesson24_stop
        assert result.stats["center_rules"]["risk_basis"] == "entry_to_frozen_c_low_including_both_costs"
    elif not gate:
        assert result.stats["center_rules"]["center_relation"] == "non_lower"
        assert result.stats["center_rules"]["ranking"] == "reward_risk"
    assert "center_rules" in BacktestResultPolicy(required_stats=frozenset()).select_stats(result.stats)
