"""Admin-only, disabled-by-default MooBee Stream catalog adapter.

Only fixed private IPv4 or an explicitly pinned Tailscale HTTPS hostname is accepted.
Never fetches HLS, issues playback URLs, or modifies the live subscriber API.
"""
import ipaddress
import json
import math
import os
import re
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

MAX_PAGE_BYTES = 256 * 1024
MAX_PAGE_ITEMS = 100
MAX_PAGES = 10
VALID_STATUSES = frozenset({"uploaded", "queued", "processing", "ready", "failed", "disabled"})
PRIVATE_RANGES = tuple(ipaddress.ip_network(c) for c in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"
))


def is_enabled():
    return os.environ.get("MOOBEE_STREAM_ENABLED", "false").strip().lower() == "true"


TAILSCALE_HOST_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.ts\.net\Z"
)


def fixed_private_origin(raw):
    """Restrict upstream to private IPv4 or an exact opt-in Tailscale HTTPS name.

    HTTPS name is validated by the regular TLS stack at request time. The
    operator must configure the exact expected hostname; arbitrary DNS names
    and public origins are never accepted.
    """
    if not isinstance(raw, str) or not raw or raw != raw.strip() or any(c in raw for c in "\\%@"):
        raise ValueError("Invalid private upstream origin")
    try:
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("Invalid scheme")
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise ValueError("Origin must have no credentials, path or query")
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError("Invalid port")
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid private upstream origin") from exc

    try:
        address = ipaddress.IPv4Address(parts.hostname)
    except ipaddress.AddressValueError:
        # No DNS-based configuration is permitted unless the operator opts in
        # to one exact Tailscale Serve hostname on HTTPS port 443.
        allowed = os.environ.get("MOOBEE_STREAM_TAILSCALE_HOSTNAME", "")
        if not (TAILSCALE_HOST_PATTERN.fullmatch(allowed)
                and parts.hostname == allowed
                and parts.scheme == "https"
                and port == 443
                and parts.netloc in (allowed, allowed + ":443")):
            raise ValueError("Untrusted private upstream hostname")
        return f"https://{allowed}:443"

    if address != ipaddress.IPv4Address("127.0.0.1") and not any(
        address in network for network in PRIVATE_RANGES
    ):
        raise ValueError("Upstream must be private IPv4")
    if address != ipaddress.IPv4Address("127.0.0.1") and parts.scheme != "https":
        raise ValueError("TLS required between hosts")
    return f"{parts.scheme}://{address}:{port}"


def configured_client():
    if not is_enabled():
        raise HTTPException(503, "MooBee Stream connector is disabled")
    raw = os.environ.get("MOOBEE_STREAM_BASE_URL", "")
    key = os.environ.get("MOOBEE_STREAM_SERVICE_KEY", "")
    if len(key.encode("utf-8")) < 32:
        raise HTTPException(503, "MooBee Stream connector is not configured")
    try:
        origin = fixed_private_origin(raw)
    except ValueError as exc:
        raise HTTPException(503, "MooBee Stream private origin is not configured") from exc
    return origin, key


def canonical_uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("Invalid video UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Invalid video UUID") from exc
    if str(parsed) != value.lower():
        raise ValueError("Invalid video UUID")
    return str(parsed)


def _safe_number(value, maximum):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid numeric metadata")
    try:
        if not math.isfinite(value) or value < 0 or value > maximum:
            raise ValueError("Invalid numeric metadata")
    except (OverflowError, ValueError) as exc:
        raise ValueError("Invalid numeric metadata") from exc
    return value


def normalize_video(row):
    if not isinstance(row, dict):
        raise ValueError("Malformed catalog record")
    video_id = canonical_uuid(row.get("id"))
    media_type, status, title = row.get("mediaType"), row.get("status"), row.get("title")
    if media_type not in ("movie", "tv", "video") or status not in VALID_STATUSES:
        raise ValueError("Unsupported upstream catalog record")
    if not isinstance(title, str) or not (1 <= len(title.strip()) <= 200):
        raise ValueError("Invalid upstream video title")
    season, episode = row.get("season"), row.get("episode")
    for value in (season, episode):
        if value is not None and (type(value) is not int or not 1 <= value <= 9999):
            raise ValueError("Invalid episode metadata")
    if media_type == "tv" and (season is None or episode is None):
        raise ValueError("TV episode metadata is incomplete")
    tmdb_id = row.get("tmdbId")
    if tmdb_id is not None and (not isinstance(tmdb_id, str) or len(tmdb_id) > 60):
        raise ValueError("Invalid upstream catalog ID")
    return {
        "video_id": video_id, "title": title.strip(), "media_type": media_type,
        "status": status, "season": season, "episode": episode, "tmdb_id": tmdb_id,
        "duration_seconds": _safe_number(row.get("durationSeconds"), 604800),
        "width": _safe_number(row.get("width"), 16384),
        "height": _safe_number(row.get("height"), 16384),
    }


def _get_json(client, origin, key, suffix):
    try:
        with client.stream(
            "GET", origin + suffix,
            headers={"x-service-key": key, "Accept": "application/json"},
            follow_redirects=False,
        ) as response:
            if response.status_code in (401, 403):
                raise HTTPException(502, "MooBee Stream service access denied")
            if response.status_code == 404:
                raise HTTPException(404, "Upstream video not found")
            if response.status_code != 200:
                raise HTTPException(502, "MooBee Stream catalog request failed")
            if "application/json" not in response.headers.get("content-type", "").lower():
                raise HTTPException(502, "MooBee Stream returned a non-JSON response")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_PAGE_BYTES:
                    raise HTTPException(502, "MooBee Stream catalog response too large")
    except HTTPException:
        raise
    except (httpx.RequestError, httpx.HTTPError, OSError) as exc:
        raise HTTPException(502, "MooBee Stream private catalog unavailable") from exc
    try:
        return json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(502, "MooBee Stream returned invalid JSON") from exc


def _client():
    return httpx.Client(
        trust_env=False, follow_redirects=False, verify=True,
        timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
    )


def fetch_catalog(max_pages=MAX_PAGES, client_factory=None):
    origin, key = configured_client()
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise HTTPException(422, "Invalid page limit")
    factory = client_factory or _client
    videos, cursor, seen = [], None, set()
    try:
        with factory() as client:
            for page_number in range(1, max_pages + 1):
                path = "/api/v1/internal/videos"
                if cursor:
                    path += "?cursor=" + cursor
                response = _get_json(client, origin, key, path)
                if not isinstance(response, dict) or not isinstance(response.get("videos"), list):
                    raise ValueError("Invalid paginated catalog")
                items = response["videos"]
                if len(items) > MAX_PAGE_ITEMS:
                    raise ValueError("Catalog page limit exceeded")
                batch = [normalize_video(item) for item in items]
                for item in batch:
                    if item["video_id"] in seen:
                        raise ValueError("Duplicate catalog video")
                    seen.add(item["video_id"])
                videos.extend(batch)
                next_cursor = response.get("nextCursor")
                if next_cursor is None:
                    return {"videos": videos, "count": len(videos), "complete": True,
                            "next_cursor": None, "pages": page_number}
                next_cursor = canonical_uuid(next_cursor)
                if not batch or batch[-1]["video_id"] != next_cursor or next_cursor == cursor:
                    raise ValueError("Catalog pagination cursor mismatch")
                cursor = next_cursor
    except ValueError as exc:
        raise HTTPException(502, "MooBee Stream returned invalid catalog metadata") from exc
    return {"videos": videos, "count": len(videos), "complete": False,
            "next_cursor": cursor, "pages": max_pages}


def fetch_video(video_uuid, client_factory=None):
    origin, key = configured_client()
    try:
        canonical = canonical_uuid(video_uuid)
    except ValueError as exc:
        raise HTTPException(422, "Invalid video UUID") from exc
    factory = client_factory or _client
    try:
        with factory() as client:
            data = _get_json(client, origin, key, "/api/v1/internal/videos/" + canonical)
        return normalize_video(data)
    except ValueError as exc:
        raise HTTPException(502, "MooBee Stream returned invalid video metadata") from exc


def ensure_mapping_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS vod_external_sources (
        kind TEXT NOT NULL CHECK(kind IN ('movie', 'episode')),
        item_id INTEGER NOT NULL CHECK(item_id > 0),
        provider TEXT NOT NULL DEFAULT 'moobee-stream'
            CHECK(provider='moobee-stream'),
        video_uuid TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        last_known_status TEXT NOT NULL DEFAULT 'unknown',
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(kind,item_id),
        UNIQUE(provider,video_uuid))""")


def mapping_rows(db):
    if not db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                         AND name='vod_external_sources'""").fetchone():
        return []
    rows = db.execute("""SELECT kind,item_id,video_uuid,enabled,
        last_known_status,updated_at
        FROM vod_external_sources ORDER BY kind,item_id""").fetchall()
    return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]


def clear_mapping_if_present(db, kind, item_id):
    """Keep mappings tidy when an ordinary VOD catalog entry is deleted."""
    if db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                     AND name='vod_external_sources'""").fetchone():
        db.execute("DELETE FROM vod_external_sources WHERE kind=? AND item_id=?",
                   (kind, item_id))
