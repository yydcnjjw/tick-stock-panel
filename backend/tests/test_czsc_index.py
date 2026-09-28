from datetime import date, datetime, timedelta

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.kline import router
from app.enriched_generation import EnrichedGenerationUnavailableError
from app.indicators import chan_signals as cs
from app.indicators.czsc_bars import prepare_bars
from app.indicators.czsc_chart import build_chart
from app.market_time import CN_TZ
from app.services.czsc_chart import get_chart
from app.tickflow.repository import DataStore, KlineRepository


def bars(symbol, value=3000.0):
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(19)]
    return pl.DataFrame({
        "symbol": [symbol] * len(days), "date": days,
        "open": value, "high": value + 2, "low": value - 2, "close": value + 1,
        "volume": 1234.0, "amount": 567890.0,
    })


@pytest.fixture
def repo(tmp_path):
    store = DataStore(tmp_path)
    result = KlineRepository(store)
    result._name_map_cache = None
    result._instruments_cache = pl.DataFrame({"symbol": ["000001.SZ"], "name": ["平安银行"]})
    result._etf_instruments_cache = pl.DataFrame({"symbol": ["510300.SH"], "name": ["沪深300ETF"]})
    result._index_instruments_cache = pl.DataFrame({
        "symbol": ["000001.SH", "000300.SH", "000832.SH"],
        "name": ["上证指数", "沪深300", "中证转债"], "asset_type": ["index"] * 3,
    })
    result.append_enriched(bars("000001.SZ", 10.0))
    result.append_index_enriched(pl.concat([bars("000001.SH"), bars("000300.SH", 3500.0)]))
    yield result
    store.db.close()


def test_index_fingerprint_changes_only_with_its_data_and_noop_is_stable(repo):
    stock_generation = repo.get_matrix_data_generation("stock", readonly=True)
    before = repo.get_index_chart_generation()
    repo.append_index_enriched(bars("000300.SH", 3500.0))
    assert repo.get_index_chart_generation() == before
    repo.append_index_enriched(bars("000300.SH", 3600.0))
    assert repo.get_index_chart_generation() != before
    assert repo.get_matrix_data_generation("stock", readonly=True) == stock_generation


@pytest.mark.parametrize("timeframe", ["1d", "1w"])
def test_index_closed_bars_share_native_chart_semantics_and_refresh(repo, timeframe):
    now = datetime(2024, 1, 19, 14, tzinfo=CN_TZ)
    first = get_chart(repo, "000300.SH", timeframe=timeframe, now=now)
    assert first["asset_type"] == "index"
    assert first["source"] == "local_index_enriched"
    expected = build_chart(prepare_bars(bars("000300.SH", 3500.0), timeframe, now=now),
                           timeframe=timeframe, now=now)
    for field in ("rows", "signals", "strokes", "coverage"):
        assert first[field] == expected[field]
    assert first["cutoff"] == ("2024-01-18" if timeframe == "1d" else "2024-01-14")
    assert get_chart(repo, "000001.SZ", now=now)["rows"][0]["close"] == 11.0
    repo.append_index_enriched(bars("000300.SH", 3600.0))
    revised = get_chart(repo, "000300.SH", timeframe=timeframe, now=now)
    assert revised["generation"] != first["generation"]
    assert revised["rows"][0]["close"] == 3601.0


def test_index_snapshot_is_bounded_readonly_and_rejects_changes(repo, monkeypatch):
    monkeypatch.setattr(repo, "get_enriched_latest", lambda: pytest.fail("read live stock cache"))
    monkeypatch.setattr(repo, "get_index_daily", lambda *a, **kw: pytest.fail("used ordinary daily path"))
    frame = repo.get_daily_published("000300.SH", date(2024, 1, 10), 3,
                                     sorted(cs.INPUT_COLUMNS), asset_type="index")
    assert frame["date"].to_list() == [date(2024, 1, i) for i in [8, 9, 10]]
    assert frame["volume"].to_list() == [1234.0] * 3
    marker = repo.store.data_dir / ".matrix_generation_index.json"
    assert not marker.exists()
    assert get_chart(repo, "000300.SH")["status"] == "ready"
    assert not marker.exists()
    original = repo.get_daily_published

    def revise_during_read(*args, **kwargs):
        result = original(*args, **kwargs)
        repo.append_index_enriched(bars("000300.SH", 3700.0))
        return result

    repo.append_index_enriched(bars("000300.SH", 3600.0))
    monkeypatch.setattr(repo, "get_daily_published", revise_during_read)
    with pytest.raises(EnrichedGenerationUnavailableError):
        get_chart(repo, "000300.SH")
    monkeypatch.setattr(repo, "get_daily_published", original)
    for path in (repo.store.data_dir / "kline_index_enriched").glob("**/*.parquet"):
        path.unlink()
    assert get_chart(repo, "000300.SH")["status"] == "empty"
    out = repo.store.data_dir / "kline_index_enriched/date=2024-01-19/part.parquet"
    out.write_bytes(b"corrupt parquet")
    with pytest.raises(pl.exceptions.ComputeError):
        get_chart(repo, "000300.SH")


def test_api_directory_search_unknown_empty_and_unsupported_period(repo, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    search = client.get("/api/kline/instruments/search", params={"q": "000001", "asset_types": "stock,index"})
    assert {(r["symbol"], r["asset_type"]) for r in search.json()["results"]} == {
        ("000001.SH", "index"), ("000001.SZ", "stock"),
    }
    url = "/api/kline/czsc"
    for symbol in ["000001.SH", "000300.SH"]:
        response = client.get(url, params={"symbol": symbol, "asset_type": "index"})
        assert response.status_code == 200
        assert response.json()["asset_type"] == "index"
        assert response.json()["rows"][0]["close"] > 1000
    assert client.get("/api/kline/czsc-daily", params={"symbol": "000300.SH"}).json()["asset_type"] == "index"
    empty = client.get(url, params={"symbol": "000832.SH"}).json()
    assert empty["status"] == "empty" and empty["asset_type"] == "index"
    for symbol, asset in [("000001.SZ", "index"), ("000001.SH", "stock"), ("999999.SH", "index"), ("510300.SH", "index")]:
        assert client.get(url, params={"symbol": symbol, "asset_type": asset}).status_code == 400
    monkeypatch.setattr("app.services.czsc_chart.minute_route", lambda: pytest.fail("index used minute route"))
    minute = client.get(url, params={"symbol": "000300.SH", "timeframe": "30m"}).json()
    assert minute["status"] == "unavailable"
    assert minute["unavailable_reason"] == "unsupported_timeframe"
    assert minute["supported_timeframes"] == ["1d", "1w"]
    assert "minute_provider" not in minute
