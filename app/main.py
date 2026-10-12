import os, sqlite3, hashlib, hmac, secrets, time, re, html, json
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse, FileResponse, HTMLResponse, Response
from epg_core import get_mappings, get_epg, render_xmltv
from vod_catalog import vod_action, VOD_ACTIONS, router as vod_router

DATA = Path('/data')
DB = DATA / 'server.sqlite3'
MEDIA = Path('/media')
BASE = os.environ.get('PUBLIC_URL', 'https://tv.example.com').rstrip('/')
app = FastAPI(title='Private Xtream-compatible Server', docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(vod_router)

def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL, disabled INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, folder TEXT NOT NULL, logo TEXT NOT NULL DEFAULT "")')
    return db

def digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

def auth(username, password):
    if not username or not password: raise HTTPException(401, 'Invalid credentials')
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    if not row or row['disabled'] or not hmac.compare_digest(row['password_hash'], digest(password, row['salt'])):
        raise HTTPException(401, 'Invalid credentials')
    return row

def channel(stream_id):
    with connect() as db:
        row=db.execute('SELECT * FROM channels WHERE id=?',(stream_id,)).fetchone()
    if not row: raise HTTPException(404, 'Unknown stream')
    return row

def stream_data(c, epg_id=''):
    return {'num':c['id'], 'name':c['name'], 'stream_type':'live', 'stream_id':c['id'], 'stream_icon':c['logo'], 'epg_channel_id':epg_id, 'added':'0', 'category_id':c['category'], 'custom_sid':'', 'tv_archive':0, 'direct_source':''}

@app.get('/healthz')
def health(): return {'status':'ok'}

@app.get('/', response_class=HTMLResponse)
def home():
    return '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Private TV</title></head><body style="background:#101827;color:#eff6ff;font:16px system-ui;padding:40px;max-width:660px;margin:auto"><h1>Private TV Server</h1><p>Self-hosted Xtream-compatible API is running.</p><p>Configure your client with this server URL and the credentials created through the server CLI.</p><p>Only authorized media is supported.</p></body></html>'

@app.get('/player_api.php')
def player_api(request:Request, username:str='', password:str='', action:str='', category_id:str='', stream_id:int=0, limit:int=10, vod_id:int=0, series_id:int=0):
    u=auth(username,password)
    with connect() as db:
        channels=db.execute('SELECT * FROM channels ORDER BY id').fetchall()
        mappings=get_mappings(db)
    categories=sorted(set(c['category'] for c in channels))
    if action=='get_live_categories': return [{'category_id':x,'category_name':x,'parent_id':0} for x in categories]
    if action=='get_live_streams': return [stream_data(c,mappings.get(c['id'],'')) for c in channels if not category_id or c['category']==category_id]
    if action in VOD_ACTIONS:
        with connect() as db:
            return vod_action(db,action,category_id,stream_id,vod_id,series_id)
    if action in ('get_short_epg','get_simple_data_table'):
        if stream_id < 1: return {'epg_listings':[]}
        with connect() as db: listings=get_epg(db,stream_id,limit)
        return {'epg_listings':listings}
    if action: raise HTTPException(400,'Unsupported API action')
    now=datetime.now(timezone.utc)
    host=request.url.hostname or 'localhost'
    return {'user_info':{'username':username,'password':password,'message':'Private TV','auth':1,'status':'Active','exp_date':None,'is_trial':'0','active_cons':'0','created_at':str(u['created_at']),'max_connections':'1','allowed_output_formats':['m3u8']},'server_info':{'url':host,'port':'443','https_port':'443','server_protocol':'https','rtmp_port':'0','timezone':'UTC','timestamp_now':int(time.time()),'time_now':now.strftime('%Y-%m-%d %H:%M:%S')}}

@app.get('/get.php', response_class=PlainTextResponse)
def get_playlist(username:str='', password:str='', type:str='m3u_plus', output:str='m3u8'):
    auth(username,password)
    if output!='m3u8': raise HTTPException(400,'Only HLS/m3u8 output is supported')
    with connect() as db:
        channels=db.execute('SELECT * FROM channels ORDER BY id').fetchall()
        mappings=get_mappings(db)
    lines=['#EXTM3U']
    for c in channels:
        safe_name=c['name'].replace('\n',' ')
        epg_id=mappings.get(c['id'],'')
        lines.append(f'#EXTINF:-1 tvg-id="{epg_id}" tvg-name="{safe_name}" tvg-logo="{c["logo"]}" group-title="{c["category"]}",{safe_name}')
        lines.append(f'{BASE}/live/{username}/{password}/{c["id"]}.m3u8')
    return '\n'.join(lines)+'\n'

@app.get('/xmltv.php')
def xmltv(username:str='',password:str=''):
    auth(username,password)
    with connect() as db: content=render_xmltv(db)
    return Response(content=content,media_type='application/xml',headers={'Cache-Control':'private, no-store'})

SAFE_SEGMENT=re.compile(r'^[A-Za-z0-9_.-]+\.(ts|m4s|mp4|aac|vtt)$')

def folder_for(c):
    folder=c['folder']
    if not re.fullmatch(r'[A-Za-z0-9_-]+',folder): raise HTTPException(500,'Invalid media folder')
    return MEDIA/folder

@app.get('/live/{username}/{password}/{stream_id}.m3u8')
def live(username:str,password:str,stream_id:int):
    auth(username,password)
    c=channel(stream_id)
    file=folder_for(c)/'index.m3u8'
    if not file.is_file(): raise HTTPException(404,'Media not available')
    lines=[]
    for line in file.read_text().splitlines():
        entry=line.strip()
        if not entry or entry.startswith('#'):
            if entry.startswith('#EXT-X-KEY') or entry.startswith('#EXT-X-MAP'):
                raise HTTPException(501, 'Encrypted HLS and map directives unsupported in MVP')
            lines.append(line)
        elif SAFE_SEGMENT.fullmatch(entry):
            lines.append(f'{BASE}/hls/{username}/{password}/{stream_id}/{entry}')
        else: raise HTTPException(400,'Unsafe or unsupported HLS entry')
    return PlainTextResponse('\n'.join(lines)+'\n',media_type='application/vnd.apple.mpegurl',headers={'Cache-Control':'no-store'})

@app.get('/hls/{username}/{password}/{stream_id}/{segment}')
def hls(username:str,password:str,stream_id:int,segment:str):
    auth(username,password)
    if not SAFE_SEGMENT.fullmatch(segment) or segment.startswith('.') or '..' in segment: raise HTTPException(400,'Invalid segment')
    c=channel(stream_id)
    file=folder_for(c)/segment
    if not file.is_file(): raise HTTPException(404,'Segment not found')
    kind='video/mp2t' if segment.endswith('.ts') else 'application/octet-stream'
    return FileResponse(file,media_type=kind,headers={'Cache-Control':'private, max-age=30'})

@app.get('/live/{username}/{password}/{stream_id}.ts')
def unsupported_ts(username:str,password:str,stream_id:int):
    auth(username,password)
    raise HTTPException(501,'Raw MPEG-TS output unavailable; select HLS/m3u8')

# Optional private admin panel (Basic auth enabled only with a strong secret).
from admin_web import router as admin_router
app.include_router(admin_router)

# Public, read-only client feature discovery (no secrets).
from capabilities import router as capabilities_router
app.include_router(capabilities_router)
from source_management import router as source_management_router
app.include_router(source_management_router)

# Private XMLTV import/mapping routes. No remote source fetching.
from admin_epg import router as epg_admin_router
app.include_router(epg_admin_router)

# Private VOD catalog control; subscriber playback stays on public TV domain.
from admin_vod import router as admin_vod_router
app.include_router(admin_vod_router)

# Disabled-by-default private MooBee Stream catalog adapter. No subscriber routes.
from admin_stream_integration import router as stream_catalog_admin_router
app.include_router(stream_catalog_admin_router)

# Feature-flagged StreamBoss remote HLS gateway; existing local VOD is the default.
from stream_gateway import router as private_hls_router
app.include_router(private_hls_router)
