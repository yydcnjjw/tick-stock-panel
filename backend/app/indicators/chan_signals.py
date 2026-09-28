"""Closed-bar chan.py events shared by charts, scanning and backtests."""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import date, datetime

import polars as pl
import psutil

from app.indicators.chan_runtime import BSP_LABELS, PROFILE_ID, VERSION, ChanReplay
from app.indicators.czsc_bars import FREQUENCIES, bar_close_time
from app.market_time import CN_TZ, cn_now

WARMUP_BARS = 500
INPUT_COLUMNS = frozenset({"symbol", "date", "open", "high", "low", "close", "volume", "amount"})
MONITOR_WARNING = "chan.py 日线确认信号仅支持收盘后的策略计算与回测, 暂不支持盘中监控"
LEGACY_WARNING = "旧 CZSC 策略已停用, 原配置和历史结果保留; 请使用独立的 chan.py 新策略"
_PARALLEL_MIN_ROWS = 40_000
_CHUNK_SYMBOLS = 16
SIGNALS = {f"signal_chan_bi_{kind}_{side}": (kind, side == "buy")
           for kind in BSP_LABELS for side in ("buy", "sell")}
SIGNAL_LABELS = {name: f"chan.py 笔级{BSP_LABELS[kind]}{'买' if buy else '卖'}确认"
                 for name, (kind, buy) in SIGNALS.items()}


class ChanReplayCancelledError(RuntimeError):
    """Cancellation never returns partial signals."""


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ChanReplayCancelledError("回测已取消")


def legacy_names(names):
    return {n for n in names if n and n.startswith(("signal_czsc_", "czsc_"))}


def retirement_reason(strategy, overrides=None):
    # Original declarations still count after a UI override clears its selection.
    names = [*getattr(strategy, "required_features", ()),
             *getattr(strategy, "entry_signals", ()), *getattr(strategy, "exit_signals", ())]
    for key in ("entry_signals", "exit_signals"):
        names.extend((overrides or {}).get(key) or [])
    return LEGACY_WARNING if legacy_names(names) else None


def selected(names):
    names = list(names)
    if legacy_names(names):
        raise ValueError(LEGACY_WARNING)
    normalized = {n if n.startswith("signal_") else f"signal_{n}" for n in names if n}
    unknown = {n for n in normalized if n.startswith("signal_chan_")} - SIGNALS.keys()
    if unknown:
        raise ValueError(f"未知 chan.py 信号: {sorted(unknown)}")
    return normalized & SIGNALS.keys()


def _load_runtime():
    from app.vendor.chanpy.Chan import CChan
    return CChan


def availability():
    try:
        _load_runtime()
    except (ImportError, AttributeError, OSError, RuntimeError) as exc:
        return {"available": False, "reason": f"chan.py 组件加载失败: {exc}", "version": VERSION, "profile_id": PROFILE_ID}
    return {"available": True, "reason": None, "version": VERSION, "profile_id": PROFILE_ID}


def validate_usage(names, *, asset_type="stock", execution_backend="polars_expr"):
    wanted = selected(names)
    if wanted:
        if asset_type != "stock":
            raise ValueError("chan.py 日线确认信号仅支持 A 股股票")
        if execution_backend != "polars_expr":
            raise ValueError("chan.py 日线确认信号仅支持普通日线策略触发器")
        _load_runtime()
    return wanted


def reason_column(name):
    return f"_{name}_reason"


def compute(df, needed, *, now=None, progress_cb=None, cancel_event=None, max_workers=1):
    return _replay(df, needed, now=now, progress_cb=progress_cb,
                   cancel_event=cancel_event, max_workers=max_workers)


def _replay_symbol(rows, wanted, cutoff, native, cancel_event=None, on_segment=None, timeframe="1d"):
    values = {name: [None] * len(rows) for name in wanted}
    reasons = {name: [None] * len(rows) for name in wanted}
    analysis = None
    minute = timeframe.endswith("m")
    for bar_id, row in enumerate(rows):
        if bar_id % 32 == 0:
            _check_cancel(cancel_event)
        day = row["date"]
        if timeframe == "1d" and (not isinstance(day, date) or isinstance(day, datetime)):
            raise ValueError("chan.py 日线 date 必须为交易日期")
        unclosed = bar_close_time(day, timeframe) > cutoff.replace(tzinfo=None)
        problem = "unclosed_bar" if unclosed else None
        numeric = [row[key] for key in ("open", "high", "low", "close", "volume", "amount")]
        if not all(isinstance(x, (int, float)) and math.isfinite(x) and (x >= 0 if minute and i >= 4 else x > 0) for i, x in enumerate(numeric)) or not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            problem = "missing_data"
        if problem:
            for name in wanted:
                reasons[name][bar_id] = problem
            if on_segment is not None and analysis is not None:
                on_segment(analysis)
            analysis = None
            continue
        if analysis is None:
            analysis = ChanReplay(row["symbol"], timeframe, collect_structure=on_segment is not None)
        events = analysis.update(row)
        for name in wanted:
            if not analysis.ready:
                reasons[name][bar_id] = "insufficient_structure"
            elif analysis.baseline:
                reasons[name][bar_id] = "baseline"
            else:
                kind, buy = SIGNALS[name]
                values[name][bar_id] = any(e["is_buy"] == buy and kind in e["types"] for e in events)
    _check_cancel(cancel_event)
    if on_segment is not None and analysis is not None:
        on_segment(analysis)
    return [row["_chan_row"] for row in rows], values, reasons


def _parallel_workers(requested: int, rows: int, symbols: int) -> int:
    if requested <= 1 or rows < _PARALLEL_MIN_ROWS or symbols < 2 or mp.current_process().daemon:
        return 1
    cpu_count = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    # 为主回测保留内存, 每个额外原生运行库按 512 MiB 预算。
    memory_workers = max(1, psutil.virtual_memory().available // (512 * 1024 * 1024) - 1)
    return max(1, min(requested, 4, cpu_count, memory_workers, symbols))


_worker_runtime = None
_worker_cancel = None


def _exit_with_parent():
    parent = mp.parent_process()
    if parent is not None:
        parent.join()
        # 主 worker 崩溃/OOM 时没有机会协作取消; 不留下失去接收方的计算进程。
        os._exit(1)


def _init_replay_worker(cancel_event):
    global _worker_runtime, _worker_cancel
    threading.Thread(target=_exit_with_parent, daemon=True).start()
    _worker_cancel = cancel_event
    _worker_runtime = _load_runtime()


def _replay_chunk(parts, wanted, cutoff):
    # 子进程只接收窄列 Python 行, 不复制整张回测面板或启动 Polars 计算线程。
    return [_replay_symbol(rows, wanted, cutoff, _worker_runtime, _worker_cancel) for rows in parts]


def _parallel_replay(parts, wanted, cutoff, workers, cancel_event):
    context = mp.get_context("spawn")
    stopped = context.Event()
    pool = ProcessPoolExecutor(
        max_workers=workers, mp_context=context,
        initializer=_init_replay_worker, initargs=(stopped,),
    )
    pending = set()
    offset = 0
    try:
        while offset < len(parts) or pending:
            _check_cancel(cancel_event)
            # 有界提交: 输入转成 Python 对象仅限至多两批, 避免全市场复制。
            while offset < len(parts) and len(pending) < workers * 2:
                chunk = parts[offset:offset + _CHUNK_SYMBOLS]
                pending.add(pool.submit(_replay_chunk, [part.to_dicts() for part in chunk], wanted, cutoff))
                offset += len(chunk)
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                _check_cancel(cancel_event)
                yield future.result()
    finally:
        # 包括取消、子进程失败和回调异常; 先让正在执行的任务协作退出再回收。
        stopped.set()
        pool.shutdown(wait=True, cancel_futures=True)


def _replay(
    df: pl.DataFrame, needed: set[str], *, now: datetime | None = None,
    on_segment: Callable | None = None, progress_cb: Callable[[dict], None] | None = None,
    cancel_event=None, max_workers: int = 1, timeframe: str = "1d",
) -> pl.DataFrame:
    """同一回放同时支持信号及可选结构快照; 普通计算不收集图表结构。"""
    if timeframe not in FREQUENCIES:
        raise ValueError("不支持的 chan.py 图表周期")
    wanted = sorted(selected(needed))
    if not wanted:
        return df
    _check_cancel(cancel_event)
    native = _load_runtime()
    missing = INPUT_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"chan.py 日线输入缺少字段: {sorted(missing)}")
    if df.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("chan.py 日线输入存在重复的标的和日期")
    cutoff = now or cn_now()
    cutoff = cutoff.replace(tzinfo=CN_TZ) if cutoff.tzinfo is None else cutoff.astimezone(CN_TZ)
    values = {name: [None] * len(df) for name in wanted}
    reasons = {name: [None] * len(df) for name in wanted}
    parts = df.select(sorted(INPUT_COLUMNS)).with_row_index("_chan_row").sort(["symbol", "date"]).partition_by("symbol")
    total = len(parts)
    completed = 0

    def progress():
        if progress_cb is not None:
            progress_cb({"phase": "chan_signals", "completed": completed, "total": total})

    def collect(results):
        nonlocal completed
        for indices, part_values, part_reasons in results:
            for name in wanted:
                for index, value, reason in zip(indices, part_values[name], part_reasons[name], strict=True):
                    values[name][index] = value
                    reasons[name][index] = reason
            completed += 1
        progress()

    progress()
    workers = _parallel_workers(max_workers, len(df), total) if max_workers > 1 and on_segment is None and timeframe == "1d" else 1
    if workers > 1:
        results = _parallel_replay(parts, wanted, cutoff, workers, cancel_event)
        try:
            for chunk in results:
                collect(chunk)
        finally:
            results.close()
    else:
        for part in parts:
            collect([_replay_symbol(part.to_dicts(), wanted, cutoff, native, cancel_event, on_segment, timeframe)])
    _check_cancel(cancel_event)
    return df.with_columns(
        [pl.Series(name, data, dtype=pl.Boolean) for name, data in values.items()]
        + [pl.Series(reason_column(name), data, dtype=pl.String) for name, data in reasons.items()]
    )


def coverage(df: pl.DataFrame, names) -> dict:
    reports = []
    for name in sorted(selected(names)):
        unavailable = df.filter(pl.col(name).is_null())
        counts = unavailable[reason_column(name)].value_counts().to_dicts()
        reports.append({
            "signal_id": name,
            "ready_rows": len(df) - len(unavailable),
            "unavailable_rows": len(unavailable),
            "unavailable_symbols": unavailable["symbol"].n_unique(),
            "reasons": {row[reason_column(name)]: row["count"] for row in counts},
        })
    return {"engine": "chan.py", "version": VERSION, "profile_id": PROFILE_ID, "signals": reports}
