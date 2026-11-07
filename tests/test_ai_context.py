import json
from datetime import timedelta
import numpy as np
import pandas as pd
import pytest
from btcquant.ai_context import build_context
from btcquant.models import ModelRegistry, ModelSpec, PROTOCOL
from btcquant.research import EventInput, EventStore, Extraction, make_features, VERSION
from btcquant.strategies import strategy_snapshot


def candles():
    times=pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("h")-pd.Timedelta(hours=1),periods=500,freq="h")
    c=100*np.exp(np.linspace(0,.3,len(times)))
    df=pd.DataFrame(dict(open_time=times,open=c,high=c*1.005,low=c*.995,close=c,volume=100.))
    df.attrs['symbol']='BTCUSDT'
    return df


def event(root, assets=None, hours=4, old=False):
    store=EventStore(root/'runtime'/'research_events.sqlite3')
    now=pd.Timestamp.now(tz='UTC')
    store.add(EventInput(title='Public macro policy announcement',url='https://example.com/news',published_at=(now-pd.Timedelta(hours=100 if old else 1)).to_pydatetime()))
    item=store.list()[0]
    store.extract(item['id'],Extraction(category='macro',assets=assets or ['BTC'],sentiment=-.4,confidence=.8,risk='high',horizon_hours=hours,summary='公开事件摘要'),'extract-model')
    return store,item


def artifact(root,df, **changes):
    x,_=make_features(df)
    report=dict(symbol='BTCUSDT',interval='1h',version=VERSION,generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        model_artifact=dict(kind='logistic-research-v1',features=list(x.columns),mean=[0.]*len(x.columns),scale=[1.]*len(x.columns),weights=[0.]*(len(x.columns)+1),data_end=(df.iloc[-1].open_time+pd.Timedelta(hours=1)-pd.Timedelta(milliseconds=1)).isoformat()),
        oos_rows=500,brier=.24,comparisons=[dict(name='ML 过滤',trades=40,return_=.02,max_drawdown=-.03)])
    report['comparisons'][0]['return']=report['comparisons'][0].pop('return_')
    report.update(changes)
    folder=root/'reports'/'research';folder.mkdir(parents=True,exist_ok=True)
    (folder/'BTCUSDT_1h_123_result.json').write_text(json.dumps(report),encoding='utf-8')
    return report


def test_disabled_sources_do_not_read_or_create_files(tmp_path):
    ctx,guard=build_context(tmp_path,candles(),'BTCUSDT','1h')
    assert guard is None and ctx['events']['status']==ctx['ml']['status']=='off'
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('flags',[dict(events=True),dict(ml=True),dict(events=True,ml=True)])
def test_selected_missing_data_fails_closed(tmp_path,flags):
    ctx,guard=build_context(tmp_path,candles(),'BTCUSDT','1h',**flags)
    assert guard


@pytest.mark.parametrize('variant',['old','unrelated','future_analysis'])
def test_events_respect_scope_expiry_and_actual_availability(tmp_path,variant):
    store,item=event(tmp_path,assets=['ETH'] if variant=='unrelated' else None,old=variant=='old')
    if variant=='future_analysis':
        with store.connect() as db:
            db.execute('UPDATE events SET analyzed_at=?',((pd.Timestamp.now(tz='UTC')+pd.Timedelta(hours=1)).isoformat(),))
    ctx,guard=build_context(tmp_path,candles(),'BTCUSDT','1h',events=True)
    assert guard and not ctx['events']['items']


def test_valid_event_only_sends_public_bounded_fields(tmp_path):
    event(tmp_path)
    ctx,guard=build_context(tmp_path,candles(),'BTCUSDT','1h',events=True)
    assert not guard and ctx['events']['status']=='ready'
    assert 'text' not in ctx['events']['items'][0]
    assert ctx['events']['items'][0]['url']=='https://example.com/news'


def test_ml_runs_saved_model_on_current_features(tmp_path):
    df=candles();artifact(tmp_path,df)
    ctx,guard=build_context(tmp_path,df,'BTCUSDT','1h',ml=True)
    assert not guard and ctx['ml']['up_score']==.5
    assert ctx['ml']['bar_time']==str(df.iloc[-1].open_time)
    assert '不是' in ctx['ml']['validation']


@pytest.mark.parametrize('variant',['expired','wrong_symbol','future_model','future_data','not_validated','legacy','bad_dimensions'])
def test_invalid_ml_not_passed_to_ai(tmp_path,variant):
    df=candles();r=artifact(tmp_path,df)
    if variant=='expired':r['generated_at']=(pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=8)).isoformat()
    if variant=='wrong_symbol':r['symbol']='ETHUSDT'
    if variant=='future_model':r['generated_at']=(pd.Timestamp.now(tz='UTC')+pd.Timedelta(days=1)).isoformat()
    if variant=='future_data':r['model_artifact']['data_end']=(pd.Timestamp.now(tz='UTC')+pd.Timedelta(days=1)).isoformat()
    if variant=='not_validated':r['comparisons'][0]['trades']=2
    if variant=='legacy':r.pop('model_artifact')
    if variant=='bad_dimensions':r['model_artifact']['weights']=[0.]
    (tmp_path/'reports/research/BTCUSDT_1h_123_result.json').write_text(json.dumps(r),encoding='utf-8')
    ctx,guard=build_context(tmp_path,df,'BTCUSDT','1h',ml=True)
    assert guard and ctx['ml']['status']=='unavailable' and 'up_score' not in ctx['ml']


def test_selected_context_without_ai_rejected():
    with pytest.raises(ValueError):ModelSpec(id='ctx-test',name='上下文',ai_events=True)


def test_ai_receives_selected_context_and_reports_provenance(tmp_path,monkeypatch):
    df=candles();event(tmp_path);artifact(tmp_path,df)
    registry=ModelRegistry(tmp_path);spec=ModelSpec(id='ctx-test',name='上下文模型',ai_mode='filter',ai_events=True,ai_ml=True);registry.save(spec)
    monkeypatch.setenv('DEEPSEEK_API_KEY','not-public-secret')
    seen=[]
    class Response:
        ok=True
        def json(self):return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(protocol=PROTOCOL,model_id=spec.id,bar_time=pd.Timestamp(df.iloc[-1].open_time).isoformat(),direction='WAIT',reason='事件风险较高，ML研究评分中性，等待。'))}}]}
    def post(url,**kw):seen.append(kw['json']);return Response()
    monkeypatch.setattr('btcquant.models.requests.post',post)
    sig=registry.evaluate(df,spec.id,'1h','futures',allow_remote=True)
    snap=strategy_snapshot(sig,'trend_v2')
    assert snap['ai_report']['status']=='ready' and snap['ai_report']['direction']=='WAIT'
    sent=json.loads(seen[0]['messages'][1]['content'].split('\n',1)[1])
    assert sent['optional_context']['events']['items'] and sent['optional_context']['ml']['up_score']==.5
    assert 'not-public-secret' not in json.dumps(seen) and 'wallet' not in json.dumps(seen)
    registry.close()


def test_missing_selected_context_never_calls_ai(tmp_path,monkeypatch):
    registry=ModelRegistry(tmp_path);m=ModelSpec(id='ctx-test',name='事件模型',ai_mode='filter',ai_events=True);registry.save(m)
    monkeypatch.setattr('btcquant.models.requests.post',lambda *a,**k:pytest.fail('must not call AI'))
    sig=registry.evaluate(candles(),m.id,'1h','futures',allow_remote=True)
    assert sig.iloc[-1].model_error and not sig.iloc[-1].entry_signal
    assert strategy_snapshot(sig,'trend_v2')['ai_report']['status']=='blocked'
    registry.close()
