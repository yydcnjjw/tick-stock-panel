"""Native inclusion/fractal timing and historical-prefix observation contracts."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.chan_center import build_center_features
from app.backtest.matrix import build_market_data_matrix
from app.indicators.chan_runtime import ChanReplay
from tests.test_chan_runtime import history


def bars(pairs):
    return [{"symbol": "600000.SH", "date": date(2024, 1, 1) + timedelta(days=i),
             "open": (high + low) / 2, "close": (high + low) / 2,
             "high": high, "low": low, "volume": 1000.0, "amount": 1000000.0}
            for i, (high, low) in enumerate(pairs)]


@pytest.mark.parametrize(("pairs", "kind", "price", "endpoint"), [
    ([(10, 8), (12, 10), (11.5, 10.5), (11, 9), (10.5, 9.5)], "top", 12, 1),
    ([(10, 8), (12, 10), (12, 10.5), (11, 9), (10.5, 9.5)], "top", 12, 2),
    ([(12, 10), (10, 8), (9.5, 8.5), (11, 9), (10.5, 9.5)], "bottom", 8, 1),
])
def test_included_bars_wait_for_right_combined_candle(pairs, kind, price, endpoint):
    rows = bars(pairs)
    replay = ChanReplay("600000.SH", "1d")
    observed = []
    for row in rows:
        replay.update(row)
        observed.append(list(replay.fractal_events))
    assert observed[:3] == [[], [], []]
    event, = observed[3]
    assert event["kind"] == kind
    assert event["price"] == price
    assert event["endpoint_at"] == rows[endpoint]["date"].isoformat()
    assert event["confirmed_at"] == rows[3]["date"].isoformat()
    assert observed[4] == []  # extending the right candle is not another signal


def test_observations_are_prefix_stable_with_real_centers_and_bsp():
    rows = history(1200)
    def compute(part):
        return build_center_features(build_market_data_matrix(pl.DataFrame(part), field_columns={"amount"}), now=datetime(2026, 1, 1))
    whole, prefix = compute(rows), compute(rows[:800])
    assert whole.values["eligible"].any()
    assert (whole.values["buy_at"] > 0).any()
    assert (whole.values["sell_at"] > 0).any()
    for name in whole.values.dtype.names:
        np.testing.assert_array_equal(whole.values[name][:800], prefix.values[name])
    assert not whole.values.flags.writeable


def test_unclosed_and_missing_right_bar_do_not_confirm_fractal():
    rows = bars([(10, 8), (12, 10), (11, 9), (10, 8), (12, 10)])
    market = build_market_data_matrix(pl.DataFrame(rows), field_columns={"amount"})
    unclosed = build_center_features(market, now=datetime(2024, 1, 3, 14, 59))
    assert np.isnan(unclosed.values["top"]).all()
    rows[2]["volume"] = 0.0
    gap = build_center_features(build_market_data_matrix(pl.DataFrame(rows), field_columns={"amount"}), now=datetime(2024, 2, 1))
    assert not gap.values["valid"][2, 0]
    assert np.isnan(gap.values["top"]).all()


def test_non_mainboard_symbols_do_not_enter_universe():
    rows = [{**row, "symbol": "300001.SZ"} for row in history(100)]
    result = build_center_features(build_market_data_matrix(pl.DataFrame(rows), field_columns={"amount"}))
    assert not result.values["eligible"].any()
    assert not result.values["valid"].any()


@pytest.mark.parametrize(("age", "high", "sure", "expected"), [
    (19, 11.0, True, True),
    (20, 11.0, True, False),
    (19, 10.99, True, False),
    (19, 11.0, False, False),
])
def test_latest_center_qualification_does_not_fall_back_to_older_wide_center(monkeypatch, age, high, sure, expected):
    class Replay:
        def __init__(self, *_):
            self.rows = []
            self.fractal_events = []
            self.level = SimpleNamespace(zs_list=[], bi_list=[SimpleNamespace(is_sure=True) for _ in range(6)])
            self.level.bi_list[5].is_sure = sure

        def update(self, row):
            self.rows.append(row)
            end = 24 - age
            if len(self.rows) > end:
                def center(begin, end, first, low, high):
                    return SimpleNamespace(begin=SimpleNamespace(idx=begin), end=SimpleNamespace(idx=end),
                                           begin_bi=SimpleNamespace(idx=first), end_bi=SimpleNamespace(idx=first + 2),
                                           low=low, high=high, is_one_bi_zs=lambda: False)
                self.level.zs_list = [center(0, 1, 0, 10.0, 15.0), center(2, end, 3, 10.0, high)]
            return []

    monkeypatch.setattr("app.backtest.chan_center.ChanReplay", Replay)
    market = build_market_data_matrix(pl.DataFrame(history(25)), field_columns={"amount"})
    result = build_center_features(market)
    latest = result.values[-1, 0]
    assert latest["high"] == high
    assert bool(latest["eligible"]) is expected
