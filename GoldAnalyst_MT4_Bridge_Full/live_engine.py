"""Deterministic live-data technical directional outlook. No guessed prices/news."""
from datetime import datetime, timezone
import json
import logging
import math
import os
from ai_engine import INSTRUMENTS


def ema(values, period):
    if len(values)<period: raise ValueError('History EMA tidak cukup')
    result=sum(values[:period])/period
    k=2/(period+1)
    for val in values[period:]: result=val*k+result*(1-k)
    return result


def atr(bars, period=14):
    trs=[]
    for i in range(1,len(bars)):
        cprev=bars[i-1]['close'];b=bars[i]
        trs.append(max(b['high']-b['low'],abs(b['high']-cprev),abs(b['low']-cprev)))
    if len(trs)<period: raise ValueError('History ATR tidak cukup')
    return sum(trs[-period:])/period


def rsi(values, period=14):
    changes=[values[i]-values[i-1] for i in range(1,len(values))]
    if len(changes)<period: raise ValueError('History RSI tidak cukup')
    ups=sum(max(0,x) for x in changes[-period:])/period
    dns=sum(max(0,-x) for x in changes[-period:])/period
    return 100 if dns==0 else 100-100/(1+ups/dns)


def analyze(snapshot):
    sym=snapshot['instrument'];frames=snapshot['frames'];q=snapshot['quote']
    px=float(q['price'])
    features={};score=0
    weights={'D1':2,'H4':3,'H1':4}
    for tf in ('D1','H4','H1'):
        bars=frames[tf];closes=[b['close'] for b in bars]
        f9=ema(closes,9);f21=ema(closes,21);mom=rsi(closes);vol=atr(bars)
        trend='BUY' if f9>=f21 else 'SELL'
        score+=weights[tf] if trend=='BUY' else -weights[tf]
        features[tf]={'ema9':f9,'ema21':f21,'rsi14':round(mom,1),'atr14':vol,
                      'trend':trend,'last_close':closes[-1],
                      'swing_high':max(b['high'] for b in bars[-20:]),
                      'swing_low':min(b['low'] for b in bars[-20:])}
    direction='BUY' if score>0 else 'SELL' if score<0 else features['H1']['trend']
    h=features['H1'];vol=h['atr14'];digits=INSTRUMENTS[sym]['digits']
    if vol<=0 or not math.isfinite(vol): raise ValueError('Volatilitas pasar tidak valid')
    # These are transparent rule-based *scenario levels*, never market orders.
    center=h['ema21'];padding=vol*.18
    lo=round(center-padding,digits);hi=round(center+padding,digits)
    risk_floor= max(vol*1.0, 10**(-digits)*5)
    if direction=='BUY':
        sl=round(min(h['swing_low'],lo-risk_floor)-.15*vol,digits)
        risk=((lo+hi)/2)-sl
        tp1=round(max(h['swing_high'],hi+1.25*risk),digits)
        tp2=round(max(tp1+.6*risk,hi+2.0*risk),digits)
        tp3=round(max(tp2+.6*risk,hi+2.75*risk),digits)
    else:
        sl=round(max(h['swing_high'],hi+risk_floor)+.15*vol,digits)
        risk=sl-((lo+hi)/2)
        tp1=round(min(h['swing_low'],lo-1.25*risk),digits)
        tp2=round(min(tp1-.6*risk,lo-2.0*risk),digits)
        tp3=round(min(tp2-.6*risk,lo-2.75*risk),digits)
    if min(lo,hi,sl,tp1,tp2,tp3)<=0: raise ValueError('Proyeksi model di luar rentang harga valid')
    rr=round(abs(tp1-(lo+hi)/2)/risk,2)
    confirm=sum(1 for tf in features if features[tf]['trend']==direction)
    # Bias always points BUY/SELL with authenticated fresh quote. Execution readiness is separate.
    ready=confirm>=2 and (direction=='BUY' and 40<=h['rsi14']<=72 or
                          direction=='SELL' and 28<=h['rsi14']<=60) and rr>=1.1
    warnings=[]
    if confirm<2: warnings.append('Bias melawan mayoritas timeframe: skenario belum siap dieksekusi.')
    if not ready: warnings.append('Bias arah tersedia, tetapi syarat kualitas eksekusi belum terpenuhi.')
    broker_feed = q.get('source','').startswith('MetaTrader 4')
    if broker_feed:
        warnings.append('Bid/ask berasal dari terminal MT4 broker; spread dan harga fill dapat berubah saat order dikirim.')
    else:
        warnings.append('Harga indikatif penyedia data, tidak identik dengan kuotasi bid/ask broker.')
    warnings.append('Target adalah proyeksi berbasis EMA, ATR, dan swing; bukan level transaksi terjamin.')
    timetext=q['asof']
    if broker_feed:
        spread_note=(f'Bid {q["bid"]:.{digits}f}, Ask {q["ask"]:.{digits}f}, spread snapshot {q["spread"]:.{digits}f}. '
                     'Slippage, berita terkini, dan kondisi margin belum diverifikasi.')
    else:
        spread_note='Spread, slippage, berita terkini, dan kondisi margin belum diverifikasi.'
    evidence=[f'{tf}: EMA9 {f["ema9"]:.{digits}f}, EMA21 {f["ema21"]:.{digits}f}, RSI14 {f["rsi14"]}; arah {f["trend"]}' for tf,f in features.items()]
    sentiment=('bullish' if direction=='BUY' else 'bearish')
    result={
      'instrument':sym,'observed_instrument':sym,'asof':timetext,
      'title':f'{sym} — {sentiment.title()} Technical Outlook (Live Feed)',
      'decision':direction,'directional_bias':direction,
      'signal_validated':bool(ready), 'execution_ready':bool(ready),
      'summary':f'Bias {direction} dihitung dari candle D1/H4/H1 dan mid-quote {px:.{digits}f} ({timetext}). Konfirmasi arah {confirm}/3 timeframe. '+('Memenuhi filter teknikal awal; tetap wajib review.' if ready else 'Belum memenuhi filter eksekusi; jangan jadikan instruksi order.'),
      'bias':f'Sentimen teknikal {sentiment}: EMA9 vs EMA21 berbobot D1/H4/H1 memberikan skor {score}. RSI H1 {h["rsi14"]:.1f}. Bukan hasil verifikasi berita atau arus institusional.',
      'market_data_macro':f'Sumber: {snapshot["provider"]}. Harga terakhir dari provider: {px:.{digits}f}; timestamp {timetext}. Perbandingan makro/kalender ekonomi belum diverifikasi.',
      'the_trap':'Potensi false breakout dan sapuan likuiditas di swing H1 harus dipantau; keberadaan order institusi tidak dapat disimpulkan dari candle saja.',
      'confluence_check':' | '.join(evidence),
      'execution_plan':f'Skenario {direction} berbasis retracement EMA21 H1 dan invalidasi struktur plus ATR. Entry bersifat proyeksi, bukan harga eksekusi. {spread_note}',
      'disclaimer':('Riset dari bid/ask terminal MT4 broker, bukan jaminan fill; ' if broker_feed else 'Riset dari data API pihak ketiga, bukan kuotasi broker; ') + 'Diterbitkan oleh pihak yang terafiliasi broker dan dapat memiliki kepentingan komersial. Risiko kerugian tinggi; verifikasi waktu tick, spread, dan kesesuaian produk sebelum publikasi.',
      'evidence':evidence,'warnings':warnings,
      'trade':{'entry_low':lo,'entry_high':hi,'stop_loss':sl,'tp1':tp1,'tp2':tp2,'tp3':tp3,
               'rr_tp1':rr,'rationale':f'Entry sekitar EMA21 H1; SL di luar swing H1 dan buffer ATR. Target adalah ekstensi terukur; bukan harga pasti.'},
      'chart_scale':{'anchors':[], 'chart_bounds':{}},
      'market':{'source':snapshot['provider'],'provider_symbol':q['provider_symbol'],
                'quote_price':px,'bid':q.get('bid'),'ask':q.get('ask'),'spread':q.get('spread'),
                'quote_timestamp':timetext,'quote_age_seconds':q['age_seconds'],
                'price_type':q['price_type'],'technical':features,
                'method':'EMA9/EMA21, RSI14, ATR14, 20-candle swing'},
      'analysis_mode':'live','article_source':'rule-based' }
    return result


def enrich_with_gemini(result):
    """AI can polish narrative, never alter numerical levels, decision, provenance or readiness."""
    if not os.getenv('GEMINI_API_KEY') or os.getenv('GEMINI_LIVE_EDITORIAL','0') != '1':
        return result
    try:
        from google import genai
        from google.genai import types
        safe={k:result[k] for k in ('instrument','decision','asof','summary','bias','market_data_macro','confluence_check','evidence','execution_plan','warnings')}
        client=genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
        response=client.models.generate_content(
            model=os.getenv('GEMINI_MODEL','gemini-3.8-flash'),
            contents='Tuliskan artikel riset finansial bahasa Indonesia berdasarkan JSON TERBATAS ini. Jangan tambahkan harga, berita, real-time data, keyakinan numerik, atau klaim baru. Return JSON dengan keys: summary,bias,the_trap,confluence_check,execution_plan.\n'+json.dumps(safe,ensure_ascii=False),
            config=types.GenerateContentConfig(response_mime_type='application/json',max_output_tokens=2300,temperature=0.15))
        extra=json.loads(response.text or '{}')
        if isinstance(extra,dict):
            for key in ('summary','bias','the_trap','confluence_check','execution_plan'):
                if isinstance(extra.get(key),str) and 12<len(extra[key])<7000:
                    result[key]=extra[key]
            result['article_source']='Gemini editorial + deterministic levels'
    except Exception:
        logging.warning('Gemini editorial unavailable, keeping deterministic report',exc_info=True)
        result['warnings'].append('Gemini editorial tidak tersedia; teks analisis dari mesin teknikal berbasis aturan.')
    return result
