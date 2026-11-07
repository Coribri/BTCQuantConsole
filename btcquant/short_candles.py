"""True 30-second candles aggregated from Binance's native 1-second bars.

Only complete UTC buckets containing every second are emitted. No fabricated
half-minute prices, forward fills, or splitting of one-minute OHLC data.
"""
from __future__ import annotations
import threading
import time
import pandas as pd

_locks={}
_frames={}
_synced={}
_lock=threading.RLock()


def aggregate_30s(frame):
    if frame.empty:return frame
    frame=frame.sort_values('open_time').drop_duplicates('open_time').copy()
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.open_time,utc=True))
    groups=frame.resample('30s',origin='epoch')
    result=groups.agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),
                      volume=('volume','sum'),quote_volume=('quote_volume','sum'),trades=('trades','sum'),
                      taker_buy_base=('taker_buy_base','sum'),taker_buy_quote=('taker_buy_quote','sum'),count=('close','count'))
    result=result[result['count']==30].drop(columns='count')
    result['open_time']=result.index
    result['close_time']=result.index+pd.Timedelta(seconds=30)-pd.Timedelta(milliseconds=1)
    result=result[result.close_time<=pd.Timestamp.now(tz='UTC')]
    result['open_time_ms']=result.open_time.map(lambda t:int(t.timestamp()*1000))
    result['ignore']=0
    return result.reset_index(drop=True)


def fetch_30s(cfg,market,wanted):
    from .data import _request_json,_rows_to_frame,_clean_klines,BinanceDataError
    key=(str(cfg.dir),market.symbol,tuple(cfg.base_urls))
    with _lock:
        guard=_locks.setdefault(key,threading.RLock())
    with guard:
        path=cfg.dir/f'{market.symbol}_30s_synthetic.csv'
        cached=_frames.get(key)
        if cached is None and path.exists():
            try:
                cached=pd.read_csv(path,parse_dates=['open_time','close_time'])
                cached=_clean_klines(cached,'30s')
            except (ValueError,OSError):cached=None
        if cached is not None and len(cached)>=wanted and time.monotonic()-_synced.get(key,0)<3:
            return cached.tail(wanted).copy().reset_index(drop=True)
        rows=[]
        if cached is not None and len(cached) and time.time()*1000-pd.Timestamp(cached.iloc[-1].close_time).timestamp()*1000>wanted*30000:
            cached=None
        if cached is not None and len(cached)>=wanted:
            cursor=int(pd.Timestamp(cached.iloc[-1].close_time).timestamp()*1000)+1
            now=int(time.time()*1000)
            while cursor<now:
                batch=_request_json(cfg,'/api/v3/klines',{'symbol':market.symbol,'interval':'1s','startTime':cursor,'limit':1000})
                if not isinstance(batch,list) or not batch:break
                rows.extend(batch);cursor=int(batch[-1][0])+1000
                if len(batch)<1000:break
        else:
            end=None
            while len(rows)<wanted*30+60:
                params={'symbol':market.symbol,'interval':'1s','limit':min(1000,wanted*30+60-len(rows))}
                if end is not None:params['endTime']=end
                batch=_request_json(cfg,'/api/v3/klines',params)
                if not isinstance(batch,list) or not batch:break
                rows=batch+rows;end=int(batch[0][0])-1
                if len(batch)<params['limit']:break
        new=aggregate_30s(_clean_klines(_rows_to_frame(rows),'1s')) if rows else pd.DataFrame()
        combined=pd.concat([cached,new],ignore_index=True) if cached is not None else new
        if combined.empty:raise BinanceDataError('尚未取得完整 30 秒数据，未生成替代或拆分 K 线')
        combined=_clean_klines(combined,'30s').tail(5000).reset_index(drop=True)
        _frames[key]=combined;_synced[key]=time.monotonic()
        if not new.empty:
            cfg.dir.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.tmp');combined.to_csv(temp,index=False);temp.replace(path)
        return combined.tail(wanted).copy().reset_index(drop=True)
