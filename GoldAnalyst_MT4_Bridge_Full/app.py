"""Gold Analyst Pro — Multi-Asset edition. Mobile-first screenshot research with human review."""
import io
import json
import os
import re
import secrets
import sqlite3
import hashlib
import hmac
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer, BadSignature
from PIL import Image, ImageOps, UnidentifiedImageError

from ai_engine import generate_analysis, normalize_analysis, INSTRUMENTS
from visuals import annotated_chart, infographic, live_source_chart
from market_feed import fetch_quote, fetch_snapshot, MarketDataError
from mt4_feed import router as mt4_router, fetch_mt4_quote, fetch_mt4_snapshot
from live_engine import analyze as analyze_live, enrich_with_gemini
from export_docx import create_document, report_text

ROOT = Path(__file__).resolve().parent
STORAGE = Path(os.environ.get('STORAGE_DIR', str(ROOT / 'storage'))).resolve()
STORAGE.mkdir(parents=True, exist_ok=True)
DB = STORAGE / 'reports.db'
SECRET = os.environ.get('APP_SECRET')
if not SECRET:
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        raise RuntimeError('APP_SECRET wajib diisi di Railway Variables')
    SECRET = secrets.token_urlsafe(40)
    logging.warning('APP_SECRET belum diset: sesi login lokal akan berubah ketika server restart')
SERIALIZER = URLSafeTimedSerializer(SECRET)
SECURE_COOKIE = os.environ.get('COOKIE_SECURE', '1' if os.environ.get('RAILWAY_ENVIRONMENT') else '0') == '1'
app = FastAPI(title='Gold Analyst Pro — Multi-Asset', version='1.0.0')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
app.include_router(mt4_router)

def use_mt4():
    return os.getenv('LIVE_PROVIDER', 'TWELVE').upper() == 'MT4'

def live_configured():
    return bool(os.getenv('MT4_BRIDGE_TOKEN')) if use_mt4() else bool(os.getenv('TWELVEDATA_API_KEY'))


def now():
    return datetime.now(timezone.utc).isoformat()


def database():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def initialize():
    with database() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, passhash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','senior','analyst'))
        );
        CREATE TABLE IF NOT EXISTS reports(
          id TEXT PRIMARY KEY, author_id INTEGER NOT NULL REFERENCES users(id),
          instrument TEXT NOT NULL, status TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL, analysis TEXT,
          feedback TEXT DEFAULT '', reviewer_id INTEGER,
          version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS report_files(
          report_id TEXT NOT NULL, timeframe TEXT NOT NULL, filename TEXT NOT NULL,
          PRIMARY KEY (report_id,timeframe)
        );
        ''')
        # Compatible migration: retain accounts and reports already in Railway Volume.
        columns={row['name'] for row in db.execute('PRAGMA table_info(reports)')}
        if 'mode' not in columns:
            db.execute("ALTER TABLE reports ADD COLUMN mode TEXT NOT NULL DEFAULT 'screenshot'")
        if 'market_snapshot' not in columns:
            db.execute("ALTER TABLE reports ADD COLUMN market_snapshot TEXT")
initialize()


def passhash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 310000).hex()


def verify(password, encoded):
    try:
        salt, _ = encoded.split(':', 1)
        return hmac.compare_digest(passhash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def account(request: Request):
    token = request.cookies.get('session')
    if not token:
        return None
    try:
        uid = SERIALIZER.loads(token, max_age=60 * 60 * 24 * 7)
    except (BadSignature, ValueError):
        return None
    with database() as db:
        u = db.execute('SELECT id,username,role FROM users WHERE id=?', (uid,)).fetchone()
    return dict(u) if u else None


def require(request, roles=None):
    user = account(request)
    if not user:
        raise HTTPException(401, 'Login diperlukan')
    if roles and user['role'] not in roles:
        raise HTTPException(403, 'Akses tidak diizinkan')
    return user


def get_report(rid, user):
    with database() as db:
        row = db.execute('SELECT * FROM reports WHERE id=?', (rid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Laporan tidak ditemukan')
    report = dict(row)
    if user['role'] == 'analyst' and user['id'] != report['author_id']:
        raise HTTPException(403, 'Tidak memiliki akses ke laporan ini')
    return report


def store_path(filename):
    path = (STORAGE / filename).resolve()
    if not path.is_relative_to(STORAGE):
        raise HTTPException(400, 'Nama file tidak valid')
    return path


async def save_picture(upload: UploadFile, filename, alpha=False):
    content = await upload.read()
    if not content or len(content) > 12 * 1024 * 1024:
        raise HTTPException(400, 'Gambar harus berukuran 1 byte hingga 12 MB')
    try:
        with Image.open(io.BytesIO(content)) as raw:
            if raw.width * raw.height > 25_000_000:
                raise HTTPException(400, 'Resolusi gambar melebihi 25 megapiksel')
            image = ImageOps.exif_transpose(raw)
            image.thumbnail((2400, 2400))
            image = image.convert('RGBA' if alpha else 'RGB')
            image.save(store_path(filename), format='PNG', optimize=True)
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, 'File bukan gambar yang dapat dibaca') from exc


@app.get('/', response_class=HTMLResponse)
def homepage():
    return (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')


@app.get('/manifest.webmanifest')
def manifest():
    return FileResponse(ROOT / 'static' / 'manifest.webmanifest', media_type='application/manifest+json')


@app.get('/sw.js')
def sw():
    return FileResponse(ROOT / 'static' / 'sw.js', media_type='application/javascript')


@app.get('/health')
def health():
    return {'status': 'ok', 'version': '2.0.0', 'assets': list(INSTRUMENTS),
            'live_feed_configured': live_configured(),
            'live_provider': 'MT4' if use_mt4() else 'Twelve Data'}


@app.get('/api/setup')
def setup_state():
    with database() as db:
        count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    return {'needs_setup': count == 0, 'ai_configured': bool(os.getenv('GEMINI_API_KEY') or live_configured()),
            'live_provider': 'MT4' if use_mt4() else 'Twelve Data',
            'instruments': list(INSTRUMENTS), 'setup_token_required': count == 0 and bool(os.getenv('SETUP_TOKEN'))}


@app.post('/api/setup')
async def setup(request: Request):
    data = await request.json()
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,36}', username) or len(password) < 12:
        raise HTTPException(400, 'Username 3–36 karakter; password minimal 12 karakter')
    expected = os.getenv('SETUP_TOKEN')
    if os.getenv('RAILWAY_ENVIRONMENT') and not expected:
        raise HTTPException(503, 'SETUP_TOKEN harus diset di Railway sebelum membuat admin')
    if expected and not hmac.compare_digest(str(data.get('setup_token') or ''), expected):
        raise HTTPException(403, 'Kode setup tidak cocok')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
            raise HTTPException(409, 'Admin pertama sudah dibuat')
        db.execute('INSERT INTO users(username,passhash,role) VALUES(?,?,?)', (username, passhash(password), 'admin'))
    return {'ok': True}


@app.post('/api/login')
async def login(request: Request):
    d = await request.json()
    with database() as db:
        record = db.execute('SELECT * FROM users WHERE username=?', (str(d.get('username') or ''),)).fetchone()
    if not record or not verify(str(d.get('password') or ''), record['passhash']):
        raise HTTPException(401, 'Username atau password salah')
    u = {k: record[k] for k in ('id', 'username', 'role')}
    resp = JSONResponse({'user': u})
    resp.set_cookie('session', SERIALIZER.dumps(u['id']), max_age=604800, httponly=True,
                    secure=SECURE_COOKIE, samesite='lax', path='/')
    return resp


@app.post('/api/logout')
def logout():
    response = JSONResponse({'ok': True})
    response.delete_cookie('session', path='/')
    return response


@app.get('/api/me')
def me(request: Request):
    return {'user': account(request)}


@app.get('/api/users')
def users(request: Request):
    require(request, ['admin'])
    with database() as db:
        rows = db.execute('SELECT id,username,role FROM users ORDER BY id').fetchall()
    return {'users': [dict(row) for row in rows]}


@app.post('/api/users')
async def new_user(request: Request):
    require(request, ['admin'])
    data = await request.json()
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    role = data.get('role')
    if role not in ('admin', 'senior', 'analyst') or not re.fullmatch(r'[A-Za-z0-9_.-]{3,36}', username) or len(password) < 12:
        raise HTTPException(400, 'Periksa username, role, dan password minimal 12 karakter')
    try:
        with database() as db:
            db.execute('INSERT INTO users(username,passhash,role) VALUES(?,?,?)', (username, passhash(password), role))
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Username sudah ada')
    return {'ok': True}


@app.post('/api/watermark')
async def watermark(request: Request, watermark: UploadFile = File(...)):
    require(request, ['admin'])
    await save_picture(watermark, 'watermark.png', alpha=True)
    return {'ok': True}


@app.post('/api/reports')
async def create_report(request: Request, instrument: str = Form(...), d1: UploadFile = File(...),
                        h4: UploadFile = File(...), h1: UploadFile = File(...),
                        m15: UploadFile | None = File(None)):
    user = require(request)
    if instrument not in INSTRUMENTS:
        raise HTTPException(400, 'Instrumen tidak didukung')
    rid = uuid.uuid4().hex[:12]
    uploaded = []
    try:
        for tf, file in (('d1', d1), ('h4', h4), ('h1', h1), ('m15', m15)):
            if file:
                filename = f'{rid}_{tf}.png'
                await save_picture(file, filename)
                uploaded.append((rid, tf, filename))
        with database() as db:
            db.execute('INSERT INTO reports(id,author_id,instrument,status,created,updated) VALUES(?,?,?,?,?,?)',
                       (rid, user['id'], instrument, 'uploaded', now(), now()))
            db.executemany('INSERT INTO report_files(report_id,timeframe,filename) VALUES(?,?,?)', uploaded)
    except Exception:
        for _, _, name in uploaded:
            store_path(name).unlink(missing_ok=True)
        raise
    return {'id': rid, 'status': 'uploaded', 'instrument': instrument}


@app.get('/api/market/{instrument}')
def market_quote(instrument: str, request: Request):
    require(request)
    try:
        return fetch_mt4_quote(instrument) if use_mt4() else fetch_quote(instrument)
    except MarketDataError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post('/api/reports/live')
async def create_live_report(request: Request):
    user = require(request)
    d = await request.json()
    instrument = str(d.get('instrument') or '').upper()
    if instrument not in INSTRUMENTS:
        raise HTTPException(400, 'Instrumen tidak didukung')
    if not live_configured():
        raise HTTPException(503, 'Live feed belum dikonfigurasi; cek LIVE_PROVIDER dan API token')
    rid=uuid.uuid4().hex[:12]
    with database() as db:
        db.execute('''INSERT INTO reports(id,author_id,instrument,status,created,updated,mode)
                      VALUES(?,?,?,?,?,?,?)''',
                   (rid,user['id'],instrument,'uploaded',now(),now(),'live'))
    return {'id':rid,'status':'uploaded','instrument':instrument,'mode':'live'}


@app.post('/api/reports/{rid}/generate')
def generate(rid: str, request: Request):
    user = require(request)
    r = get_report(rid, user)
    if r['status'] not in ('uploaded', 'revision', 'draft'):
        raise HTTPException(409, 'Laporan tidak dapat digenerate pada status ini')
    live_mode = r.get('mode') == 'live'
    if live_mode and not live_configured():
        raise HTTPException(503,'Live feed belum dikonfigurasi')
    if not live_mode and not os.getenv('GEMINI_API_KEY'):
        raise HTTPException(503, 'GEMINI_API_KEY belum dikonfigurasi')
    with database() as db:
        files = {item['timeframe']: store_path(item['filename']) for item in db.execute(
            'SELECT timeframe,filename FROM report_files WHERE report_id=?', (rid,))}
        modified = db.execute('UPDATE reports SET status=?,updated=? WHERE id=? AND status=?',
                              ('generating', now(), rid, r['status'])).rowcount
    if not modified:
        raise HTTPException(409, 'Laporan sedang diproses')
    try:
        if live_mode:
            snapshot=fetch_mt4_snapshot(r['instrument']) if use_mt4() else fetch_snapshot(r['instrument'])
            result=analyze_live(snapshot)
            result=enrich_with_gemini(result)
            # Generated chart source is deterministic and scale-calibrated.
            files['h1']=store_path(f'{rid}_h1.png')
            live_source_chart(snapshot,result,files['h1'])
            with database() as db:
                db.execute('''INSERT OR REPLACE INTO report_files(report_id,timeframe,filename)
                              VALUES(?,?,?)''',(rid,'h1',f'{rid}_h1.png'))
                db.execute('UPDATE reports SET market_snapshot=? WHERE id=?',
                           (json.dumps(snapshot,ensure_ascii=False),rid))
        else:
            raw = generate_analysis(r['instrument'], files)
            result = normalize_analysis(raw, r['instrument'])
        # The report is saved even if a graphic fails, with warnings displayed to the reviewer.
        try:
            annotated_chart(files['h1'], result, store_path('watermark.png'), store_path(f'{rid}_annotated.png'))
        except Exception:
            logging.exception('Chart renderer gagal')
            result['warnings'].append('Rendering chart gagal; mohon periksa file H1')
        try:
            infographic(result, store_path('watermark.png'), store_path(f'{rid}_infographic.png'))
        except Exception:
            logging.exception('Infografis gagal')
            result['warnings'].append('Rendering infografis gagal')
    except Exception as exc:
        logging.exception('Generate AI gagal')
        with database() as db:
            db.execute('UPDATE reports SET status=?,updated=? WHERE id=?', (r['status'], now(), rid))
        message = str(exc)
        if len(message) > 260:
            message = message[:260]
        raise HTTPException(502, f'AI belum berhasil: {message}')
    with database() as db:
        db.execute('UPDATE reports SET analysis=?,status=?,updated=?,version=version+1 WHERE id=?',
                   (json.dumps(result, ensure_ascii=False), 'draft', now(), rid))
    return {'ok': True, 'report': result}


@app.get('/api/reports')
def list_reports(request: Request):
    user = require(request)
    sql = ('SELECT r.id,r.instrument,r.status,r.created,r.updated,r.version,u.username AS author '
           'FROM reports AS r JOIN users AS u ON u.id=r.author_id')
    args = ()
    if user['role'] == 'analyst':
        sql += ' WHERE r.author_id=?'
        args = (user['id'],)
    sql += ' ORDER BY r.created DESC'
    with database() as db:
        reports = [dict(x) for x in db.execute(sql, args)]
    return {'reports': reports}


@app.get('/api/reports/{rid}')
def report_detail(rid: str, request: Request):
    r = get_report(rid, require(request))
    r['analysis'] = json.loads(r['analysis']) if r['analysis'] else None
    return {'report': r}


@app.post('/api/reports/{rid}/submit')
def submit(rid: str, request: Request):
    r = get_report(rid, require(request))
    if r['status'] != 'draft':
        raise HTTPException(409, 'Hanya draft yang dapat dikirim')
    data = json.loads(r['analysis'] or '{}')
    if not data:
        raise HTTPException(409, 'Laporan harus dianalisis dahulu')
    # Direction-only research may be reviewed, but cannot be presented as an executable order.
    # The review UI and exported files keep execution_ready/signal_validated separate.
    with database() as db:
        db.execute('UPDATE reports SET status=?,updated=? WHERE id=?', ('in_review', now(), rid))
    return {'ok': True}


@app.post('/api/reports/{rid}/review')
async def review(rid: str, request: Request):
    user = require(request, ['senior', 'admin'])
    r = get_report(rid, user)
    if r['status'] != 'in_review':
        raise HTTPException(409, 'Laporan belum dalam review')
    d = await request.json()
    action = d.get('action')
    feedback = str(d.get('feedback') or '').strip()[:2000]
    if action not in ('approve', 'revision', 'reject'):
        raise HTTPException(400, 'Aksi review tidak dikenal')
    if action != 'approve' and not feedback:
        raise HTTPException(400, 'Catatan revisi/penolakan wajib diisi')
    status = {'approve': 'approved', 'revision': 'revision', 'reject': 'rejected'}[action]
    with database() as db:
        db.execute('UPDATE reports SET status=?,feedback=?,reviewer_id=?,updated=? WHERE id=?',
                   (status, feedback, user['id'], now(), rid))
    if status == 'approved':
        # Rebuild graphic with approval badge; no AI call or change of numeric values.
        try:
            a = json.loads(r['analysis'])
            infographic(a, store_path('watermark.png'), store_path(f'{rid}_infographic.png'), approved=True)
        except Exception:
            logging.exception('Approved infographic refresh failed')
    return {'ok': True, 'status': status}


@app.get('/api/reports/{rid}/asset/{kind}')
def asset(rid: str, kind: str, request: Request):
    get_report(rid, require(request))
    if kind in ('annotated', 'infographic'):
        p = store_path(f'{rid}_{kind}.png')
    elif kind in ('d1', 'h4', 'h1', 'm15'):
        with database() as db:
            row = db.execute('SELECT filename FROM report_files WHERE report_id=? AND timeframe=?', (rid, kind)).fetchone()
        if not row:
            raise HTTPException(404, 'Chart tidak ditemukan')
        p = store_path(row['filename'])
    else:
        raise HTTPException(404, 'Jenis file tidak dikenal')
    if not p.exists():
        raise HTTPException(404, 'Gambar belum dibuat')
    return FileResponse(p, media_type='image/png', filename=f'{rid}_{kind}.png')


@app.get('/api/reports/{rid}/article')
def article(rid: str, request: Request):
    r = get_report(rid, require(request))
    if not r['analysis']:
        raise HTTPException(404, 'Analisis belum ada')
    p = store_path(f'{rid}_article.txt')
    p.write_text(report_text(json.loads(r['analysis']), rid, r['status']), encoding='utf-8')
    return FileResponse(p, media_type='text/plain; charset=utf-8', filename=f'{r["instrument"]}_{rid}.txt')


@app.get('/api/reports/{rid}/docx')
def docx(rid: str, request: Request):
    r = get_report(rid, require(request))
    if not r['analysis']:
        raise HTTPException(404, 'Analisis belum ada')
    p = create_document(rid, json.loads(r['analysis']), r['status'], STORAGE)
    return FileResponse(p, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        filename=f'{r["instrument"]}_Full_Research_{rid}.docx')
