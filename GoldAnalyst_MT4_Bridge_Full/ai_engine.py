"""Gemini screenshot analysis and conservative rule-based numeric checks."""
import json
import logging
import math
import os
import random
import re
import time
from datetime import datetime, timezone

INSTRUMENTS = {
    'XAUUSD': {'name': 'Gold / US Dollar', 'digits': 2, 'kind': 'Logam mulia'},
    'XAGUSD': {'name': 'Silver / US Dollar', 'digits': 3, 'kind': 'Logam mulia'},
    'EURUSD': {'name': 'Euro / US Dollar', 'digits': 5, 'kind': 'Forex'},
    'GBPUSD': {'name': 'British Pound / US Dollar', 'digits': 5, 'kind': 'Forex'},
    'USDJPY': {'name': 'US Dollar / Japanese Yen', 'digits': 3, 'kind': 'Forex'},
    'USOIL':  {'name': 'WTI Crude Oil', 'digits': 2, 'kind': 'Energi'},
}

SYSTEM = '''Anda analis riset teknikal multi-aset untuk publikasi perusahaan broker.
Buat riset profesional dalam bahasa Indonesia berdasarkan HANYA screenshot D1 H4 H1 (M15 opsional).
- Identifikasi simbol dari grafik dan cocokkan dengan instrument yang diminta. Jika mismatch, keputusan WAIT.
- Pisahkan pengamatan aktual pada screenshot, hipotesis teknikal, dan informasi yang belum terverifikasi.
- Gunakan D1 untuk konteks, H4 untuk struktur, H1 untuk skenario. Jangan menganggap simbol/label timeframe pasti jika tak terbaca.
- Sentimen pasar yang dapat dipastikan hanya SENTIMEN TEKNIKAL dari chart. Makro, rilis berita, yield, dan data live tidak boleh dikarang.
- Pilih directional_bias BUY atau SELL jika ada bukti; jika belum ada dasar boleh NEUTRAL.
- decision BUY/SELL hanya jika setup terlihat, harga dapat dibaca jelas, invalidasi bermakna, target berurutan, dan bukti mendukung.
- decision WAIT bila setup tak memadai. Jangan paksa order demi volume transaksi broker. Level yang belum terbaca harus null.
- Jelaskan level entry berbasis demand/supply, struktur, konfirmasi dan risiko stop hunt jika tergambar; istilah institusi hanya hipotesis.
- Isi evidence dengan frasa yang mengikat kesimpulan ke timeframe/label harga yang terlihat, bukan klaim tak terukur.
- Jangan memberikan probabilitas 85% dan jangan menyatakan hasil pasti.
- Kalibrasi sumbu screenshot H1: sertakan 2-4 anchor {price,y_ratio} berdasarkan label nyata di sumbu kanan,
  y_ratio=posisi pusat label dibagi tinggi screenshot H1 (0 di atas, 1 di bawah),
  chart_bounds={left_x_ratio,right_x_ratio} berdasarkan batas plotting chart.
  Bila tidak yakin, anchors kosong. Jangan menebak posisi piksel.
- Semua level angka harus berupa numeric JSON, titik sebagai desimal (mis EURUSD 1.12345).
- Gaya artikel tajam, spesifik, mudah dipublikasikan, tidak repetitif. Sertakan disclosure afiliasi broker.
- Hanya output JSON; tanpa markdown. Keys berikut wajib:
  instrument, observed_instrument, title, asof, decision, directional_bias, bias,
  summary, market_data_macro, the_trap, confluence_check, execution_plan,
  disclaimer, evidence (array), warnings (array),
  trade (entry_low,entry_high,stop_loss,tp1,tp2,tp3,rationale),
  chart_scale (anchors array of {price,y_ratio}, chart_bounds object {left_x_ratio,right_x_ratio}).
- Jika skala tidak terbaca, chart_scale anchors=[], trade level yang tak terverifikasi null.
'''


def parse_json(text):
    s = (text or '').strip()
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find('{'), s.rfind('}')
        if start >= 0 and end > start:
            return json.loads(s[start:end + 1])
        raise ValueError('Gemini tidak mengembalikan objek JSON')


def generate_analysis(instrument, files):
    from google import genai
    from google.genai import types, errors

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError('GEMINI_API_KEY belum diisi')
    model = os.getenv('GEMINI_MODEL', 'gemini-3.6-flash')
    now = datetime.now(timezone.utc).isoformat()
    parts = [types.Part.from_text(text=(
        f'INSTRUMEN DIPILIH: {instrument} / {INSTRUMENTS[instrument]["name"]}. '
        f'Waktu unggah UTC: {now}. Screenshot bukan harga live. '
        'Jangan gunakan pengetahuan eksternal untuk mengklaim harga real-time. '
        'Berikan data makro belum diverifikasi jika tidak ada sumber.'
    ))]
    for tf in ('d1', 'h4', 'h1', 'm15'):
        if tf in files:
            parts.append(types.Part.from_text(text=f'--- SCREENSHOT {instrument} {tf.upper()} ---'))
            parts.append(types.Part.from_bytes(data=files[tf].read_bytes(), mime_type='image/png'))
    client = genai.Client(api_key=api_key)
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[types.Content(role='user', parts=parts)],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    response_mime_type='application/json',
                    max_output_tokens=8192,
                    temperature=0.2,
                ),
            )
            if not response.text:
                raise ValueError('Gemini mengembalikan respons kosong')
            return parse_json(response.text)
        except errors.APIError as exc:
            code = getattr(exc, 'code', None)
            if code not in (429, 500, 502, 503, 504) or attempt == 2:
                logging.warning('Gemini failed (HTTP %s)', code)
                if code == 429:
                    raise RuntimeError('Kuota/rate limit Gemini tercapai; periksa AI Studio') from exc
                if code == 503:
                    raise RuntimeError('Gemini sedang sibuk (503); coba generate lagi') from exc
                raise RuntimeError(f'Gemini tidak tersedia (HTTP {code}); periksa model/API key') from exc
            time.sleep((2 ** (attempt + 1)) + random.uniform(0, 0.5))


def num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        # Reject grouped commas; for 4,063.00 use standard JSON numeric 4063.00.
        if isinstance(value, str):
            value = value.strip().replace(' ', '')
            if ',' in value:
                return None
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError):
        return None


def textify(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return '\n'.join(f'{k}: {textify(x)}' for k, x in v.items())
    if isinstance(v, list):
        return '\n'.join('• ' + textify(x) for x in v)
    return '' if v is None else str(v)


def items(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [textify(x) for x in v if textify(x)]
    return [textify(v)] if textify(v) else []


def normalize_scale(scale):
    if not isinstance(scale, dict):
        return {'anchors': [], 'chart_bounds': {}}
    raw_anchors = scale.get('anchors') or []
    anchors = []
    for a in raw_anchors if isinstance(raw_anchors, list) else []:
        if not isinstance(a, dict):
            continue
        price = num(a.get('price'))
        try:
            y = float(a.get('y_ratio'))
        except (ValueError, TypeError):
            continue
        if price and 0 <= y <= 1:
            anchors.append({'price': price, 'y_ratio': y})
    bounds = scale.get('chart_bounds') if isinstance(scale.get('chart_bounds'), dict) else {}
    try:
        left, right = float(bounds.get('left_x_ratio')), float(bounds.get('right_x_ratio'))
        if not (0 <= left < right <= 1 and right - left > 0.35):
            bounds = {}
        else:
            bounds = {'left_x_ratio': left, 'right_x_ratio': right}
    except (ValueError, TypeError):
        bounds = {}
    return {'anchors': anchors[:5], 'chart_bounds': bounds}


def normalize_analysis(raw, instrument):
    if not isinstance(raw, dict):
        raise ValueError('Analisis harus berformat objek JSON')
    a = {field: textify(raw.get(field)) for field in (
        'title', 'asof', 'bias', 'summary', 'market_data_macro',
        'the_trap', 'confluence_check', 'execution_plan', 'disclaimer')}
    a['instrument'] = instrument
    observed = textify(raw.get('observed_instrument')).upper().replace('/', '').replace('-', '').replace(' ', '')
    a['observed_instrument'] = observed
    a['evidence'] = items(raw.get('evidence'))
    a['warnings'] = items(raw.get('warnings'))
    a['directional_bias'] = str(raw.get('directional_bias') or 'NEUTRAL').upper()
    if a['directional_bias'] not in ('BUY', 'SELL', 'NEUTRAL'):
        a['directional_bias'] = 'NEUTRAL'
    decision = str(raw.get('decision') or 'WAIT').upper()
    if decision not in ('BUY', 'SELL', 'WAIT'):
        decision = 'WAIT'
    trade_in = raw.get('trade') if isinstance(raw.get('trade'), dict) else {}
    trade = {k: num(trade_in.get(k)) for k in ('entry_low','entry_high','stop_loss','tp1','tp2','tp3')}
    trade['rationale'] = textify(trade_in.get('rationale'))
    trade['rr_tp1'] = None
    if observed and instrument not in observed:
        a['warnings'].append(f'Simbol pada screenshot diduga {observed}, tidak cocok dengan {instrument}.')
        decision = 'WAIT'
    values = [trade[k] for k in ('entry_low','entry_high','stop_loss','tp1','tp2','tp3')]
    if None in values:
        decision = 'WAIT'
        a['warnings'].append('Satu atau lebih level harga tidak terbaca; setup belum layak dieksekusi.')
    else:
        lo, hi, sl, tp1, tp2, tp3 = values
        is_buy = a['directional_bias'] == 'BUY'
        is_sell = a['directional_bias'] == 'SELL'
        valid_order = (lo <= hi and ((is_buy and sl < lo <= hi < tp1 < tp2 < tp3)
                                    or (is_sell and tp3 < tp2 < tp1 < lo <= hi < sl)))
        if not valid_order:
            decision = 'WAIT'
            a['warnings'].append('Urutan level Entry/SL/TP tidak valid secara matematis.')
        else:
            mid = (lo + hi) / 2
            risk = abs(mid - sl)
            rr = abs(tp1 - mid) / risk if risk > 0 else 0
            trade['rr_tp1'] = round(rr, 2)
            if rr < 1.0:
                decision = 'WAIT'
                a['warnings'].append('Rasio potensi reward/risk menuju TP1 di bawah 1:1.')
    if decision in ('BUY', 'SELL') and decision != a['directional_bias']:
        decision = 'WAIT'
        a['warnings'].append('Arah transaksi bertentangan dengan bias teknikal.')
    if not a['evidence']:
        decision = 'WAIT'
        a['warnings'].append('Bukti teknikal berbasis timeframe belum tersedia.')
    a['decision'] = decision
    a['signal_validated'] = decision in ('BUY', 'SELL')
    a['trade'] = trade
    a['chart_scale'] = normalize_scale(raw.get('chart_scale'))
    if not a['disclaimer']:
        a['disclaimer'] = 'Analisis hanya dari screenshot, bukan harga live. Hasil memerlukan verifikasi analis dan tidak menjamin keuntungan.'
    if not a['summary']:
        a['summary'] = a['bias']
    return a
