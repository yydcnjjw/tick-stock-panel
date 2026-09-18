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


@pytest.mark.parametrize("timeframe", ["1m", "5m", "30m", "1w"])
def test_period_chart_matches_native_strokes_and_keeps_signal_prefix(history, timeframe):
    from app.indicators.czsc_bars import FREQUENCIES
    from app.indicators.czsc_chart import build_chart, structure_snapshot

    native = cs._load_runtime()
    minute = timeframe.endswith("m")
    if minute:
        step = int(timeframe[:-1])
        per_session = 120 // step
        dates = [datetime(2018, 1, 2) + timedelta(days=i // (per_session * 2),
                 minutes=(570 if i // per_session % 2 == 0 else 780) + (i % per_session + 1) * step)
                 for i in range(len(history))]
    else:
        dates = [date(2000, 1, 7) + timedelta(weeks=i) for i in range(len(history))]
    frame = history.with_columns(pl.Series("date", dates))
    if minute:
        frame = frame.with_columns(pl.lit(0.0).alias("volume"), pl.lit(0.0).alias("amount"))
    chart = build_chart(frame, timeframe=timeframe)
    analysis = native.CZSC([
        native.RawBar(symbol=r["symbol"], id=i,
                      dt=r["date"] if minute else datetime.combine(r["date"], datetime.min.time().replace(hour=15)),
                      freq=getattr(native.Freq, FREQUENCIES[timeframe]),
                      open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                      vol=r["volume"] * 100, amount=r["amount"])
        for i, r in enumerate(frame.iter_rows(named=True))
    ], max_bi_num=50, min_bi_len=6)
    assert chart["strokes"] == structure_snapshot(analysis, timeframe)["strokes"]
    assert chart["strokes"] and not chart["invalid_dates"]
    assert ("T" in chart["rows"][0]["date"]) == minute
    prefix = build_chart(frame.head(800), timeframe=timeframe)
    assert prefix["signals"] == [s for s in chart["signals"] if s["date"] <= prefix["rows"][-1]["date"]]


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

    def get_minute_chart_generation(self):
        return self.generation

    def get_minute_published(self, symbol, end, limit):
        self.reads += 1
        if self.change_during_read:
            self.generation += "x"
        return self.frame.filter(pl.col("datetime") <= end).tail(limit)


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


def test_multi_period_api_cache_revision_and_minute_close_boundary():
    from app.services.czsc_chart import get_chart

    times = [datetime(2024, 1, 2, 9, 30) + timedelta(minutes=i) for i in range(1, 241)
             if i <= 120]  # Morning session, no synthetic lunch bars.
    frame = pl.DataFrame({"symbol": ["600000.SH"] * len(times), "datetime": times,
                          "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
                          "volume": 10.0, "amount": 11000.0})
    repo = Repo(frame)
    now = datetime(2024, 1, 2, 10, 17, tzinfo=CN_TZ)
    first = get_chart(repo, "600000.SH", timeframe="30m", now=now)
    assert first["cutoff"] == "2024-01-02T10:00"
    assert get_chart(repo, "600000.SH", timeframe="30m", now=now) == first
    assert repo.reads == 1
    five = get_chart(repo, "600000.SH", timeframe="5m", now=now)
    assert five["cutoff"] == "2024-01-02T10:15" and repo.reads == 2
    repo.frame = frame.with_columns(pl.lit(10.5).alias("close"))
    repo.generation = "b"
    assert get_chart(repo, "600000.SH", timeframe="5m", now=now)["rows"][-1]["close"] == 10.5
    repo.change_during_read = True
    repo.generation = "c"
    with pytest.raises(EnrichedGenerationUnavailableError):
        get_chart(repo, "600000.SH", timeframe="5m", now=now)
    repo.change_during_read = False
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    assert client.get("/api/kline/czsc", params={"symbol": "600000.SH", "timeframe": "30m"}).json()["timeframe"] == "30m"
    assert client.get("/api/kline/czsc", params={"symbol": "600000.SH", "timeframe": "3m"}).status_code == 422
    assert client.get("/api/kline/czsc", params={"symbol": "510300.SH", "timeframe": "1m"}).status_code == 400
    app.state.repo = Repo(frame.clear())
    assert client.get("/api/kline/czsc", params={"symbol": "600000.SH", "timeframe": "1m"}).json()["status"] == "empty"


def test_minute_repository_read_is_bounded_revision_sensitive_and_never_swallows_corruption(tmp_path):
    from app.tickflow.repository import KlineRepository

    root = tmp_path / "kline_minute" / "date=2024-01-02"
    root.mkdir(parents=True)
    path = root / "part.parquet"
    repo = object.__new__(KlineRepository)
    repo.store = SimpleNamespace(data_dir=tmp_path)
    repo._minute_glob = str(tmp_path / "kline_minute" / "**" / "*.parquet")
    before = repo.get_minute_chart_generation()
    frame = pl.DataFrame({"symbol": ["600000.SH"] * 3,
                          "datetime": [datetime(2024, 1, 2, 9, m) for m in [31, 32, 33]],
                          "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
                          "volume": 0.0, "amount": 0.0})
    frame.write_parquet(path)
    assert repo.get_minute_chart_generation() != before
    result = repo.get_minute_published("600000.SH", datetime(2024, 1, 2, 9, 32), 1)
    assert result["datetime"].to_list() == [datetime(2024, 1, 2, 9, 32)]
    path.write_bytes(b"corrupt parquet")
    with pytest.raises(pl.exceptions.ComputeError):
        repo.get_minute_published("600000.SH", datetime(2024, 1, 2, 10), 10)


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
