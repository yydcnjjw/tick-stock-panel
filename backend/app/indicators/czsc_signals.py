"""按已收盘日线回放 CZSC 原生辅助信号; 不负责取数或成交模拟。

输入沿用 enriched 前复权 OHLC、成交量(手)、成交额(元)。状态在识别当日
产生, 不能回填到笔端点。None 表示不可计算/尚未建立基线, 与 False 区分。
"""
from __future__ import annotations

import importlib
import math
import multiprocessing as mp
import os
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import cached_property
from importlib.metadata import PackageNotFoundError, version

import polars as pl
import psutil

from app.indicators.czsc_bars import FREQUENCIES, bar_close_time
from app.market_time import CN_TZ, cn_now

VERSION = "1.0.1"
# 读取目标, 不是结构充分性的保证; 逐日仍按实际笔和均线输入检查。
WARMUP_BARS = 500
INPUT_COLUMNS = frozenset({"symbol", "date", "open", "high", "low", "close", "volume", "amount"})
MONITOR_WARNING = "CZSC 日线辅助信号仅支持收盘后的策略计算与回测, 暂不支持盘中监控"
_PARALLEL_MIN_ROWS = 40_000
_CHUNK_SYMBOLS = 16


class CzscReplayCancelledError(RuntimeError):
    """取消回放时不返回不完整的信号或覆盖统计。"""


def _check_cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CzscReplayCancelledError("回测已取消")


@dataclass(frozen=True)
class SignalSpec:
    function: str
    value: str
    period: int = 0

    @property
    def params(self) -> dict:
        return {"di": 1, **({"ma_type": "SMA", "timeperiod": self.period} if self.period else {})}


SIGNALS = {
    "signal_czsc_first_buy": SignalSpec("cxt_first_buy_V221126", "一买"),
    "signal_czsc_first_sell": SignalSpec("cxt_first_sell_V221126", "一卖"),
    "signal_czsc_second_buy": SignalSpec("cxt_second_bs_V230320", "二买", 21),
    "signal_czsc_second_sell": SignalSpec("cxt_second_bs_V230320", "二卖", 21),
    "signal_czsc_third_buy": SignalSpec("cxt_third_bs_V230318", "三买", 34),
    "signal_czsc_third_sell": SignalSpec("cxt_third_bs_V230318", "三卖", 34),
}


def selected(names) -> set[str]:
    normalized = {n if n.startswith("signal_") else f"signal_{n}" for n in names if n}
    unknown = {n for n in normalized if n.startswith("signal_czsc_")} - SIGNALS.keys()
    if unknown:
        raise ValueError(f"未知 CZSC 信号: {sorted(unknown)}")
    return normalized & SIGNALS.keys()


def _load_runtime():
    try:
        installed = version("czsc")
    except PackageNotFoundError as exc:
        raise ValueError("CZSC 信号组件未安装, 请安装 czsc 可选依赖") from exc
    if installed != VERSION:
        raise ValueError(f"CZSC 信号要求版本 {VERSION}, 当前为 {installed}")
    try:
        native = importlib.import_module("czsc._native")
        if not {spec.function for spec in SIGNALS.values()} <= set(native.list_signal_names()):
            raise ImportError("missing signal functions")
        return native
    except (ImportError, AttributeError, OSError, RuntimeError) as exc:
        raise ValueError("CZSC 信号组件加载失败, 请检查可选依赖安装") from exc


def availability() -> dict:
    try:
        installed = version("czsc")
    except PackageNotFoundError:
        installed = None
    try:
        _load_runtime()
    except ValueError as exc:
        return {"available": False, "reason": str(exc), "version": installed}
    return {"available": True, "reason": None, "version": installed}


def validate_usage(names, *, asset_type="stock", execution_backend="polars_expr") -> set[str]:
    wanted = selected(names)
    if wanted:
        if asset_type != "stock":
            raise ValueError("CZSC 日线辅助信号首版仅支持 A 股股票")
        if execution_backend != "polars_expr":
            raise ValueError("CZSC 日线辅助信号仅支持普通日线策略触发器")
        _load_runtime()
    return wanted


def reason_column(name: str) -> str:
    return f"_{name}_reason"


class _Readiness:
    """仅在当前 K 线内复用原生结构对象; update 后必须重新建立。"""

    def __init__(self, analysis):
        self.analysis = analysis

    @cached_property
    def bis(self):
        return self.analysis.bi_list

    @cached_property
    def raw_index(self):
        return {bar.id: i for i, bar in enumerate(self.analysis.bars_raw)}


def _ready(state: _Readiness, spec: SignalSpec) -> bool:
    bis = state.bis
    if not spec.period:
        return len(bis) >= 5
    if len(bis) < 7:
        return False
    b1, _, b3, _, b5 = bis[-5:]
    # 原生二买卖使用分型倒数第二根原始K; 三买卖使用最后一根。
    fractals = [b1.fx_b, b3.fx_b, b5.fx_b]
    offset = -1
    if spec.period == 21:
        fractals.append(b5.fx_a)
        offset = -2
    raw_index = state.raw_index
    for fractal in fractals:
        raw = fractal.raw_bars
        if len(raw) < abs(offset) or raw_index.get(raw[offset].id, -1) < spec.period - 1:
            return False
    return True


def compute(
    df: pl.DataFrame, needed: set[str], *, now: datetime | None = None,
    progress_cb: Callable[[dict], None] | None = None, cancel_event=None, max_workers: int = 1,
) -> pl.DataFrame:
    return _replay(
        df, needed, now=now, progress_cb=progress_cb,
        cancel_event=cancel_event, max_workers=max_workers,
    )


def _replay_symbol(rows, wanted, cutoff, native, cancel_event=None, on_segment=None, timeframe="1d"):
    values = {name: [None] * len(rows) for name in wanted}
    reasons = {name: [None] * len(rows) for name in wanted}
    analysis = None
    previous = dict.fromkeys(wanted)
    freq = getattr(native.Freq, FREQUENCIES[timeframe])
    minute = timeframe.endswith("m")
    for bar_id, row in enumerate(rows):
        if bar_id % 32 == 0:
            _check_cancel(cancel_event)
        day = row["date"]
        if timeframe == "1d":
            if not isinstance(day, date) or isinstance(day, datetime):
                raise ValueError("CZSC 日线 date 必须为交易日期")
            bar_dt = datetime.combine(day, time(15))
            unclosed = day > cutoff.date() or (day == cutoff.date() and cutoff.time() < time(15))
        else:
            closed_at = bar_close_time(day, timeframe)
            bar_dt = day if minute else datetime.combine(day, time(15))
            unclosed = closed_at > cutoff.replace(tzinfo=None)
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
            previous = dict.fromkeys(wanted)
            continue
        bar = native.RawBar(
            symbol=row["symbol"], id=bar_id, dt=bar_dt,
            freq=freq, open=row["open"], high=row["high"], low=row["low"],
            close=row["close"], vol=row["volume"] * 100.0, amount=row["amount"],
        )
        if analysis is None:
            analysis = native.CZSC([bar], max_bi_num=50, min_bi_len=6)
        else:
            analysis.update(bar)
        results: dict[str, str] = {}
        readiness: dict[int, bool] = {}
        state = _Readiness(analysis)
        for name in wanted:
            spec = SIGNALS[name]
            # 一买/一卖是不同原生函数, 但结构就绪条件相同。
            if spec.period not in readiness:
                readiness[spec.period] = _ready(state, spec)
            if not readiness[spec.period]:
                reasons[name][bar_id] = "insufficient_structure"
                previous[name] = None
                continue
            if spec.function not in results:
                native_result = native.call_signal(spec.function, analysis, spec.params)
                if len(native_result) != 1:
                    raise ValueError("CZSC 原生信号返回了非预期结果")
                results[spec.function] = native_result[0].v1
            hit = results[spec.function] == spec.value
            if previous[name] is None:
                reasons[name][bar_id] = "baseline"
            else:
                values[name][bar_id] = hit and not previous[name]
            previous[name] = hit
    _check_cancel(cancel_event)
    if on_segment is not None and analysis is not None:
        on_segment(analysis)
    return [row["_czsc_row"] for row in rows], values, reasons


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
        raise ValueError("不支持的 CZSC 图表周期")
    wanted = sorted(selected(needed))
    if not wanted:
        return df
    _check_cancel(cancel_event)
    native = _load_runtime()
    missing = INPUT_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CZSC 日线输入缺少字段: {sorted(missing)}")
    if df.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("CZSC 日线输入存在重复的标的和日期")
    cutoff = now or cn_now()
    cutoff = cutoff.replace(tzinfo=CN_TZ) if cutoff.tzinfo is None else cutoff.astimezone(CN_TZ)
    values = {name: [None] * len(df) for name in wanted}
    reasons = {name: [None] * len(df) for name in wanted}
    parts = df.select(sorted(INPUT_COLUMNS)).with_row_index("_czsc_row").sort(["symbol", "date"]).partition_by("symbol")
    total = len(parts)
    completed = 0

    def progress():
        if progress_cb is not None:
            progress_cb({"phase": "czsc_signals", "completed": completed, "total": total})

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
    return {"version": VERSION, "signals": reports}
