from dataclasses import replace
import json
import threading

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from btcquant.config import load_config
from btcquant.models import ModelRegistry,ModelSpec,ModelSignal,PROTOCOL,formula
from btcquant.quant_risk import update_trailing,data_guard
from btcquant.futures_paper import FuturesPaperService
from btcquant.service import TradingService
from btcquant.strategies import generate_signals
from btcquant.web import create_app


def config(tmp_path):
    (tmp_path/'config.toml').write_text('',encoding='utf-8')
    return load_config(tmp_path/'config.toml')


def frame(n=500,direction=1):
    end=pd.Timestamp.now(tz='UTC').floor('h')-pd.Timedelta(hours=1)
    times=pd.date_range(end=end,periods=n,freq='h')
    close=100*np.exp(np.linspace(0,direction*.3,n))
    return pd.DataFrame({'open_time':times,'open':close,'close':close,'high':close*1.005,'low':close*.995,'volume':100.})


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_trailing_activates_and_only_tightens(side):
    pos={'entry_price':100.,'side':side,'trailing_enabled':True,'trail_activation_pct':.02,'trail_distance_pct':.01}
    favorable=1 if side=='LONG' else -1
    update_trailing(pos,100+favorable,.0005,.0002)
    assert not pos.get('trailing_active')
    update_trailing(pos,100+favorable*3,.0005,.0002)
    assert pos['trailing_active']
    first=pos['trailing_stop']
    update_trailing(pos,100+favorable*5,.0005,.0002)
    better=pos['trailing_stop']
    assert better>first if side=='LONG' else better<first
    update_trailing(pos,100+favorable*4,.0005,.0002)
    assert pos['trailing_stop']==better
    # Exit at trigger includes entry/exit fees and exit slippage.
    fill=better*(1-.0002 if side=='LONG' else 1+.0002)
    net=favorable*(fill-100)-.0005*(100+fill)
    assert net>0


@pytest.mark.parametrize('side,peak,reversal',[('LONG',104,102.8),('SHORT',96,97.2)])
def test_trailing_exit_works_with_auto_off(tmp_path,side,peak,reversal):
    svc=FuturesPaperService(config(tmp_path));price={'v':100.}
    svc.price=lambda:price['v']
    svc.place_order(side=side,qty=1,leverage=3)
    p=svc._positions()[0]
    svc.set_trailing(p['id'],True,.02,.01)
    price['v']=peak;svc.poll()
    assert svc._positions()[0]['trailing_active']
    price['v']=reversal;svc.poll()
    assert not svc._positions()
    assert svc.state['orders'][-1]['reason']=='trailing_profit'
    assert svc.state['wallet']>10000


def test_trailing_does_not_activate_beyond_cost_breakeven():
    p={'entry_price':100.,'side':'LONG','trailing_enabled':True,'trail_activation_pct':.001,'trail_distance_pct':.0005}
    update_trailing(p,100.11,.001,.0002)
    assert not p.get('trailing_active')


@pytest.mark.parametrize('direction',[1,-1])
def test_trend_v2_has_causal_symmetric_signals(direction):
    df=frame(600,direction);s=generate_signals(df,'trend_v2')
    expected='entry_signal' if direction==1 else 'short_entry_signal'
    assert s[expected].iloc[350:].any()
    changed=df.copy();changed.loc[450:,['close','open','high','low']]*=2
    other=generate_signals(changed,'trend_v2')
    pd.testing.assert_frame_equal(s.iloc[:450],other.iloc[:450])


def test_data_quality_stale_future_and_gap():
    df=frame()
    assert data_guard(df,'1h') is None
    assert '过期' in data_guard(df.assign(open_time=df.open_time-pd.Timedelta(days=3)),'1h')
    assert '尚未收盘' in data_guard(df.assign(open_time=df.open_time+pd.Timedelta(hours=2)),'1h')
    assert '缺口' in data_guard(df.drop(len(df)-5),'1h')


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_futures_auto_both_directions_have_bounded_risk_and_stops(tmp_path,monkeypatch,side):
    svc=FuturesPaperService(config(tmp_path));df=frame(500,1 if side=='LONG' else -1)
    svc.price=lambda:100.
    svc.candles=lambda *a,**k:df
    sig=generate_signals(df,'trend_v2');i=sig.index[-1]
    sig.loc[i,'entry_signal']=side=='LONG';sig.loc[i,'short_entry_signal']=side=='SHORT'
    sig.loc[i,'exit_signal']=False;sig.loc[i,'short_exit_signal']=False
    monkeypatch.setattr(svc.models,'evaluate_nonblocking',lambda *a,**k:sig)
    svc.set_auto(True,'1h',5,.005)
    svc._check_auto(100.)
    p=svc._positions()[0]
    assert p['side']==side and p['stop_loss'] and p['trailing_enabled']
    assert p['initial_risk_price']*p['qty']<10000*.005
    # Repeated polling or disable/re-enable cannot double-open the same candle.
    old=p['qty'];svc._last_auto_check=0;svc._check_auto(100.);assert svc._positions()[0]['qty']==old
    svc.set_auto(False,'1h',5);svc.set_auto(True,'1h',5);svc._check_auto(100.)
    assert svc._positions()[0]['qty']==old


def test_auto_does_not_close_manual_positions(tmp_path,monkeypatch):
    svc=FuturesPaperService(config(tmp_path));svc.price=lambda:100.;svc.place_order(side='LONG',qty=1)
    df=frame();sig=generate_signals(df,'trend_v2');sig.loc[sig.index[-1],'exit_signal']=True
    svc.candles=lambda *a,**k:df;monkeypatch.setattr(svc.models,'evaluate_nonblocking',lambda *a,**k:sig)
    svc.set_auto(True,'1h',5);svc._check_auto(100.)
    assert svc._positions()[0]['reason']=='market'


def test_spot_ignores_legacy_stop_and_keeps_profit_trailing(tmp_path):
    svc = TradingService(config(tmp_path), force_paper=True)
    svc.broker.price = lambda: 100.0
    order = svc.broker.buy_quote(100, stop_price=95, reason="auto_entry")
    svc.broker.state["model_protection"] = {
        "entry_price": order["price"],
        "trailing_enabled": True,
        "trail_activation_pct": 0.02,
        "trail_distance_pct": 0.01,
    }

    def fail(*args, **kwargs):
        raise ValueError("history unavailable")

    svc.broker.klines = fail
    svc.broker.price = lambda: 90.0
    status = svc.poll()
    assert status["account"]["position_qty"] == order["qty"]
    assert status["account"]["stop_price"] is None
    assert svc.broker.state["stop_price"] is None
    assert not status["auto_enabled"]

    svc.broker.price = lambda: 110.0
    assert svc.poll()["account"]["position_qty"] == order["qty"]
    svc.broker.price = lambda: 107.0
    status = svc.poll()
    assert status["account"]["position_qty"] == 0
    assert any(order["reason"] == "trailing_profit" for order in status["orders"])


def test_futures_margin_uses_free_margin(tmp_path):
    svc=FuturesPaperService(config(tmp_path));svc.price=lambda:100.
    svc.place_order(side='LONG',qty=80,leverage=1)
    with pytest.raises(ValueError,match='margin'):
        svc.place_order(side='SHORT',qty=40,leverage=1)


def test_model_registry_roundtrip_and_bounds(tmp_path):
    r=ModelRegistry(tmp_path)
    m=ModelSpec(id='my-model',name='自创模型',parameters={'fast':12,'slow':50,'regime':160},prompt='等待成交量确认')
    r.save(m);assert r.get(m.id)==m
    assert 'EMA(12)' in formula(m)
    assert next(c for c in r.cards() if c['id']==m.id)['fingerprint']
    with pytest.raises(ValueError):r.save(r.get('trend-v2'))
    with pytest.raises(ValueError):ModelSpec(id='bad',name='坏参数',parameters={'fast':80,'slow':40})
    with pytest.raises(ValueError):ModelSpec(id='bad',name='坏端点',kind='http',endpoint='http://169.254.169.254/latest')
    r.close()


def test_model_cannot_recurse_into_console(tmp_path):
    r=ModelRegistry(tmp_path,8790)
    m=ModelSpec(id='recursive',name='递归模型',kind='http',endpoint='http://127.0.0.1:8790/api/models/recursive/preview')
    with pytest.raises(ValueError,match='自身端口'):
        r.save(m)
    r.close()


def test_real_external_http_adapter_roundtrip(tmp_path):
    from http.server import ThreadingHTTPServer
    from examples.external_model_server import Handler
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    r=ModelRegistry(tmp_path)
    try:
        m=ModelSpec(id='http-test',name='HTTP 测试',kind='http',endpoint=f'http://127.0.0.1:{server.server_port}/predict')
        r.save(m);sig=r.evaluate(frame(),m.id,'1h','futures',allow_remote=True)
        assert sig.iloc[-1].model_error==''
        assert '适配器示例' in sig.iloc[-1].model_reason
    finally:server.shutdown();server.server_close();r.close()


@pytest.mark.parametrize('ai_mode',['filter','agent'])
def test_optional_ai_enforces_scope_schema_and_public_only(tmp_path,monkeypatch,ai_mode):
    r=ModelRegistry(tmp_path);m=ModelSpec(id='ai-test',name='AI 测试',scope='spot',ai_mode=ai_mode);r.save(m)
    df=frame();seen=[]
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-secret')
    class Response:
        ok=True
        def json(self):return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'protocol':PROTOCOL,'model_id':m.id,'bar_time':pd.Timestamp(df.iloc[-1].open_time).isoformat(),'direction':'SHORT','reason':'测试反向信号'})}}]}
    def post(url,**kw):
        seen.append(kw['json']);return Response()
    monkeypatch.setattr('btcquant.models.requests.post',post)
    sig=r.evaluate(df,m.id,'1h','spot',allow_remote=True)
    assert not sig.iloc[-1].short_entry_signal
    if ai_mode=='filter':assert not sig.iloc[-1].entry_signal
    text=json.dumps(seen)
    assert 'test-secret' not in text and 'wallet' not in text and 'position_qty' not in text
    assert 'EMA(20)' in text
    r.close()


@pytest.mark.parametrize('malformation',['timestamp','model_id','protocol','extra'])
def test_wrong_timestamp_or_unsafe_fields_rejected(tmp_path,monkeypatch,malformation):
    r=ModelRegistry(tmp_path);m=ModelSpec(id='bad-http',name='错误适配器',kind='http',endpoint='http://127.0.0.1:9001/predict');r.save(m)
    class Response:
        status_code=200;content=b'{}'
        def json(self):
            body={'protocol':PROTOCOL,'model_id':m.id,'bar_time':pd.Timestamp(frame().iloc[-1].open_time).isoformat(),'direction':'LONG'}
            if malformation=='timestamp':body['bar_time']='2020-01-01T00:00:00Z'
            elif malformation=='model_id':body['model_id']='another-model'
            elif malformation=='protocol':body['protocol']='future-incompatible-v9'
            else:body['qty']=1000000
            return body
    monkeypatch.setattr('btcquant.models.requests.post',lambda *a,**k:Response())
    s=r.evaluate(frame(),m.id,'1h','futures',allow_remote=True)
    assert s.iloc[-1].model_error and not s.iloc[-1].entry_signal
    r.close()


def test_model_endpoints_schema_compile_and_csrf(tmp_path):
    app=create_app(config(tmp_path),force_paper=True);client=TestClient(app)
    m=ModelSpec(id='api-model',name='接口模型')
    assert client.post('/api/models',json=m.model_dump()).status_code==200
    assert any(x['id']==m.id for x in client.get('/api/models').json()['models'])
    assert client.get('/api/models/schema').json()['protocol']==PROTOCOL
    assert 'EMA(20)' in client.post('/api/models/compile',json=m.model_dump()).json()['formula']
    assert client.post('/api/models',json=m.model_dump(),headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/models').status_code==200
    app.state.research.close()


def test_ai_is_nonblocking_for_protection_loop(tmp_path,monkeypatch):
    import time
    r=ModelRegistry(tmp_path);m=ModelSpec(id='slow-ai',name='慢速 AI',ai_mode='agent');r.save(m)
    gate=threading.Event();entered=threading.Event()
    original=r.evaluate
    def delayed(*args,**kwargs):
        if kwargs.get('allow_remote'):
            entered.set();gate.wait(3)
        return original(*args,**kwargs)
    monkeypatch.setattr(r,'evaluate',delayed)
    try:
        start=time.monotonic();sig=r.evaluate_nonblocking(frame(),m.id,'1h','futures',allow_remote=True)
        assert time.monotonic()-start<1.5
        assert not sig.iloc[-1].entry_signal
        assert '等待' in sig.iloc[-1].model_error
        assert entered.wait(1)
    finally:gate.set();r.close()
