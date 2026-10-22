from datetime import date, datetime, timedelta

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.kline import router
from app.enriched_generation import EnrichedGenerationUnavailableError
from app.indicators import chan_signals as cs
from app.indicators.czsc_bars import prepare_bars
from app.indicators.czsc_chart import build_chart
from app.services.czsc_chart import get_chart
from app.tickflow.repository import DataStore, KlineRepository


def bars(close=4.0):
    return pl.DataFrame({
        'symbol': ['510300.SH'] * 40,
        'date': [date(2024, 1, 1) + timedelta(days=i) for i in range(40)],
        'open': close, 'high': close + .1, 'low': close - .1, 'close': close,
        'volume': 1234.0, 'amount': 500000.0,
    })


@pytest.fixture
def repo(tmp_path):
    store = DataStore(tmp_path)
    repo = KlineRepository(store)
    repo._name_map_cache = None
    repo._instruments_cache = pl.DataFrame({'symbol': ['000001.SZ'], 'name': ['平安银行']})
    repo._etf_instruments_cache = pl.DataFrame({'symbol': ['510300.SH'], 'name': ['沪深300ETF']})
    repo._index_instruments_cache = pl.DataFrame({'symbol': ['000300.SH'], 'name': ['沪深300']})
    repo.append_etf_enriched(bars())
    yield repo
    store.db.close()


@pytest.mark.parametrize('timeframe', ['1d', '1w'])
def test_etf_uses_published_etf_prices_and_existing_engine(repo, monkeypatch, timeframe):
    monkeypatch.setattr(repo, 'get_etf_daily', lambda *a, **kw: pytest.fail('used mutable ordinary daily path'))
    now = datetime(2024, 2, 9, 16)
    result = get_chart(repo, '510300.SH', asset_type='etf', timeframe=timeframe, now=now)
    expected = build_chart(prepare_bars(bars(), timeframe, now=now), timeframe=timeframe, now=now)
    assert result['asset_type'] == 'etf'
    assert result['supported_timeframes'] == ['1m', '5m', '30m', '1d', '1w']
    for field in ['rows', 'strokes', 'signals', 'coverage']:
        assert result[field] == expected[field]
    generation = result['generation']
    repo.append_etf_enriched(bars(5.0))
    updated = get_chart(repo, '510300.SH', asset_type='etf', timeframe=timeframe, now=now)
    assert updated['generation'] != generation
    assert updated['rows'][-1]['close'] == 5.0


def test_etf_api_validates_asset_and_handles_empty_and_corrupt_data(repo, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.repo = repo
    client = TestClient(app)
    url = '/api/kline/czsc'
    assert client.get(url, params={'symbol': '510300.SH', 'asset_type': 'etf'}).json()['status'] == 'ready'
    assert client.get(url, params={'symbol': '510300.SH'}).json()['asset_type'] == 'etf'
    assert client.get(url, params={'symbol': '510300.SH', 'asset_type': 'stock'}).status_code == 400
    assert client.get(url, params={'symbol': '000300.SH', 'asset_type': 'etf'}).status_code == 400
    original = repo.get_daily_published

    def publishing(*args, **kwargs):
        raise EnrichedGenerationUnavailableError('updating')

    monkeypatch.setattr(repo, 'get_daily_published', publishing)
    repo.append_etf_enriched(bars(5.0))
    assert client.get(url, params={'symbol': '510300.SH', 'asset_type': 'etf'}).status_code == 503
    monkeypatch.setattr(repo, 'get_daily_published', original)
    paths = list((repo.store.data_dir / 'kline_etf_enriched').glob('**/*.parquet'))
    for path in paths:
        path.unlink()
    assert client.get(url, params={'symbol': '510300.SH', 'asset_type': 'etf'}).json()['status'] == 'empty'
    paths[0].write_bytes(b'corrupt parquet')
    with pytest.raises(pl.exceptions.ComputeError):
        repo.get_daily_published('510300.SH', date(2025, 1, 1), 10, sorted(cs.INPUT_COLUMNS), asset_type='etf')
