"""VPN-only, Basic-authenticated management for local authorized VOD content."""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from admin_web import guard, audit
from main import connect
from vod_catalog import ensure_vod_schema, local_manifest, SAFE_FOLDER

router = APIRouter(prefix="/admin/api/vod")


class MovieInput(BaseModel):
    id: int = Field(ge=1, le=2147483647)
    name: str = Field(min_length=1, max_length=150)
    category: str = Field(min_length=1, max_length=80)
    folder: str = Field(min_length=1, max_length=80)
    poster: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=2000)
    release_year: int | None = Field(default=None, ge=1888, le=2100)


class SeriesInput(BaseModel):
    id: int = Field(ge=1, le=2147483647)
    name: str = Field(min_length=1, max_length=150)
    category: str = Field(min_length=1, max_length=80)
    poster: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=2000)


class EpisodeInput(BaseModel):
    id: int = Field(ge=1, le=2147483647)
    series_id: int = Field(ge=1, le=2147483647)
    season: int = Field(ge=1, le=500)
    episode: int = Field(ge=1, le=2000)
    name: str = Field(min_length=1, max_length=150)
    folder: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)


def check_poster(url):
    if url and (not url.startswith("https://") or any(x in url for x in "\r\n\t")):
        raise HTTPException(422,"Poster must be an HTTPS URL")


def check_media(folder):
    if not SAFE_FOLDER.fullmatch(folder):
        raise HTTPException(422,"Invalid media folder name")
    root = local_manifest(folder)
    index=(root/"index.m3u8").resolve()
    if not index.is_relative_to(root) or not index.is_file():
        raise HTTPException(422,"Authorized local HLS index.m3u8 is missing")


@router.get("")
def catalog(actor: str=Depends(guard)):
    with connect() as db:
        ensure_vod_schema(db)
        movies=[dict(r) for r in db.execute(
            "SELECT id,name,category,folder,poster,description,release_year FROM vod_movies ORDER BY id")]
        series=[dict(r) for r in db.execute(
            "SELECT id,name,category,poster,description FROM vod_series ORDER BY id")]
        episodes=[dict(r) for r in db.execute(
            "SELECT id,series_id,season,episode,name,folder,description FROM vod_episodes ORDER BY series_id,season,episode")]
    return {"movies":movies,"series":series,"episodes":episodes}


@router.post("/movies", status_code=201)
def add_movie(item:MovieInput, actor: str=Depends(guard)):
    check_poster(item.poster)
    check_media(item.folder)
    with connect() as db:
        ensure_vod_schema(db)
        try:
            db.execute("""INSERT INTO vod_movies
                (id,name,category,folder,poster,description,release_year)
                VALUES(?,?,?,?,?,?,?)""",
                (item.id,item.name,item.category,item.folder,item.poster,
                 item.description,item.release_year))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Movie ID already exists")
        audit(db,actor,"vod.movie.create",str(item.id))
        db.commit()
    return {"created":item.id}


@router.post("/series", status_code=201)
def add_series(item:SeriesInput, actor: str=Depends(guard)):
    check_poster(item.poster)
    with connect() as db:
        ensure_vod_schema(db)
        try:
            db.execute("""INSERT INTO vod_series
                (id,name,category,poster,description) VALUES(?,?,?,?,?)""",
                (item.id,item.name,item.category,item.poster,item.description))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Series ID already exists")
        audit(db,actor,"vod.series.create",str(item.id))
        db.commit()
    return {"created":item.id}


@router.post("/episodes", status_code=201)
def add_episode(item:EpisodeInput, actor: str=Depends(guard)):
    check_media(item.folder)
    with connect() as db:
        ensure_vod_schema(db)
        if not db.execute("SELECT 1 FROM vod_series WHERE id=?", (item.series_id,)).fetchone():
            raise HTTPException(404,"Series does not exist")
        try:
            db.execute("""INSERT INTO vod_episodes
                (id,series_id,season,episode,name,folder,description)
                VALUES(?,?,?,?,?,?,?)""",
                (item.id,item.series_id,item.season,item.episode,item.name,item.folder,item.description))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Episode ID or series season/episode already exists")
        audit(db,actor,"vod.episode.create",str(item.id))
        db.commit()
    return {"created":item.id}


@router.delete("/movies/{item_id}")
def remove_movie(item_id:int, actor: str=Depends(guard)):
    with connect() as db:
        ensure_vod_schema(db)
        changed=db.execute("DELETE FROM vod_movies WHERE id=?", (item_id,)).rowcount
        if not changed: raise HTTPException(404,"Movie does not exist")
        audit(db,actor,"vod.movie.delete",str(item_id))
        db.commit()
    return {"deleted":item_id}


@router.delete("/episodes/{item_id}")
def remove_episode(item_id:int, actor: str=Depends(guard)):
    with connect() as db:
        ensure_vod_schema(db)
        changed=db.execute("DELETE FROM vod_episodes WHERE id=?", (item_id,)).rowcount
        if not changed: raise HTTPException(404,"Episode does not exist")
        audit(db,actor,"vod.episode.delete",str(item_id))
        db.commit()
    return {"deleted":item_id}


@router.delete("/series/{item_id}")
def remove_series(item_id:int, actor: str=Depends(guard)):
    with connect() as db:
        ensure_vod_schema(db)
        changed=db.execute("DELETE FROM vod_series WHERE id=?", (item_id,)).rowcount
        if not changed: raise HTTPException(404,"Series does not exist")
        db.execute("DELETE FROM vod_episodes WHERE series_id=?", (item_id,))
        audit(db,actor,"vod.series.delete",str(item_id))
        db.commit()
    return {"deleted":item_id}
