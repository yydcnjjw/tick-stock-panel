"""交易所分钟源的字段、单位、范围及失败隔离契约。"""

from datetime import datetime

import httpx
import pytest

from app.market_time import CN_TZ
from app.plugins.exchange_minute.provider import ExchangeMinuteProvider


def provider_with_payload(payload, requests):
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=payload)
    provider = ExchangeMinuteProvider()
    provider._client.close()
    provider._client = httpx.Client(transport=httpx.MockTransport(handle))
    return provider


def test_minute_units_time_window_and_market():
    requests = []
    payload = {"code": "002487", "kline": [
        [20260917093000, 10, 10, 10, 10, 1200, 12000],
        [20260917093100, 10, 12, 9, 11, 1500, 16000],
        [20260917093200, 11, 12, 10, 12, 2000, 23000],
    ]}
    provider = provider_with_payload(payload, requests)
    progress = []
    try:
        df = provider.get_minute(["002487.SZ"], datetime(2026, 9, 17, 9, 31, tzinfo=CN_TZ),
                                 datetime(2026, 9, 17, 9, 31), on_chunk_done=lambda *v: progress.append(v))
    finally:
        provider.close()
    assert df.height == 1
    assert df["datetime"][0] == datetime(2026, 9, 17, 9, 31)
    assert df["volume"][0] == 15
    assert df["amount"][0] == 16000
    assert [df[c][0] for c in ("open", "high", "low", "close")] == [10, 12, 9, 11]
    assert requests[0].url.path == "/v1/sz1/mink/002487"
    assert requests[0].url.params["period"] == "1"
    assert 0 < -int(requests[0].url.params["begin"]) <= 70001
    assert progress == [(1, 1)]


@pytest.mark.parametrize("payload", [
    {"code": "600000", "kline": []},
    {"code": "600276"},
    {"code": "600276", "kline": [[20260917093100, 10]]},
    {"code": "600276", "kline": [[20260917093100, 10, 9, 8, 11, 100, 1000]]},
])
def test_malformed_response_is_not_empty_success(payload):
    provider = provider_with_payload(payload, [])
    try:
        with pytest.raises(ValueError):
            provider.get_minute(["600276.SH"], None, None)
    finally:
        provider.close()


def test_empty_and_unsupported_assets_do_not_invent_prices():
    requests = []
    provider = provider_with_payload({"code": "600276", "kline": []}, requests)
    try:
        assert provider.get_minute(["600276.SH"], None, None).is_empty()
        assert provider.get_minute(["920001.BJ"], None, None).is_empty()
        assert provider.get_minute(["000001.SH"], None, None, asset_type="index").is_empty()
        assert len(requests) == 1
    finally:
        provider.close()


def test_network_failure_propagates():
    provider = ExchangeMinuteProvider()
    provider._client.close()
    def fail(request):
        raise httpx.ConnectError("offline", request=request)
    provider._client = httpx.Client(transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(httpx.ConnectError):
            provider.get_minute(["600276.SH"], None, None)
    finally:
        provider.close()
