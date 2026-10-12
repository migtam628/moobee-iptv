"""Subscriber-authenticated proxy for MooBee Stream adaptive HLS.

Opt-in only. The Stream signed paths and service key stay on the server;
StreamBoss receives only MooBee IPTV URLs. No dynamic upstream hosts, redirects,
remote playlist URI tags, directory traversal or public Stream URLs.
"""
import os
import re
import time
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response

from stream_catalog import (
    _client, _get_json, canonical_uuid, configured_client, is_enabled
)

router = APIRouter()
MAX_MANIFEST = 256_000
MAX_SEGMENT = 16 * 1024 * 1024
RENDITION = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
SEGMENT = re.compile(r"segment_[0-9]{5}\.ts\Z")
SIGNATURE = re.compile(
    r"/hls/([0-9a-f-]{36})/([1-9][0-9]{9,11})/([A-Za-z0-9_-]{20,64})/master\.m3u8\Z"
)
MASTER_TAGS = frozenset({
    "#EXTM3U", "#EXT-X-VERSION", "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-STREAM-INF"
})
MEDIA_TAGS = frozenset({
    "#EXTM3U", "#EXT-X-VERSION", "#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE",
    "#EXT-X-PLAYLIST-TYPE", "#EXT-X-INDEPENDENT-SEGMENTS", "#EXTINF",
    "#EXT-X-ENDLIST", "#EXT-X-DISCONTINUITY"
})
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def gateway_enabled():
    return (is_enabled()
            and os.environ.get("MOOBEE_STREAM_PLAYBACK_ENABLED", "false").lower() == "true")


def mapped_uuid(kind, item_id):
    """Read only: existing local VOD row and explicit ready/enabled remote mapping."""
    import main
    if kind not in ("movie", "episode") or item_id < 1 or not gateway_enabled():
        return None
    with main.connect() as db:
        if not db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                             AND name='vod_external_sources'""").fetchone():
            return None
        table = "vod_movies" if kind == "movie" else "vod_episodes"
        if not db.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone():
            return None
        row = db.execute("""SELECT video_uuid FROM vod_external_sources
            WHERE kind=? AND item_id=? AND provider='moobee-stream'
            AND enabled=1 AND last_known_status='ready'""", (kind, item_id)).fetchone()
    if row is None:
        return None
    try:
        return canonical_uuid(row["video_uuid"])
    except ValueError:
        return None


def _signed_media(client, origin, service_key, video_uuid):
    # This resolver verifies the upstream video's *current* ready status.
    info = _get_json(
        client, origin, service_key, "/api/v1/videos/" + video_uuid + "/playback"
    )
    if not isinstance(info, dict) or info.get("id") != video_uuid:
        raise HTTPException(502, "Invalid private playback response")
    signed_path = info.get("stream")
    if not isinstance(signed_path, str):
        raise HTTPException(502, "Invalid private playback path")
    match = SIGNATURE.fullmatch(signed_path)
    if not match or match.group(1) != video_uuid:
        raise HTTPException(502, "Invalid private playback path")
    expires = info.get("expiresAt")
    if type(expires) is not int or expires != int(match.group(2)):
        raise HTTPException(502, "Invalid private playback expiration")
    if expires < int(time.time()) + 15 or expires > int(time.time()) + 86400:
        raise HTTPException(502, "Invalid private playback expiration")
    return signed_path.rsplit("/", 1)[0] + "/"


def _binary(client, origin, signed_prefix, asset, *, max_bytes):
    if not (asset == "master.m3u8" or
            re.fullmatch(r"[A-Za-z0-9_-]{1,40}/(?:index\.m3u8|segment_[0-9]{5}\.ts)",
                         asset)):
        raise HTTPException(400, "Unsupported private HLS asset")
    try:
        with client.stream(
            "GET", origin + signed_prefix + asset,
            follow_redirects=False,
            headers={"Accept": "application/vnd.apple.mpegurl, video/mp2t, application/octet-stream"}
        ) as response:
            if response.status_code != 200:
                raise HTTPException(502, "Private media request failed")
            if response.headers.get("Content-Encoding", "identity").lower() not in ("identity", ""):
                raise HTTPException(502, "Compressed private media not supported")
            body = bytearray()
            for chunk in response.iter_raw():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise HTTPException(502, "Private media exceeds response limit")
            return bytes(body)
    except HTTPException:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(502, "Private media service unavailable") from exc


def _lines(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(502, "Invalid private HLS text") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "#EXTM3U":
        raise HTTPException(502, "Invalid private HLS manifest")
    return lines


def rewrite_manifest(data, *, mode, username, password, kind, item_id, rendition=None):
    """Return only same-server playlist/segment URLs with subscriber credentials."""
    import main
    if kind not in ("movie", "episode") or mode not in ("master", "media"):
        raise HTTPException(400, "Invalid HLS mode")
    if mode == "media" and (not rendition or not RENDITION.fullmatch(rendition)):
        raise HTTPException(400, "Invalid HLS rendition")
    tags = MASTER_TAGS if mode == "master" else MEDIA_TAGS
    prefix = (f"{main.BASE}/stream-vod/{quote(username,safe='')}/"
              f"{quote(password,safe='')}/{kind}/{item_id}")
    rewritten, entries = [], 0
    for raw in _lines(data):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            tag = line.split(":", 1)[0]
            if tag not in tags or "URI=" in line.upper() or len(line) > 1024:
                raise HTTPException(502, "Unsupported private HLS directive")
            rewritten.append(line)
            continue
        if mode == "master":
            match = re.fullmatch(r"([A-Za-z0-9_-]{1,40})/index\.m3u8", line)
            if not match:
                raise HTTPException(502, "Unsafe private rendition reference")
            rewritten.append(f"{prefix}/{match.group(1)}/index.m3u8")
        else:
            if not SEGMENT.fullmatch(line):
                raise HTTPException(502, "Unsafe private segment reference")
            rewritten.append(f"{prefix}/{rendition}/{line}")
        entries += 1
    if not entries:
        raise HTTPException(502, "Empty private HLS manifest")
    if mode == "master" and not any(line.startswith("#EXT-X-STREAM-INF:") for line in rewritten):
        raise HTTPException(502, "Private master HLS lacks stream information")
    return "\n".join(rewritten) + "\n"


def _resolve(username, password, kind, item_id):
    import main
    main.auth(username, password)
    video_id = mapped_uuid(kind, item_id)
    if video_id is None:
        raise HTTPException(404, "Remote VOD not enabled or not mapped")
    return video_id


def manifest(username, password, kind, item_id, *, rendition=None):
    video_id = _resolve(username, password, kind, item_id)
    origin, key = configured_client()
    with _client() as client:
        signed = _signed_media(client, origin, key, video_id)
        asset = "master.m3u8" if rendition is None else f"{rendition}/index.m3u8"
        data = _binary(client, origin, signed, asset, max_bytes=MAX_MANIFEST)
    body = rewrite_manifest(
        data, mode="master" if rendition is None else "media",
        username=username, password=password, kind=kind, item_id=item_id,
        rendition=rendition
    )
    return PlainTextResponse(
        body, media_type="application/vnd.apple.mpegurl", headers=HEADERS)


@router.get("/stream-vod/{username}/{password}/{kind}/{item_id}/{rendition}/index.m3u8")
def rendition_manifest(username: str, password: str, kind: str, item_id: int,
                       rendition: str):
    return manifest(username, password, kind, item_id, rendition=rendition)


@router.get("/stream-vod/{username}/{password}/{kind}/{item_id}/{rendition}/{segment}")
def proxy_segment(username: str, password: str, kind: str, item_id: int,
                  rendition: str, segment: str):
    video_id = _resolve(username, password, kind, item_id)
    if not RENDITION.fullmatch(rendition) or not SEGMENT.fullmatch(segment):
        raise HTTPException(400, "Invalid media filename")
    origin, key = configured_client()
    with _client() as client:
        signed = _signed_media(client, origin, key, video_id)
        content = _binary(
            client, origin, signed, f"{rendition}/{segment}",
            max_bytes=MAX_SEGMENT
        )
    return Response(
        content, media_type="video/mp2t", headers=HEADERS)
