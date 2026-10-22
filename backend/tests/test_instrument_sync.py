"""Incomplete exchange responses must not erase cached stock metadata."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.services import instrument_sync


@pytest.fixture
def exchange_client(monkeypatch):
    rows = {
        "SH": [{"symbol": "600000.SH", "name": "浦发银行"}],
        "SZ": [{"symbol": "000538.SZ", "name": "云南白药"}],
        "BJ": [{"symbol": "920001.BJ", "name": "测试北交所股票"}],
    }
    fetch = Mock(side_effect=lambda exchange, **_: rows[exchange])
    monkeypatch.setattr(instrument_sync, "_fetch_instruments_via_provider", lambda: None)
    monkeypatch.setattr(
        instrument_sync, "get_client",
        lambda: SimpleNamespace(exchanges=SimpleNamespace(get_instruments=fetch)),
    )
    return rows, fetch


@pytest.mark.parametrize("failed_exchange", ["SH", "SZ", "BJ"])
@pytest.mark.parametrize("failure", ["error", "empty"])
@pytest.mark.parametrize("existing", [True, False])
def test_incomplete_sync_keeps_previous_snapshot(
    tmp_path, exchange_client, failed_exchange, failure, existing,
):
    rows, fetch = exchange_client
    target = tmp_path / "instruments" / "instruments.parquet"
    old = pl.DataFrame({
        "symbol": ["600000.SH", "000538.SZ"],
        "name": ["浦发银行", "云南白药"],
        "float_shares": [100.0, 200.0],
        "as_of": [date(2026, 10, 13)] * 2,
    })
    if existing:
        target.parent.mkdir()
        old.write_parquet(target)
        before = target.read_bytes()

    def get_instruments(exchange, **_):
        if exchange == failed_exchange:
            if failure == "error":
                raise RuntimeError("503 Service Unavailable")
            return []
        return rows[exchange]

    fetch.side_effect = get_instruments

    assert instrument_sync.sync_instruments(tmp_path) == 0
    if existing:
        assert target.read_bytes() == before
        assert_frame_equal(pl.read_parquet(target), old)
    else:
        assert not target.exists()


def test_complete_sync_replaces_snapshot(tmp_path, exchange_client):
    target = tmp_path / "instruments" / "instruments.parquet"
    target.parent.mkdir()
    pl.DataFrame({"symbol": ["600001.SH"], "name": ["旧标的"]}).write_parquet(target)

    assert instrument_sync.sync_instruments(tmp_path) == 3

    saved = pl.read_parquet(target)
    assert dict(saved.select("symbol", "name").iter_rows()) == {
        "600000.SH": "浦发银行",
        "000538.SZ": "云南白药",
        "920001.BJ": "测试北交所股票",
    }
    assert saved["as_of"].to_list() == [date.today()] * 3


def test_custom_provider_snapshot_does_not_require_all_exchanges(tmp_path, monkeypatch):
    monkeypatch.setattr(
        instrument_sync, "_fetch_instruments_via_provider",
        lambda: [{"symbol": "000538.SZ", "name": "云南白药"}],
    )
    client = Mock(side_effect=AssertionError("must not fetch TickFlow"))
    monkeypatch.setattr(instrument_sync, "get_client", client)

    assert instrument_sync.sync_instruments(tmp_path) == 1
    saved = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert saved["name"].to_list() == ["云南白药"]
    client.assert_not_called()
