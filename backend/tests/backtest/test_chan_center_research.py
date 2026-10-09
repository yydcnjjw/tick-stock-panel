"""Broader center hypotheses retain observable-time and portfolio constraints."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from app.backtest.chan_center import CenterBook, CenterFeatures, CenterPolicy
from app.backtest.chan_center_context import CenterContext, build_center_context
from app.backtest.engine import BacktestEngine, MatcherConfig
from tests.backtest.test_chan_center import features, simulate


def market_data(n=90):
    close = np.repeat(np.linspace(10, 14, n)[:, None], 2, axis=1)
    return SimpleNamespace(close=close, open=close - .1, high=close + .2,
                           low=close - .2, volume=np.ones_like(close) * 1000,
                           symbols=("600000.SH", "000001.SZ"), shape=close.shape)


def test_context_is_prefix_stable_and_does_not_bridge_missing_days():
    market = market_data()
    full = build_center_context(market)
    prefix = market_data()
    for name in ("close", "open", "high", "low", "volume"):
        setattr(prefix, name, getattr(prefix, name)[:70])
    prefix.shape = prefix.close.shape
    short = build_center_context(prefix)
    for name, array in vars(full).items():
        np.testing.assert_allclose(array[:70], getattr(short, name), equal_nan=True)
        assert not array.flags.writeable
    market.close[30, 0] = np.nan
    broken = build_center_context(market)
    assert np.isnan(broken.ma20[49, 0])
    assert np.isfinite(broken.ma20[50, 0])
    assert np.isnan(broken.atr14[44, 0])
    assert np.isfinite(broken.atr14[45, 0])


def test_context_requirement_fails_closed():
    values, days = features()
    with pytest.raises(ValueError, match="上下文"):
        CenterBook(CenterFeatures(values, days), policy=CenterPolicy(entry_trend="ma20_rising"))


@pytest.mark.parametrize(("policy", "signal"), [
    (dict(target_position=.5), "center_target_sell"),
    (dict(max_hold_bars=2), "center_time_sell"),
    (dict(trail_activate=.02, trail_drawdown=.02), "center_trailing_sell"),
    (dict(close_loss_cap=.02), "center_loss_cap_sell"),
])
def test_research_exits_are_observed_at_close_and_filled_next_open(policy, signal):
    values, days = features(8)
    values["bottom"][0, 0] = 10.1
    patches = {(1, 0): {"high": 10.6},
               (2, 0): {"close": 11.2, "high": 11.4},
               (3, 0): {"close": 10.2, "low": 10.1}}
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        center_policy=replace(CenterPolicy.for_rule_set("完整新版"), **policy))
    r, _ = simulate(values, days, patches=patches, config=cfg)
    trade, = r.trades
    expected_signal_day = "2024-01-03" if signal in ("center_target_sell", "center_time_sell") else "2024-01-04"
    assert trade.exit_signal_id == signal
    assert trade.exit_signal_date == expected_signal_day
    assert trade.exit_date > trade.exit_signal_date
    assert trade.exit_date > trade.entry_date


def test_risk_floor_reduces_shares_without_widening_the_center_stop():
    values, days = features(7)
    values["low"] = 10.49
    values["bottom"][0, 0] = 10.49
    values["top"][3, 0] = 11.9
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        fees_pct=0, slippage_bps=0,
                        center_policy=replace(CenterPolicy.for_rule_set("完整新版"), risk_floor_pct=.05))
    result, _ = simulate(values, days, config=cfg)
    trade, = result.trades
    assert trade.shares == 1900
    assert trade.center_reference["low"] == 10.49
    assert trade.center_reference["sizing_risk_amount"] == pytest.approx(997.5)
    assert trade.center_reference["planned_risk_amount"] == pytest.approx(19)


def test_new_context_does_not_change_legacy_fills():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    values["top"][3, 0] = 11.9
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2)
    before, matrix = simulate(values, days, config=cfg)
    context = build_center_context(SimpleNamespace(
        **{k: getattr(matrix, k) for k in ("open", "close", "high", "low", "volume", "symbols", "shape")}))
    matrix = replace(matrix, center_features=replace(matrix.center_features, context=context))
    after = BacktestEngine(None).simulate_market_matrix(matrix, cfg)
    assert before.trades == after.trades
    assert before.equity_curve == after.equity_curve


@pytest.mark.parametrize("params", [dict(entry_trend="typo"), dict(ranking="typo"),
                                   dict(risk_floor_pct=-.01), dict(target_position=2),
                                   dict(trail_activate=.1), dict(risk_fraction=np.nan)])
def test_invalid_experiments_fail_closed(params):
    with pytest.raises(ValueError):
        CenterPolicy(**params)


def test_cooldown_uses_market_bars_after_actual_exit():
    values, days = features(12)
    days[4:] += 7  # A market closure must not consume seven cooldown bars.
    values["bottom"][0, 0] = 10.1
    values["top"][2, 0] = 11.9
    values["bottom"][4, 0] = 10.1
    values["bottom_at"][4, 0] = days[4]
    values["bottom"][8, 0] = 10.1
    values["bottom_at"][8, 0] = days[8]
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        center_policy=replace(CenterPolicy.for_rule_set("完整新版"), cooldown_bars=5))
    r, _ = simulate(values, days, config=cfg)
    first, = r.trades
    held, = r.stats["open_position_details"]
    assert first.exit_date == "2024-01-04"
    assert held["entry_date"] == "2024-01-17"


def context_for(shape):
    arrays = [np.full(shape, value, dtype=np.float32)
              for value in (.2, 10, 10, 9.9, .1, 10, 1)]
    return CenterContext(*arrays, np.full(shape[0], .6, dtype=np.float32))


@pytest.mark.parametrize(("params", "field", "first_values", "expected"), [
    (dict(entry_trend="above_ma60"), "ma60", [10, 11], {"600000.SH"}),
    (dict(volume_min=1.2), "volume_ratio", [.8, 1.3], {"600001.SH"}),
    (dict(breadth_min=.5), "breadth60", .4, set()),
])
def test_quality_filter_uses_signal_day_and_never_retries_old_fractal(params, field, first_values, expected):
    values, days = features(7, 2)
    values["bottom"][0] = 10.1
    values["top"][3] = 11.9
    _, matrix = simulate(values, days)
    context = context_for(matrix.shape)
    getattr(context, field)[0] = first_values
    # Following observations would allow entries, but the failed order is expired.
    matrix = replace(matrix, center_features=replace(matrix.center_features, context=context.readonly()))
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        center_policy=replace(CenterPolicy.for_rule_set("完整新版"), **params))
    result = BacktestEngine(None).simulate_market_matrix(matrix, cfg)
    assert {t.symbol for t in result.trades} == expected


def test_signal_day_momentum_ranking_selects_candidates_before_fill():
    values, days = features(7, 3)
    values["bottom"][0] = 10.1
    values["top"][3] = 11.9
    _, matrix = simulate(values, days)
    context = context_for(matrix.shape)
    context.momentum20[0] = [.1, .3, .2]
    context.momentum20[1:] = [.9, .1, .2]
    matrix = replace(matrix, center_features=replace(matrix.center_features, context=context.readonly()))
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        center_policy=replace(CenterPolicy.for_rule_set("完整新版"), ranking="momentum"))
    result = BacktestEngine(None).simulate_market_matrix(matrix, cfg)
    assert {t.symbol for t in result.trades} == {"600001.SH", "600002.SH"}


def test_atr_floor_uses_signal_volatility_and_includes_whole_lots():
    values, days = features(7)
    values["bottom"][0] = 10.1
    values["top"][3] = 11.9
    _, matrix = simulate(values, days)
    context = context_for(matrix.shape)
    context.atr14[0] = 1
    context.atr14[1:] = .1
    matrix = replace(matrix, center_features=replace(matrix.center_features, context=context.readonly()))
    cfg = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=2,
                        fees_pct=0, slippage_bps=0,
                        center_policy=replace(CenterPolicy.for_rule_set("完整新版"), atr_risk_multiple=2))
    result = BacktestEngine(None).simulate_market_matrix(matrix, cfg)
    trade, = result.trades
    assert trade.shares == 500
    assert trade.center_reference["sizing_risk_amount"] == 1000
