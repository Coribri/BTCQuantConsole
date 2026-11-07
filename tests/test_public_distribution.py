from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from btcquant.binance_spot import BinanceSpotReadOnly
from btcquant.broker import LiveExecution
from btcquant.config import load_config
from btcquant.web import create_app


def test_public_distribution_omits_partner_interfaces(tmp_path):
    config = replace(load_config('config.toml'), root=tmp_path)
    app = create_app(config, force_paper=True)
    assert app.version == '1.0.0'
    assert all('referral' not in route.path for route in app.routes)
    client = TestClient(app, base_url='http://127.0.0.1:8787', client=('127.0.0.1', 50000))
    response = client.get('/api/live/referrals')
    assert response.status_code == 404
    ui = Path('btcquant/static/ui.js').read_text(encoding='utf-8')
    assert 'initReferrals' not in ui
    assert '返佣' not in ui


def test_public_order_ids_ignore_unrelated_local_partner_settings(tmp_path):
    adapter = BinanceSpotReadOnly(tmp_path)
    adapter.save('public-fixture-key', 'public-fixture-secret')
    (tmp_path/'runtime/binance_link.json').write_text('{"spot":{"link_id":"UNUSED"}}', encoding='utf-8')
    execution = LiveExecution(tmp_path, {('binance', 'spot'): adapter})
    preview = execution.preview({'exchange':'binance', 'market':'spot', 'symbol':'BTCUSDT',
                                 'type':'MARKET', 'side':'BUY', 'quote':'10'})
    token = preview['confirmation']
    assert preview['order'].get('client_order_id', token) == token
    assert execution.previews[token][3][2]['newClientOrderId'] == token
