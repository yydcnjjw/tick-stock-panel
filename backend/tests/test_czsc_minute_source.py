from datetime import datetime
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.kline import router
from app.data_providers import capabilities, custom
from app.services import czsc_chart, kline_sync, preferences
from app.tickflow.capabilities import CapabilitySet
from app.tickflow.repository import KlineRepository


def bars(price):
    return pl.DataFrame({
        "symbol": ["001332.SZ"], "datetime": [datetime(2026, 9, 21, 9, 31)],
        "open": [price], "high": [price], "low": [price], "close": [price],
        "volume": [10.0], "amount": [1000.0],
    })


@pytest.fixture
def setup(monkeypatch, tmp_path):
    repo = object.__new__(KlineRepository)
    repo.store = SimpleNamespace(data_dir=tmp_path)
    repo._minute_glob = str(tmp_path / "kline_minute" / "**" / "*.parquet")
    repo._write_lock = RLock()
    repo.resolve_asset_type = lambda _: "stock"
    repo.get_name_map = lambda symbols: dict.fromkeys(symbols, "测试股票")
    provider = SimpleNamespace(get_minute=MagicMock(return_value=bars(20.0)))
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "stocksdk")
    monkeypatch.setattr(preferences, "get_czsc_minute_data_provider", lambda: "exchange_minute")
    monkeypatch.setattr(custom, "get_provider", lambda _: provider)
    monkeypatch.setattr(custom, "provider_has_dataset", lambda name, dataset: name == "exchange_minute" and dataset == "minute")
    monkeypatch.setattr(capabilities, "_declared_sources", lambda: [{
        "name": "exchange_minute", "display": "交易所分钟行情", "datasets": {"minute"},
        "available": True, "status": "ok", "kind": "plugin",
    }])
    native = MagicMock(side_effect=AssertionError("unexpected cross-source fallback"))
    monkeypatch.setattr(kline_sync, "get_client", native)
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    app.state.capabilities = CapabilitySet()
    return repo, provider, TestClient(app), native


def test_sync_and_chart_isolate_same_symbol_and_timestamp(setup):
    repo, provider, client, native = setup
    kline_sync._write_minute_partition(bars(10.0), repo.store.data_dir / "kline_minute")
    original = repo.get_minute_chart_generation()
    response = client.post("/api/kline/sync_minute_single", json={
        "symbol": "001332.SZ", "days": 30, "purpose": "czsc", "provider": "exchange_minute",
    })
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "exchange_minute"
    assert repo.get_minute_chart_generation() == original
    assert repo.get_minute_published("001332.SZ", datetime(2027, 1, 1), 5)["close"].to_list() == [10.0]
    assert repo.get_minute_published("001332.SZ", datetime(2027, 1, 1), 5, provider="exchange_minute")["close"].to_list() == [20.0]
    generation = repo.get_minute_chart_generation(provider="exchange_minute")
    kline_sync._write_minute_partition(bars(12.0), repo.store.data_dir / "kline_minute")
    assert repo.get_minute_chart_generation(provider="exchange_minute") == generation
    assert czsc_chart.get_chart(repo, "001332.SZ", timeframe="1m", now=datetime(2026, 9, 21, 10))["rows"][0]["close"] == 20.0
    provider.get_minute.assert_called_once()
    native.assert_not_called()


@pytest.mark.parametrize("failure,status", [(RuntimeError("fetch failed"), 502), (None, 422)])
def test_czsc_failed_or_empty_sync_never_falls_back_or_writes(setup, failure, status):
    repo, provider, client, native = setup
    if failure:
        provider.get_minute.side_effect = failure
    else:
        provider.get_minute.return_value = pl.DataFrame()
    response = client.post("/api/kline/sync_minute_single", json={"symbol": "001332.SZ", "purpose": "czsc"})
    assert response.status_code == status, response.text
    assert not list(repo.store.data_dir.rglob("*.parquet"))
    native.assert_not_called()


def test_czsc_missing_capability_and_changed_source_reject_before_fetch(setup, monkeypatch):
    _, provider, client, _ = setup
    response = client.post("/api/kline/sync_minute_single", json={
        "symbol": "001332.SZ", "purpose": "czsc", "provider": "stocksdk",
    })
    assert response.status_code == 409
    monkeypatch.setattr(capabilities, "_declared_sources", lambda: [])
    response = client.post("/api/kline/sync_minute_single", json={"symbol": "001332.SZ", "purpose": "czsc"})
    assert response.status_code == 403
    provider.get_minute.assert_not_called()


def test_source_switch_never_reuses_other_source_or_legacy_history(setup, monkeypatch):
    repo, _, _, _ = setup
    kline_sync._write_minute_partition(bars(10.0), repo.store.data_dir / "kline_minute")
    kline_sync._write_minute_partition(bars(20.0), repo.minute_chart_root("exchange_minute"))
    now = datetime(2026, 9, 21, 10)
    assert czsc_chart.get_chart(repo, "001332.SZ", timeframe="1m", now=now)["rows"][0]["close"] == 20.0
    monkeypatch.setattr(preferences, "get_czsc_minute_data_provider", lambda: "stocksdk")
    result = czsc_chart.get_chart(repo, "001332.SZ", timeframe="1m", now=now)
    assert result["status"] == "empty"
    assert result["minute_provider"] == "stocksdk"
    assert result["minute_sync_available"] is False


def test_minute_provider_directory_cannot_escape_data_root(setup):
    repo, _, _, _ = setup
    with pytest.raises(ValueError):
        repo.minute_chart_root("../../outside")
