import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from btcquant.config import load_config
from btcquant.research import (EventStore, EventInput, Extraction, event_features,
                               make_features, run_experiment, simulate)
from btcquant.web import create_app


def candles(n=1400):
    rng = np.random.default_rng(193)
    times = pd.date_range("2022-01-01", periods=n, freq="4h", tz="UTC")
    price = 100*np.exp(np.cumsum(rng.normal(.0004, .009, n)))
    return pd.DataFrame({"open_time": times, "close_time": times+pd.Timedelta(hours=4)-pd.Timedelta(milliseconds=1),
                         "open": price, "high": price*1.012, "low": price*.988,
                         "close": price*(1+rng.normal(0,.002,n)), "volume": rng.uniform(100,300,n)})


def event():
    return {"published_at":"2022-01-01T00:00:00+00:00", "received_at":"2022-01-01T01:00:00+00:00",
            "analyzed_at":"2022-01-01T02:00:00+00:00", "analysis":{
                "assets":["BTC"], "sentiment":-1., "confidence":.9,"risk":"high","horizon_hours":4}}


def test_event_cannot_appear_before_receipt_or_analysis_and_expires():
    times = pd.date_range("2022-01-01", periods=7, freq="h", tz="UTC")
    f = event_features(times, [event()], "BTCUSDT")
    assert list(f.event_count)==[0,0,1,1,1,0,0]
    assert f.event_score.iloc[2] < 0
    assert event_features(times,[event()],"ETHUSDT").event_count.sum()==0
    old=event();old["analyzed_at"]="2022-02-01T00:00:00+00:00"
    assert event_features(times,[old],"BTCUSDT").event_count.sum()==0


def test_dedup_preserves_first_seen_and_analysis(tmp_path):
    store=EventStore(tmp_path/"events.sqlite3")
    item=EventInput(title="Public security incident",url="https://example.com/event",published_at=datetime(2022,1,1,tzinfo=timezone.utc))
    assert store.add(item)
    first=store.list()[0]
    assert not store.add(item)
    assert store.list()[0]["received_at"]==first["received_at"]
    a=Extraction(category="security",assets=["BTC"],sentiment=-.5,confidence=.8,risk="high",horizon_hours=4,summary="公开测试事件")
    store.extract(first["id"],a,"test-model")
    seen=store.list()[0]
    store.extract(first["id"],a.model_copy(update={"sentiment":1}),"later-model")
    assert store.list()[0]["analysis"]==seen["analysis"]
    assert store.list()[0]["model"]=="test-model"


def test_features_are_causal():
    df=candles();x,_=make_features(df)
    changed=df.copy();changed.loc[900:,"close"]*=2
    y,_=make_features(changed)
    pd.testing.assert_frame_equal(x.iloc[:900],y.iloc[:900])


def test_walk_forward_purge_and_no_news_claim():
    r=run_experiment(candles(),[],"BTCUSDT","4h")
    for f in r["folds"]:
        # Last training label ends before the first validation candle.
        assert pd.Timestamp(f["train_end"])+pd.Timedelta(hours=4*r["horizon_bars"]) < pd.Timestamp(f["test_start"])
    assert r["event_covered_bars"]==0
    assert r["comparisons"][1]["return"]==r["comparisons"][2]["return"]
    assert any("覆盖不足" in n for n in r["notes"])
    json.dumps(r,allow_nan=False)


def test_future_prices_do_not_change_earlier_oos_predictions_equity():
    df=candles();r=run_experiment(df,[],"BTCUSDT","4h")
    changed=df.copy();changed.loc[1250:,["open","close","high","low"]]*=1.5
    updated=run_experiment(changed,[],"BTCUSDT","4h")
    cutoff=pd.Timestamp(df.iloc[1249].close_time)
    for a,b in zip(r["comparisons"],updated["comparisons"]):
        pa=[v for v in a["curve"] if pd.Timestamp(v["time"])<cutoff]
        pb=[v for v in b["curve"] if pd.Timestamp(v["time"])<cutoff]
        assert pa==pb


def test_next_open_costs_and_intratrade_drawdown():
    df=candles(10);df[["open","close"]]=100.
    df.loc[2,"close"]=80.
    signals=np.ones(10)
    free=simulate(df,signals,0,8,4,0,0)
    paid=simulate(df,signals,0,8,4,10,2)
    assert free["return"]==0
    assert paid["return"]<0
    assert free["max_drawdown"]<=-.2+1e-8
    df.loc[0,"close"]=10000.
    assert simulate(df,signals,0,8,4,0,0)["return"]==free["return"]


def test_rejects_gaps_and_short_data():
    with pytest.raises(ValueError,match="1000"):
        run_experiment(candles(900),[],"BTCUSDT","4h")
    with pytest.raises(ValueError,match="缺口"):
        run_experiment(candles().drop(800),[],"BTCUSDT","4h")


def test_research_routes_do_not_start_brokers_and_hide_keys(tmp_path,monkeypatch):
    (tmp_path/"config.toml").write_text("",encoding="utf-8")
    app=create_app(load_config(tmp_path/"config.toml"),force_paper=True)
    client=TestClient(app)  # Do not enter lifespan: trading services are unrelated.
    monkeypatch.setenv("DEEPSEEK_API_KEY","secret-test-key")
    response=client.get("/api/research/status")
    assert response.status_code==200
    assert "secret-test-key" not in response.text
    assert response.json()["deepseek"]["configured"]
    assert app.state.spot_services=={}
    assert client.get("/research").status_code==200
    assert client.post("/api/research/events/analyze",headers={"Origin":"https://malicious.example"}).status_code==403
    monkeypatch.setattr(app.state.research, "start", lambda **settings: {"interval": settings["interval"]})
    for interval in ("1m", "15m", "2h", "12h", "1d"):
        started = client.post("/api/research/run", json={"interval": interval})
        assert started.status_code == 202
        assert started.json()["interval"] == interval
    assert client.post("/api/research/run",json={"interval":"30s"}).status_code==422
    assert app.state.spot_services == {}
    app.state.research.close()


@pytest.mark.parametrize("bad",["", " ", '{"category":"made_up"}', '{"category":"security","sentiment":NaN}'])
def test_invalid_llm_output_is_not_used(tmp_path,monkeypatch,bad):
    from btcquant.research import ResearchService
    (tmp_path/"config.toml").write_text("",encoding="utf-8")
    service=ResearchService(load_config(tmp_path/"config.toml"))
    service.store.add(EventInput(title="Public security incident",url="https://example.com/event",published_at=datetime(2022,1,1,tzinfo=timezone.utc)))
    monkeypatch.setenv("DEEPSEEK_API_KEY","test-private-key")
    class Response:
        ok=True
        def json(self):
            return {"choices":[{"finish_reason":"stop","message":{"content":bad}}]}
    monkeypatch.setattr("btcquant.research.requests.post",lambda *a,**k:Response())
    service._events("analyze")
    assert service.store.list()[0]["analysis"] is None
    assert "test-private-key" not in service.status()["event_message"]
    assert not service.event_busy
    service.close()


def test_deepseek_uses_only_public_event_text_and_validates_output(tmp_path,monkeypatch):
    from btcquant.research import ResearchService
    (tmp_path/"config.toml").write_text("",encoding="utf-8")
    service=ResearchService(load_config(tmp_path/"config.toml"))
    service.store.add(EventInput(title="Public security incident",text="Public facts",url="https://example.com/event",published_at=datetime(2022,1,1,tzinfo=timezone.utc)))
    monkeypatch.setenv("DEEPSEEK_API_KEY","test-private-key")
    def post(url,**kwargs):
        assert url=="https://api.deepseek.com/chat/completions"
        sent=json.loads(kwargs["json"]["messages"][1]["content"])
        assert set(sent)=={"title","text","published_at","source"}
        class Response:
            ok=True
            def json(self):
                return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({
                    "category":"security","assets":["BTC"],"sentiment":-.5,"confidence":.8,
                    "risk":"high","horizon_hours":4,"summary":"公开安全事件"})}}]}
        return Response()
    monkeypatch.setattr("btcquant.research.requests.post",post)
    service._events("analyze")
    assert service.store.list()[0]["analysis"]["risk"]=="high"
    assert service.store.list()[0]["analyzed_at"]
    service.close()


def test_settings_preserve_other_credentials_and_do_not_echo_key(tmp_path,monkeypatch):
    (tmp_path/"config.toml").write_text("",encoding="utf-8")
    (tmp_path/".env").write_text("OTHER_KEY=preserve-me\n",encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY",raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL",raising=False)
    app=create_app(load_config(tmp_path/"config.toml"),force_paper=True)
    client=TestClient(app)
    response=client.post("/api/research/settings",json={"api_key":"test-key-123","model":"deepseek-flash"})
    assert response.status_code==200
    assert "test-key-123" not in response.text
    assert "OTHER_KEY=preserve-me" in (tmp_path/".env").read_text()
    assert client.post("/api/research/settings",json={"api_key":"invalid\nkey"}).status_code==400
    app.state.research.close()
