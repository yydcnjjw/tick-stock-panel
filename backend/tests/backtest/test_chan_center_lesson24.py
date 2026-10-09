"""Lesson 24 exceptions must be known at entry, bounded, and fill aware."""
from dataclasses import replace

import numpy as np
import pytest

from app.backtest.chan_center import CenterBook, CenterFeatures, CenterPolicy
from app.backtest.chan_center_lesson24 import (
    DownsideEvidence,
    Lesson24Features,
    Observation,
    Rebound,
)
from app.backtest.engine import MatcherConfig
from tests.backtest.test_chan_center import features, simulate


def setup_book():
    values, days = features(9)
    evidence = DownsideEvidence(1, 10., 12., int(days[0] - 20), int(days[0] - 15),
                                int(days[0] - 5), int(days[0] - 1), 9.5, 4., 2., int(days[0]))
    observations = {(int(days[0]), 0): Observation((evidence,))}
    payload = Lesson24Features(observations)
    policy = CenterPolicy.for_rule_set("第24课止损实验版")
    book = CenterBook(CenterFeatures(values, days, lesson24=payload), policy=policy)
    return book, values, days, evidence, observations


def enter(book):
    order = book.on_close(0, np.array([10.3]), {})[0][0]
    book.on_fill(0, order)
    return order


def test_divergence_entry_freezes_c_low_and_allows_below_zd():
    book, _, _, evidence, _ = setup_book()
    order = enter(book)
    assert order.risk_low == 9.5
    assert order.evidence == evidence
    assert not book.on_close(1, np.array([9.8]), {0: {}})[1]
    sell = book.on_close(2, np.array([9.5]), {0: {}})[1][0]
    assert sell.signal_id == "center_l24_risk_stop"
    assert sell.reason == "center_stop"


@pytest.mark.parametrize("peak,signal", [(9.9, "center_l24_failed_rebound"),
                                        (10., "center_lower_stop"), (10.2, "center_lower_stop")])
def test_only_first_rebound_from_exact_c_endpoint_resolves_exception(peak, signal):
    book, _, days, e, observations = setup_book()
    enter(book)
    observations[int(days[1]), 0] = Observation(rebounds=(Rebound(e.c_end - 2, int(days[1]), 9.7),))
    assert not book.on_close(1, np.array([9.8]), {0: {}})[1]
    observations[int(days[2]), 0] = Observation(rebounds=(Rebound(e.c_end, int(days[2]), peak),))
    assert book.on_close(2, np.array([9.8]), {0: {}})[1][0].signal_id == signal


def test_gap_or_retracted_evidence_restores_lower_stop_without_moving_risk_line():
    for gap in (True, False):
        book, values, days, e, observations = setup_book()
        enter(book)
        if gap:
            values["valid"][1, 0] = False
        else:
            observations[int(days[1]), 0] = Observation(revoked=(e.c_end,))
        assert book.on_close(1, np.array([9.8]), {0: {}})[1][0].signal_id == "center_lower_stop"
        assert book.states[0].risk_low == 9.5


def test_missing_payload_rejected_and_unproven_entry_does_not_trade():
    book, values, days, _, observations = setup_book()
    with pytest.raises(ValueError, match="第24课"):
        CenterBook(CenterFeatures(values, days), policy=book.policy)
    observations.clear()
    values["bottom"][0, 0] = 10.1
    assert not book.on_close(0, np.array([10.3]), {})[0]


def test_evidence_after_close_cannot_cancel_stop_or_backdate_entry():
    book, _, days, e, observations = setup_book()
    observations[int(days[0]), 0] = Observation((replace(e, observed_at=int(days[1])),))
    assert not book.on_close(0, np.array([10.3]), {})[0]


def test_later_proof_can_never_lower_frozen_risk_line():
    book, _, days, e, observations = setup_book()
    enter(book)
    observations[int(days[1]), 0] = Observation(rebounds=(Rebound(e.c_end, int(days[0]), 10.4),))
    assert not book.on_close(1, np.array([10.1]), {0: {}})[1]
    assert book.states[0].downside is None
    newer = replace(e, c_end=int(days[1]), c_low=9.3, observed_at=int(days[2]))
    observations[int(days[2]), 0] = Observation((newer,))
    assert book.on_close(2, np.array([9.8]), {0: {}})[1][0].signal_id == "center_lower_stop"
    assert book.states[0].risk_low == 9.5


def test_later_matching_proof_and_its_first_rebound_use_frozen_center():
    book, values, days, e, observations = setup_book()
    enter(book)
    observations[int(days[1]), 0] = Observation(rebounds=(Rebound(e.c_end, int(days[0]), 10.4),))
    book.on_close(1, np.array([10.1]), {0: {}})
    newer = replace(e, c_end=int(days[1]), c_low=9.6, observed_at=int(days[2]))
    observations[int(days[2]), 0] = Observation((newer,))
    assert not book.on_close(2, np.array([9.8]), {0: {}})[1]
    values["low"][3, 0] = 9.0  # a newer current center must not rescue the old position
    observations[int(days[3]), 0] = Observation(rebounds=(Rebound(newer.c_end, int(days[2]), 9.9),))
    order = book.on_close(3, np.array([9.8]), {0: {}})[1][0]
    assert order.signal_id == "center_l24_failed_rebound"
    assert order.exit_evidence["first_rebound"]["high"] == 9.9


def test_proof_for_another_center_and_completed_rebound_do_not_allow_entry():
    book, _, days, e, observations = setup_book()
    observations[int(days[0]), 0] = Observation((replace(e, center=2),))
    assert not book.on_close(0, np.array([10.3]), {})[0]
    observations[int(days[0]), 0] = Observation((e,), (Rebound(e.c_end, int(days[0]) - 1, 10.3),))
    assert not book.on_close(0, np.array([10.3]), {})[0]


@pytest.mark.parametrize("price", [9.5, 9.8, 12.1])
def test_gap_open_outside_center_cancels_order(price):
    book, *_ = setup_book()
    order = book.on_close(0, np.array([10.3]), {})[0][0]
    assert book.policy.reject_entry(order, price)


def test_sold_position_discards_proof_and_risk():
    book, _, days, _, _ = setup_book()
    enter(book)
    book.on_exit(0, days[1])
    assert book.states[0].downside is None
    assert book.states[0].risk_low == 0
    assert book.states[0].lesson_reference is None


def test_payload_slice_keeps_absolute_dates_and_is_readonly():
    book, _, days, _, _ = setup_book()
    payload = book.features.readonly()
    assert payload.slice(1, 5).lesson24.observations == {}
    assert payload.slice(0, 2).lesson24.observations == payload.lesson24.observations
    with pytest.raises(TypeError):
        payload.lesson24.observations[(int(days[1]), 0)] = Observation()


def test_c_low_risk_sizing_next_open_t1_costs_and_blocked_stop():
    book, values, days, _, _ = setup_book()
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        commission_pct=.001, stamp_tax_pct=.002, slippage_bps=10,
                        center_policy=book.policy)
    result, _ = simulate(values, days, config=cfg, lesson24=book.features.lesson24, patches={
        (1, 0): {"close": 9.4, "low": 9.4},
        (2, 0): {"open": 9., "close": 9., "high": 9., "low": 9., "signal_limit_down": True},
        (3, 0): {"open": 8.7, "low": 8.5},
    })
    trade, = result.trades
    assert trade.entry_signal_id == "center_l24_divergence_buy"
    assert trade.entry_signal_date == "2024-01-01"
    assert trade.entry_date == "2024-01-02"
    assert trade.exit_signal_date == "2024-01-02"
    assert trade.exit_date == "2024-01-04"
    assert trade.exit_signal_id == "center_l24_risk_stop"
    assert trade.blocked_exit_days == 1
    assert trade.shares == 900  # 1000 / (10.5*1.002 - 9.5*.996), rounded down
    assert trade.center_reference["risk_low"] == 9.5
    assert trade.center_reference["lesson24"]["c_area"] == 2.
    assert trade.center_reference["planned_risk_amount"] == pytest.approx(953.1)
