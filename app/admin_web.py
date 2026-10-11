import os
import re
import time
import hmac
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from main import connect, digest

router = APIRouter(prefix="/admin")
basic = HTTPBasic(auto_error=False)
SAFE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
FOLDER = re.compile(r"^[A-Za-z0-9_-]+$")

def guard(credentials: HTTPBasicCredentials | None = Depends(basic)):
    expected_user = os.environ.get("ADMIN_USERNAME", "admin")
    expected_password = os.environ.get("ADMIN_PASSWORD", "")
    if not expected_password or len(expected_password) < 20:
        raise HTTPException(503, "Admin password not configured (minimum 20 characters)")
    if not credentials or not (hmac.compare_digest(credentials.username.encode(), expected_user.encode())
                               and hmac.compare_digest(credentials.password.encode(), expected_password.encode())):
        raise HTTPException(401, "Admin authentication required", headers={"WWW-Authenticate": 'Basic realm="MooBee Admin"'})
    return credentials.username

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str | None = Field(default=None, max_length=256)

class UserState(BaseModel):
    disabled: bool

class ChannelInput(BaseModel):
    id: int = Field(ge=1, le=2147483647)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    folder: str = Field(min_length=1, max_length=80)
    logo: str = Field(default="", max_length=500)

def audit(db, actor, action, subject):
    db.execute("CREATE TABLE IF NOT EXISTS admin_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL)")
    db.execute("INSERT INTO admin_audit(ts,actor,action,subject) VALUES(?,?,?,?)", (int(time.time()),actor,action,subject))

@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, actor: str = Depends(guard)):
    page = Path(__file__).with_name("admin.html").read_text(encoding="utf-8")
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

@router.get("/api/overview")
def overview(actor: str = Depends(guard)):
    with connect() as db:
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = db.execute("SELECT COUNT(*) FROM users WHERE disabled=0").fetchone()[0]
        channels = db.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        db.execute("CREATE TABLE IF NOT EXISTS admin_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL)")
        recent = [dict(row) for row in db.execute("SELECT ts,actor,action,subject FROM admin_audit ORDER BY id DESC LIMIT 10")]
    return {"users":users,"active_users":active,"channels":channels,"recent":recent,"playback_sessions":"not tracked in v0.2"}

@router.get("/api/users")
def users(actor: str = Depends(guard)):
    with connect() as db:
        return [dict(x) for x in db.execute("SELECT username,created_at,disabled FROM users ORDER BY username")]

@router.post("/api/users", status_code=201)
def create_user(item: UserCreate, actor: str = Depends(guard)):
    if not SAFE.fullmatch(item.username):
        raise HTTPException(422, "Username must use 3-40 letters, numbers, underscore or hyphen")
    password = item.password or secrets.token_urlsafe(24)
    if len(password) < 16:
        raise HTTPException(422, "Password must be at least 16 characters")
    salt = secrets.token_hex(16)
    with connect() as db:
        if db.execute("SELECT 1 FROM users WHERE username=?", (item.username,)).fetchone():
            raise HTTPException(409, "User already exists")
        db.execute("INSERT INTO users(username,salt,password_hash,created_at,disabled) VALUES(?,?,?,?,0)",
                   (item.username,salt,digest(password,salt),int(time.time())))
        audit(db, actor, "user.create", item.username)
        db.commit()
    return {"username":item.username,"password":password}

@router.patch("/api/users/{username}")
def set_user_state(username: str, item: UserState, actor: str = Depends(guard)):
    with connect() as db:
        result=db.execute("UPDATE users SET disabled=? WHERE username=?", (int(item.disabled),username))
        if result.rowcount == 0: raise HTTPException(404, "User not found")
        audit(db, actor, "user.disable" if item.disabled else "user.enable", username)
        db.commit()
    return {"username":username,"disabled":item.disabled}

@router.get("/api/channels")
def channels(actor: str = Depends(guard)):
    with connect() as db:
        return [dict(x) for x in db.execute("SELECT id,name,category,folder,logo FROM channels ORDER BY id")]

@router.post("/api/channels", status_code=201)
def create_channel(item: ChannelInput, actor: str = Depends(guard)):
    if not FOLDER.fullmatch(item.folder): raise HTTPException(422, "Invalid media folder")
    if item.logo and not item.logo.startswith("https://"): raise HTTPException(422,"Logo must be HTTPS")
    with connect() as db:
        if db.execute("SELECT 1 FROM channels WHERE id=?", (item.id,)).fetchone(): raise HTTPException(409,"Channel ID already exists")
        db.execute("INSERT INTO channels(id,name,category,folder,logo) VALUES(?,?,?,?,?)",
                   (item.id,item.name,item.category,item.folder,item.logo))
        audit(db,actor,"channel.create",str(item.id))
        db.commit()
    return item

@router.put("/api/channels/{stream_id}")
def update_channel(stream_id: int,item: ChannelInput,actor: str=Depends(guard)):
    if item.id != stream_id: raise HTTPException(422,"Channel ID cannot be changed")
    if not FOLDER.fullmatch(item.folder): raise HTTPException(422, "Invalid media folder")
    if item.logo and not item.logo.startswith("https://"): raise HTTPException(422,"Logo must be HTTPS")
    with connect() as db:
        result=db.execute("UPDATE channels SET name=?,category=?,folder=?,logo=? WHERE id=?",
                          (item.name,item.category,item.folder,item.logo,stream_id))
        if result.rowcount == 0: raise HTTPException(404,"Channel not found")
        audit(db,actor,"channel.update",str(stream_id))
        db.commit()
    return item

@router.delete("/api/channels/{stream_id}")
def delete_channel(stream_id:int,actor:str=Depends(guard)):
    with connect() as db:
        result=db.execute("DELETE FROM channels WHERE id=?", (stream_id,))
        if result.rowcount == 0: raise HTTPException(404,"Channel not found")
        audit(db,actor,"channel.delete",str(stream_id))
        db.commit()
    return {"deleted":stream_id}
