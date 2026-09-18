"""The outer worker's hard-cancel fallback must also stop replay descendants."""
import multiprocessing as mp
import threading
import time

import psutil
import pytest

from app.backtest import worker


def _watched_replay_child(ready):
    from app.indicators.czsc_signals import _init_replay_worker

    _init_replay_worker(mp.get_context("spawn").Event())
    ready.set()
    time.sleep(60)


def _uncooperative_worker(task, events, cancel_event):
    context = mp.get_context("spawn")
    ready = context.Event()
    watched = task["config"].get("watch_parent", False)
    child = context.Process(
        target=_watched_replay_child if watched else time.sleep,
        args=(ready,) if watched else (60,),
    )
    child.start()
    if watched:
        assert ready.wait(timeout=10)
    events.put({"type": "progress", "payload": {"child_pid": child.pid}})
    time.sleep(60)


def test_hard_cancel_stops_nested_replay_process(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "_worker_entry", _uncooperative_worker)
    monkeypatch.setattr(worker, "_CANCEL_GRACE_SECONDS", 0.05)
    cancelled = threading.Event()
    children = []

    def progress(message):
        children.append(psutil.Process(message["child_pid"]))
        cancelled.set()

    with pytest.raises(worker.BacktestWorkerError, match="after cancellation"):
        worker.run_worker_task(
            {"kind": "backtest", "data_dir": str(tmp_path), "config": {}}, progress, cancelled,
        )
    assert children
    for child in children:
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE


def test_replay_child_exits_if_outer_worker_crashes(monkeypatch, tmp_path):
    pytest.importorskip("czsc")
    monkeypatch.setattr(worker, "_worker_entry", _uncooperative_worker)
    children = []

    def crash_outer_worker(message):
        child = psutil.Process(message["child_pid"])
        children.append(child)
        psutil.Process(child.ppid()).kill()

    try:
        with pytest.raises(worker.BacktestWorkerError, match="exited without result"):
            worker.run_worker_task(
                {"kind": "backtest", "data_dir": str(tmp_path), "config": {"watch_parent": True}},
                crash_outer_worker,
            )
        assert children
        deadline = time.monotonic() + 5
        while any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in children) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert all(not p.is_running() or p.status() == psutil.STATUS_ZOMBIE for p in children)
    finally:
        for child in children:
            if child.is_running():
                child.kill()
