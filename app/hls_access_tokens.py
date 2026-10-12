"""Stateless, signed, expiring HLS bearer tokens for private remote VOD.

The Xtream *entry* URL still authenticates with username/password. From the
returned master manifest onward, URLs contain only a signed scoped bearer
token, never the password. Tokens are replayable until expiry, must be kept
out of logs, and are checked against current subscriber and mapping state
on EVERY rendition/segment request.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from fastapi import HTTPException

VERSION = 1
TOKEN_TTL_SECONDS = 4 * 60 * 60
CLOCK_SKEW_SECONDS = 60
MAX_TOKEN_CHARS = 768
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{80,700}\.[A-Za-z0-9_-]{43}\Z")
USER_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,40}\Z")


def secret_key():
    raw = os.environ.get("MOOBEE_STREAM_HLS_TOKEN_SECRET", "").encode("utf-8")
    if len(raw) < 32:
        raise HTTPException(503, "Private HLS token signing is not configured")
    return raw


def _b64(payload):
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode(payload):
    try:
        return base64.b64decode(
            payload + "=" * (-len(payload) % 4),
            altchars=b"-_", validate=True
        )
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(401, "Invalid HLS access token") from exc


def _binding(row, key):
    # Account-derived, keyed fingerprint binds tokens to a specific generation
    # of the subscriber credentials. A password reset or delete/re-create
    # (new salt) revokes previously issued tokens.
    material = ("account-v1|" + row["username"] + "|" + row["salt"] +
                "|" + row["password_hash"] + "|" + str(row["created_at"]))
    return _b64(hmac.digest(key, material.encode("utf-8"), "sha256")[:16])


def issue(row, kind, item_id, video_uuid):
    if kind not in ("movie", "episode") or type(item_id) is not int or item_id < 1:
        raise HTTPException(400, "Invalid remote media identity")
    from stream_catalog import canonical_uuid
    if not USER_PATTERN.fullmatch(row["username"]):
        raise HTTPException(400, "Invalid subscriber identity")
    try:
        video_uuid = canonical_uuid(video_uuid)
    except ValueError as exc:
        raise HTTPException(400, "Invalid remote video identifier") from exc
    key = secret_key()
    now = int(time.time())
    claims = {
        "v": VERSION, "u": row["username"], "k": kind,
        "i": item_id, "vid": video_uuid, "iat": now,
        "exp": now + TOKEN_TTL_SECONDS, "b": _binding(row, key),
        "n": secrets.token_urlsafe(12),
    }
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.digest(key, body.encode("ascii"), "sha256"))
    result = body + "." + signature
    if len(result) > MAX_TOKEN_CHARS:
        raise HTTPException(503, "Private HLS access token is too long")
    return result


def verify(token, kind, item_id):
    """Validate HMAC, age, subscriber revision, live status and mapping."""
    import main
    from stream_gateway import gateway_enabled, mapped_uuid

    if not gateway_enabled():
        raise HTTPException(404, "Private HLS playback disabled")
    if kind not in ("movie", "episode") or type(item_id) is not int or item_id < 1:
        raise HTTPException(401, "Invalid HLS access token")
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or not TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(401, "Invalid HLS access token")

    key = secret_key()
    body, signature = token.split(".", 1)
    expected = _b64(hmac.digest(key, body.encode("ascii"), "sha256"))
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid HLS access token")
    try:
        claims = json.loads(_decode(body))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(401, "Invalid HLS access token") from exc
    if not isinstance(claims, dict) or set(claims) != {
        "v", "u", "k", "i", "vid", "iat", "exp", "b", "n"
    }:
        raise HTTPException(401, "Invalid HLS access token")
    now = int(time.time())
    if (type(claims["v"]) is not int or claims["v"] != VERSION
            or type(claims["i"]) is not int or claims["i"] != item_id
            or claims["k"] != kind
            or type(claims["iat"]) is not int or type(claims["exp"]) is not int
            or claims["iat"] > now + CLOCK_SKEW_SECONDS
            or claims["exp"] <= now
            or claims["exp"] <= claims["iat"]
            or claims["exp"] - claims["iat"] > TOKEN_TTL_SECONDS
            or not isinstance(claims["u"], str)
            or not USER_PATTERN.fullmatch(claims["u"])
            or not isinstance(claims["b"], str)
            or not isinstance(claims["n"], str)
            or len(claims["n"]) < 12):
        raise HTTPException(401, "Invalid HLS access token")

    with main.connect() as db:
        row = db.execute(
            "SELECT username,salt,password_hash,created_at,disabled"
            " FROM users WHERE username=?", (claims["u"],)
        ).fetchone()
    if row is None or row["disabled"]:
        raise HTTPException(401, "HLS subscriber is unavailable")
    if not hmac.compare_digest(claims["b"], _binding(row, key)):
        raise HTTPException(401, "HLS access revoked")

    current = mapped_uuid(kind, item_id)
    if current is None or current != claims["vid"]:
        raise HTTPException(404, "Remote media mapping revoked")
    return current
