"""VPN-only MooBee Stream import preview and explicit UUID mapping.

Feature-disabled by default. Never changes the subscriber API, HLS
routing, movie/episode folder paths, or the remote Stream service.
"""
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

from admin_web import guard, audit
from main import connect
import main
from vod_catalog import ensure_vod_schema
from stream_catalog import (
    is_enabled, configured_client, fetch_catalog, fetch_video,
    ensure_mapping_schema, mapping_rows,
)

router = APIRouter(prefix="/admin/api/integrations/moobee-stream")


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_pages: int = Field(default=3, ge=1, le=10)


class MappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: str = Field(min_length=36, max_length=36)
    enabled: bool = False


@router.get("/status")
def integration_status(actor: str = Depends(guard)):
    """No remote call and no credentials/origin disclosed."""
    return {
        "enabled": is_enabled(),
        "configured": _configured(),
        "playback_supported": False,
        "catalog_write_mode": "manual-mapping-only",
    }


def _configured():
    if not is_enabled():
        return False
    try:
        configured_client()
        return True
    except HTTPException:
        return False


@router.post("/preview")
def import_preview(item: PreviewRequest, actor: str = Depends(guard)):
    """Pure metadata preview: no CREATE TABLE, INSERT, UPDATE or DELETE."""
    result = fetch_catalog(max_pages=item.max_pages)
    # Explicitly read-only: no table creation or journal-mode changes in preview.
    mapped = {}
    if main.DB.is_file():
        with sqlite3.connect("file:" + str(main.DB) + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            existing_table = db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='vod_external_sources'""").fetchone()
            if existing_table:
                mapped = {row["video_uuid"]: (row["kind"], row["item_id"])
                          for row in db.execute(
                              "SELECT kind,item_id,video_uuid FROM vod_external_sources")}
    for record in result["videos"]:
        link = mapped.get(record["video_id"])
        record["mapping"] = ({"kind": link[0], "item_id": link[1]}
                             if link else None)
    return result


@router.get("/mappings")
def list_mappings(actor: str = Depends(guard)):
    with connect() as db:
        return {"mappings": mapping_rows(db), "playback_supported": False}


@router.put("/mappings/{kind}/{item_id}")
def assign_mapping(kind: Literal["movie", "episode"], item_id: int,
                   item: MappingRequest, actor: str = Depends(guard)):
    if item_id < 1:
        raise HTTPException(422, "Invalid catalog item ID")
    # Always verify upstream metadata first; local mapping never accepts a URL.
    video = fetch_video(item.video_id)
    expected_media = "movie" if kind == "movie" else "tv"
    if video["media_type"] != expected_media:
        raise HTTPException(422, "Video media type does not match catalog item")
    if item.enabled and video["status"] != "ready":
        raise HTTPException(422, "Only ready videos can be enabled for future integration")
    with connect() as db:
        ensure_vod_schema(db)
        table = "vod_movies" if kind == "movie" else "vod_episodes"
        if db.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone() is None:
            raise HTTPException(404, "Local movie or episode does not exist")
        ensure_mapping_schema(db)
        try:
            db.execute("""INSERT INTO vod_external_sources
                (kind,item_id,provider,video_uuid,enabled,last_known_status,updated_at)
                VALUES(?,?,'moobee-stream',?,?,?,?)
                ON CONFLICT(kind,item_id) DO UPDATE SET
                    video_uuid=excluded.video_uuid,
                    enabled=excluded.enabled,
                    last_known_status=excluded.last_known_status,
                    updated_at=excluded.updated_at""",
                (kind,item_id,video["video_id"],int(item.enabled),video["status"],int(time.time())))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Video UUID is already mapped to another catalog item") from exc
        audit(db, actor, "stream.mapping.upsert", f"{kind}:{item_id}")
        db.commit()
    return {
        "kind": kind, "item_id": item_id, "video_id": video["video_id"],
        "enabled": item.enabled, "upstream_status": video["status"],
        "playback_supported": False,
    }


@router.delete("/mappings/{kind}/{item_id}")
def delete_mapping(kind: Literal["movie", "episode"], item_id: int,
                   actor: str = Depends(guard)):
    if item_id < 1:
        raise HTTPException(422, "Invalid catalog item ID")
    with connect() as db:
        if not db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                             AND name='vod_external_sources'""").fetchone():
            raise HTTPException(404, "Mapping does not exist")
        deleted = db.execute("DELETE FROM vod_external_sources WHERE kind=? AND item_id=?",
                             (kind,item_id)).rowcount
        if not deleted:
            raise HTTPException(404, "Mapping does not exist")
        audit(db, actor, "stream.mapping.delete", f"{kind}:{item_id}")
        db.commit()
    return {"deleted": True, "kind": kind, "item_id": item_id}
