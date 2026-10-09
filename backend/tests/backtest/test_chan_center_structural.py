"""Structural hypotheses use newly confirmed events and past center observations."""
from dataclasses import replace

import numpy as np
import pytest

from app.backtest.chan_center import CenterBook, CenterFeatures, CenterPolicy
from app.backtest.engine import MatcherConfig
from tests.backtest.test_chan_center import features, simulate


@pytest.mark.parametrize(("mode", "code", "allowed"), [
    ("bsp", 1, True), ("bsp1", 2, False), ("bsp2", 2, True),
    ("bsp2", 3, False), ("bsp3", 3, True), ("bsp3", 4, True),
])
def test_bsp_does_not_require_center_change_or_backdate_fills(mode, code, allowed):
    values, days = features(9)
    values["buy_at"][3, 0] = days[0]
    values["buy_code"][3, 0] = code
    values["sell_at"][6, 0] = days[5]
    values["sell_code"][6, 0] = 2
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        center_policy=CenterPolicy(entry_structure=mode, exit_structure="bsp", risk_sizing=True))
    r, _ = simulate(values, days, config=cfg)
    assert len(r.trades) == int(allowed)
    if allowed:
        trade, = r.trades
        assert trade.entry_date == "2024-01-05"
        assert trade.entry_signal_date == "2024-01-04"
        assert trade.exit_date == "2024-01-08"
        assert trade.center_reference["phase"] == "123"


def test_confirmed_pullback_requires_later_fractal_and_expires_on_sell_or_gap():
    values, days = features(12)
    values["bottom"][:] = 10.1
    values["bottom_at"][:, 0] = days - 1
    values["buy_at"][3, 0] = days[1]
    values["buy_code"][3, 0] = 2
    values["sell_at"][6, 0] = days[5]
    values["buy_at"][7, 0] = days[6]
    values["buy_code"][7, 0] = 2
    values["valid"][8, 0] = False
    policy = CenterPolicy(entry_structure="confirmed_pullback", exit_structure="combined")
    book = CenterBook(CenterFeatures(values, days), policy=policy)
    allowed = [bool(book.on_close(t, np.array([10.3]), {})[0]) for t in range(12)]
    assert allowed == [False, False, False, False, True, True, False, False, False, False, False, False]


def test_pullback_window_counts_market_bars_and_requires_new_endpoint():
    values, days = features(9)
    days[4:] += 10
    values["buy_at"][0, 0] = days[0] - 1
    values["buy_code"][0, 0] = 2
    values["bottom"][:] = 10.1
    values["bottom_at"][:, 0] = days - 1
    policy = CenterPolicy(entry_structure="confirmed_pullback", confirmation_bars=4)
    book = CenterBook(CenterFeatures(values, days), policy=policy)
    allowed = [bool(book.on_close(t, np.array([10.3]), {})[0]) for t in range(9)]
    assert allowed == [False, True, True, True, True, False, False, False, False]


@pytest.mark.parametrize(("relation", "new_low", "new_high", "allowed"), [
    ("non_lower", 11, 13, True), ("non_lower", 9, 13, False),
    ("above", 12, 14, False), ("above", 12.1, 14, True),
])
def test_center_filter_is_past_only_and_boundary_revision_is_not_new_center(relation, new_low, new_high, allowed):
    values, days = features(8)
    values["bottom"][:] = 10.1
    values["bottom_at"][:, 0] = days - 1
    values["high"][1:, 0] = 12.05
    values["center"][3:, 0] = 2
    values["low"][3:, 0], values["high"][3:, 0] = new_low, new_high
    values["bottom"][3:, 0] = new_low
    policy = CenterPolicy(entry_structure="range", exit_structure="top", center_relation=relation)
    book = CenterBook(CenterFeatures(values, days), policy=policy)
    for t in range(3):
        assert not book.on_close(t, np.array([10.3]), {})[0]
    assert bool(book.on_close(3, np.array([new_low + .1]), {})[0]) == allowed
    values["valid"][4, 0] = False
    book.on_close(4, np.array([new_low + .1]), {})
    assert not book.on_close(5, np.array([new_low + .1]), {})[0]


def test_combined_sell_beats_buy_and_lower_stop_beats_bsp_sell():
    values, days = features(8)
    values["buy_at"][0, 0] = days[0] - 1
    values["buy_code"][0, 0] = 2
    policy = CenterPolicy(entry_structure="bsp", exit_structure="combined")
    book = CenterBook(CenterFeatures(values, days), policy=policy)
    buy = book.on_close(0, np.array([10.3]), {})[0][0]
    book.on_fill(0, buy)
    values["sell_at"][1, 0] = days[1]
    values["sell_code"][1, 0] = 3
    assert book.on_close(1, np.array([9.9]), {0: {}})[1][0].signal_id == "center_lower_stop"
    values["sell_at"][0, 0] = days[0]
    values["sell_code"][0, 0] = 1
    assert not CenterBook(CenterFeatures(values, days), policy=policy).on_close(0, np.array([10.3]), {})[0]


@pytest.mark.parametrize("params", [dict(entry_structure="typo"), dict(exit_structure="typo"),
                                   dict(center_relation="typo"), dict(confirmation_bars=0)])
def test_unknown_structure_fails_closed(params):
    with pytest.raises(ValueError):
        replace(CenterPolicy(), **params)
