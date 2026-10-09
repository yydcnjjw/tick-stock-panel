"""Confirmed execution controls: price gates, cost-aware risk and independent runs."""
from dataclasses import replace

import pytest

from app.backtest.chan_center import CenterPolicy
from app.backtest.engine import BacktestEngine, MatcherConfig
from tests.backtest.test_chan_center import features, simulate


def config(rule="完整新版", **kw):
    return MatcherConfig(matching="open_t+1", initial_capital=100000,
                         max_positions=2, center_policy=CenterPolicy.for_rule_set(rule), **kw)


@pytest.mark.parametrize(("price", "reason"), [
    (10.0, "buy_center_lower"), (9.9, "buy_center_lower"),
    (11.0, "buy_center_position"), (10.6, "buy_center_distance"),
])
def test_price_gates_use_fill_not_signal_close_and_do_not_retry(price, reason):
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    values["top"][3, 0] = 11.9
    result, _ = simulate(values, days, config=config(), patches={
        (1, 0): {"open": price, "low": min(price, 10.0)},
    })
    assert result.trades == []
    assert result.stats["open_positions"] == 0
    assert result.stats["execution"][reason] == 1


@pytest.mark.parametrize(("low", "high", "price"), [(10, 11, 10.4), (9.5, 11, 10)])
def test_inclusive_position_and_distance_boundaries(low, high, price):
    values, days = features(7)
    values["low"], values["high"] = low, high
    values["bottom"][0, 0] = low + .01
    values["top"][3, 0] = high
    result, _ = simulate(values, days, config=config(), patches={(1, 0): {"open": price}})
    assert len(result.trades) == 1


def test_upper_breakout_top_exits_on_confirmation_next_open_only():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    values["top"][3, 0] = 13
    baseline, matrix = simulate(values, days, config=config("原版"))
    revised = BacktestEngine(None).simulate_market_matrix(matrix, config("仅扩大退出"))
    again = BacktestEngine(None).simulate_market_matrix(matrix, config("原版"))
    assert not baseline.trades and not again.trades
    trade, = revised.trades
    assert trade.exit_date == "2024-01-05"
    assert trade.exit_signal_date == "2024-01-04"
    assert trade.exit_signal_id == "center_top_sell"
    assert baseline.equity_curve == again.equity_curve
    assert not matrix.center_features.values.flags.writeable


def test_risk_size_includes_both_costs_and_keeps_unspent_cash():
    values, days = features(7, 2)
    values["bottom"][0] = 10.1
    values["top"][3] = 11.9
    cfg = config(commission_pct=.001, stamp_tax_pct=.002, slippage_bps=10)
    result, _ = simulate(values, days, config=cfg)
    assert len(result.trades) == 2
    # Per-share loss at ZD = 10.5*1.002 - 10*.996 = .561; 1000/.561 -> 1700 shares.
    assert [t.shares for t in result.trades] == [1700, 1700]
    assert result.equity_curve[1]["cash"] == pytest.approx(64228.60)
    for trade in result.trades:
        ref = trade.center_reference
        assert ref["entry_equity"] == 100000
        assert ref["planned_risk_amount"] == pytest.approx(953.7)
        assert ref["planned_risk_amount"] <= ref["risk_budget_amount"] == 1000


def test_invalid_candidate_and_under_one_lot_do_not_consume_slots():
    values, days = features(7, 4)
    values["low"][:, 1] = 1000
    values["high"][:, 1] = 1400
    values["bottom"][0] = [10.1, 1001, 10.1, 10.1]
    values["top"][3] = [11.9, 1350, 11.9, 11.9]
    patches = {(t, 1): {"open": 1010, "high": 1100, "low": 1000, "close": 1010}
               for t in range(7)}
    patches[1, 0] = {"open": 11.0}
    result, _ = simulate(values, days, config=config(), patches=patches)
    assert {t.symbol for t in result.trades} == {"600002.SH", "600003.SH"}
    assert result.stats["execution"]["buy_center_position"] == 1
    assert result.stats["execution"]["buy_lot_size"] == 1


def test_123_above_center_still_enters_with_risk_sizing():
    values, days = features(9)
    values["high"][1:] = 13
    values["buy_at"][3, 0], values["buy_code"][3, 0] = days[2], 3
    values["sell_at"][6, 0], values["sell_code"][6, 0] = days[5], 2
    patches = {(t, 0): {"open": 14.5, "high": 15, "close": 14} for t in range(3, 9)}
    result, _ = simulate(values, days, patches=patches, config=config(fees_pct=0, slippage_bps=0))
    trade, = result.trades
    assert trade.entry_signal_id == "center_bsp_3a_buy"
    assert trade.center_reference["phase"] == "123"
    assert trade.shares == 200  # budget 1000 / risk 4.5, rounded to whole lots


def test_gap_loss_can_exceed_planned_budget_and_stop_still_wins():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    values["top"][1, 0] = 13
    result, _ = simulate(values, days, config=config(fees_pct=0, slippage_bps=0), patches={
        (1, 0): {"close": 9.8, "low": 9.7},
        (2, 0): {"open": 8.0, "low": 8.0},
    })
    trade, = result.trades
    assert trade.exit_reason == "center_stop"
    assert trade.exit_date == "2024-01-03"
    assert trade.pnl_amount == -5000
    assert trade.center_reference["planned_risk_amount"] == 1000


def test_half_equity_cap_applies_even_with_one_slot_and_tiny_stop_distance():
    values, days = features(7)
    values["low"] = 10.49
    values["bottom"][0, 0] = 10.49
    values["top"][3, 0] = 11.9
    cfg = config(fees_pct=0, slippage_bps=0)
    cfg.max_positions = 1
    result, _ = simulate(values, days, config=cfg)
    trade, = result.trades
    assert trade.shares == 4700
    assert trade.entry_value <= 50000
    assert trade.position_pct <= .5


def test_unclosed_fill_retains_risk_audit_without_forced_sale():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    result, _ = simulate(values, days, config=config())
    assert result.trades == []
    held, = result.stats["open_position_details"]
    assert held["entry_signal_id"] == "center_bottom_buy"
    assert held["center_reference"]["planned_risk_amount"] <= 1000


def test_rule_sets_keep_each_ablation_independent():
    expected = {"原版": (False, False, False), "仅成交门槛": (True, False, False),
                "仅扩大退出": (False, True, False), "仅风险定仓": (False, False, True),
                "完整新版": (True, True, True)}
    for name, flags in expected.items():
        p = CenterPolicy.for_rule_set(name)
        assert (p.entry_gate, p.extended_exit, p.risk_sizing) == flags
    with pytest.raises(ValueError):
        CenterPolicy.for_rule_set("unknown")


def test_entry_gate_alone_preserves_score_weighted_sizing():
    values, days = features(7, 3)
    values["bottom"][0] = 10.1
    values["top"][3] = 11.9
    _, matrix = simulate(values, days, config=config("原版"))
    scores = matrix.score.copy()
    scores[:, 0], scores[:, 1], scores[:, 2] = 3, 1, .5
    matrix = replace(matrix, score=scores)
    engine = BacktestEngine(None)
    baseline = engine.simulate_market_matrix(matrix, config("原版", position_sizing="score_weight"))
    gated = engine.simulate_market_matrix(matrix, config("仅成交门槛", position_sizing="score_weight"))
    assert [t.shares for t in gated.trades] == [t.shares for t in baseline.trades]
    assert gated.trades[0].shares > gated.trades[1].shares
