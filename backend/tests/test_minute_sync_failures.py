"""分钟补齐失败不能伪装为成功、0 行; 覆盖 provider 到单股同步入口。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api import kline as kline_api
from app.services import kline_sync
from app.tickflow.capabilities import CapabilitySet


def _request(repo):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo, capabilities=CapabilitySet(),
    )))


def test_single_minute_sync_reports_provider_failure_without_writes(monkeypatch, tmp_path):
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.store.data_dir = tmp_path
    monkeypatch.setattr(kline_api, "_minute_allowed", lambda _: True)
    provider = MagicMock()
    provider.get_minute.side_effect = RuntimeError("fetch failed")
    monkeypatch.setattr(kline_sync.preferences, "get_minute_data_provider", lambda: "test_provider")
    monkeypatch.setattr(kline_sync, "_resolve_minute_provider", lambda _: (provider, False, None))
    for name in ("_cleanup_null_datetime_minute", "_migrate_symbol_to_date_partition", "_latest_minute_datetime"):
        monkeypatch.setattr(kline_sync, name, lambda _: None)
    native = MagicMock()
    monkeypatch.setattr(kline_sync, "get_client", native)
    write = MagicMock()
    monkeypatch.setattr(kline_sync, "_write_minute_partition", write)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(kline_api.sync_minute_single(_request(repo), {"symbol": "600276.SH", "days": 5}))
    assert exc.value.status_code == 502
    assert "test_provider" in exc.value.detail
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
