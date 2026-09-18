"""分钟补齐失败不能伪装为成功、0 行; 覆盖 Node 桥接到单股同步入口。"""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api import kline as kline_api
from app.plugins.stocksdk import bridge
from app.plugins.stocksdk.provider import StockSDKProvider
from app.services import kline_sync
from app.tickflow.capabilities import CapabilitySet


@pytest.mark.parametrize("fail", [False, True])
def test_minute_bridge_distinguishes_empty_from_failed_request(tmp_path, fail):
    bridge_path = tmp_path / "bridge.mjs"
    shutil.copyfile(bridge._BRIDGE_MJS, bridge_path)
    pkg = tmp_path / "node_modules" / "stock-sdk"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({
        "name": "stock-sdk", "type": "module", "main": "index.js",
    }))
    # 一个标的正常返回空数组, 另一个抛出真实请求失败形状, 验证并发池不吞错。
    (pkg / "index.js").write_text("""
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


def _request(repo):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo, capabilities=CapabilitySet(),
    )))


def test_single_minute_sync_reports_provider_failure_without_writes(monkeypatch, tmp_path):
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.store.data_dir = tmp_path
    monkeypatch.setattr(kline_api, "_minute_allowed", lambda _: True)
    monkeypatch.setattr(kline_sync.preferences, "get_minute_data_provider", lambda: "stocksdk")
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda _: (StockSDKProvider(), False, None))
    for name in ("_cleanup_null_datetime_minute", "_migrate_symbol_to_date_partition", "_latest_minute_datetime"):
        monkeypatch.setattr(kline_sync, name, lambda _: None)
    monkeypatch.setattr(bridge, "run_job", MagicMock(side_effect=bridge.StockSDKBridgeError("fetch failed")))
    native = MagicMock()
    monkeypatch.setattr(kline_sync, "get_client", native)
    write = MagicMock()
    monkeypatch.setattr(kline_sync, "_write_minute_partition", write)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(kline_api.sync_minute_single(_request(repo), {"symbol": "600276.SH", "days": 5}))
    assert exc.value.status_code == 502
    assert "stocksdk" in exc.value.detail
    assert "请求失败" in exc.value.detail
    write.assert_not_called()
    native.assert_not_called()


def test_single_minute_sync_does_not_report_success_for_zero_rows(monkeypatch):
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    monkeypatch.setattr(kline_api, "_minute_allowed", lambda _: True)
    monkeypatch.setattr(kline_sync, "sync_and_persist_minute", MagicMock(return_value=0))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(kline_api.sync_minute_single(_request(repo), {"symbol": "600276.SH", "days": 5}))
    assert exc.value.status_code == 422
    assert "未返回" in exc.value.detail
