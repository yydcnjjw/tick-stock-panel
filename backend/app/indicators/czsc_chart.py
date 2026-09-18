"""CZSC 收盘结构快照与识别日事件; 复用信号回放,不承担取数。"""
from __future__ import annotations

from datetime import datetime

import polars as pl

from app.indicators import czsc_signals as cs
from app.indicators.czsc_bars import closed_bars
from app.market_time import cn_now


def _label(dt, timeframe: str) -> str:
    return dt.isoformat(timespec="minutes") if timeframe.endswith("m") else dt.date().isoformat()


def _stroke(bi, timeframe: str) -> dict:
    return {
        "start": _label(bi.fx_a.dt, timeframe), "end": _label(bi.fx_b.dt, timeframe),
        "start_price": bi.fx_a.fx, "end_price": bi.fx_b.fx,
    }


def structure_snapshot(analysis, timeframe: str = "1d") -> dict:
    finished = analysis.finished_bis
    strokes = [_stroke(bi, timeframe) for bi in finished]
    centers = [
        {"start": _label(z.sdt, timeframe), "end": _label(z.edt, timeframe), "low": z.zd, "high": z.zg,
         "bi_count": len(z.bis)}
        for z in analysis.zs_list if len(z.bis) >= 3 and z.is_valid()
    ]
    fractals = [
        {"date": _label(fx.dt, timeframe), "price": fx.fx, "kind": "top" if str(fx.mark) == "顶分型" else "bottom"}
        for fx in analysis.fx_list
    ]
    # bi_list 的末笔也可能尚未确认; 不能把它漏画或画成已完成笔。
    unfinished = [_stroke(bi, timeframe) for bi in analysis.bi_list[len(finished):]]
    ubi = analysis.ubi
    if ubi and ubi.get("fx_a"):
        start = ubi["fx_a"]
        upward = str(ubi["direction"]) == "向上"
        end = ubi["high_bar"] if upward else ubi["low_bar"]
        if end.dt > start.dt:
            unfinished.append({"start": _label(start.dt, timeframe), "end": _label(end.dt, timeframe),
                               "start_price": start.fx, "end_price": end.high if upward else end.low})
    return {"strokes": strokes, "centers": centers, "fractals": fractals, "unfinished": unfinished}


def build_chart(df: pl.DataFrame, *, now: datetime | None = None, timeframe: str = "1d") -> dict:
    cutoff = now or cn_now()
    df = closed_bars(df, timeframe, cutoff)
    shapes = {key: [] for key in ("strokes", "centers", "fractals", "unfinished")}

    def collect(analysis):
        for key, values in structure_snapshot(analysis, timeframe).items():
            shapes[key].extend(values)

    result = cs._replay(df, set(cs.SIGNALS), now=cutoff, on_segment=collect, timeframe=timeframe)
    invalid_expr = (pl.col(cs.reason_column(next(iter(cs.SIGNALS)))) == "missing_data").fill_null(False)
    invalid = result.filter(invalid_expr)
    invalid_dates = [(day.isoformat(timespec="minutes") if timeframe.endswith("m") else day.isoformat()) for day in invalid["date"]]
    # Use the shared indicator pipeline, with a separate group after each invalid row.
    from app.indicators.pipeline import compute_indicators

    valid = result.with_columns(invalid_expr.cast(pl.UInt32).cum_sum().alias("_segment")).filter(~invalid_expr)
    if not valid.is_empty():
        valid = valid.with_columns(pl.col("_segment").cast(pl.String).alias("symbol"))
        valid = compute_indicators(valid, needed={"macd_dif", "macd_dea", "macd_hist"}).sort("date")
    rows, signals = [], []
    for row in valid.iter_rows(named=True):
        day = row["date"].isoformat(timespec="minutes") if timeframe.endswith("m") else row["date"].isoformat()
        close = row["close"]
        rows.append({"date": day, **{k: row[k] for k in ("open", "high", "low", "close", "volume", "amount")},
                     **{k: row[k] for k in ("macd_dif", "macd_dea", "macd_hist")}})
        for name in cs.SIGNALS:
            if row[name] is True:
                signals.append({"date": day, "signal_id": name, "price": close})
    return {"rows": rows, **shapes, "signals": signals, "invalid_dates": invalid_dates,
            "coverage": cs.coverage(result, set(cs.SIGNALS))}
