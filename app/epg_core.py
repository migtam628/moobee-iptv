"""Bounded local XMLTV import, storage and Xtream/XMLTV responses.

No network fetches, external entities, or DTDs. Only admin-supplied XML is read.
"""
import base64
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

MAX_XML_BYTES = 1_000_000
MAX_CHANNELS = 400
MAX_PROGRAMMES = 5000
XMLTV_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DATE = re.compile(r"^(\d{12}(?:\d{2})?)\s+([+-]\d{4})$")
DTD = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS epg_channels (
        xmltv_id TEXT PRIMARY KEY, display_name TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS epg_programmes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, xmltv_id TEXT NOT NULL,
        start_ts INTEGER NOT NULL, stop_ts INTEGER NOT NULL,
        title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        UNIQUE(xmltv_id,start_ts,stop_ts))""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_epg_lookup
        ON epg_programmes(xmltv_id,stop_ts,start_ts)""")
    db.execute("""CREATE TABLE IF NOT EXISTS epg_channel_map (
        stream_id INTEGER PRIMARY KEY, xmltv_id TEXT NOT NULL)""")

def clean_text(element, max_chars):
    if element is None:
        return ""
    value = " ".join("".join(element.itertext()).split())
    if len(value) > max_chars:
        raise ValueError("XMLTV text field exceeds permitted length")
    return value

def parse_time(raw):
    match = DATE.fullmatch(raw.strip())
    if not match:
        raise ValueError("XMLTV times require an explicit timezone, e.g. 20261011200000 +0000")
    digits, offset = match.groups()
    if len(digits) == 12:
        digits += "00"
    try:
        parsed = datetime.strptime(digits + " " + offset, "%Y%m%d%H%M%S %z")
    except ValueError as exc:
        raise ValueError("Invalid XMLTV programme time") from exc
    if parsed.year < 2000 or parsed.year > 2100:
        raise ValueError("Programme year out of range")
    return int(parsed.timestamp())

def parse_xmltv(xml):
    raw = xml.encode("utf-8")
    if len(raw) > MAX_XML_BYTES:
        raise ValueError("XMLTV exceeds 1 MB limit")
    if DTD.search(raw):
        raise ValueError("DOCTYPE and ENTITY declarations are not allowed")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Malformed XMLTV document") from exc
    if root.tag != "tv":
        raise ValueError("Expected <tv> XMLTV document")

    channel_elements = root.findall("channel")
    programme_elements = root.findall("programme")
    if len(channel_elements) > MAX_CHANNELS or len(programme_elements) > MAX_PROGRAMMES:
        raise ValueError("XMLTV channel/programme limit exceeded")
    channels = []
    seen_ids = set()
    for node in channel_elements:
        xmltv_id = node.get("id", "")
        if not XMLTV_ID.fullmatch(xmltv_id) or xmltv_id in seen_ids:
            raise ValueError("Invalid or repeated XMLTV channel id")
        name = clean_text(node.find("display-name"), 120)
        if not name:
            raise ValueError("XMLTV channel display-name required")
        channels.append((xmltv_id, name))
        seen_ids.add(xmltv_id)
    if not channels:
        raise ValueError("XMLTV document contains no channels")

    programmes = []
    for node in programme_elements:
        xmltv_id = node.get("channel", "")
        if xmltv_id not in seen_ids:
            raise ValueError("Programme references an unknown XMLTV channel")
        start = parse_time(node.get("start", ""))
        stop = parse_time(node.get("stop", ""))
        if stop <= start or stop - start > 86400:
            raise ValueError("Invalid programme duration (maximum 24 hours)")
        title = clean_text(node.find("title"), 300)
        desc = clean_text(node.find("desc"), 2000)
        if not title:
            raise ValueError("Programme title required")
        programmes.append((xmltv_id, start, stop, title, desc))
    return channels, programmes

def get_mappings(db):
    ensure_schema(db)
    return {int(row["stream_id"]): row["xmltv_id"]
            for row in db.execute("SELECT stream_id, xmltv_id FROM epg_channel_map")}

def get_epg(db, stream_id, limit=10, now=None):
    ensure_schema(db)
    now = int(time.time()) if now is None else now
    limit = max(1, min(int(limit), 50))
    row = db.execute("SELECT xmltv_id FROM epg_channel_map WHERE stream_id=?", (stream_id,)).fetchone()
    if row is None:
        return []
    listings = db.execute("""SELECT id,xmltv_id,start_ts,stop_ts,title,description
        FROM epg_programmes WHERE xmltv_id=? AND stop_ts>? AND start_ts<?
        ORDER BY start_ts LIMIT ?""",
        (row["xmltv_id"], now, now + 86400, limit)).fetchall()
    def encoded(value):
        return base64.b64encode(value.encode("utf-8")).decode("ascii")
    def utc(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return [{
        "id":str(item["id"]), "epg_id":item["xmltv_id"], "channel_id":str(stream_id),
        "title":encoded(item["title"]), "description":encoded(item["description"]),
        "lang":"en", "start":utc(item["start_ts"]), "end":utc(item["stop_ts"]),
        "start_timestamp":str(item["start_ts"]), "stop_timestamp":str(item["stop_ts"]),
        "now_playing":1 if item["start_ts"] <= now < item["stop_ts"] else 0,
        "has_archive":0
    } for item in listings]

def render_xmltv(db, now=None):
    ensure_schema(db)
    now = int(time.time()) if now is None else now
    tv = ET.Element("tv", {"generator-info-name":"MooBee IPTV"})
    rows = db.execute("""SELECT DISTINCT m.xmltv_id, e.display_name
        FROM epg_channel_map AS m
        JOIN channels AS c ON c.id=m.stream_id
        JOIN epg_channels AS e ON e.xmltv_id=m.xmltv_id
        ORDER BY m.xmltv_id""").fetchall()
    ids = [r["xmltv_id"] for r in rows]
    for row in rows:
        channel = ET.SubElement(tv, "channel", {"id":row["xmltv_id"]})
        ET.SubElement(channel, "display-name").text = row["display_name"]
    def xml_time(ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d%H%M%S +0000")
    for xmltv_id in ids:
        programmes = db.execute("""SELECT start_ts,stop_ts,title,description
            FROM epg_programmes WHERE xmltv_id=? AND stop_ts>? AND start_ts<?
            ORDER BY start_ts LIMIT 5000""", (xmltv_id, now - 86400, now + 14*86400)).fetchall()
        for item in programmes:
            programme = ET.SubElement(tv, "programme", {
                "channel":xmltv_id, "start":xml_time(item["start_ts"]),
                "stop":xml_time(item["stop_ts"])})
            ET.SubElement(programme, "title").text = item["title"]
            if item["description"]:
                ET.SubElement(programme, "desc").text = item["description"]
    return ET.tostring(tv, encoding="utf-8", xml_declaration=True)
