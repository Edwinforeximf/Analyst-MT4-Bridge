"""Complete Word research export with chart, infographic, audit caveats."""
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from ai_engine import INSTRUMENTS

SECTIONS = (
    ('1. DATA PASAR & FUNDAMENTAL', 'market_data_macro'),
    ('2. THE TRAP / RISIKO LIKUIDITAS', 'the_trap'),
    ('3. CONFLUENCE CHECK', 'confluence_check'),
    ('4. EXECUTION PLAN', 'execution_plan'),
    ('5. DISCLAIMER & MANAJEMEN RISIKO', 'disclaimer'),
    ('EVIDENCE BERDASARKAN CHART', 'evidence'),
    ('CATATAN VALIDASI', 'warnings'),
)


def as_text(v):
    if v is None:
        return 'Belum tersedia'
    if isinstance(v,list):
        return '\n'.join('• '+as_text(x) for x in v)
    if isinstance(v,dict):
        return '\n'.join(f'{k}: {as_text(x)}' for k,x in v.items())
    return str(v)


def report_text(a, rid, status):
    trade=a.get('trade') or {}
    parts=[a.get('title') or f'{a.get("instrument", "MARKET")} - TECHNICAL OUTLOOK',
           'GOLD ANALYST PRO | MULTI-ASSET EDITION',
           f'ID: {rid} | Status: {status} | Tanggal: {as_text(a.get("asof"))}',
           f'Instrumen: {a.get("instrument", "—")} | Keputusan: {a.get("decision", "WAIT")}',
           f'Sentimen: {a.get("directional_bias", "NEUTRAL")}',
           '\nEXECUTIVE SUMMARY\n'+as_text(a.get('summary')),
           '\nSENTIMEN TEKNIKAL\n'+as_text(a.get('bias')),
           '\nTRADING PLAN']
    for label,key in (('Entry Low','entry_low'),('Entry High','entry_high'),('Stop Loss','stop_loss'),
                       ('TP1','tp1'),('TP2','tp2'),('TP3','tp3'),('R/R TP1','rr_tp1')):
        parts.append(f'{label}: {as_text(trade.get(key))}')
    parts.append('Alasan teknikal: '+as_text(trade.get('rationale')))
    for title,key in SECTIONS:
        parts.append('\n'+title+'\n'+as_text(a.get(key)))
    parts.append('\nSUMBER: '+('data provider indikatif, bukan harga broker.' if a.get('analysis_mode')=='live' else 'screenshot, bukan quote live.')+' Afiliasi: materi riset perusahaan broker. Tidak ada kepastian hasil; verifikasi reviewer wajib.')
    return '\n'.join(parts)


def create_document(rid, a, status, storage):
    storage=Path(storage)
    doc=Document()
    sec=doc.sections[0]
    sec.top_margin=Inches(.7);sec.bottom_margin=Inches(.7)
    normal=doc.styles['Normal']
    normal.font.name='Aptos';normal.font.size=Pt(10.5)
    normal.font.color.rgb=RGBColor(32,47,72)
    normal.paragraph_format.space_after=Pt(8)
    brand=doc.add_paragraph()
    brand.alignment=WD_ALIGN_PARAGRAPH.CENTER
    run=brand.add_run('GOLD ANALYST PRO')
    run.bold=True;run.font.size=Pt(24);run.font.color.rgb=RGBColor(15,36,98)
    p=doc.add_paragraph('MULTI-ASSET RESEARCH  /  QUICKPRO')
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading(a.get('title') or 'Technical Outlook',1)
    doc.add_paragraph(f'Instrumen: {a.get("instrument")}  •  UTC: {as_text(a.get("asof"))}  •  Status: {status.upper()}')
    doc.add_heading('EXECUTIVE SUMMARY',2)
    doc.add_paragraph(as_text(a.get('summary')))
    doc.add_paragraph('Keputusan: '+as_text(a.get('decision'))+'   |   Bias: '+as_text(a.get('directional_bias')))
    doc.add_heading('SENTIMEN TEKNIKAL',2)
    doc.add_paragraph(as_text(a.get('bias')))
    trade=a.get('trade') or {}
    doc.add_heading('TRADING PLAN',2)
    table=doc.add_table(rows=1,cols=2)
    table.style='Light Shading Accent 1'
    table.rows[0].cells[0].text='Parameter'
    table.rows[0].cells[1].text='Harga'
    digits=INSTRUMENTS.get(a.get('instrument'),INSTRUMENTS['XAUUSD'])['digits']
    for title,key in (('Entry Low','entry_low'),('Entry High','entry_high'),('Stop Loss','stop_loss'),
                      ('Take Profit 1','tp1'),('Take Profit 2','tp2'),('Take Profit 3','tp3'),('Reward/Risk TP1','rr_tp1')):
        cells=table.add_row().cells
        cells[0].text=title
        v=trade.get(key)
        cells[1].text=f'{v:,.{2 if key=="rr_tp1" else digits}f}' if isinstance(v,(int,float)) else '—'
    doc.add_paragraph('ALASAN LEVEL: '+as_text(trade.get('rationale')))
    for heading,key in SECTIONS:
        doc.add_heading(heading,2)
        for para in as_text(a.get(key)).split('\n'):
            if para.strip():
                doc.add_paragraph(para)
    for heading,file in (('CHART TEKNIKAL H1',f'{rid}_annotated.png'),('INFOGRAFIS PUBLIKASI',f'{rid}_infographic.png')):
        p=storage/file
        if p.exists():
            doc.add_page_break()
            doc.add_heading(heading,1)
            # Inline image stays within usable page width.
            doc.add_picture(str(p),width=Inches(5.75))
    doc.add_paragraph('Sumber: '+('data provider indikatif, bukan harga broker.' if a.get('analysis_mode')=='live' else 'screenshot chart, bukan data live.')+' Afiliasi perusahaan broker. Tidak ada jaminan hasil; publikasi mengikuti persetujuan reviewer.')
    result=storage/f'{rid}_research.docx'
    doc.save(result)
    return result
