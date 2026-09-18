"""图表周期输入: 北京时间收盘标签、分时段聚合和缺口保留。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import polars as pl

from app.market_time import CN_TZ

FREQUENCIES = {"1m": "F1", "5m": "F5", "30m": "F30", "1d": "D", "1w": "W"}
PRICE_COLUMNS = ("open", "high", "low", "close")
VALUE_COLUMNS = (*PRICE_COLUMNS, "volume", "amount")


def bar_close_time(value: date | datetime, timeframe: str) -> datetime:
    """返回北京时间墙钟闭合边界; 周线保守等到周五,不猜测节假日。"""
    if timeframe.endswith("m"):
        if not isinstance(value, datetime) or value.tzinfo is not None:
            raise ValueError("CZSC 分钟时间必须为北京时间墙钟 datetime")
        return value
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("CZSC 日/周线 date 必须为交易日期")
    if timeframe == "1w":
        value += timedelta(days=4 - value.weekday())
    return datetime.combine(value, time(15))


def closed_bars(df: pl.DataFrame, timeframe: str, now: datetime) -> pl.DataFrame:
    if timeframe not in FREQUENCIES:
        raise ValueError("不支持的 CZSC 图表周期")
    if df.is_empty():
        return df
    stamp = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    if timeframe.endswith("m"):
        dtype = df.schema["date"]
        if not isinstance(dtype, pl.Datetime) or dtype.time_zone is not None:
            raise ValueError("CZSC 分钟时间必须为北京时间墙钟 datetime")
        end = pl.col("date")
    else:
        if df.schema["date"] != pl.Date:
            raise ValueError("CZSC 日/周线 date 必须为交易日期")
        day = pl.col("date")
        if timeframe == "1w":
            day = day.dt.truncate("1w") + pl.duration(days=4)
        end = day.cast(pl.Datetime("us")) + pl.duration(hours=15)
    return df.filter(end <= stamp.replace(tzinfo=None)).sort("date")


def _valid_values(*, minute: bool) -> pl.Expr:
    prices_valid = (pl.col("low") <= pl.min_horizontal("open", "close")) & (
        pl.max_horizontal("open", "close") <= pl.col("high")
    )
    return pl.all_horizontal([
        pl.col(c).is_not_null() & pl.col(c).is_finite()
        & ((pl.col(c) >= 0) if minute and c in {"volume", "amount"} else (pl.col(c) > 0))
        for c in VALUE_COLUMNS
    ]) & prices_valid


def _aggregations() -> list[pl.Expr]:
    return [pl.col("open").first(), pl.col("high").max(), pl.col("low").min(),
            pl.col("close").last(), pl.col("volume").sum(), pl.col("amount").sum(),
            pl.col("_valid").all().alias("_valid")]


def prepare_bars(df: pl.DataFrame, timeframe: str, *, now: datetime) -> pl.DataFrame:
    """缺失/非法桶以空价格保留为断点, 不伪造可绘制 K 线。

    分钟源使用结束标签。09:30 竞价行并入首桶, 不抵充正常分钟数量;
    只聚合 09:31..11:30 和 13:01..15:00, 午休不跨桶。
    """
    if timeframe not in FREQUENCIES:
        raise ValueError("不支持的 CZSC 图表周期")
    if df.is_empty():
        return df
    minute = timeframe.endswith("m")
    column = "datetime" if minute else "date"
    missing = {"symbol", column, *VALUE_COLUMNS} - set(df.columns)
    if missing:
        raise ValueError(f"CZSC 行情缺少字段: {sorted(missing)}")
    if df[column].null_count():
        raise ValueError("CZSC 行情存在空时间")
    if df.select(pl.struct("symbol", column).is_duplicated().any()).item():
        raise ValueError("CZSC 行情存在重复的标的和时间")
    if timeframe == "1d":
        return closed_bars(df, timeframe, now)
    df = df.sort(["symbol", column]).with_columns(_valid_values(minute=minute).alias("_valid"))
    if not minute:
        df = closed_bars(df, "1d", now)
        groups = df.group_by("symbol", pl.col("date").dt.truncate("1w").alias("_week")).agg(
            pl.col("date").last(), *_aggregations(),
        )
    else:
        df = closed_bars(df.rename({"datetime": "date"}), timeframe, now)
        step = int(timeframe[:-1])
        minute_of_day = pl.col("date").dt.hour().cast(pl.Int32) * 60 + pl.col("date").dt.minute()
        df = df.with_columns(minute_of_day.alias("_minute"))
        regular = pl.col("_minute").is_between(571, 690) | pl.col("_minute").is_between(781, 900)
        # Start-labelled or out-of-session bars cannot silently become end-labelled input.
        allowed = regular | (pl.col("_minute") == 570)
        if df.filter(~allowed | (pl.col("date").dt.second() != 0)
                     | (pl.col("date").dt.microsecond() != 0)).height:
            raise ValueError("分钟时间不符合结束标签口径: 需 09:31-11:30、13:01-15:00,可含 09:30 竞价行")
        days = df.select("symbol", pl.col("date").dt.date().alias("_day")).unique()
        start = pl.when(pl.col("_minute") <= 690).then(570).otherwise(780)
        offset = pl.max_horizontal(pl.col("_minute") - start, pl.lit(1))
        end = pl.col("date").dt.truncate("1d") + pl.duration(minutes=start + ((offset + step - 1) // step) * step)
        df = df.with_columns(end.alias("_end"), regular.alias("_regular"))
        groups = df.group_by("symbol", pl.col("_end").alias("date")).agg(
            *_aggregations(), pl.col("_regular").sum().alias("_count"),
        )
        # Entire missing buckets must also interrupt the replay, including gaps at session edges.
        grid = days.with_columns(pl.concat_list([
            pl.datetime_ranges(pl.col("_day").cast(pl.Datetime("us")) + pl.duration(minutes=s + step),
                               pl.col("_day").cast(pl.Datetime("us")) + pl.duration(minutes=e),
                               interval=f"{step}m")
            for s, e in [(570, 690), (780, 900)]
        ]).alias("date")).explode("date", empty_as_null=True).select("symbol", "date")
        groups = grid.join(groups, on=["symbol", "date"], how="left").with_columns(
            (pl.col("_valid") & (pl.col("_count") == step)).fill_null(False).alias("_valid"),
        )
    groups = groups.with_columns([
        pl.when(pl.col("_valid")).then(pl.col(c)).otherwise(None).alias(c) for c in VALUE_COLUMNS
    ]).select("symbol", "date", *VALUE_COLUMNS)
    return closed_bars(groups, timeframe, now)
