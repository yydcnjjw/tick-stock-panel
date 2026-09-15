"""CZSC 收盘结构快照与识别日事件; 复用信号回放,不承担取数。"""
from __future__ import annotations

from datetime import datetime, time

import polars as pl

from app.indicators import czsc_signals as cs
from app.market_time import CN_TZ, cn_now


def _day(dt) -> str:
    return dt.date().isoformat()


def _stroke(bi) -> dict:
    return {
        "start": _day(bi.fx_a.dt), "end": _day(bi.fx_b.dt),
        "start_price": bi.fx_a.fx, "end_price": bi.fx_b.fx,
    }


def structure_snapshot(analysis) -> dict:
    finished = analysis.finished_bis
    strokes = [_stroke(bi) for bi in finished]
    centers = [
        {"start": _day(z.sdt), "end": _day(z.edt), "low": z.zd, "high": z.zg,
         "bi_count": len(z.bis)}
        for z in analysis.zs_list if len(z.bis) >= 3 and z.is_valid()
    ]
    fractals = [
        {"date": _day(fx.dt), "price": fx.fx, "kind": "top" if str(fx.mark) == "顶分型" else "bottom"}
        for fx in analysis.fx_list
    ]
    # bi_list 的末笔也可能尚未确认; 不能把它漏画或画成已完成笔。
    unfinished = [_stroke(bi) for bi in analysis.bi_list[len(finished):]]
    ubi = analysis.ubi
    if ubi and ubi.get("fx_a"):
        start = ubi["fx_a"]
        upward = str(ubi["direction"]) == "向上"
        end = ubi["high_bar"] if upward else ubi["low_bar"]
        if end.dt > start.dt:
            unfinished.append({"start": _day(start.dt), "end": _day(end.dt),
                               "start_price": start.fx, "end_price": end.high if upward else end.low})
    return {"strokes": strokes, "centers": centers, "fractals": fractals, "unfinished": unfinished}


def build_chart(df: pl.DataFrame, *, now: datetime | None = None) -> dict:
    cutoff = now or cn_now()
    cutoff = cutoff.replace(tzinfo=CN_TZ) if cutoff.tzinfo is None else cutoff.astimezone(CN_TZ)
    df = df.filter(
        (pl.col("date") < cutoff.date())
        | ((pl.col("date") == cutoff.date()) & pl.lit(cutoff.time() >= time(15)))
    ).sort("date")
    shapes = {key: [] for key in ("strokes", "centers", "fractals", "unfinished")}

    def collect(analysis):
        for key, values in structure_snapshot(analysis).items():
            shapes[key].extend(values)

    result = cs._replay(df, set(cs.SIGNALS), now=cutoff, on_segment=collect)
    invalid_expr = (pl.col(cs.reason_column(next(iter(cs.SIGNALS)))) == "missing_data").fill_null(False)
    invalid = result.filter(invalid_expr)
    invalid_dates = [day.isoformat() for day in invalid["date"]]
    # Use the shared indicator pipeline, with a separate group after each invalid row.
    from app.indicators.pipeline import compute_indicators

    valid = result.with_columns(invalid_expr.cast(pl.UInt32).cum_sum().alias("_segment")).filter(~invalid_expr)
    if not valid.is_empty():
        valid = valid.with_columns(pl.col("_segment").cast(pl.String).alias("symbol"))
        valid = compute_indicators(valid, needed={"macd_dif", "macd_dea", "macd_hist"}).sort("date")
    rows, signals = [], []
    for row in valid.iter_rows(named=True):
        day = row["date"].isoformat()
        close = row["close"]
        rows.append({"date": day, **{k: row[k] for k in ("open", "high", "low", "close", "volume", "amount")},
                     **{k: row[k] for k in ("macd_dif", "macd_dea", "macd_hist")}})
        for name in cs.SIGNALS:
            if row[name] is True:
                signals.append({"date": day, "signal_id": name, "price": close})
    return {"rows": rows, **shapes, "signals": signals, "invalid_dates": invalid_dates,
            "coverage": cs.coverage(result, set(cs.SIGNALS))}
