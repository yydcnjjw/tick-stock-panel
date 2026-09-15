"""持仓告警只能使用本轮新鲜快照, 盘中量比在计算回退时保持一致。"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from test_realtime_enriched_resume import _live_state, _previous_enriched

from app.indicators import pipeline
from app.market_time import CN_TZ
from app.services import quote_service
from app.services.quote_service import QuoteService
from app.strategy.monitor import MonitorRuleEngine

NOW = datetime(2026, 9, 14, 10, 30, tzinfo=CN_TZ)
NOW_MS = int(NOW.timestamp() * 1000)


@pytest.fixture
def monitor(monkeypatch, tmp_path):
    monkeypatch.setattr(quote_service, "cn_now", lambda: NOW)
    monkeypatch.setattr(quote_service, "cn_today", lambda: NOW.date())
    service = QuoteService()
    repo = MagicMock()
    repo.store.data_dir = tmp_path
    repo.get_name_map.return_value = {}
    engine = MonitorRuleEngine()
    engine.set_rules([{
        "id": "fresh_price", "name": "价格提醒", "type": "price",
        "scope": "all", "enabled": True, "cooldown_seconds": 3600,
        "conditions": [{"field": "close", "op": ">", "value": 10}],
    }])
    service.set_repo(repo)
    service.set_app_state(SimpleNamespace(repo=repo, monitor_engine=engine))
    monkeypatch.setattr(service, "_is_continuous_trading", lambda: True)
    monkeypatch.setattr(service, "_inject_intraday_signals", lambda df, *_: df)
    monkeypatch.setattr(service, "_enrich_alerts_ext", lambda events: None)
    monkeypatch.setattr(service, "_maybe_send_system_notifications", lambda events: None)
    deliveries = []
    monkeypatch.setattr(service, "_maybe_send_webhook", lambda events, _: deliveries.extend(events))
    return service, deliveries


def _snapshot(symbols, timestamps):
    return pl.DataFrame({
        "symbol": symbols, "close": [11.0] * len(symbols),
        "change_pct": [0.1] * len(symbols),
        "quote_ts": pl.Series(timestamps, dtype=pl.Int64),
    })


def test_only_current_fresh_rows_can_alert(monitor):
    service, deliveries = monitor
    symbols = ["fresh", "boundary", "stale", "missing", "future", "yesterday", "old_cache"]
    timestamps = [NOW_MS - 1000, NOW_MS - 120000, NOW_MS - 120001,
                  None, NOW_MS + 1000, NOW_MS - 86400000, NOW_MS - 10000]
    cached = _snapshot(symbols, timestamps)
    daily = _snapshot(symbols, [*timestamps[:-1], NOW_MS - 1000])
    service.get_enriched_today = lambda: (cached, NOW.date())
    service._evaluate_monitors(daily, None)
    assert {event["symbol"] for event in deliveries} == {"fresh", "boundary"}


@pytest.mark.parametrize("daily", [pl.DataFrame(), _snapshot(["A"], [NOW_MS]).drop("quote_ts")])
def test_missing_current_snapshot_does_not_replay_cache(monitor, daily):
    service, deliveries = monitor
    service.get_enriched_today = lambda: (_snapshot(["A"], [NOW_MS]), NOW.date())
    service._evaluate_monitors(daily, None)
    assert deliveries == []


def test_stale_snapshot_does_not_consume_cooldown(monitor):
    service, deliveries = monitor
    current = _snapshot(["A"], [NOW_MS - 120001])
    service.get_enriched_today = lambda: (current, NOW.date())
    service._evaluate_monitors(current, None)
    assert deliveries == []
    current = _snapshot(["A"], [NOW_MS])
    service._evaluate_monitors(current, None)
    assert [event["symbol"] for event in deliveries] == ["A"]


@pytest.mark.parametrize("hour,minute,expected", [(10, 30, 4.0), (12, 0, 2.0), (15, 0, 1.0)])
def test_live_volume_ratio_and_signals_match_incremental_and_fallback(
    monkeypatch, tmp_path, hour, minute, expected,
):
    monkeypatch.setattr(quote_service, "cn_today", lambda: NOW.date())
    # Both built-in and user signals must see the adjusted ratio.
    exprs = {"csg_volume_test": (pl.col("vol_ratio_5d") >= 2).alias("csg_volume_test")}
    monkeypatch.setattr(pipeline, "_custom_signal_exprs", exprs)
    monkeypatch.setattr(pipeline, "_custom_signal_exprs_today", exprs)
    monkeypatch.setattr(pipeline, "attach_deviation_columns_today", lambda df, *_: df)
    timestamp = int(NOW.replace(hour=hour, minute=minute).timestamp() * 1000)
    rows = [{
        "symbol": "600001.SH", "date": NOW.date() - timedelta(days=offset),
        "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
        "volume": 1000.0, "amount": 10000.0,
    } for offset in range(1, 71)]
    hist = pl.DataFrame(rows)
    monkeypatch.setattr(quote_service, "scan_daily_parquet", lambda _: hist.lazy())
    daily = pl.DataFrame([{
        **rows[0], "date": NOW.date(), "quote_ts": timestamp,
    }])
    outputs = []
    for incremental in (True, False):
        repo = MagicMock()
        repo.store.data_dir = tmp_path
        repo.get_live_agg.return_value = _live_state() if incremental else pl.DataFrame()
        repo.get_enriched_latest.return_value = (_previous_enriched(), NOW.date() - timedelta(days=3))
        repo.get_instruments.return_value = pl.DataFrame()
        repo.get_historical_shares.return_value = pl.DataFrame()
        service = QuoteService()
        service.set_repo(repo)
        service._flush_live_enriched(daily)
        repo.flush_live_enriched_asset.assert_called_once()
        outputs.append(repo.flush_live_enriched_asset.call_args.args[1].row(0, named=True))
    for row in outputs:
        assert row["vol_ratio_5d"] == pytest.approx(expected)
        assert row["signal_volume_surge"] is (expected >= 2)
        assert row["csg_volume_test"] is (expected >= 2)
