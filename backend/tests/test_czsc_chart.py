from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.kline import router
from app.enriched_generation import EnrichedGenerationUnavailableError
from app.indicators import czsc_signals as cs
from app.market_time import CN_TZ


@pytest.fixture
def history():
    pytest.importorskip("czsc")
    rng = np.random.default_rng(2019)
    n = 900
    prices = 20 * np.exp(np.cumsum(rng.normal(0, .025, n)))
    return pl.DataFrame({
        "symbol": ["600000.SH"] * n,
        "date": [date(2018, 1, 1) + timedelta(days=i) for i in range(n)],
        "open": prices, "close": prices, "high": prices * 1.015, "low": prices * .985,
        "volume": rng.uniform(1000, 3000, n), "amount": prices * 200000,
    })


def test_chart_matches_signal_events_and_native_structure(history):
    from app.indicators.czsc_chart import build_chart

    chart = build_chart(history)
    expected = cs.compute(history, set(cs.SIGNALS))
    for name in cs.SIGNALS:
        assert [event["date"] for event in chart["signals"] if event["signal_id"] == name] == [
            day.isoformat() for day in expected.filter(pl.col(name))["date"]
        ]
    assert chart["coverage"] == cs.coverage(expected, set(cs.SIGNALS))
    assert chart["strokes"] and chart["centers"]
    native = cs._load_runtime()
    analysis = native.CZSC([
        native.RawBar(symbol=r["symbol"], id=i, dt=datetime.combine(r["date"], datetime.min.time()),
                      freq=native.Freq.D, open=r["open"], high=r["high"], low=r["low"],
                      close=r["close"], vol=r["volume"] * 100, amount=r["amount"])
        for i, r in enumerate(history.iter_rows(named=True))
    ], max_bi_num=50, min_bi_len=6)
    assert [(x["start"], x["start_price"], x["end"], x["end_price"]) for x in chart["strokes"]] == [
        (b.fx_a.dt.date().isoformat(), b.fx_a.fx, b.fx_b.dt.date().isoformat(), b.fx_b.fx)
        for b in analysis.finished_bis
    ]
    assert [(x["low"], x["high"]) for x in chart["centers"]] == [
        (z.zd, z.zg) for z in analysis.zs_list if len(z.bis) >= 3 and z.is_valid()
    ]


def test_bad_data_splits_structures_and_unclosed_tail_is_excluded(history):
    from app.indicators.czsc_chart import build_chart

    split_day = history[450, "date"]
    broken = history.with_columns(pl.when(pl.col("date") == split_day).then(None).otherwise(pl.col("close")).alias("close"))
    chart = build_chart(broken)
    assert split_day.isoformat() in chart["invalid_dates"]
    for shape in chart["strokes"] + chart["centers"] + chart["unfinished"]:
        assert not shape["start"] < split_day.isoformat() < shape["end"]
    last_day = history[-1, "date"]
    before_close = build_chart(history, now=datetime.combine(last_day, datetime.min.time()).replace(hour=14, tzinfo=CN_TZ))
    prefix = build_chart(history.head(899))
    assert before_close == prefix


def test_center_needs_three_bis_even_when_native_validates():
    from app.indicators.czsc_chart import structure_snapshot

    analysis = SimpleNamespace(finished_bis=[], fx_list=[], bi_list=[], ubi=None,
                               zs_list=[SimpleNamespace(bis=[1, 2], is_valid=lambda: True)])
    assert structure_snapshot(analysis)["centers"] == []


class Repo:
    def __init__(self, frame):
        self.frame = frame
        self.generation = "a"
        self.reads = 0
        self.change_during_read = False

    def resolve_asset_type(self, symbol):
        return "etf" if symbol == "510300.SH" else "stock"

    def get_name_map(self, symbols):
        return {symbol: "测试股票" for symbol in symbols}

    def get_matrix_data_generation(self, asset_type, *, readonly=False):
        assert readonly
        return self.generation

    def get_daily_published(self, symbol, end, limit, columns):
        self.reads += 1
        if self.change_during_read:
            self.generation += "x"
        return self.frame.filter(pl.col("date") <= end).tail(limit)


def test_cache_revision_cutoff_and_failed_publication(history):
    from app.services.czsc_chart import get_chart

    repo = Repo(history)
    now = datetime(2024, 1, 1, 16, tzinfo=CN_TZ)
    first = get_chart(repo, "600000.SH", now=now)
    assert get_chart(repo, "600000.SH", now=now) == first
    assert repo.reads == 1
    repo.generation = "b"
    get_chart(repo, "600000.SH", now=now)
    assert repo.reads == 2
    repo.change_during_read = True
    repo.generation = "c"
    with pytest.raises(EnrichedGenerationUnavailableError):
        get_chart(repo, "600000.SH", now=now)


def test_api_errors_empty_and_optional_dependency(history, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.repo = Repo(history)
    client = TestClient(app)
    url = "/api/kline/czsc-daily"
    assert client.get(url, params={"symbol": "510300.SH"}).status_code == 400
    assert client.get(url, params={"symbol": "../../foo"}).status_code == 422
    ok = client.get(url, params={"symbol": "600000.SH"})
    assert ok.status_code == 200
    assert ok.json()["rows"] and ok.json()["signals"]
    app.state.repo = Repo(history.clear())
    assert client.get(url, params={"symbol": "600000.SH"}).json()["status"] == "empty"
    monkeypatch.setattr(cs, "availability", lambda: {"available": False, "reason": "未安装", "version": None})
    assert client.get(url, params={"symbol": "600000.SH"}).json()["status"] == "unavailable"


def test_published_read_ignores_live_cache_and_counts_rows(history, tmp_path):
    from app.tickflow.repository import KlineRepository

    root = tmp_path / "kline_daily_enriched" / "date=2020-06-18"
    root.mkdir(parents=True)
    history.write_parquet(root / "data.parquet")
    repo = object.__new__(KlineRepository)
    repo.store = SimpleNamespace(data_dir=tmp_path)
    repo._enriched_glob = str(tmp_path / "kline_daily_enriched" / "**" / "*.parquet")
    repo.get_enriched_latest = lambda: pytest.fail("published chart used live cache")
    result = repo.get_daily_published("600000.SH", date(2024, 1, 1), 100, sorted(cs.INPUT_COLUMNS))
    assert result.equals(history.tail(100).select(sorted(cs.INPUT_COLUMNS)))


def test_chart_generation_read_never_recovers_an_incomplete_publication(tmp_path, monkeypatch):
    import json

    from app import enriched_generation as eg

    marker = tmp_path / ".matrix_generation_stock.json"
    original = json.dumps({"state": "publishing", "owner_pid": 99999999, "generation": "old"})
    marker.write_text(original)
    monkeypatch.setattr(eg, "_process_is_alive", lambda pid: False)
    with pytest.raises(EnrichedGenerationUnavailableError):
        eg.get_enriched_generation(tmp_path, initialize=False, recover=False)
    assert marker.read_text() == original


def test_macd_uses_shared_pipeline_and_invalid_data_keeps_diagnostics(history):
    from app.indicators.czsc_chart import build_chart
    from app.indicators.pipeline import compute_indicators

    chart = build_chart(history)
    expected = compute_indicators(history, needed={"macd_dif", "macd_dea", "macd_hist"})
    assert [r["macd_hist"] for r in chart["rows"]] == expected["macd_hist"].to_list()
    invalid = history.with_columns(pl.lit(float("nan")).alias("close"))
    chart = build_chart(invalid)
    assert chart["rows"] == []
    assert len(chart["invalid_dates"]) == len(history)
    assert chart["coverage"]["signals"][0]["reasons"]["missing_data"] == len(history)
