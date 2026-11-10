"""Twelve Data indicative market feed. Fails closed on unavailable or stale data."""
import json
import math
import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SYMBOLS = {
    'XAUUSD': 'XAU/USD',
    'XAGUSD': 'XAG/USD',
    'EURUSD': 'EUR/USD',
    'GBPUSD': 'GBP/USD',
    'USDJPY': 'USD/JPY',
    # Spot WTI as listed in Twelve Data's commodities catalogue; broker CFD may differ.
    'USOIL': 'WTI/USD',
}

class MarketDataError(ValueError):
    pass


def mapping(instrument):
    if instrument not in SYMBOLS:
        raise MarketDataError('Instrumen tidak didukung')
    value = os.getenv('LIVE_SYMBOL_' + instrument, SYMBOLS[instrument])
    if not value:
        raise MarketDataError(f'Simbol {instrument} belum dikonfigurasi')
    return value.strip()


def _request(endpoint, params):
    key = os.getenv('TWELVEDATA_API_KEY')
    if not key:
        raise MarketDataError('TWELVEDATA_API_KEY belum dikonfigurasi di Railway')
    query = urlencode({**params, 'apikey': key, 'format': 'JSON'})
    req = Request('https://api.twelvedata.com/' + endpoint + '?' + query,
                  headers={'Accept': 'application/json', 'User-Agent': 'GoldAnalystPro/2'})
    try:
        with urlopen(req, timeout=18) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise MarketDataError(f'HTTP {exc.code} dari penyedia data') from exc
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise MarketDataError('Penyedia market data tidak merespons dengan benar') from exc
    if not isinstance(data, dict) or data.get('status') == 'error' or 'code' in data and 'values' not in data and 'close' not in data:
        reason = str(data.get('message', 'data tidak tersedia')) if isinstance(data,dict) else 'data tidak tersedia'
        raise MarketDataError('Market data: ' + reason[:230])
    return data


def as_float(value):
    if isinstance(value,bool): raise MarketDataError('Nilai harga tidak valid')
    try:
        x=float(value)
    except (TypeError,ValueError) as exc:
        raise MarketDataError('Nilai harga kosong/tidak valid') from exc
    if not math.isfinite(x) or x<=0:
        raise MarketDataError('Nilai harga tidak masuk akal')
    return x


def parse_quote(data, instrument):
    # /quote usually reports last price in close. It is an indicative provider quote,
    # not the broker's bid/ask or executable price.
    px=as_float(data.get('close') or data.get('price'))
    # Timestamp is from the provider; NEVER substitute the HTTP retrieval time.
    dt=None
    for stamp in (data.get('last_quote_at'),data.get('last_update_at'),data.get('timestamp')):
        if stamp is None: continue
        try:
            if isinstance(stamp,(int,float)) or str(stamp).isdigit():
                candidate=datetime.fromtimestamp(int(stamp),tz=timezone.utc)
            else:
                candidate=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
                if candidate.tzinfo is None: continue
                candidate=candidate.astimezone(timezone.utc)
            dt=candidate
            break
        except (ValueError,OverflowError,TypeError):
            continue
    if dt is None:
        raise MarketDataError('Provider tidak memberikan timestamp harga berzona waktu yang dapat diverifikasi')
    seconds=(datetime.now(timezone.utc)-dt).total_seconds()
    allowed=int(os.getenv('MAX_QUOTE_AGE_SECONDS','300'))
    if seconds < -300 or seconds > allowed:
        raise MarketDataError(f'Harga {instrument} sudah kedaluwarsa ({int(seconds/60)} menit); tidak membuat sinyal.')
    return {'price': px, 'asof': dt.isoformat(), 'age_seconds': max(0,int(seconds)),
            'source': 'Twelve Data', 'price_type': 'indicative quote / provider close; bukan broker bid-ask'}


def parse_bars(data, interval):
    raw=data.get('values')
    if not isinstance(raw,list) or len(raw)<35:
        raise MarketDataError(f'Candle {interval} tidak cukup (minimal 35) atau tidak termasuk paket API')
    bars=[]
    for item in raw:
        if not isinstance(item,dict): continue
        try:
            o,h,l,c=(as_float(item.get(k)) for k in ('open','high','low','close'))
        except MarketDataError:
            continue
        if not(l<=o<=h and l<=c<=h):
            raise MarketDataError('OHLC provider tidak konsisten')
        bars.append({'time':str(item.get('datetime') or ''), 'open':o,'high':h,'low':l,'close':c})
    if len(bars)<35:
        raise MarketDataError(f'Data {interval} tidak cukup')
    if interval in ('1h','4h'):
        latest=bars[0].get('time','')  # API order desc
        try:
            candle_time=datetime.strptime(latest,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                candle_time=datetime.strptime(latest,'%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise MarketDataError('Timestamp candle intraday tidak valid') from exc
        age=(datetime.now(timezone.utc)-candle_time).total_seconds()
        limit=3*3600 if interval=='1h' else 9*3600
        if age<(-10*60) or age>limit:
            raise MarketDataError(f'Candle {interval} kedaluwarsa; tidak membuat sinyal.')
    return list(reversed(bars))


def fetch_quote(instrument):
    symbol=mapping(instrument)
    result=parse_quote(_request('quote', {'symbol':symbol,'timezone':'UTC'}),instrument)
    result.update({'instrument':instrument,'provider_symbol':symbol})
    return result


def fetch_snapshot(instrument):
    symbol=mapping(instrument)
    quote=fetch_quote(instrument)
    frames={}
    for name, interval in [('D1','1day'),('H4','4h'),('H1','1h')]:
        data=_request('time_series', {'symbol':symbol,'interval':interval,
                                       'outputsize':100,'timezone':'UTC','order':'desc'})
        returned=str((data.get('meta') or {}).get('symbol') or '')
        # Reject silently substituted symbols; provider aliases may differ, but symbol must match.
        if returned and returned.replace('/','').upper()!=symbol.replace('/','').upper():
            raise MarketDataError(f'Provider mengembalikan simbol lain ({returned}) untuk {symbol}')
        frames[name]=parse_bars(data,interval)
    return {'instrument':instrument,'quote':quote,'frames':frames,
            'retrieved_at':datetime.now(timezone.utc).isoformat(),
            'provider':'Twelve Data (indicative; broker prices may differ)'}
