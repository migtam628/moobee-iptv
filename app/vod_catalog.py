"""Local, authorized VOD catalog and HLS playback for MooBee IPTV.

Deliberately no remote media fetches, transcoding, embeds or internal service credentials.
"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, FileResponse

SAFE_FOLDER = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:ts|m4s|mp4|aac|vtt)$")
VOD_ACTIONS = frozenset({
    "get_vod_categories", "get_vod_streams", "get_vod_info",
    "get_series_categories", "get_series", "get_series_info",
})
router = APIRouter()


def ensure_vod_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS vod_movies (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
        folder TEXT NOT NULL, poster TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '', release_year INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS vod_series (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
        poster TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS vod_episodes (
        id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL,
        season INTEGER NOT NULL, episode INTEGER NOT NULL,
        name TEXT NOT NULL, folder TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        UNIQUE(series_id, season, episode),
        FOREIGN KEY(series_id) REFERENCES vod_series(id))""")


def vod_action(db, action, category_id="", stream_id=0, vod_id=0, series_id=0):
    ensure_vod_schema(db)
    if action == "get_vod_categories":
        rows = db.execute("SELECT DISTINCT category FROM vod_movies ORDER BY category").fetchall()
        return [{"category_id":r["category"],"category_name":r["category"],"parent_id":0} for r in rows]
    if action == "get_series_categories":
        rows = db.execute("SELECT DISTINCT category FROM vod_series ORDER BY category").fetchall()
        return [{"category_id":r["category"],"category_name":r["category"],"parent_id":0} for r in rows]
    if action == "get_vod_streams":
        rows = db.execute("SELECT * FROM vod_movies WHERE (?='' OR category=?) ORDER BY id",
                          (category_id,category_id)).fetchall()
        return [dict(num=r["id"],name=r["name"],stream_type="movie",stream_id=r["id"],
                     stream_icon=r["poster"],rating="",added="0",
                     category_id=r["category"],container_extension="m3u8",
                     direct_source="") for r in rows]
    if action == "get_series":
        rows = db.execute("SELECT * FROM vod_series WHERE (?='' OR category=?) ORDER BY id",
                          (category_id,category_id)).fetchall()
        return [dict(num=r["id"],name=r["name"],series_id=r["id"],cover=r["poster"],
                     plot=r["description"],category_id=r["category"],last_modified="0") for r in rows]
    if action == "get_vod_info":
        target = vod_id or stream_id
        row = db.execute("SELECT * FROM vod_movies WHERE id=?", (target,)).fetchone()
        if not row: raise HTTPException(404,"Unknown movie")
        return {"info":{"name":row["name"],"movie_image":row["poster"],"plot":row["description"],
                        "releasedate":str(row["release_year"] or ""),"duration":""},
                "movie_data":{"stream_id":row["id"],"name":row["name"],
                              "container_extension":"m3u8"}}
    if action == "get_series_info":
        target = series_id or stream_id
        series = db.execute("SELECT * FROM vod_series WHERE id=?", (target,)).fetchone()
        if not series: raise HTTPException(404,"Unknown series")
        episodes = db.execute("SELECT * FROM vod_episodes WHERE series_id=? ORDER BY season,episode",
                              (target,)).fetchall()
        seasons = sorted({r["season"] for r in episodes})
        return {"info":{"name":series["name"],"cover":series["poster"],
                        "plot":series["description"]},
                "seasons":[{"season_number":s,"name":f"Season {s}"} for s in seasons],
                "episodes":{str(s):[{
                    "id":str(r["id"]),"episode_num":r["episode"],"title":r["name"],
                    "container_extension":"m3u8",
                    "info":{"plot":r["description"],"movie_image":series["poster"]},
                    "season":s} for r in episodes if r["season"] == s] for s in seasons}}
    raise HTTPException(400,"Unsupported VOD API action")


def local_manifest(folder):
    import main
    if not SAFE_FOLDER.fullmatch(folder):
        raise HTTPException(500,"Invalid stored media folder")
    root = main.MEDIA.resolve()
    media_folder = (root / folder).resolve()
    if not media_folder.is_relative_to(root):
        raise HTTPException(404,"Media not available")
    return media_folder


def vod_entry(kind, item_id):
    import main
    with main.connect() as db:
        ensure_vod_schema(db)
        if kind == "movie":
            row = db.execute("SELECT folder FROM vod_movies WHERE id=?", (item_id,)).fetchone()
        elif kind == "episode":
            row = db.execute("SELECT folder FROM vod_episodes WHERE id=?", (item_id,)).fetchone()
        else:
            raise HTTPException(404,"Unknown VOD type")
    if not row: raise HTTPException(404,"Unknown VOD item")
    return local_manifest(row["folder"])


def authenticated_vod(username, password, kind, item_id):
    import main
    main.auth(username,password)
    return vod_entry(kind,item_id)


def manifest(username, password, kind, item_id):
    import main
    media_folder = authenticated_vod(username,password,kind,item_id)
    file = (media_folder / "index.m3u8").resolve()
    if not file.is_relative_to(media_folder) or not file.is_file():
        raise HTTPException(404,"VOD HLS playlist unavailable")
    if file.stat().st_size > 256_000:
        raise HTTPException(422,"HLS playlist too large")
    lines=[]
    for line in file.read_text(encoding="utf-8").splitlines():
        value=line.strip()
        if value.startswith("#EXT-X-KEY") or value.startswith("#EXT-X-MAP"):
            raise HTTPException(501,"Encryption and init-map playlists unsupported")
        if not value or value.startswith("#"):
            lines.append(line)
        elif SAFE_SEGMENT.fullmatch(value):
            lines.append(f"{main.BASE}/vod-hls/{username}/{password}/{kind}/{item_id}/{value}")
        else:
            raise HTTPException(422,"Nested, absolute and remote HLS paths unsupported")
    if not lines or lines[0].strip() != "#EXTM3U":
        raise HTTPException(422,"Invalid HLS manifest")
    return PlainTextResponse("\n".join(lines)+"\n",
                             media_type="application/vnd.apple.mpegurl",
                             headers={"Cache-Control":"private, no-store"})


@router.get("/movie/{username}/{password}/{movie_id}.m3u8")
def play_movie(username:str,password:str,movie_id:int):
    return manifest(username,password,"movie",movie_id)


@router.get("/series/{username}/{password}/{episode_id}.m3u8")
def play_episode(username:str,password:str,episode_id:int):
    return manifest(username,password,"episode",episode_id)


@router.get("/vod-hls/{username}/{password}/{kind}/{item_id}/{segment}")
def vod_segment(username:str,password:str,kind:str,item_id:int,segment:str):
    if not SAFE_SEGMENT.fullmatch(segment) or ".." in segment:
        raise HTTPException(400,"Invalid segment name")
    folder = authenticated_vod(username,password,kind,item_id)
    item=(folder/segment).resolve()
    if not item.is_relative_to(folder):
        raise HTTPException(404,"Segment unavailable")
    if not item.is_file(): raise HTTPException(404,"Segment unavailable")
    content_type="video/mp2t" if segment.endswith(".ts") else "application/octet-stream"
    return FileResponse(item,media_type=content_type,
                        headers={"Cache-Control":"private, max-age=30"})
