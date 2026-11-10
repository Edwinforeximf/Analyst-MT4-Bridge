"""Pixel-calibrated chart annotations and navy/gold social publishing graphic."""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from ai_engine import INSTRUMENTS

BG = '#020328'
NAVY = '#101B3D'
CARD = '#17294B'
GOLD = '#EAC678'
WHITE = '#F6F8FF'
SOFT = '#B2C6E7'
GREEN = '#35D88C'
RED = '#F16B72'
CYAN = '#03E1E8'


def fnt(size, bold=False):
    preferred = ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    try:
        return ImageFont.truetype(preferred, size)
    except OSError:
        return ImageFont.load_default()


def lines(draw, s, font, width, max_lines=3):
    out, ln = [], ''
    for word in str(s).split():
        candidate = (ln + ' ' + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] > width and ln:
            out.append(ln)
            ln = word
        else:
            ln = candidate
    if ln:
        out.append(ln)
    if len(out) > max_lines:
        out = out[:max_lines]
        out[-1] = out[-1][:max(0, len(out[-1])-2)] + '…'
    return out


def price(v, instrument):
    if v is None:
        return '—'
    try:
        return f'{float(v):,.{INSTRUMENTS[instrument]["digits"]}f}'
    except (TypeError, ValueError):
        return '—'


def fit_image(im, box):
    x1, y1, x2, y2 = box
    # Intentionally upscale smaller phone screenshots to fill the available chart area.
    ratio = min((x2-x1)/im.width, (y2-y1)/im.height)
    copy = im.resize((max(1,round(im.width*ratio)),max(1,round(im.height*ratio))),Image.Resampling.LANCZOS)
    x = x1 + (x2-x1-copy.width)//2
    y = y1 + (y2-y1-copy.height)//2
    return copy, (x, y)


def watermark(base, path, placement, opacity=0.10, maxw=None):
    if not Path(path).exists():
        return
    mark = Image.open(path).convert('RGBA')
    bx1, by1, bx2, by2 = placement
    maxw = maxw or int((bx2-bx1)*0.57)
    mark.thumbnail((maxw, int((by2-by1)*0.36)), Image.Resampling.LANCZOS)
    alpha = mark.getchannel('A').point(lambda n: int(n*opacity))
    mark.putalpha(alpha)
    x = (bx1+bx2-mark.width)//2
    y = (by1+by2-mark.height)//2
    layer = Image.new('RGBA', base.size)
    layer.paste(mark, (x, y))
    composed = Image.alpha_composite(base.convert('RGBA'), layer)
    base.paste(composed.convert('RGB'))


def calc_scale(data):
    """Return y ratio = slope * price + offset if anchors form a consistent linear axis."""
    anchors = data.get('anchors') or []
    bounds = data.get('chart_bounds') or {}
    if len(anchors) < 2 or not bounds:
        return None
    px = [a['price'] for a in anchors]
    ys = [a['y_ratio'] for a in anchors]
    if max(px)-min(px) <= 0 or max(ys)-min(ys) < .12:
        return None
    avgp, avgy = sum(px)/len(px), sum(ys)/len(ys)
    denom = sum((p-avgp)**2 for p in px)
    if denom == 0:
        return None
    slope = sum((p-avgp)*(y-avgy) for p,y in zip(px,ys))/denom
    off = avgy-slope*avgp
    if not math.isfinite(slope) or slope >= 0:
        return None
    if max(abs(slope*p+off-y) for p,y in zip(px,ys)) > .025:
        return None
    return {'slope': slope,'offset': off,'left': bounds['left_x_ratio'], 'right': bounds['right_x_ratio']}


def dot_line(d, xy, color, width=4, dash=24, gap=13):
    x1,y,x2 = xy
    x = x1
    while x < x2:
        d.line((x,y,min(x+dash,x2),y),fill=color,width=width)
        x += dash+gap


def arrow(d, first, last, color, width=12):
    d.line((*first,*last),fill=color,width=width, joint='curve')
    theta = math.atan2(last[1]-first[1], last[0]-first[0])
    l = width*3.5
    p1 = (last[0]-l*math.cos(theta-.45),last[1]-l*math.sin(theta-.45))
    p2 = (last[0]-l*math.cos(theta+.45),last[1]-l*math.sin(theta+.45))
    d.polygon([last,p1,p2],fill=color)


def annotated_chart(chart_path, analysis, watermark_path, output):
    source = Image.open(chart_path).convert('RGB')
    W = 1600
    H = min(2300,max(1300, int(source.height/max(source.width,1)*980)+340))
    im = Image.new('RGB',(W,H),BG)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((30,24,W-30,H-26),radius=35,outline='#344D79',width=3,fill=NAVY)
    d.text((75,62), f'{analysis["instrument"]}  /  TECHNICAL OUTLOOK',font=fnt(47,True),fill=WHITE)
    status = analysis.get('decision','WAIT')
    if status == 'WAIT':
        direction=analysis.get('directional_bias','')
        status=(direction+' BIAS') if direction in ('BUY','SELL') else 'NO SETUP'
    col = GREEN if status.startswith('BUY') else RED if status.startswith('SELL') else GOLD
    d.rounded_rectangle((1250,60,1522,128),radius=19,fill='#293A5D')
    d.text((1260,68),status,font=fnt(27 if len(status)>6 else 44,True),fill=col)
    chart_box = (55,168,1120,H-190)
    rendered, (x,y) = fit_image(source,chart_box)
    im.paste(rendered,(x,y))
    image_rect=(x,y,x+rendered.width,y+rendered.height)
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((x-3,y-3,x+rendered.width+3,y+rendered.height+3),radius=12,outline='#46739A',width=3)
    scale=calc_scale(analysis.get('chart_scale') or {})
    trade=analysis.get('trade') or {}
    visible_lines=[]
    if scale and analysis.get('signal_validated'):
        left=x+int(rendered.width*scale['left'])
        right=x+int(rendered.width*scale['right'])
        y_from=lambda p: y+int(rendered.height*(scale['slope']*p+scale['offset']))
        low,high=trade['entry_low'],trade['entry_high']
        ya,yb=y_from(high),y_from(low)
        if y+8<=ya<=y+rendered.height-8 and y+8<=yb<=y+rendered.height-8:
            overlay=Image.new('RGBA',im.size,(0,0,0,0)); od=ImageDraw.Draw(overlay)
            od.rectangle((left,ya,right,yb),fill=(31,113,250,66),outline=(28,129,255,255),width=4)
            im=Image.alpha_composite(im.convert('RGBA'),overlay).convert('RGB')
            d=ImageDraw.Draw(im)
            zone_label = f'{analysis["decision"]} LIMIT AREA  {price(low,analysis["instrument"])} – {price(high,analysis["instrument"])}'
            zone_font = fnt(24,True)
            bx = max(left+8,right-d.textbbox((0,0),zone_label,font=zone_font)[2]-10)
            zy = max(y+5,min(int((ya+yb)/2)-17,y+rendered.height-38))
            d.rounded_rectangle((bx-4,zy-3,right-5,zy+32),radius=6,fill='#0A1835')
            d.text((bx,zy),zone_label,font=zone_font,fill='#A8C8FF')
        for label,p,color in (('SL',trade['stop_loss'],RED),('TP1',trade['tp1'],GREEN),('TP2',trade['tp2'],GREEN),('TP3',trade['tp3'],GREEN)):
            ty=y_from(p)
            if y+10<=ty<=y+rendered.height-10:
                dot_line(d,(left,ty,right),color,5)
                # Caption goes on the chart itself, like a trading-view annotation.
                text_label = f'{label}  {price(p,analysis["instrument"])}'
                label_font = fnt(25,True)
                boxw = d.textbbox((0,0),text_label,font=label_font)[2] + 20
                chipx = max(left+8,right-boxw-10)
                chipy = max(y+5,min(ty-35,y+rendered.height-43))
                d.rounded_rectangle((chipx,chipy,chipx+boxw,chipy+34),radius=8,fill='#0A1835')
                d.text((chipx+9,chipy+2),text_label,font=label_font,fill=color)
                visible_lines.append(label)
        entry_mid=(low+high)/2
        start_y,end_y=y_from(entry_mid),y_from(trade['tp2'])
        if y<start_y<y+rendered.height and y<end_y<y+rendered.height:
            arrow(d,(int(left+(right-left)*.55),start_y),(int(left+(right-left)*.86),end_y),CYAN,11)
    # Branding: central watermark over chart with alpha 10%; separate from footer logo.
    watermark(im,watermark_path,image_rect,opacity=.10)
    d=ImageDraw.Draw(im)
    rx1=1152
    d.text((rx1,184),'TRADE PLAN',font=fnt(31,True),fill=GOLD)
    d.text((rx1,238),('LIVE FEED INDICATIVE' if analysis.get('analysis_mode')=='live' else 'SCREENSHOT-BASED'),font=fnt(20),fill=SOFT)
    rows=[('ENTRY AREA',price(trade.get('entry_low'),analysis['instrument'])+' -\n'+price(trade.get('entry_high'),analysis['instrument'])),
          ('STOP LOSS',price(trade.get('stop_loss'),analysis['instrument'])),
          ('TARGET 1',price(trade.get('tp1'),analysis['instrument'])),
          ('TARGET 2',price(trade.get('tp2'),analysis['instrument'])),
          ('TARGET 3',price(trade.get('tp3'),analysis['instrument']))]
    step=min(220,max(142,(H-620)//5))
    for i,(label,val) in enumerate(rows):
        top=280+i*step
        d.rounded_rectangle((1146,top,1532,top+step-14),radius=20,fill=CARD)
        d.text((1174,top+18),label,font=fnt(25,True),fill=GOLD if i==0 else SOFT)
        for j,line in enumerate(val.split('\n')):
            d.text((1174,top+61+j*44),line,font=fnt(34,True),fill=WHITE if i==0 else (RED if i==1 else GREEN))
    footer_y=H-153
    d.line((70,footer_y,1530,footer_y),fill='#42638B',width=3)
    note='KALIBRASI SUMBU: VALIDASI ESTIMASI' if scale and analysis.get('signal_validated') else 'LEVEL DALAM PANEL — SUMBU HARGA BELUM TERKALIBRASI'
    d.text((73,footer_y+19),note,font=fnt(25,True),fill=GOLD)
    d.text((73,footer_y+67),('Provider quote · bukan harga broker • Review wajib' if analysis.get('analysis_mode')=='live' else 'Bukan harga live • Proyeksi, bukan jaminan • Verifikasi reviewer wajib'),font=fnt(23),fill=SOFT)
    im.save(output,optimize=True)
    return {'calibrated':bool(scale),'drawn_levels':visible_lines}


def infographic(a, watermark_path, output, approved=False):
    W,H=1080,1440
    im=Image.new('RGB',(W,H),BG)
    d=ImageDraw.Draw(im)
    for yy in range(H):
        d.line((0,yy,W,yy),fill=(2,3,int(40+25*yy/H)))
    d.rounded_rectangle((42,35,1038,1398),radius=32,outline='#334A77',width=3,fill='#0D1835')
    d.rectangle((58,54,1022,64),fill=GOLD)
    symbol=a['instrument']
    status=a.get('decision','WAIT')
    if status=='WAIT':
        direction=a.get('directional_bias','')
        status=(direction+' BIAS') if direction in ('BUY','SELL') else 'NO SETUP'
    col=GREEN if status.startswith('BUY') else RED if status.startswith('SELL') else GOLD
    d.text((92,96),'GOLD ANALYST PRO',font=fnt(45,True),fill=GOLD)
    d.text((94,166),f'{symbol}  |  DAILY OUTLOOK',font=fnt(41,True),fill=WHITE)
    d.rounded_rectangle((86,246,996,439),radius=26,fill='#1A2E50')
    d.text((123,266),'DIRECTIONAL SIGNAL',font=fnt(26,True),fill=SOFT)
    d.text((120,316),status,font=fnt(58 if len(status)>6 else 93,True),fill=col)
    d.text((462,341),'BIAS: '+a.get('directional_bias','NEUTRAL'),font=fnt(28,True),fill=WHITE)
    d.text((87,478),'KEY PRICE LEVELS',font=fnt(31,True),fill=GOLD)
    trade=a.get('trade') or {}
    rows=[('ENTRY LOW',trade.get('entry_low')),('ENTRY HIGH',trade.get('entry_high')),
          ('STOP LOSS',trade.get('stop_loss')),('TAKE PROFIT 1',trade.get('tp1')),
          ('TAKE PROFIT 2',trade.get('tp2')),('TAKE PROFIT 3',trade.get('tp3'))]
    for i,(label,val) in enumerate(rows):
        colidx,row=i%2,i//2
        x=86+colidx*464; yy=532+row*171
        d.rounded_rectangle((x,yy,x+445,yy+154),radius=22,fill='#182C4C',outline='#304B72',width=2)
        d.text((x+24,yy+21),label,font=fnt(25,True),fill=SOFT)
        value=price(val,symbol)
        size=47 if len(value)<=11 else 35
        d.text((x+23,yy+66),value,font=fnt(size,True),fill=RED if label=='STOP LOSS' else GREEN if 'PROFIT' in label else WHITE)
    d.text((88,1074),'KENAPA ARAH INI?',font=fnt(28,True),fill=GOLD)
    summary=(a.get('summary') or a.get('bias') or 'Analisis berbasis chart')
    f=fnt(26)
    for i,line in enumerate(lines(d,summary,f,880,max_lines=5)):
        d.text((90,1116+i*38),line,font=f,fill=WHITE)
    d.line((89,1331,990,1331),fill='#405B82',width=2)
    d.text((90,1344),('APPROVED • REVIEWER' if approved else 'DRAFT • REVIEW REQUIRED')+('  |  PROVIDER QUOTE, BUKAN HARGA BROKER' if a.get('analysis_mode')=='live' else '  |  BUKAN HARGA LIVE'),
           font=fnt(20,True),fill=GREEN if approved else GOLD)
    # Small official logo at footer, 90% opacity; no central obscuring of text.
    if Path(watermark_path).exists():
        try:
            wm=Image.open(watermark_path).convert('RGBA')
            wm.thumbnail((185,74),Image.Resampling.LANCZOS)
            a_ch=wm.getchannel('A').point(lambda v:int(v*.9));wm.putalpha(a_ch)
            layer=Image.new('RGBA',im.size)
            layer.paste(wm,(W-wm.width-65,83))
            im=Image.alpha_composite(im.convert('RGBA'),layer).convert('RGB')
        except OSError:
            pass
    im.save(output,optimize=True)


def live_source_chart(snapshot, analysis, destination):
    """Draw reproducible H1 candles from API OHLC and exact axis anchors.

    Unlike screenshot OCR, the pixel-to-price calibration is derived from the
    same bar values used for the chart and is therefore mathematically exact.
    """
    sym=analysis['instrument'];digits=INSTRUMENTS[sym]['digits']
    data=snapshot['frames']['H1'][-60:]
    tr=analysis.get('trade') or {}
    values=[p for b in data for p in (b['low'],b['high'])]
    values += [float(tr[k]) for k in ('entry_low','entry_high','stop_loss','tp1','tp2','tp3') if tr.get(k) is not None]
    low, high=min(values), max(values)
    margin=max((high-low)*.08,high*.00005)
    lo=max(0.0000001,low-margin); hi=high+margin
    W,H=1200,780; left,top,right,bottom=78,95,1035,695
    canvas=Image.new('RGB',(W,H),'#071329');d=ImageDraw.Draw(canvas)
    d.text((left,22),f'{sym} • H1 • TWELVE DATA (INDICATIVE)',font=fnt(29,True),fill=GOLD)
    d.rectangle((left,top,right,bottom),outline='#32476A',width=2)
    yprice=lambda p:round(bottom-(p-lo)/(hi-lo)*(bottom-top))
    for i in range(7):
        yy=top+round(i*(bottom-top)/6)
        p=hi-(hi-lo)*i/6
        d.line((left,yy,right,yy),fill='#253B5B',width=1)
        d.text((right+10,yy-12),f'{p:,.{digits}f}',font=fnt(18),fill=SOFT)
    step=(right-left)/max(len(data),1)
    for i,b in enumerate(data):
        x=round(left+(i+.5)*step)
        o,c=b['open'],b['close']
        color=GREEN if c>=o else RED
        d.line((x,yprice(b['high']),x,yprice(b['low'])),fill=color,width=2)
        xpad=max(2,round(step*.3))
        yt,yb=sorted((yprice(o),yprice(c)))
        d.rectangle((x-xpad,yt,x+xpad,max(yt+1,yb)),fill=color)
    d.text((left,722),'SOURCE: PROVIDER OHLC · NOT EXECUTABLE BID/ASK · REVIEW REQUIRED',font=fnt(19),fill=SOFT)
    canvas.save(destination,optimize=True)
    analysis['chart_scale']={'anchors':[{'price':round(hi,11),'y_ratio':top/H},
                                         {'price':round(lo,11),'y_ratio':bottom/H}],
                             'chart_bounds':{'left_x_ratio':left/W,'right_x_ratio':right/W}}
    return destination
