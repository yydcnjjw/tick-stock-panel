from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from app.indicators.czsc_bars import prepare_bars
from app.market_time import CN_TZ


def minutes(day=date(2026, 9, 14)):
    times = [datetime.combine(day, datetime.min.time()) + timedelta(minutes=m)
             for m in [570, *range(571, 691), *range(781, 901)]]
    n = len(times)
    return pl.DataFrame({
        "symbol": ["600000.SH"] * n, "datetime": times,
        "open": [10.0] * n, "high": [12.0] * n, "low": [9.0] * n,
        "close": [11.0] * n, "volume": [2.0] * n, "amount": [2200.0] * n,
    })


@pytest.mark.parametrize(("timeframe", "count"), [("1m", 240), ("5m", 48), ("30m", 8)])
def test_minute_sessions_auction_and_ohlcv(timeframe, count):
    frame = minutes().with_columns(
        pl.when(pl.col("datetime").dt.time() == datetime.min.time().replace(hour=9, minute=30))
        .then(9.5).otherwise(pl.col("open")).alias("open"),
    )
    bars = prepare_bars(frame, timeframe, now=datetime(2026, 9, 14, 15, tzinfo=CN_TZ))
    assert len(bars) == count
    assert bars[0, "open"] == 9.5
    assert bars[0, "high"] == 12 and bars[0, "low"] == 9 and bars[0, "close"] == 11
    assert bars[0, "volume"] == (240 // count + 1) * 2
    assert bars["volume"].sum() == 482
    assert bars["amount"].sum() == 530200
    assert not any(datetime.min.time().replace(hour=11, minute=30) < t.time()
                   <= datetime.min.time().replace(hour=13) for t in bars["date"])


def test_closed_minute_buckets_gaps_and_invalid_ohlc():
    now = datetime(2026, 9, 14, 10, 17, tzinfo=CN_TZ)
    frame = minutes()
    bars = prepare_bars(frame, "30m", now=now)
    assert bars["date"].to_list() == [datetime(2026, 9, 14, 10)]
    assert prepare_bars(frame, "5m", now=now)[-1, "date"] == datetime(2026, 9, 14, 10, 15)
    missing = frame.filter(pl.col("datetime") != datetime(2026, 9, 14, 9, 42))
    assert prepare_bars(missing, "30m", now=now)[0, "close"] is None
    bad = frame.with_columns(pl.when(pl.col("datetime") == datetime(2026, 9, 14, 9, 42))
                             .then(None).otherwise(pl.col("open")).alias("open"))
    assert prepare_bars(bad, "30m", now=now)[0, "close"] is None
    zero = frame.with_columns(pl.lit(0.0).alias("volume"), pl.lit(0.0).alias("amount"))
    assert prepare_bars(zero, "30m", now=now)[0, "close"] == 11
    with pytest.raises(ValueError, match="重复"):
        prepare_bars(pl.concat([frame, frame.head(1)]), "5m", now=now)


def test_whole_missing_bucket_is_retained_as_a_structure_break():
    frame = minutes().filter(~pl.col("datetime").is_between(
        datetime(2026, 9, 14, 10, 1), datetime(2026, 9, 14, 10, 30)))
    bars = prepare_bars(frame, "30m", now=datetime(2026, 9, 14, 15, tzinfo=CN_TZ))
    assert len(bars) == 8 and bars[1, "close"] is None
    assert bars[2, "close"] == 11


def test_minute_clock_timezone_and_unsupported_timestamp_contract():
    frame = minutes()
    local = prepare_bars(frame, "5m", now=datetime(2026, 9, 14, 10, 17, tzinfo=CN_TZ))
    utc = prepare_bars(frame, "5m", now=datetime(2026, 9, 14, 2, 17, tzinfo=UTC))
    assert local.equals(utc)
    assert prepare_bars(frame, "1m", now=datetime(2026, 9, 14, 9, 30, tzinfo=CN_TZ)).is_empty()
    # 13:00 could mean a start-labelled series; it cannot be guessed into the 13:01 bucket.
    wrong = frame.head(1).with_columns(pl.lit(datetime(2026, 9, 14, 13)).alias("datetime"))
    with pytest.raises(ValueError, match="结束标签"):
        prepare_bars(wrong, "5m", now=datetime(2026, 9, 14, 15, tzinfo=CN_TZ))


def test_weekly_aggregation_uses_closed_weeks_and_does_not_hide_bad_daily_rows():
    days = [date(2026, 9, 7) + timedelta(days=i) for i in [0, 1, 2, 3, 4, 7, 8, 9, 10, 11]]
    frame = pl.DataFrame({"symbol": ["600000.SH"] * 10, "date": days,
                          "open": [10.0] * 10, "high": [12.0] * 10, "low": [9.0] * 10,
                          "close": [11.0] * 10, "volume": [2.0] * 10, "amount": [2200.0] * 10})
    before = prepare_bars(frame, "1w", now=datetime(2026, 9, 18, 14, 59, tzinfo=CN_TZ))
    assert before["date"].to_list() == [date(2026, 9, 11)]
    assert before[0, "volume"] == 10 and before[0, "amount"] == 11000
    after = prepare_bars(frame, "1w", now=datetime(2026, 9, 18, 15, tzinfo=CN_TZ))
    assert len(after) == 2
    broken = frame.with_columns(pl.when(pl.col("date") == date(2026, 9, 8))
                                .then(None).otherwise(pl.col("high")).alias("high"))
    assert prepare_bars(broken, "1w", now=datetime(2026, 9, 18, 15, tzinfo=CN_TZ))[0, "close"] is None
    # A short holiday week is confirmed conservatively after Friday, not on its last available day.
    short = frame.filter(pl.col("date") <= date(2026, 9, 9))
    assert prepare_bars(short, "1w", now=datetime(2026, 9, 9, 16, tzinfo=CN_TZ)).is_empty()
    assert prepare_bars(short, "1w", now=datetime(2026, 9, 11, 15, tzinfo=CN_TZ))[0, "date"] == date(2026, 9, 9)
