from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import monitor_rules as monitor_api
from app.config import settings
from app.indicators import czsc_signals
from app.strategy import config, monitor_rules
from app.strategy.engine import StrategyEngine, StrategyResult
from app.strategy.monitor import MonitorRuleEngine


def _rule(**changes):
    return monitor_rules.normalize({
        "id": "czsc_rule",
        "name": "日线策略监控",
        "type": "strategy",
        "strategy_id": "demo",
        "scope": "all",
        "notify_events": ["buy_signal", "sell_signal", "pool_entry", "pool_exit"],
        **changes,
    })


def _strategy(sid="demo"):
    return SimpleNamespace(
        meta={"id": sid, "name": "示例策略"},
        entry_signals=["signal_ma20_breakout"],
        exit_signals=[],
        required_features=frozenset(),
        execution_backend="polars_expr",
        filter_history_fn=None,
    )


def _quotes():
    return pl.DataFrame({"symbol": ["600415.SH"], "close": [10.0], "change_pct": [0.01]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # 任何导入原生组件或重算的尝试都使测试失败, 即使业务层吞掉异常。
    runtime = Mock(side_effect=AssertionError("盘中禁止加载 CZSC"))
    compute = Mock(side_effect=AssertionError("盘中禁止重算 CZSC"))
    monkeypatch.setattr(czsc_signals, "_load_runtime", runtime)
    monkeypatch.setattr(czsc_signals, "compute", compute)
    strategy = _strategy()
    strategies = {"demo": strategy, "ordinary": _strategy("ordinary")}
    engine = Mock()
    engine.get.side_effect = strategies.__getitem__
    engine.validate_context.side_effect = StrategyEngine.validate_context
    engine.required_history_bars.return_value = 1
    engine.run.return_value = StrategyResult(
        as_of=date(2026, 9, 14), strategy_id="demo", rows=_quotes().to_dicts(),
        total=1, scores={},
        entry_signal_hits=[{"symbol": "600415.SH", "signals": ["signal_ma20_breakout"]}],
    )
    handler = Mock()
    monitor = MonitorRuleEngine(alert_handler=handler)
    monitor.set_strategy_engine(engine)
    monitor.set_data_dir(tmp_path)
    history = Mock(side_effect=AssertionError("禁止加载历史数据"))
    monitor.set_history_loader(history)
    app = FastAPI()
    app.include_router(monitor_api.router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.state.strategy_engine = engine
    app.state.monitor_engine = monitor
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, path=tmp_path, strategy=strategy, engine=engine,
            monitor=monitor, history=history, handler=handler,
        )
    runtime.assert_not_called()
    compute.assert_not_called()


@pytest.mark.parametrize("source", [
    "entry", "exit", "override_entry", "override_exit", "fallback_entry", "required",
])
def test_save_rejects_czsc_from_effective_strategy_dependencies(env, source):
    if source == "entry":
        env.strategy.entry_signals = ["signal_czsc_first_buy"]
    elif source == "exit":
        env.strategy.exit_signals = ["signal_czsc_first_sell"]
    elif source == "required":
        env.strategy.required_features = frozenset({"signal_czsc_third_buy"})
        config.save_override(env.path, "demo", {"entry_signals": [], "exit_signals": []})
    elif source == "fallback_entry":
        env.strategy.entry_signals = ["signal_czsc_first_buy"]
        config.save_override(env.path, "demo", {"entry_signals": None})
    else:
        config.save_override(env.path, "demo", {
            f"{source.removeprefix('override_')}_signals": ["czsc_second_sell"],
        })

    response = env.client.post("/api/monitor-rules", json=_rule())
    assert response.status_code == 400
    assert response.json()["detail"] == czsc_signals.MONITOR_WARNING
    assert monitor_rules.load_all(env.path) == []
    assert env.monitor.rule_count == 0
    # 即使默认只保存为停用规则, 也不能新建不受支持的盘中监控。
    assert env.client.post("/api/monitor-rules", json=_rule(enabled=False)).status_code == 400
    monitor_rules.save_one(env.path, _rule())
    assert env.client.get("/api/monitor-rules").json()["rules"][0]["runtime_warning"] == czsc_signals.MONITOR_WARNING


def test_legacy_rule_warning_rejects_enable_but_allows_disabling(env):
    legacy = _rule()
    monitor_rules.save_one(env.path, legacy)
    config.save_override(env.path, "demo", {"exit_signals": ["signal_czsc_third_sell"]})

    listed = env.client.get("/api/monitor-rules").json()["rules"]
    assert listed[0]["runtime_warning"] == czsc_signals.MONITOR_WARNING
    assert "runtime_warning" not in monitor_rules.load_one(env.path, legacy["id"])
    disabled = env.client.post("/api/monitor-rules", json=_rule(enabled=False))
    assert disabled.status_code == 200
    assert env.monitor.rule_count == 0
    enabled = env.client.post("/api/monitor-rules", json=_rule(enabled=True))
    assert enabled.status_code == 400
    assert enabled.json()["detail"] == czsc_signals.MONITOR_WARNING
    assert monitor_rules.load_one(env.path, legacy["id"])["enabled"] is False
    assert env.client.get("/api/monitor-rules").json()["rules"][0]["runtime_warning"]

    config.save_override(env.path, "demo", {"exit_signals": []})
    assert "runtime_warning" not in env.client.get("/api/monitor-rules").json()["rules"][0]


@pytest.mark.parametrize("signal", czsc_signals.SIGNALS)
def test_plain_rule_cannot_use_czsc_conditions(env, signal):
    rule = _rule(type="signal", strategy_id=None, conditions=[{"field": signal, "op": "truth"}])
    response = env.client.post("/api/monitor-rules", json=rule)
    assert response.status_code == 400
    assert response.json()["detail"] == czsc_signals.MONITOR_WARNING
    assert monitor_rules.load_all(env.path) == []


def test_monitor_options_exclude_czsc(env):
    response = env.client.get("/api/monitor-rules/options")
    assert response.status_code == 200
    signals = {item["key"] for item in response.json()["builtin_signals"]}
    assert not signals.intersection(czsc_signals.SIGNALS)
    assert "signal_ma20_breakout" in signals


@pytest.mark.parametrize("snapshot", ["quotes", "empty", "outside_scope"])
def test_override_change_skips_runtime_clears_only_affected_state_and_rebaselines(env, snapshot):
    monitor = env.monitor
    rule = _rule(scope="symbols", symbols=["600415.SH"])
    ordinary = _rule(id="ordinary_rule", strategy_id="ordinary")
    monitor.set_rules([rule, ordinary])
    assert monitor.evaluate(_quotes()) == []
    assert set(monitor.latest_strategy_results()) == {"demo", "ordinary"}
    monitor._last_fire[(rule["id"], "600415.SH", "buy_signal")] = 100
    monitor._last_fire[(ordinary["id"], "600415.SH", "buy_signal")] = 200
    ordinary_pools = {k: v for k, v in monitor._strategy_pools.items() if k[0] == ordinary["id"]}
    ordinary_states = {k: v for k, v in monitor._strategy_signal_state.items() if k[0] == ordinary["id"]}
    ordinary_seen = {k: v for k, v in monitor._strategy_signal_seen.items() if k[0] == ordinary["id"]}
    published = monitor.latest_strategy_results()
    config.save_override(env.path, "demo", {"entry_signals": ["signal_czsc_first_buy"]})
    env.engine.run.reset_mock()
    current = _quotes()
    if snapshot == "empty":
        current = current.clear()
    elif snapshot == "outside_scope":
        current = current.with_columns(pl.lit("000001.SZ").alias("symbol"))

    assert monitor.evaluate(current, reset_strategy_results=False) == []
    assert all(call.args[0] != "demo" for call in env.engine.run.call_args_list)
    assert set(monitor.latest_strategy_results()) == {"ordinary"}
    assert "demo" in published  # 已发布快照不被就地修改。
    assert "demo" not in monitor._building_strategy_results
    assert monitor._strategy_pools == ordinary_pools
    assert monitor._strategy_signal_state == ordinary_states
    assert monitor._strategy_signal_seen == ordinary_seen
    assert monitor._last_fire == {(ordinary["id"], "600415.SH", "buy_signal"): 200}
    env.history.assert_not_called()
    env.handler.assert_not_called()

    config.save_override(env.path, "demo", {"entry_signals": []})
    assert monitor.evaluate(_quotes()) == []
    assert "demo" in monitor.latest_strategy_results()
    env.handler.assert_not_called()


@pytest.mark.parametrize("backend", ["polars_expr", "matrix_native"])
def test_required_czsc_skips_before_history_matrix_or_strategy_run(env, backend):
    env.strategy.execution_backend = backend
    env.strategy.required_features = frozenset({"signal_czsc_second_buy"})
    env.monitor.set_rules([_rule()])

    assert env.monitor.evaluate(_quotes()) == []
    env.engine.run.assert_not_called()
    env.engine.required_history_bars.assert_not_called()
    env.engine.prepare_realtime_matrix.assert_not_called()
    env.history.assert_not_called()
    env.handler.assert_not_called()


@pytest.mark.parametrize("cleared_defaults", [False, True])
def test_no_effective_czsc_preserves_save_list_and_runtime(env, cleared_defaults):
    if cleared_defaults:
        env.strategy.entry_signals = ["signal_czsc_first_buy"]
        env.strategy.exit_signals = ["signal_czsc_first_sell"]
        config.save_override(env.path, "demo", {"entry_signals": [], "exit_signals": []})
    response = env.client.post("/api/monitor-rules", json=_rule())
    assert response.status_code == 200
    assert "runtime_warning" not in env.client.get("/api/monitor-rules").json()["rules"][0]
    assert env.monitor.evaluate(_quotes()) == []
    env.engine.run.assert_called_once()
    assert env.monitor.latest_strategy_results()["demo"]["total"] == 1
    env.handler.assert_not_called()


def test_invalidate_one_strategy_preserves_other_strategies_and_shared_market(env):
    monitor = env.monitor
    monitor.set_rules([_rule(), _rule(id="ordinary_rule", strategy_id="ordinary")])
    assert monitor.evaluate(_quotes()) == []
    monitor._last_fire = {
        ("czsc_rule", "600415.SH", "buy_signal"): 100,
        ("ordinary_rule", "600415.SH", "buy_signal"): 200,
    }
    shared_market = object()
    monitor._active_matrix_snapshots = {"stock": shared_market}
    states = (monitor._strategy_pools, monitor._strategy_signal_state, monitor._strategy_signal_seen)
    expected = [{k: v for k, v in state.items() if k[1] == "ordinary"} for state in states]
    published = monitor.latest_strategy_results()

    monitor.invalidate_strategy_state(strategy_id="demo")

    assert [monitor._strategy_pools, monitor._strategy_signal_state, monitor._strategy_signal_seen] == expected
    assert monitor._last_fire == {("ordinary_rule", "600415.SH", "buy_signal"): 200}
    assert set(monitor.latest_strategy_results()) == {"ordinary"}
    assert set(monitor._building_strategy_results) == {"ordinary"}
    assert monitor._latest_strategy_result_ids == {"ordinary"}
    assert monitor._active_matrix_snapshots == {"stock": shared_market}
    assert set(published) == {"demo", "ordinary"}
    assert monitor.rule_count == 2

    monitor.invalidate_strategy_state()
    assert not monitor._strategy_pools
    assert not monitor._strategy_signal_state
    assert not monitor._strategy_signal_seen
    assert not monitor.latest_strategy_results()
    assert not monitor._building_strategy_results
    assert not monitor._latest_strategy_result_ids
    assert not monitor._active_matrix_snapshots
    # 无参仍保留原有规则冷却行为。
    assert monitor._last_fire == {("ordinary_rule", "600415.SH", "buy_signal"): 200}


def test_multiple_rules_for_blocked_strategy_keep_cache_invalidation_update(env):
    monitor = env.monitor
    monitor.set_rules([_rule(), _rule(id="second_rule")])
    assert monitor.evaluate(_quotes()) == []
    monitor.consume_strategy_result_updates()
    config.save_override(env.path, "demo", {"exit_signals": ["signal_czsc_first_sell"]})
    env.engine.run.reset_mock()

    assert monitor.evaluate(_quotes()) == []
    assert not monitor.latest_strategy_results()
    assert not monitor._strategy_pools
    assert not monitor._strategy_signal_state
    assert not monitor._strategy_signal_seen
    assert monitor.consume_strategy_result_updates() is True
    env.engine.run.assert_not_called()
