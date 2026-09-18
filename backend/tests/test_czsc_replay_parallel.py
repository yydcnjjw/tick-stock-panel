"""Replay equivalence, cancellation and resource lifetime across real spawned workers."""
import multiprocessing as mp
import threading
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.indicators import czsc_signals as cs
from app.market_time import CN_TZ


@pytest.fixture
def history():
    pytest.importorskip("czsc")
    rng = np.random.default_rng(2019)
    n = 900
    prices = 20 * np.exp(np.cumsum(rng.normal(0, .025, n)))
    frame = pl.DataFrame({
        "symbol": ["600000.SH"] * n,
        "date": [date(2018, 1, 1) + timedelta(days=i) for i in range(n)],
        "open": prices, "close": prices, "high": prices * 1.015, "low": prices * .985,
        "volume": rng.uniform(1000, 3000, n), "amount": prices * 200000,
        "unrelated": list(range(n)),
    })
    broken = frame.with_columns(
        pl.lit("600001.SH").alias("symbol"),
        pl.when(pl.col("unrelated") == 450).then(None).otherwise(pl.col("close")).alias("close"),
    )
    return pl.concat([frame, broken, frame.with_columns(pl.lit("600002.SH").alias("symbol"))]).reverse()


def test_readiness_reads_the_native_structure_once_per_bar(monkeypatch):
    reads = []

    class Analysis:
        def __init__(self, bars, **kwargs):
            self.last = bars[-1]

        def update(self, bar):
            self.last = bar

        @property
        def bi_list(self):
            reads.append(self.last.id)
            return []

    monkeypatch.setattr(cs, "_load_runtime", lambda: SimpleNamespace(
        CZSC=Analysis, RawBar=lambda **kw: SimpleNamespace(**kw), Freq=SimpleNamespace(D="日线"),
    ))
    frame = pl.DataFrame({
        "symbol": ["600000.SH"] * 4,
        "date": [date(2024, 1, i) for i in range(1, 5)],
        "open": [10.] * 4, "close": [10.] * 4, "high": [11.] * 4, "low": [9.] * 4,
        "volume": [100.] * 4, "amount": [100000.] * 4,
    })
    result = cs.compute(frame, set(cs.SIGNALS))
    assert reads == [0, 1, 2, 3]
    assert all(result[name].null_count() == 4 for name in cs.SIGNALS)


def test_spawned_replay_matches_serial_with_gaps_order_and_unclosed_tail(history, monkeypatch):
    monkeypatch.setattr(cs, "_parallel_workers", lambda *args: 2)
    wanted = set(cs.SIGNALS)
    cutoff = datetime.combine(history[0, "date"], datetime.min.time()).replace(hour=14, tzinfo=CN_TZ)
    expected = cs.compute(history, wanted, now=cutoff)
    progress = []
    before = {p.pid for p in mp.active_children()}
    result = cs.compute(history, wanted, now=cutoff, max_workers=4, progress_cb=progress.append)
    assert result.equals(expected)
    assert cs.coverage(result, wanted) == cs.coverage(expected, wanted)
    assert progress[0] == {"phase": "czsc_signals", "completed": 0, "total": 3}
    assert progress[-1] == {"phase": "czsc_signals", "completed": 3, "total": 3}
    assert [p["completed"] for p in progress] == sorted(p["completed"] for p in progress)
    assert {p.pid for p in mp.active_children()} <= before


@pytest.mark.parametrize("parallel", [False, True])
def test_cancellation_returns_no_partial_signals_and_reaps_children(history, monkeypatch, parallel):
    monkeypatch.setattr(cs, "_parallel_workers", lambda *args: 2)
    # Several blocks ensure cancellation happens with work still pending.
    monkeypatch.setattr(cs, "_CHUNK_SYMBOLS", 1)
    event = threading.Event()
    before = {p.pid for p in mp.active_children()}

    def progress(message):
        if message["completed"] > 0:
            event.set()

    with pytest.raises(cs.CzscReplayCancelledError):
        cs.compute(history, set(cs.SIGNALS), max_workers=4 if parallel else 1,
                   cancel_event=event, progress_cb=progress)
    assert {p.pid for p in mp.active_children()} <= before


def test_cancel_before_start_does_not_load_runtime(history, monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(cs, "_load_runtime", lambda: pytest.fail("cancelled work loaded CZSC"))
    with pytest.raises(cs.CzscReplayCancelledError):
        cs.compute(history, set(cs.SIGNALS), cancel_event=event, max_workers=4)


def test_worker_failure_is_propagated_without_leaking_processes(history, monkeypatch):
    monkeypatch.setattr(cs, "_parallel_workers", lambda *args: 2)
    invalid = history.with_columns(pl.col("date").cast(pl.String))
    before = {p.pid for p in mp.active_children()}
    with pytest.raises(ValueError, match="date 必须为交易日期"):
        cs.compute(invalid, set(cs.SIGNALS), max_workers=4)
    assert {p.pid for p in mp.active_children()} <= before
