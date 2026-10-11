"""Authenticated admin operations for local XMLTV import and channel mappings."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from main import connect
from admin_web import audit, guard
from epg_core import parse_xmltv, ensure_schema, MAX_XML_BYTES, XMLTV_ID

router = APIRouter(prefix="/admin/api/epg")

class ImportXMLTV(BaseModel):
    xml: str = Field(min_length=10, max_length=MAX_XML_BYTES)
    dry_run: bool = True

class ChannelMapping(BaseModel):
    xmltv_id: str | None = Field(default=None, max_length=128)

@router.post("/import")
def import_guide(item: ImportXMLTV, actor: str=Depends(guard)):
    try:
        channels, programmes = parse_xmltv(item.xml)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if item.dry_run:
        return {"dry_run":True,"channels":len(channels),"programmes":len(programmes),
                "preview":[{"xmltv_id":cid,"display_name":name} for cid,name in channels]}
    with connect() as db:
        ensure_schema(db)
        # Replace guide content atomically. A parser failure never erases old data.
        db.execute("DELETE FROM epg_programmes")
        db.execute("DELETE FROM epg_channels")
        db.executemany("INSERT INTO epg_channels(xmltv_id,display_name) VALUES(?,?)", channels)
        db.executemany("""INSERT OR REPLACE INTO epg_programmes
            (xmltv_id,start_ts,stop_ts,title,description) VALUES(?,?,?,?,?)""", programmes)
        audit(db,actor,"epg.import",str(len(programmes)))
        db.commit()
    return {"dry_run":False,"channels":len(channels),"programmes":len(programmes)}

@router.get("")
def guide_overview(actor: str=Depends(guard)):
    with connect() as db:
        ensure_schema(db)
        channels=[dict(r) for r in db.execute(
            "SELECT xmltv_id,display_name FROM epg_channels ORDER BY display_name")]
        mappings=[dict(r) for r in db.execute("""SELECT c.id AS stream_id,c.name AS stream_name,
            m.xmltv_id, e.display_name AS guide_name
            FROM channels AS c LEFT JOIN epg_channel_map AS m ON m.stream_id=c.id
            LEFT JOIN epg_channels AS e ON e.xmltv_id=m.xmltv_id ORDER BY c.id""")]
        programmes=db.execute("SELECT COUNT(*) FROM epg_programmes").fetchone()[0]
    return {"channels":channels,"mappings":mappings,"programmes":programmes}

@router.put("/mappings/{stream_id}")
def set_guide_mapping(stream_id: int, mapping: ChannelMapping, actor: str=Depends(guard)):
    if stream_id < 1:
        raise HTTPException(422,"Invalid stream ID")
    xmltv_id = mapping.xmltv_id
    if xmltv_id is not None and not XMLTV_ID.fullmatch(xmltv_id):
        raise HTTPException(422, "Invalid XMLTV channel ID")
    with connect() as db:
        ensure_schema(db)
        if db.execute("SELECT 1 FROM channels WHERE id=?", (stream_id,)).fetchone() is None:
            raise HTTPException(404,"Unknown stream")
        if xmltv_id is not None:
            if db.execute("SELECT 1 FROM epg_channels WHERE xmltv_id=?", (xmltv_id,)).fetchone() is None:
                raise HTTPException(404,"Unknown XMLTV channel")
            db.execute("""INSERT INTO epg_channel_map(stream_id,xmltv_id) VALUES(?,?)
                ON CONFLICT(stream_id) DO UPDATE SET xmltv_id=excluded.xmltv_id""",
                (stream_id, xmltv_id))
        else:
            db.execute("DELETE FROM epg_channel_map WHERE stream_id=?", (stream_id,))
        audit(db,actor,"epg.map",str(stream_id))
        db.commit()
    return {"stream_id":stream_id,"xmltv_id":xmltv_id}

@router.get("/programmes/{stream_id}")
def guide_programmes(stream_id: int, actor: str=Depends(guard)):
    with connect() as db:
        ensure_schema(db)
        if db.execute("SELECT 1 FROM channels WHERE id=?", (stream_id,)).fetchone() is None:
            raise HTTPException(404,"Unknown stream")
        rows=db.execute("""SELECT p.start_ts,p.stop_ts,p.title,p.description FROM epg_programmes p
            JOIN epg_channel_map m ON m.xmltv_id=p.xmltv_id
            WHERE m.stream_id=? ORDER BY p.start_ts LIMIT 100""", (stream_id,)).fetchall()
    return [dict(r) for r in rows]
