"""Position-dependent rules must use fills, not hypothetical signal positions."""
from datetime import date

import numpy as np
import polars as pl
import pytest

from app.backtest.chan_center import CENTER_DTYPE, CenterBook, CenterFeatures
from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.matrix import (
    build_market_data_matrix,
    build_market_matrix_from_signals,
    make_signal_matrix,
)


def features(days=10, assets=1):
    values = np.zeros((days, assets), dtype=CENTER_DTYPE)
    for field in ("bottom", "top"):
        values[field] = np.nan
    values["valid"] = True
    values["center"] = 1
    values["low"] = 10
    values["high"] = 12
    values["eligible"] = True
    ordinals = np.arange(date(2024, 1, 1).toordinal(), date(2024, 1, 1).toordinal() + days)
    values["bottom_at"] = ordinals[0] - 1
    values["top_at"] = ordinals[0] - 1
    return values, ordinals


def test_freeze_only_on_fill_and_stop_beats_top():
    values, days = features()
    values["bottom"][0, 0] = 10.1
    values["bottom_at"][0, 0] = days[0] - 1
    book = CenterBook(CenterFeatures(values, days))
    buys, _ = book.on_close(0, np.array([10.5]), {})
    assert 0 in buys
    assert book.states[0].frozen is None
    book.on_fill(0, buys[0])
    values["low"][1:, 0] = 9
    values["top"][1, 0] = 11.9
    _, sells = book.on_close(1, np.array([9.8]), {0: {}})
    assert sells[0].reason == "center_stop"
    assert book.states[0].frozen == (10.0, 12.0)


def test_phase_switch_temporal_filter_and_above_center_entry():
    values, days = features()
    book = CenterBook(CenterFeatures(values, days))
    book.on_close(0, np.array([11.0]), {})
    values["high"][1:, 0] = 13
    values["buy_at"][1:4, 0] = [days[0], days[1], days[2]]
    values["buy_code"][1:4, 0] = 3
    assert not book.on_close(1, np.array([14.0]), {})[0]
    assert not book.on_close(2, np.array([14.0]), {})[0]
    buys, _ = book.on_close(3, np.array([14.0]), {})
    assert buys[0].signal_id == "center_bsp_3a_buy"
    book.on_fill(0, buys[0])
    assert book.states[0].frozen == (10.0, 13.0)


def test_reentry_requires_new_endpoint_after_exit_and_sell_wins():
    values, days = features()
    values["bottom"][:] = 10.1
    values["bottom_at"][:] = days[0]
    book = CenterBook(CenterFeatures(values, days))
    buy = book.on_close(1, np.array([10.3]), {})[0][0]
    book.on_fill(0, buy)
    book.on_exit(0, days[2])
    assert not book.on_close(3, np.array([10.3]), {})[0]
    values["bottom_at"][4, 0] = days[3]
    assert book.on_close(4, np.array([10.3]), {})[0]
    values["top"][5, 0] = 11.9
    values["bottom_at"][5, 0] = days[4]
    assert not book.on_close(5, np.array([10.3]), {})[0]


def simulate(values, days, *, patches=None, absent=(), config=None, lesson24=None):
    patches = patches or {}
    rows = []
    for t, ordinal in enumerate(days):
        for a in range(values.shape[1]):
            if (t, a) in absent:
                continue
            rows.append({"symbol": f"60000{a}.SH", "date": date.fromordinal(int(ordinal)),
                         "open": 10.5, "high": 11.0, "low": 10.0, "close": 10.5,
                         "volume": 1000.0, "amount": 1050000.0,
                         "signal_limit_up": False, "signal_limit_down": False,
                         **patches.get((t, a), {})})
    market = build_market_data_matrix(pl.DataFrame(rows))
    entry = np.isfinite(values["bottom"]) | (values["buy_at"] > 0)
    if lesson24 is not None:
        for (day, asset), observation in lesson24.observations.items():
            if observation.divergences:
                entry[np.searchsorted(days, day), asset] = True
    signals = make_signal_matrix(market.shape, entry=entry,
                                 score=np.ones(market.shape),
                                 center_features=CenterFeatures(values, days, lesson24=lesson24))
    matrix = build_market_matrix_from_signals(market, signals, entry_delay_bars=1, exit_delay_bars=1)
    result = BacktestEngine(repo=None).simulate_market_matrix(
        matrix, config or MatcherConfig(matching="open_t+1", initial_capital=100000,
                                       max_positions=1, fees_pct=0, slippage_bps=0))
    return result, matrix


def test_next_market_day_missing_buy_expires_instead_of_next_symbol_row():
    values, days = features(6, 2)
    values["bottom"][0, 0] = 10.1
    result, matrix = simulate(values, days, absent={(1, 0)})
    assert not matrix.entry[2, 0]
    assert not result.trades


def test_close_stop_next_open_with_gap_and_blocked_sell_keeps_request():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    result, _ = simulate(values, days, patches={
        (1, 0): {"close": 9.8, "low": 9.7},
        (2, 0): {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "signal_limit_down": True},
        (3, 0): {"open": 8.7, "low": 8.5},
    })
    trade, = result.trades
    assert trade.entry_date == "2024-01-02"
    assert trade.exit_date == "2024-01-04"
    assert trade.exit_signal_date == "2024-01-02"
    assert trade.exit_price == 8.7
    assert trade.exit_reason == "center_stop"
    assert trade.blocked_exit_days == 1


@pytest.mark.parametrize("config", [MatcherConfig(), MatcherConfig(matching="open_t+1", max_hold_days=3)])
def test_incompatible_execution_settings_fail_closed(config):
    values, days = features(4)
    values["bottom"][0, 0] = 10.1
    with pytest.raises(ValueError, match="中枢震荡"):
        simulate(values, days, config=config)


def test_top_exit_uses_frozen_reference_and_preserves_signal_and_costs():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    values["top"][2, 0] = 11.9
    config = MatcherConfig(matching="open_t+1", initial_capital=100000, max_positions=1,
                           commission_pct=.001, stamp_tax_pct=.002, slippage_bps=10)
    result, _ = simulate(values, days, patches={(3, 0): {"open": 11.7, "high": 12.0}}, config=config)
    trade, = result.trades
    assert trade.entry_signal_id == "center_bottom_buy"
    assert trade.exit_signal_id == "center_top_sell"
    assert trade.exit_signal_date == "2024-01-03"
    assert trade.exit_date == "2024-01-04"
    assert trade.center_reference == {"low": 10.0, "high": 12.0, "phase": "range"}
    assert trade.shares % 100 == 0
    assert trade.entry_value == pytest.approx(trade.shares * 10.5 * 1.002, abs=.01)
    assert trade.exit_value == pytest.approx(trade.shares * 11.7 * .996, abs=.02)


def test_limit_up_buy_is_not_retried_and_no_phantom_position():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    values["top"][3, 0] = 11.9
    result, _ = simulate(values, days, patches={
        (1, 0): {"open": 11.55, "high": 11.55, "low": 11.55, "close": 11.55, "signal_limit_up": True},
    })
    assert result.trades == []
    assert result.stats["execution"]["buy_limit_up"] == 1
    assert result.stats["open_positions"] == 0


def test_no_time_stop_and_no_forced_end_sale():
    values, days = features(30)
    values["bottom"][0, 0] = 10.1
    result, _ = simulate(values, days)
    assert result.trades == []
    assert result.stats["open_positions"] == 1
    assert result.equity_curve[-1]["positions"] == 1


def test_payload_survives_pipeline_slicing_and_masks():
    from app.backtest.matrix import (
        MatrixPipelineConfig,
        MatrixStrategyPipeline,
        apply_time_masks,
        slice_signal_matrix,
    )

    values, days = features(7)
    class Strategy:
        def compute_signals(self, market, params):
            return make_signal_matrix(market.shape, entry=np.ones(market.shape),
                                      center_features=CenterFeatures(values, days))
    rows = [{"symbol": "600000.SH", "date": date.fromordinal(int(d)), "open": 11.0,
             "high": 12.0, "low": 10.0, "close": 11.0, "volume": 100.0} for d in days]
    market = build_market_data_matrix(pl.DataFrame(rows))
    result = MatrixStrategyPipeline().run(Strategy(), market, {}, MatrixPipelineConfig(
        basic_filter={"enabled": False}, scoring={}, order_by="score", descending=True))
    sliced = slice_signal_matrix(result, 2, 5)
    masked = apply_time_masks(sliced, np.array([False, True, True]), np.ones(3, dtype=bool))
    np.testing.assert_array_equal(masked.center_features.days, days[2:5])
    assert masked.entry[:, 0].tolist() == [0, 1, 1]
    assert not masked.center_features.values.flags.writeable


def test_123_round_trip_above_center_and_sell_confirmation_dates():
    values, days = features(9)
    values["high"][1:] = 13.0
    values["buy_at"][3, 0] = days[2]
    values["buy_code"][3, 0] = 3
    values["sell_at"][6, 0] = days[5]
    values["sell_code"][6, 0] = 2
    patches = {(t, 0): {"open": 14.5, "high": 15.0, "close": 14.0} for t in range(3, 9)}
    result, _ = simulate(values, days, patches=patches)
    trade, = result.trades
    assert trade.entry_date == "2024-01-05"
    assert trade.entry_signal_date == "2024-01-04"
    assert trade.entry_signal_id == "center_bsp_3a_buy"
    assert trade.exit_date == "2024-01-08"
    assert trade.exit_signal_date == "2024-01-07"
    assert trade.exit_signal_id == "center_bsp_2_sell"
    assert trade.center_reference == {"low": 10.0, "high": 13.0, "phase": "123"}


def test_same_center_extension_does_not_switch_phase():
    values, days = features(8)
    book = CenterBook(CenterFeatures(values, days))
    for t in range(8):
        book.on_close(t, np.array([11.0]), {})
    assert book.states[0].phase_since == 0


def test_blocked_fractal_exit_preserves_original_signal_id():
    values, days = features(7)
    values["bottom"][0, 0] = 10.1
    values["top"][2, 0] = 11.9
    result, _ = simulate(values, days, patches={(3, 0): {"volume": 0.0}})
    trade, = result.trades
    assert trade.exit_date == "2024-01-05"
    assert trade.exit_signal_date == "2024-01-03"
    assert trade.exit_signal_id == "center_top_sell"
    assert trade.blocked_exit_days == 1
