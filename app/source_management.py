import re
import time
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from admin_web import guard, audit
from main import connect, MEDIA

router = APIRouter(prefix="/admin/api")
ATTR = re.compile(r'([\w-]+)="([^"]*)"')
FOLDER = re.compile(r'^[A-Za-z0-9_-]+$')

class ImportInput(BaseModel):
    playlist: str = Field(min_length=10, max_length=300000)
    dry_run: bool = True

def parse_local_m3u(playlist):
    if not playlist.lstrip().startswith("#EXTM3U"):
        raise ValueError("Expected #EXTM3U header")
    lines = [line.strip() for line in playlist.splitlines()]
    found = []
    meta = None
    for line in lines[1:]:
        if line.startswith("#EXTINF:"):
            attrs = dict(ATTR.findall(line))
            label = line.split(",", 1)[1].strip() if "," in line else ""
            meta = {"name": (attrs.get("tvg-name") or label)[:120],
                    "category": (attrs.get("group-title") or "Imported")[:80],
                    "logo": attrs.get("tvg-logo", "")[:500]}
            if not meta["name"]: raise ValueError("Channel name required")
        elif line and not line.startswith("#"):
            if meta is None: raise ValueError("Stream URL missing preceding #EXTINF")
            # Import only symbolic local folder references, never arbitrary remote URLs.
            if not line.startswith("moobee-local://"):
                raise ValueError("Only moobee-local://folder references are supported in this release")
            folder = line.removeprefix("moobee-local://")
            if not FOLDER.fullmatch(folder):
                raise ValueError("Invalid local media folder")
            if meta["logo"] and not meta["logo"].startswith("https://"):
                raise ValueError("Channel logo must use HTTPS")
            found.append({**meta, "folder": folder})
            meta = None
    if meta: raise ValueError("Trailing channel metadata without a local reference")
    if not found: raise ValueError("Playlist contains no channels")
    if len(found)>200: raise ValueError("Maximum 200 channels per import")
    return found

@router.post("/import-m3u")
def import_m3u(data: ImportInput, actor: str=Depends(guard)):
    try: channels = parse_local_m3u(data.playlist)
    except ValueError as exc: raise HTTPException(422, str(exc))
    if len({c["folder"] for c in channels}) != len(channels):
        raise HTTPException(422, "Duplicate media folders in import")
    for c in channels:
        if not (MEDIA / c["folder"] / "index.m3u8").is_file():
            raise HTTPException(422, "Local HLS media missing for folder: "+c["folder"])
    if data.dry_run:
        return {"dry_run":True,"count":len(channels),"channels":channels}
    with connect() as db:
        existing={r["folder"] for r in db.execute("SELECT folder FROM channels")}
        pending=[c for c in channels if c["folder"] not in existing]
        next_id=db.execute("SELECT COALESCE(MAX(id),0)+1 FROM channels").fetchone()[0]
        for c in pending:
            db.execute("INSERT INTO channels(id,name,category,folder,logo) VALUES(?,?,?,?,?)",
                       (next_id,c["name"],c["category"],c["folder"],c["logo"]))
            next_id+=1
        audit(db,actor,"channel.import",str(len(pending)))
        db.commit()
    return {"dry_run":False,"created":len(pending),"skipped_existing":len(channels)-len(pending)}

@router.get("/channel-health")
def channel_health(actor: str=Depends(guard)):
    with connect() as db:
        channels=[dict(x) for x in db.execute("SELECT id,name,folder FROM channels ORDER BY id")]
    result=[]
    for c in channels:
        manifest=MEDIA/c["folder"]/"index.m3u8"
        status="missing"
        if FOLDER.fullmatch(c["folder"]) and manifest.is_file():
            try:
                body=manifest.read_text(encoding="utf-8", errors="replace")[:4096]
                status="ready" if body.lstrip().startswith("#EXTM3U") else "invalid"
            except OSError:
                status="unreadable"
        result.append({**c,"status":status})
    return {"checked_at":int(time.time()),"channels":result,
            "note":"Checks local HLS manifest only; not player availability or external source uptime"}
