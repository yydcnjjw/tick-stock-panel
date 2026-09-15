"""按已收盘日线回放 CZSC 原生辅助信号; 不负责取数或成交模拟。

输入沿用 enriched 前复权 OHLC、成交量(手)、成交额(元)。状态在识别当日
产生, 不能回填到笔端点。None 表示不可计算/尚未建立基线, 与 False 区分。
"""
from __future__ import annotations

import importlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from importlib.metadata import PackageNotFoundError, version

import polars as pl

from app.market_time import CN_TZ, cn_now

VERSION = "1.0.1"
# 读取目标, 不是结构充分性的保证; 逐日仍按实际笔和均线输入检查。
WARMUP_BARS = 500
INPUT_COLUMNS = frozenset({"symbol", "date", "open", "high", "low", "close", "volume", "amount"})
MONITOR_WARNING = "CZSC 日线辅助信号仅支持收盘后的策略计算与回测, 暂不支持盘中监控"


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


def _ready(analysis, spec: SignalSpec) -> bool:
    bis = analysis.bi_list
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
    raw_index = {bar.id: i for i, bar in enumerate(analysis.bars_raw)}
    for fractal in fractals:
        raw = fractal.raw_bars
        if len(raw) < abs(offset) or raw_index.get(raw[offset].id, -1) < spec.period - 1:
            return False
    return True


def compute(df: pl.DataFrame, needed: set[str], *, now: datetime | None = None) -> pl.DataFrame:
    return _replay(df, needed, now=now)


def _replay(
    df: pl.DataFrame, needed: set[str], *, now: datetime | None = None,
    on_segment: Callable | None = None,
) -> pl.DataFrame:
    """同一回放同时支持信号及可选结构快照; 普通计算不收集图表结构。"""
    wanted = selected(needed)
    if not wanted:
        return df
    native = _load_runtime()
    missing = INPUT_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CZSC 日线输入缺少字段: {sorted(missing)}")
    if df.select(pl.struct("symbol", "date").is_duplicated().any()).item():
        raise ValueError("CZSC 日线输入存在重复的标的和日期")
    cutoff = now or cn_now()
    cutoff = cutoff.replace(tzinfo=CN_TZ) if cutoff.tzinfo is None else cutoff.astimezone(CN_TZ)
    values = {name: [None] * len(df) for name in sorted(wanted)}
    reasons = {name: [None] * len(df) for name in sorted(wanted)}
    for part in df.with_row_index("_czsc_row").sort(["symbol", "date"]).partition_by("symbol"):
        analysis = None
        previous = dict.fromkeys(wanted)
        for bar_id, row in enumerate(part.select("_czsc_row", *sorted(INPUT_COLUMNS)).iter_rows(named=True)):
            index = row["_czsc_row"]
            day = row["date"]
            if not isinstance(day, date) or isinstance(day, datetime):
                raise ValueError("CZSC 日线 date 必须为交易日期")
            problem = None
            if day > cutoff.date() or (day == cutoff.date() and cutoff.time() < time(15)):
                problem = "unclosed_bar"
            numeric = [row[key] for key in ("open", "high", "low", "close", "volume", "amount")]
            if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in numeric) or not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                problem = "missing_data"
            if problem:
                for name in wanted:
                    reasons[name][index] = problem
                if on_segment is not None and analysis is not None:
                    on_segment(analysis)
                analysis = None
                previous = dict.fromkeys(wanted)
                continue
            bar = native.RawBar(
                symbol=row["symbol"], id=bar_id, dt=datetime.combine(day, time(15)),
                freq=native.Freq.D, open=row["open"], high=row["high"], low=row["low"],
                close=row["close"], vol=row["volume"] * 100.0, amount=row["amount"],
            )
            if analysis is None:
                analysis = native.CZSC([bar], max_bi_num=50, min_bi_len=6)
            else:
                analysis.update(bar)
            results: dict[str, str] = {}
            readiness: dict[str, bool] = {}
            for name in sorted(wanted):
                spec = SIGNALS[name]
                if spec.function not in readiness:
                    readiness[spec.function] = _ready(analysis, spec)
                if not readiness[spec.function]:
                    reasons[name][index] = "insufficient_structure"
                    previous[name] = None
                    continue
                if spec.function not in results:
                    native_result = native.call_signal(spec.function, analysis, spec.params)
                    if len(native_result) != 1:
                        raise ValueError("CZSC 原生信号返回了非预期结果")
                    results[spec.function] = native_result[0].v1
                hit = results[spec.function] == spec.value
                if previous[name] is None:
                    reasons[name][index] = "baseline"
                else:
                    values[name][index] = hit and not previous[name]
                previous[name] = hit
        if on_segment is not None and analysis is not None:
            on_segment(analysis)
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
