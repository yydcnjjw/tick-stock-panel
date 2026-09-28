from datetime import date
from types import SimpleNamespace

import pytest

from app.api.strategy import _strategy_detail
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.indicators import chan_signals, czsc_signals
from app.strategy.engine import StrategyDataContext, StrategyEngine
from tests.test_czsc_signals import strategy


@pytest.mark.parametrize("field", ["entry_signals", "exit_signals", "required_features"])
def test_retired_declarations_cannot_be_reactivated_by_empty_overrides(field):
    spec = strategy(entry_signals=[], exit_signals=[], **({} if field != "required_features" else {field: []}))
    setattr(spec, field, ["signal_czsc_first_buy"])
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    overrides = {"entry_signals": [], "exit_signals": []}
    detail = _strategy_detail(spec, overrides, engine)
    assert detail["id"] == "czsc_test"
    assert detail["execution_available"] is False
    assert "已停用" in detail["execution_unavailable_reason"]
    with pytest.raises(ValueError, match="已停用"):
        engine.run("czsc_test", StrategyDataContext(asset_type="stock", timeframe="1d", as_of=date(2024, 1, 2)), overrides=overrides)


def test_retired_backtest_rejects_before_loading_data():
    spec = strategy(entry_signals=["signal_czsc_first_buy"])
    service = StrategyBacktestService(SimpleNamespace(), SimpleNamespace(get=lambda _: spec))
    result = service.run(StrategyBacktestConfig(strategy_id="czsc_test", symbols=None,
                                              start=date(2024, 1, 1), end=date(2024, 2, 1)))
    assert "已停用" in result.error


def test_legacy_ids_are_never_aliased_to_new_meanings():
    assert not czsc_signals.availability()["available"]
    with pytest.raises(ValueError, match="已停用"):
        czsc_signals.compute(None, {"signal_czsc_first_buy"})
    with pytest.raises(ValueError, match="已停用"):
        chan_signals.selected(["czsc_first_buy"])
    assert chan_signals.selected(["chan_bi_1_buy"]) == {"signal_chan_bi_1_buy"}


def test_explicit_bulk_run_rejects_retired_strategy_before_data_access(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.api import screener

    spec = strategy(entry_signals=["signal_czsc_first_buy"])
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine)))
    monkeypatch.setattr(screener, "ScreenerService", lambda *a, **kw: SimpleNamespace())
    with pytest.raises(HTTPException) as error:
        screener.run_all(request, {"as_of": "2024-01-02", "strategy_ids": ["czsc_test"]})
    assert error.value.status_code == 400
    assert "已停用" in str(error.value.detail)
    assert engine.list_strategies()[0]["execution_available"] is False


def test_default_bulk_run_skips_retired_overrides(tmp_path, monkeypatch):
    from app.api import strategy as strategy_api
    from app.services import screener

    spec = strategy(entry_signals=[], exit_signals=[])
    engine = StrategyEngine([])
    engine._strategies["czsc_test"] = spec
    seen = []

    class Service:
        def __init__(self, *args, **kwargs):
            pass

        def build_strategy_context(self, engine, as_of, ids, **kwargs):
            seen.extend(ids)

    monkeypatch.setattr(screener, "ScreenerService", Service)
    monkeypatch.setattr(strategy_api.strategy_config, "list_overrides", lambda _: {
        "czsc_test": {"entry_signals": ["signal_czsc_first_buy"]},
    })
    monkeypatch.setattr(engine, "run_all", lambda *a, **kw: {})
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine)))
    result = strategy_api.run_all(strategy_api.RunAllRequest(as_of=date(2024, 1, 2)), request)
    assert result["results"] == {}
    assert seen == []
