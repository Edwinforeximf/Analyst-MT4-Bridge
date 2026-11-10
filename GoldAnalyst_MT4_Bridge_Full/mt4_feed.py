"""Read-only authenticated MT4 market-data bridge, stored separately from reports."""
import hmac
import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from market_feed import MarketDataError

router = APIRouter()
INSTRUMENTS = ('XAUUSD', 'XAGUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'USOIL')
SYMBOL_PATTERN = re.compile(r'^[A-Za-z0-9_.#-]{1,36}$')
BAR_TIME_PATTERN = re.compile(r'^\d{4}[.-]\d{2}[.-]\d{2} \d{2}:\d{2}:\d{2}$')
MAX_BODY = 500_000


def _db_file():
    folder = Path(os.environ.get('STORAGE_DIR', str(Path(__file__).resolve().parent / 'storage')))
    folder.mkdir(parents=True, exist_ok=True)
    return folder / 'mt4_feed.db'


def _connection():
    con = sqlite3.connect(_db_file(), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('''CREATE TABLE IF NOT EXISTS mt4_latest (
        instrument TEXT PRIMARY KEY, broker_symbol TEXT NOT NULL,
        sent_epoch INTEGER NOT NULL, received_epoch INTEGER NOT NULL,
        json_payload TEXT NOT NULL
    )''')
    return con


def _positive(x, name):
    if isinstance(x, bool):
        raise ValueError(f'{name} tidak valid')
    try:
        result = float(x)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{name} harus angka') from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f'{name} di luar batas')
    return result


def _as_int(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f'{name} tidak valid')
    return value


def validate_payload(p):
    if not isinstance(p, dict):
        raise ValueError('Payload harus JSON object')
    instrument = p.get('instrument')
    if instrument not in INSTRUMENTS:
        raise ValueError('Instrumen tidak diizinkan')
    symbol = p.get('broker_symbol')
    if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError('Simbol MT4 broker tidak valid')
    bid, ask = _positive(p.get('bid'), 'Bid'), _positive(p.get('ask'), 'Ask')
    if ask < bid:
        raise ValueError('Ask lebih rendah daripada Bid')
    digits = _as_int(p.get('digits'), 'digits', 0, 8)
    sent = _as_int(p.get('sent_at_utc'), 'sent_at_utc', 1500000000, 9999999999)
    age = _as_int(p.get('tick_age_seconds'), 'tick_age_seconds', 0, 864000)
    # Bound MT4 decimal and split to prevent cross-symbol/price mistakes.
    point = 10 ** (-digits)
    if ask - bid > max(point * 3000, bid * .08):
        raise ValueError('Spread abnormal; periksa simbol dan spesifikasi broker')
    frames = p.get('frames')
    if not isinstance(frames, dict) or set(frames) != {'D1', 'H4', 'H1'}:
        raise ValueError('Wajib candle D1, H4, H1')
    clean = {}
    for tf in ('D1', 'H4', 'H1'):
        bars = frames[tf]
        if not isinstance(bars, list) or not (45 <= len(bars) <= 120):
            raise ValueError(f'Candle {tf} wajib 45–120 batang')
        out = []
        last_stamp = ''
        for b in bars:
            if not isinstance(b, dict):
                raise ValueError(f'Format candle {tf} salah')
            stamp = b.get('time')
            if not isinstance(stamp, str) or not BAR_TIME_PATTERN.fullmatch(stamp):
                raise ValueError(f'Timestamp candle {tf} tidak valid')
            norm_stamp = stamp.replace('.', '-')
            if norm_stamp <= last_stamp:
                raise ValueError(f'Urutan waktu candle {tf} tidak naik')
            last_stamp = norm_stamp
            o = _positive(b.get('open'), 'open')
            h = _positive(b.get('high'), 'high')
            l = _positive(b.get('low'), 'low')
            c = _positive(b.get('close'), 'close')
            tolerance = max(h, 1) * 1e-11
            if l > min(o, c) + tolerance or h < max(o, c) - tolerance or h < l:
                raise ValueError(f'OHLC {tf} tidak konsisten')
            out.append({'time': norm_stamp, 'open': o, 'high': h, 'low': l, 'close': c})
        clean[tf] = out
    tick_server = p.get('tick_time_broker')
    if not isinstance(tick_server, str) or not BAR_TIME_PATTERN.fullmatch(tick_server):
        raise ValueError('Tick time broker tidak valid')
    return {'instrument': instrument, 'broker_symbol': symbol, 'bid': bid,
            'ask': ask, 'digits': digits, 'sent_at_utc': sent,
            'tick_age_seconds': age, 'tick_time_broker': tick_server,
            'frames': clean}


@router.post('/api/mt4/ingest')
async def ingest(request: Request):
    token = os.environ.get('MT4_BRIDGE_TOKEN', '')
    if len(token) < 32:
        raise HTTPException(503, 'Bridge MT4 belum dikonfigurasi')
    incoming = request.headers.get('x-mt4-token', '')
    if not hmac.compare_digest(token, incoming):
        raise HTTPException(401, 'Autentikasi MT4 gagal')
    body = await request.body()
    if not body or len(body) > MAX_BODY:
        raise HTTPException(413, 'Payload MT4 terlalu besar/kosong')
    try:
        cleaned = validate_payload(json.loads(body))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)[:180]) from exc
    now = int(time.time())
    if abs(cleaned['sent_at_utc'] - now) > 120:
        raise HTTPException(422, 'Jam PC MT4 tidak sinkron dengan UTC')
    if cleaned['tick_age_seconds'] > int(os.environ.get('MT4_MAX_TICK_AGE_SECONDS', '120')):
        raise HTTPException(422, 'Tick MT4 kedaluwarsa; menunggu quote baru')
    # Store latest payload, prevent replay; send timestamps use correctly synchronized PC UTC.
    with _connection() as con:
        con.execute('BEGIN IMMEDIATE')
        old = con.execute('SELECT sent_epoch FROM mt4_latest WHERE instrument=?',
                          (cleaned['instrument'],)).fetchone()
        if old and cleaned['sent_at_utc'] <= old['sent_epoch']:
            raise HTTPException(409, 'Paket MT4 duplikat atau lebih lama')
        con.execute('''INSERT INTO mt4_latest(instrument,broker_symbol,sent_epoch,received_epoch,json_payload)
                       VALUES(?,?,?,?,?) ON CONFLICT(instrument) DO UPDATE SET
                       broker_symbol=excluded.broker_symbol,sent_epoch=excluded.sent_epoch,
                       received_epoch=excluded.received_epoch,json_payload=excluded.json_payload''',
                    (cleaned['instrument'], cleaned['broker_symbol'], cleaned['sent_at_utc'],
                     now, json.dumps(cleaned, ensure_ascii=True, separators=(',', ':'))))
    return {'ok': True, 'instrument': cleaned['instrument'], 'received_at_utc':
            datetime.fromtimestamp(now, timezone.utc).isoformat()}


def _read(instrument):
    if instrument not in INSTRUMENTS:
        raise MarketDataError('Instrumen tidak dikenal')
    with _connection() as con:
        row = con.execute('SELECT * FROM mt4_latest WHERE instrument=?', (instrument,)).fetchone()
    if row is None:
        raise MarketDataError(f'Belum ada feed MT4 {instrument}; cek EA dan pemetaan simbol broker')
    age = time.time() - row['received_epoch']
    ttl = int(os.environ.get('MT4_FEED_TTL_SECONDS', '150'))
    if age < -5 or age > ttl:
        raise MarketDataError(f'Feed MT4 {instrument} tidak segar ({int(age)} detik); tidak membuat sinyal')
    data = json.loads(row['json_payload'])
    tick_age = data['tick_age_seconds'] + max(0, int(age))
    tick_limit = int(os.environ.get('MT4_MAX_TICK_AGE_SECONDS', '120'))
    if tick_age > tick_limit:
        raise MarketDataError(f'Tick {instrument} terakhir sudah {tick_age} detik; pasar mungkin tutup')
    return data, tick_age, row['received_epoch']


def fetch_mt4_quote(instrument):
    data, tick_age, recv = _read(instrument)
    price = (data['bid'] + data['ask']) / 2
    asof = datetime.fromtimestamp(recv - tick_age, timezone.utc).isoformat()
    return {'instrument': instrument, 'provider_symbol': data['broker_symbol'],
            'bid': data['bid'], 'ask': data['ask'], 'spread': data['ask'] - data['bid'],
            'price': price, 'asof': asof, 'age_seconds': tick_age,
            'tick_time_broker': data['tick_time_broker'],
            'source': 'MetaTrader 4 (broker terminal)',
            'price_type': 'bid/ask broker MT4; midpoint indikatif, bukan jaminan harga eksekusi'}


def fetch_mt4_snapshot(instrument):
    data, _, recv = _read(instrument)
    quote = fetch_mt4_quote(instrument)
    return {'instrument': instrument, 'quote': quote, 'frames': data['frames'],
            'provider': 'MetaTrader 4 broker feed (terminal read-only)',
            'retrieved_at': datetime.fromtimestamp(recv, timezone.utc).isoformat(),
            'candle_time_basis': 'broker-server local time, not UTC'}
