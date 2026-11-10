import importlib
import math
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sample(instrument='EURUSD'):
    now=int(time.time())
    base=1.10 if instrument=='EURUSD' else 4120.0
    step=.00025 if instrument=='EURUSD' else .6
    digits=5 if instrument=='EURUSD' else 2
    frames={}
    for tf,sec in (('D1',86400),('H4',14400),('H1',3600)):
        candles=[]
        for i in range(70):
            px=base + step*i + math.sin(i/5)*step*2
            candles.append({'time':time.strftime('%Y.%m.%d %H:%M:%S',time.gmtime(now-(70-i)*sec)),
                            'open':px, 'high':px+step*2, 'low':px-step*2, 'close':px+step/2})
        frames[tf]=candles
    return {'instrument':instrument,'broker_symbol':instrument+'m',
            'bid':base+step*70,'ask':base+step*71, 'digits':digits,
            'sent_at_utc':now, 'tick_time_broker':time.strftime('%Y.%m.%d %H:%M:%S',time.gmtime(now)),
            'tick_age_seconds':2,'frames':frames}


def bridge_client(monkeypatch,tmp_path):
    monkeypatch.setenv('STORAGE_DIR',str(tmp_path))
    monkeypatch.setenv('MT4_BRIDGE_TOKEN','S'*45)
    import mt4_feed
    app=FastAPI();app.include_router(mt4_feed.router)
    return TestClient(app)


def test_bridge_auth_and_valid(monkeypatch,tmp_path):
    client=bridge_client(monkeypatch,tmp_path)
    body=sample()
    assert client.post('/api/mt4/ingest',json=body).status_code==401
    assert client.post('/api/mt4/ingest',json=body,headers={'X-MT4-Token':'wrong'}).status_code==401
    r=client.post('/api/mt4/ingest',json=body,headers={'X-MT4-Token':'S'*45})
    assert r.status_code==200,r.text
    from mt4_feed import fetch_mt4_quote,fetch_mt4_snapshot
    q=fetch_mt4_quote('EURUSD')
    assert q['bid']==body['bid'] and q['ask']==body['ask']
    assert q['provider_symbol']=='EURUSDm'
    snap=fetch_mt4_snapshot('EURUSD')
    assert len(snap['frames']['H1'])==70
    assert len(snap['frames']['D1'])==70
    assert snap['quote']['source'].startswith('MetaTrader 4')
    dupe=client.post('/api/mt4/ingest',json=body,headers={'X-MT4-Token':'S'*45})
    assert dupe.status_code==409


def test_bridge_invalid_and_stale(monkeypatch,tmp_path):
    client=bridge_client(monkeypatch,tmp_path)
    headers={'X-MT4-Token':'S'*45}
    bad=sample();bad['frames']['H1'][3]['high']=0
    assert client.post('/api/mt4/ingest',json=bad,headers=headers).status_code==422
    bad=sample();bad['instrument']='BTCUSD'
    assert client.post('/api/mt4/ingest',json=bad,headers=headers).status_code==422
    bad=sample();bad['tick_age_seconds']=1000
    assert client.post('/api/mt4/ingest',json=bad,headers=headers).status_code==422
    bad=sample();bad['sent_at_utc']-=400
    assert client.post('/api/mt4/ingest',json=bad,headers=headers).status_code==422
    good=sample()
    assert client.post('/api/mt4/ingest',json=good,headers=headers).status_code==200
    monkeypatch.setenv('MT4_FEED_TTL_SECONDS','1')
    import mt4_feed
    original=mt4_feed.time.time
    monkeypatch.setattr(mt4_feed.time,'time',lambda:original()+200)
    with pytest.raises(mt4_feed.MarketDataError):
        mt4_feed.fetch_mt4_snapshot('EURUSD')


def test_end_to_end_railway_app(monkeypatch,tmp_path):
    monkeypatch.setenv('STORAGE_DIR',str(tmp_path))
    monkeypatch.setenv('APP_SECRET','secret-for-tests-very-long-will-not-be-used')
    monkeypatch.setenv('SETUP_TOKEN','T'*45)
    monkeypatch.setenv('MT4_BRIDGE_TOKEN','S'*45)
    monkeypatch.setenv('LIVE_PROVIDER','MT4')
    monkeypatch.setenv('COOKIE_SECURE','0')
    monkeypatch.delenv('RAILWAY_ENVIRONMENT',raising=False)
    sys.modules.pop('app',None)
    service=importlib.import_module('app')
    client=TestClient(service.app)
    assert client.get('/health').json()['live_provider']=='MT4'
    assert client.get('/api/market/EURUSD').status_code==401
    assert client.post('/api/setup',json={'username':'newadmin','password':'Mypassword-1234567','setup_token':'T'*45}).status_code==200
    assert client.post('/api/login',json={'username':'newadmin','password':'Mypassword-1234567'}).status_code==200
    assert client.get('/api/market/EURUSD').status_code==503
    assert client.post('/api/mt4/ingest',json=sample(),headers={'X-MT4-Token':'S'*45}).status_code==200
    assert client.get('/api/market/EURUSD').status_code==200
    r=client.post('/api/reports/live',json={'instrument':'EURUSD'})
    assert r.status_code==200,r.text
    rid=r.json()['id']
    result=client.post(f'/api/reports/{rid}/generate')
    assert result.status_code==200,result.text
    data=result.json()['report']
    assert data['market']['source'].startswith('MetaTrader 4')
    assert data['market']['bid'] is not None
    assert data['decision'] in ('BUY','SELL')
    assert client.get(f'/api/reports/{rid}/docx').status_code==200
    assert client.get(f'/api/reports/{rid}/asset/annotated').status_code==200
    assert client.get(f'/api/reports/{rid}/asset/infographic').status_code==200
