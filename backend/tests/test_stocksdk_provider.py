"""Restored stock-sdk plugin contracts, with no live market requests."""

import json
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from app.data_providers.custom import loader
from app.plugins.stocksdk import bridge
from app.plugins.stocksdk.provider import StockSDKProvider


@pytest.mark.parametrize("available", [False, True])
def test_plugin_loader_isolates_missing_dependencies(monkeypatch, available):
    monkeypatch.setattr(loader, "_PROVIDERS", {})
    monkeypatch.setattr(loader, "_PLUGIN_STATUS", {})
    monkeypatch.setattr(bridge, "availability", lambda: (available, "test dependency status"))
    manifest = loader.plugin_manifest("stocksdk")
    assert manifest is not None
    loader._register_one_plugin(manifest)
    assert loader._PLUGIN_STATUS["stocksdk"]["available"] is available
    if available:
        provider = loader._PROVIDERS["stocksdk"]
        assert isinstance(provider, StockSDKProvider)
        assert set(provider.config.datasets) == {"daily", "adj_factor", "minute", "realtime"}
        assert provider.minute_history_days == 5
    else:
        assert "stocksdk" not in loader._PROVIDERS


def test_minute_timestamps_are_beijing_wall_clock():
    stamp = int(datetime(2026, 10, 21, 1, 35, tzinfo=UTC).timestamp() * 1000)
    frame = StockSDKProvider._minute_df([{
        "timestamp": stamp, "open": 10, "high": 11, "low": 9, "close": 10.5,
        "volume": 123, "amount": 123000,
    }], "600000.SH")
    row = frame.row(0, named=True)
    assert row["datetime"] == datetime(2026, 10, 21, 9, 35)
    assert row["symbol"] == "600000.SH"
    assert row["volume"] == 123
    assert row["amount"] == 123000


def test_degenerate_minute_opens_are_missing_instead_of_invented():
    rows = [{
        "date": f"2026-10-21 09:{35 + i:02d}",
        "open": 10, "high": 12, "low": 9, "close": 10 + i / 100,
        "volume": 123, "amount": 123000,
    } for i in range(15)]
    frame = StockSDKProvider._minute_df(rows, "600000.SH")
    assert frame["open"].null_count() == len(rows)
    assert frame["close"].null_count() == 0


def test_minute_request_failure_reaches_sync_caller(monkeypatch):
    def fail(*args, **kwargs):
        raise bridge.StockSDKBridgeError("fetch failed")

    monkeypatch.setattr(bridge, "run_job", fail)
    with pytest.raises(bridge.StockSDKBridgeError, match="fetch failed"):
        StockSDKProvider().get_minute(["600000.SH"], None, None)


def test_realtime_percent_and_amount_units(monkeypatch):
    monkeypatch.setattr(bridge, "run_job", lambda *args, **kwargs: {"rows": [{
        "symbol": "600000.SH", "change_pct": 2.5, "amount": 1234,
    }]})
    row = StockSDKProvider().get_realtime()[0]
    assert row["change_pct"] == pytest.approx(0.025)
    assert row["amount"] == 12340000


def test_missing_node_fails_with_explicit_dependency_error(monkeypatch):
    monkeypatch.setattr(bridge, "_node_bin", lambda: None)
    with pytest.raises(bridge.StockSDKBridgeError, match="node"):
        bridge.run_job({"op": "ping"})


@pytest.mark.parametrize("fail", [False, True])
def test_minute_bridge_distinguishes_empty_from_failed_request(tmp_path, fail):
    bridge_path = tmp_path / "bridge.mjs"
    shutil.copyfile(bridge._BRIDGE_MJS, bridge_path)
    package = tmp_path / "node_modules" / "stock-sdk"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "stock-sdk", "type": "module", "main": "index.js",
    }))
    (package / "index.js").write_text("""
export class StockSDK {
  kline = { cnMinute: async (symbol) => {
    if (symbol === '600276.SH') throw new Error('fetch failed');
    return [];
  } };
}
""")
    symbols = ["002487.SZ", "600276.SH"] if fail else ["002487.SZ"]
    result = subprocess.run(
        ["node", str(bridge_path)],
        input=json.dumps({"op": "minute", "symbols": symbols, "period": "1"}),
        capture_output=True, text=True, timeout=20, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is not fail
    if fail:
        assert "600276.SH" in payload["error"]
        assert "fetch failed" in payload["error"]
    else:
        assert payload["rows"] == {"002487.SZ": []}
