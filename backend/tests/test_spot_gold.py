from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.data_providers.spot_gold import GoldBar, GoldHistory
from app.indicators.spot_gold_chart import build_gold_charts
from app.market_time import CN_TZ
from app.plugins.sina_gold.provider import SinaGoldProvider, parse_rows
from app.services.spot_gold import SpotGoldService

NOW = datetime(2026, 10, 13, 16, 40, tzinfo=CN_TZ)


def payload(rows):
    return '/* provider comment */\nvar _data=(' + json.dumps(rows) + ');'


def source_row(stamp='2026-10-13 16:00:00', **values):
    return {'d': stamp, 'o': '4180', 'h': '4200', 'l': '4170', 'c': '4190', **values}


def bar(stamp, close=4190):
    return GoldBar(datetime.fromisoformat(stamp), 4180, max(4200, close), 4170, close)


def history():
    return GoldHistory('sina_gold', '新浪伦敦金（现货黄金）CFD',
                       'https://finance.sina.com.cn/futures/quotes/XAU.shtml', NOW,
                       {'1d': (bar('2026-10-09'), bar('2026-10-12'), bar('2026-10-13')),
                        '4h': (bar('2026-10-13 12:00'), bar('2026-10-13 16:00'), bar('2026-10-13 20:00')),
                        '1h': (bar('2026-10-13 15:00'), bar('2026-10-13 16:00'), bar('2026-10-13 17:00'))})


def test_jsonp_normalizes_prices_without_fabricating_volume():
    rows = parse_rows(payload([source_row()]), '1h', NOW)
    assert rows == (bar('2026-10-13 16:00'),)
    assert not hasattr(rows[0], 'volume')


@pytest.mark.parametrize('rows', [
    [source_row(h='4100')], [source_row(c='NaN')],
    [source_row(), source_row()], [source_row('2026-10-13 20:00:00')],
    [source_row('not-a-date')], [],
])
def test_invalid_data_is_rejected_without_repair(rows):
    with pytest.raises(ValueError):
        parse_rows(payload(rows), '1h', NOW)


def test_malformed_response_is_not_empty_history():
    for text in ['null', 'var _data=({"__ERROR":3});', '<html>blocked</html>']:
        with pytest.raises(ValueError):
            parse_rows(text, '1h', NOW)


def test_daily_window_excludes_only_out_of_scope_history():
    valid = {'date': '2021-01-04', 'open': '1800', 'high': '1900', 'low': '1700', 'close': '1850'}
    old = {**valid, 'date': '2020-03-31', 'close': '-1'}
    rows = parse_rows(payload([old, valid]), '1d', NOW)
    assert len(rows) == 1 and rows[0].close == 1850
    with pytest.raises(ValueError):
        parse_rows(payload([{**valid, 'close': '-1'}]), '1d', NOW)


def test_provider_requests_native_periods_and_does_not_substitute_failures():
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if 'DailyKLine' in request.url.path:
            rows = [{'date': '2026-10-12', 'open': '4180', 'high': '4200', 'low': '4170', 'close': '4190'}]
        else:
            rows = [source_row()]
        return httpx.Response(200, text=payload(rows))

    provider = SinaGoldProvider(transport=httpx.MockTransport(respond))
    snapshot = provider.get_spot_gold(now=NOW)
    assert set(snapshot.frames) == {'1d', '4h', '1h'}
    assert len(requests) == 3
    assert any('type=240' in url for url in requests)
    assert any('type=60' in url for url in requests)
    broken = SinaGoldProvider(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    with pytest.raises(httpx.HTTPError):
        broken.get_spot_gold(now=NOW)


def test_four_periods_close_independently_and_keep_intraday_labels():
    result = build_gold_charts(history(), now=NOW)
    panels = {p['timeframe']: p for p in result['panels']}
    assert list(panels) == ['1w', '1d', '4h', '1h']
    assert panels['1d']['cutoff'] == '2026-10-12'
    assert panels['1w']['cutoff'] == '2026-10-05'
    assert panels['4h']['cutoff'] == '2026-10-13T16:00'
    assert panels['1h']['cutoff'] == '2026-10-13T16:00'
    assert all(not p['rows'][-1]['closed'] for p in panels.values())
    assert panels['1w']['rows'][-1]['open'] == 4180
    assert all(p['centers'] == [] for p in panels.values())


def test_daily_midnight_is_not_the_gold_session_close():
    before = build_gold_charts(history(), now=datetime(2026, 10, 13, 1, tzinfo=CN_TZ))
    daily = next(p for p in before['panels'] if p['timeframe'] == '1d')
    assert daily['cutoff'] == '2026-10-09'
    assert daily['rows'][1]['closed'] is False


def test_gold_price_indicators_have_full_ma_warmups_and_no_fabricated_volume():
    start = datetime(2026, 8, 1)
    bars = tuple(GoldBar(start + timedelta(hours=i), 4000 + i, 4002 + i,
                         3999 + i, 4001 + i) for i in range(65))
    h = GoldHistory('fake', '测试源', 'https://example.com', NOW,
                    {'1d': (), '4h': (), '1h': bars})
    rows = build_gold_charts(h, now=NOW)['panels'][-1]['rows']
    for period in (5, 10, 20, 60):
        assert rows[period - 2][f'ma{period}'] is None
        assert rows[period - 2][f'ema{period}'] is None
        assert rows[period - 1][f'ma{period}'] == pytest.approx(4001 + (period - 1) / 2)
        seed = 4001.0
        alpha = 2 / (period + 1)
        for i in range(1, period):
            seed = alpha * (4001 + i) + (1 - alpha) * seed
        assert rows[period - 1][f'ema{period}'] == pytest.approx(seed)
    fast = slow = 4001.0
    dea = 0.0
    for i, row in enumerate(rows):
        if i:
            fast = 2 / 13 * row['close'] + 11 / 13 * fast
            slow = 2 / 27 * row['close'] + 25 / 27 * slow
        dif = fast - slow
        dea = 2 / 10 * dif + 8 / 10 * dea
        assert row['macd_dif'] == pytest.approx(dif)
        assert row['macd_dea'] == pytest.approx(dea)
        assert row['macd_hist'] == pytest.approx(2 * (dif - dea))
        assert 'volume' not in row and 'amount' not in row


def test_unclosed_prices_cannot_change_structure():
    import math

    start = datetime(2026, 8, 1)
    rows = tuple(GoldBar(start + timedelta(hours=i), 4100 + 70 * math.sin(i / 8),
                        4110 + 70 * math.sin(i / 8), 4090 + 70 * math.sin(i / 8),
                        4102 + 70 * math.sin(i / 8)) for i in range(600))
    now = (rows[-1].time - timedelta(minutes=20)).replace(tzinfo=CN_TZ)
    h = GoldHistory('fake', '测试源', 'https://example.com', now, {'1d': (), '4h': (), '1h': rows})
    first = build_gold_charts(h, now=now)['panels'][-1]
    revised = (*rows[:-1], GoldBar(rows[-1].time, 4100, 8000, 1000, 7000))
    second = build_gold_charts(GoldHistory('fake', '测试源', 'https://example.com', now,
                                         {**h.frames, '1h': revised}), now=now)['panels'][-1]
    assert first['strokes'] and first['segments']
    for key in ['strokes', 'unfinished', 'segments', 'centers', 'segment_centers']:
        assert first[key] == second[key]
        assert all(shape['end'] <= first['cutoff'] for shape in first[key])
    assert first['rows'][:-1] == second['rows'][:-1]


class Repo:
    def __init__(self):
        self.calls = 0
        self.error = None

    def get_snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return history()


def test_cache_refresh_and_failure_keep_original_timestamp():
    repo = Repo()
    service = SpotGoldService(repo, clock=lambda: NOW)
    initial = service.get_chart()
    assert service.get_chart() == initial and repo.calls == 1
    service.get_chart(refresh=True)
    assert repo.calls == 2
    repo.error = ValueError('数据格式无效')
    stale = service.get_chart(refresh=True)
    assert stale['status'] == 'stale' and stale['reason']
    assert stale['refreshed_at'] == initial['refreshed_at']
    assert stale['panels'] == initial['panels']
    assert initial['status'] == 'ready'
    assert service.get_chart()['status'] == 'stale'
    assert repo.calls == 3
    with pytest.raises(ValueError):
        SpotGoldService(repo, clock=lambda: NOW).get_chart()


def test_simultaneous_requests_share_one_fetch():
    entered, release = Event(), Event()

    class BlockingRepo(Repo):
        def get_snapshot(self):
            entered.set()
            assert release.wait(5)
            return super().get_snapshot()

    repo = BlockingRepo()
    service = SpotGoldService(repo, clock=lambda: NOW)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(service.get_chart)
        assert entered.wait(5)
        second = pool.submit(service.get_chart)
        release.set()
        assert first.result() == second.result()
    assert repo.calls == 1


def test_extension_registers_and_api_errors_are_explicit(monkeypatch):
    from app.custom import spot_gold
    from app.extensions.loader import configure_backend_extensions

    app = FastAPI()
    registry, errors = configure_backend_extensions(app)
    assert 'tickflow.spot-gold' in registry.extension_ids() and not errors
    repo = Repo()
    service = SpotGoldService(repo, clock=lambda: NOW)
    monkeypatch.setattr(spot_gold, 'get_service', lambda provider: service)
    with TestClient(app) as client:
        response = client.get('/api/custom/spot-gold/chart')
        assert response.status_code == 200
        assert len(response.json()['panels']) == 4
        client.post('/api/custom/spot-gold/refresh')
        assert repo.calls == 2
        repo.error = ValueError('错误字段')
        service = SpotGoldService(repo, clock=lambda: NOW)
        response = client.get('/api/custom/spot-gold/chart')
        assert response.status_code == 503
        assert 'detail' in response.json()


@pytest.mark.parametrize(('now', 'daily_cutoff', 'weekly_cutoff'), [
    (datetime(2026, 10, 10, 7, 59, tzinfo=CN_TZ), None, None),
    (datetime(2026, 10, 10, 8, tzinfo=CN_TZ), '2026-10-09', '2026-10-05'),
])
def test_weekly_and_daily_conservative_close_boundary(now, daily_cutoff, weekly_cutoff):
    result = build_gold_charts(history(), now=now)
    assert result['panels'][0]['cutoff'] == weekly_cutoff
    assert result['panels'][1]['cutoff'] == daily_cutoff


def test_repository_requires_capability_and_correct_provider(monkeypatch):
    from types import SimpleNamespace

    from app.data_providers import custom
    from app.tickflow.spot_gold_repository import SpotGoldRepository

    monkeypatch.setattr(custom, 'provider_has_dataset', lambda name, dataset: False)
    with pytest.raises(ValueError, match='不可用'):
        SpotGoldRepository('sina_gold').get_snapshot()
    monkeypatch.setattr(custom, 'provider_has_dataset', lambda name, dataset: True)
    monkeypatch.setattr(custom, 'get_provider', lambda name: SimpleNamespace())
    with pytest.raises(ValueError, match='未实现'):
        SpotGoldRepository('sina_gold').get_snapshot()
    monkeypatch.setattr(custom, 'get_provider', lambda name: SimpleNamespace(get_spot_gold=history))
    assert SpotGoldRepository('sina_gold').get_snapshot() == history()
    with pytest.raises(ValueError, match='身份不一致'):
        SpotGoldRepository('another_gold').get_snapshot()


def test_preferences_preserve_missing_source_and_cache_isolates_routes(monkeypatch):
    from app.custom.spot_gold import get_service
    from app.services import preferences

    monkeypatch.setattr(preferences, 'load', lambda: {})
    assert preferences.get_spot_gold_data_provider() == 'sina_gold'
    monkeypatch.setattr(preferences, 'load', lambda: {'spot_gold_data_provider': 'missing_gold'})
    assert preferences.get_spot_gold_data_provider() == 'missing_gold'
    assert get_service('sina_gold') is get_service('sina_gold')
    assert get_service('sina_gold') is not get_service('missing_gold')
    get_service.cache_clear()


def test_cache_expires_after_one_minute():
    clock = [NOW]
    repo = Repo()
    service = SpotGoldService(repo, clock=lambda: clock[0])
    service.get_chart()
    clock[0] += timedelta(seconds=59)
    service.get_chart()
    assert repo.calls == 1
    clock[0] += timedelta(seconds=1)
    service.get_chart()
    assert repo.calls == 2


def test_plugin_load_failure_is_isolated(monkeypatch):
    from pathlib import Path

    import yaml

    from app.data_providers.custom import loader

    monkeypatch.setattr(loader, '_PROVIDERS', {})
    monkeypatch.setattr(loader, '_PLUGIN_STATUS', {})
    manifest = yaml.safe_load((Path(__file__).parents[1] / 'app/plugins/sina_gold/plugin.yaml').read_text())
    loader._register_one_plugin({**manifest, 'name': 'broken_gold', 'entry': 'missing.gold:Provider'})
    loader._register_one_plugin(manifest)
    assert not loader._PLUGIN_STATUS['broken_gold']['available']
    assert 'broken_gold' not in loader._PROVIDERS
    assert loader.provider_has_dataset('sina_gold', 'spot_gold')
    assert not loader.provider_has_dataset('sina_gold', 'daily')
