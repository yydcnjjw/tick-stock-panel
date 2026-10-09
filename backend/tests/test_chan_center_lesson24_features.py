"""Real native replay plus numeric evidence tests, without future endpoints."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.chan_center import build_center_features
from app.backtest.chan_center_lesson24 import Lesson24Replay, negative_area
from app.backtest.matrix import build_market_data_matrix
from tests.test_chan_runtime import history


def pen(idx, start, end, low, histogram, *, down=True, sure=True):
    units = [SimpleNamespace(idx=start + i, macd=SimpleNamespace(macd=v)) for i, v in enumerate(histogram)]
    return SimpleNamespace(idx=idx, is_sure=sure, is_down=lambda: down, is_up=lambda: not down,
              get_begin_klu=lambda: SimpleNamespace(idx=start), get_end_klu=lambda: SimpleNamespace(idx=end),
              get_end_val=lambda: low, klc_lst=[SimpleNamespace(lst=units)])


def native_fixture(c_hist=(-1., 1., -.5), c_low=9., sure=True):
    a = pen(0, 0, 2, 9.5, [-2., 1., -2.])
    c = pen(4, 5, 7, c_low, c_hist, sure=sure)
    middle = [SimpleNamespace(idx=i, is_sure=True) for i in range(1, 4)]
    center = SimpleNamespace(bi_in=a, bi_out=c, is_one_bi_zs=lambda: False,
                begin=SimpleNamespace(idx=2), begin_bi=middle[0], end_bi=middle[-1], low=10., high=12.)
    # The first up pen is virtual. It must not resolve the exception.
    up = pen(5, 7, 8, 9.8, [1., 2.], down=False, sure=False)
    rows = [{"date": date(2024, 1, 1) + timedelta(days=i)} for i in range(10)]
    # Middle pens are only used for the center completeness test.
    for b in middle:
        b.is_up = lambda: False
    return SimpleNamespace(rows=rows, level=SimpleNamespace(zs_list=[center], bi_list=[a, *middle, c, up])), c, up


def test_full_negative_area_and_first_observed_event_not_peak_or_single_bar():
    replay, c, up = native_fixture()
    tracker = Lesson24Replay()
    assert negative_area(c) == 1.5
    observation = tracker.update(replay)
    e, = observation.divergences
    assert (e.a_area, e.c_area) == (4., 1.5)
    assert e.c_end < e.observed_at
    assert not observation.rebounds
    assert not tracker.update(replay).divergences
    up.is_sure = True
    rebound, = tracker.update(replay).rebounds
    assert rebound.start == e.c_end
    assert rebound.high == 9.8
    c.is_sure = False
    assert tracker.update(replay).revoked == (e.c_end,)


@pytest.mark.parametrize("kwargs", [dict(c_hist=(-3., 1., -2.)), dict(c_hist=(-2., 1., -2.)),
                                    dict(c_hist=(1., 2., 1.)), dict(c_low=9.5), dict(sure=False)])
def test_no_evidence_for_stronger_equal_zero_area_no_new_low_or_unfinished(kwargs):
    replay, *_ = native_fixture(**kwargs)
    assert not Lesson24Replay().update(replay).divergences


def test_late_proof_includes_previously_observed_first_rebound():
    replay, c, up = native_fixture(sure=False)
    up.is_sure = True
    tracker = Lesson24Replay()
    assert tracker.update(replay).rebounds
    c.is_sure = True
    o = tracker.update(replay)
    assert o.rebounds[0].start == o.divergences[0].c_end


def test_real_native_prefix_is_identical_including_revocations_and_gap_reset():
    rows = history(1200)
    def compute(data):
        market = build_market_data_matrix(pl.DataFrame(data), field_columns={"amount"})
        return build_center_features(market, now=datetime(2026, 1, 1), lesson24=True)
    full, prefix = compute(rows), compute(rows[:800])
    full.validate(full.values.shape)
    assert any(o.divergences for o in full.lesson24.observations.values())
    assert any(o.revoked for o in full.lesson24.observations.values())
    assert full.slice(0, 800).lesson24.observations == prefix.lesson24.observations
    for name in full.values.dtype.names:
        np.testing.assert_array_equal(full.values[name][:800], prefix.values[name])
    rows[800] = {**rows[800], "volume": 0.}
    gap = compute(rows)
    assert not gap.values["valid"][800, 0]
    assert all(e.a_start > gap.days[800] for (day, _), o in gap.lesson24.observations.items()
               if day > gap.days[800] for e in o.divergences)
