"""chan.py chart output; module path retained for existing chart integrations."""
from __future__ import annotations

from datetime import datetime

import polars as pl

from app.indicators import chan_signals as cs
from app.indicators.chan_runtime import label_time
from app.indicators.czsc_bars import closed_bars
from app.market_time import cn_now


def structure_snapshot(state, timeframe="1d"):
    from app.vendor.chanpy.Common.CEnum import FX_TYPE

    def at(unit):
        return label_time(state.rows[unit.idx]["date"], timeframe)

    def line(item):
        return {"start": at(item.get_begin_klu()), "end": at(item.get_end_klu()),
                "start_price": item.get_begin_val(), "end_price": item.get_end_val(),
                "confirmed": bool(item.is_sure), "dashed": not item.is_sure}

    def centers(items):
        return [{"start": at(z.begin), "end": at(z.end), "low": z.low, "high": z.high,
                 "bi_count": z.end_bi.idx - z.begin_bi.idx + 1, "confirmed": bool(z.is_sure)}
                for z in items if not z.is_one_bi_zs() and z.low <= z.high]

    fractals = []
    for candle in state.level.lst:
        if candle.fx not in (FX_TYPE.TOP, FX_TYPE.BOTTOM):
            continue
        top = candle.fx == FX_TYPE.TOP
        unit = candle.get_peak_klu(top)
        fractals.append({"date": at(unit), "price": unit.high if top else unit.low,
                         "kind": "top" if top else "bottom"})
    return {"strokes": [line(b) for b in state.level.bi_list if b.is_sure],
            "unfinished": [line(b) for b in state.level.bi_list if not b.is_sure],
            "segments": [line(s) for s in state.level.seg_list],
            "centers": centers(state.level.zs_list), "segment_centers": centers(state.level.segzs_list),
            "fractals": fractals, "bsp_points": state.points_snapshot()}


def build_chart(df: pl.DataFrame, *, now: datetime | None = None, timeframe="1d"):
    cutoff = now or cn_now()
    df = closed_bars(df, timeframe, cutoff)
    shapes = {key: [] for key in ("strokes", "centers", "fractals", "unfinished", "segments", "segment_centers", "bsp_points")}
    events = []

    def collect(state):
        for key, values in structure_snapshot(state, timeframe).items():
            shapes[key].extend(values)
        for event in state.events:
            for kind in event["types"]:
                events.append({**event, "event_id": event["event_id"] + ":" + kind,
                               "signal_id": f"signal_chan_{event['level']}_{kind}_{'buy' if event['is_buy'] else 'sell'}"})

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
    rows = []
    for row in valid.iter_rows(named=True):
        day = row["date"].isoformat(timespec="minutes") if timeframe.endswith("m") else row["date"].isoformat()
        rows.append({"date": day, **{k: row[k] for k in ("open", "high", "low", "close", "volume", "amount")},
                     **{k: row[k] for k in ("macd_dif", "macd_dea", "macd_hist")}})
    return {"rows": rows, **shapes, "signals": events, "invalid_dates": invalid_dates,
            "coverage": cs.coverage(result, set(cs.SIGNALS))}
