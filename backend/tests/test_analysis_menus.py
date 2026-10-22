from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.analysis import router


def client_for(data_dir):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    app.include_router(router)
    return TestClient(app)


def test_gold_extension_round_trip_preserves_other_pages(tmp_path):
    with client_for(tmp_path) as client:
        old = {'label': '已有明细', 'data_source': 'existing', 'template': 'table'}
        assert client.post('/api/analysis-menus/old', json=old).status_code == 200
        gold = {'label': '伦敦金', 'data_source': 'spot_gold', 'template': 'spot_gold', 'order': 100, 'nav_group': '商品行情'}
        response = client.post('/api/analysis-menus/spot_gold', json=gold)
        assert response.status_code == 200, response.text
        created = response.json()['created_at']
        gold.update(label='黄金观察', visible=False)
        assert client.post('/api/analysis-menus/spot_gold', json=gold).status_code == 200
    with client_for(tmp_path) as client:
        saved = client.get('/api/analysis-menus/spot_gold').json()
        assert saved['template'] == 'spot_gold' and saved['data_source'] == 'spot_gold'
        assert saved['label'] == '黄金观察' and not saved['visible']
        assert saved['created_at'] == created
        assert saved['nav_group'] == '商品行情'
        assert saved['detail_columns'] == [] and saved['group_columns'] == []
        assert {m['id'] for m in client.get('/api/analysis-menus').json()['items']} == {'old', 'spot_gold'}
        assert client.get('/api/analysis-menus/old').json()['data_source'] == 'existing'
        assert client.get('/api/analysis-menus/old').json()['nav_group'] is None
        assert client.get('/api/analysis-menus/missing').status_code == 404
        assert client.delete('/api/analysis-menus/spot_gold').status_code == 200
        assert [m['id'] for m in client.get('/api/analysis-menus').json()['items']] == ['old']


def test_gold_template_rejects_incompatible_source(tmp_path):
    with client_for(tmp_path) as client:
        for template, source in [('spot_gold', 'stocks'), ('unknown_chart', 'spot_gold')]:
            response = client.post('/api/analysis-menus/gold', json={
                'label': '黄金', 'template': template, 'data_source': source,
            })
            assert response.status_code == 422
        assert client.get('/api/analysis-menus').json()['items'] == []
