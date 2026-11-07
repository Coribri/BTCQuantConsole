import pandas as pd
import pytest
from btcquant.short_candles import aggregate_30s,fetch_30s
from btcquant.config import DataConfig,MarketConfig


def seconds_frame(n=90):
    t=pd.date_range('2026-01-01',periods=n,freq='s',tz='UTC')
    return pd.DataFrame({'open_time':t,'close_time':t+pd.Timedelta(milliseconds=999),
        'open':range(100,100+n),'high':range(102,102+n),'low':range(99,99+n),'close':range(101,101+n),
        'volume':1.,'quote_volume':2.,'trades':1,'taker_buy_base':.4,'taker_buy_quote':.8})


def test_30s_is_real_ohlc_aggregation():
    out=aggregate_30s(seconds_frame())
    assert len(out)==3
    first=out.iloc[0]
    assert (first.open,first.high,first.low,first.close)==(100,131,99,130)
    assert first.volume==30 and first.trades==30
    assert first.quote_volume==60 and first.taker_buy_base==pytest.approx(12)
    assert first.close_time-first.open_time==pd.Timedelta(seconds=30)-pd.Timedelta(milliseconds=1)


def test_missing_seconds_and_partial_buckets_are_not_fabricated():
    out=aggregate_30s(seconds_frame(85).drop(35))
    assert len(out)==1
    assert out.iloc[0].open_time==pd.Timestamp('2026-01-01',tz='UTC')


def test_unclosed_30s_bucket_is_not_emitted():
    f=seconds_frame(30)
    latest=pd.Timestamp.now(tz='UTC').floor('30s')
    f.open_time=pd.date_range(latest,periods=30,freq='s')
    assert aggregate_30s(f).empty
