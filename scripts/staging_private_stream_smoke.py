"""One-shot, disposable smoke test against the real private Stream VPS.

Runs entirely in memory plus a TemporaryDirectory. No listening ports,
production database access, real subscribers or filesystem mutations outside
/tmp. Must be launched only in the isolated staging Docker image with the
explicit environment options documented below.
"""
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, "/srv/app")

from fastapi.testclient import TestClient
import main
import stream_catalog
import stream_gateway
from vod_catalog import ensure_vod_schema

MOVIE = "efd11f2b-fbc9-4259-a713-81afc4c90bda"
EPISODE = "1eb75e2b-b46a-40f9-8d80-eb57747e51b0"
STAGE_BASE = "https://stage.invalid"
HOST = "moobee-stream-vps.tail08e9d7.ts.net"


def check(condition, label):
    if not condition:
        raise RuntimeError("FAIL: " + label)
    print("PASS:", label)


def successful_path(content, suffix):
    """Extract and validate the first rewritten URL without printing credentials."""
    urls = [line.strip() for line in content.splitlines()
            if line.strip() and not line.startswith("#")]
    check(bool(urls), "HLS manifest contains a referenced asset")
    url = urlsplit(urls[0])
    check(url.scheme == "https" and url.netloc == "stage.invalid" and
          url.path.startswith("/stream-vod/") and
          url.path.endswith(suffix) and not url.query and not url.fragment,
          "HLS uses only staging gateway URLs")
    return url.path


def run():
    needed = ("MOOBEE_STREAM_SERVICE_KEY", "MOOBEE_STREAM_BASE_URL",
              "MOOBEE_STREAM_TAILSCALE_HOSTNAME")
    if any(not os.environ.get(var) for var in needed):
        raise RuntimeError("Missing private staging configuration")
    if os.environ["MOOBEE_STREAM_BASE_URL"] != "https://" + HOST or (
        os.environ["MOOBEE_STREAM_TAILSCALE_HOSTNAME"] != HOST
    ):
        raise RuntimeError("Unexpected staging Stream origin")
    if len(os.environ["MOOBEE_STREAM_SERVICE_KEY"].encode()) < 32:
        raise RuntimeError("Invalid staging service key")

    os.environ["MOOBEE_STREAM_ENABLED"] = "true"
    os.environ["MOOBEE_STREAM_PLAYBACK_ENABLED"] = "false"

    meta_movie = stream_catalog.fetch_video(MOVIE)
    meta_episode = stream_catalog.fetch_video(EPISODE)
    check(meta_movie["status"] == "ready" and meta_movie["media_type"] == "movie",
          "Real upstream movie is ready")
    check(meta_episode["status"] == "ready" and meta_episode["media_type"] == "tv"
          and meta_episode["season"] == 1 and meta_episode["episode"] == 1,
          "Real upstream episode is ready")

    with tempfile.TemporaryDirectory(prefix="moobee-stage-") as td:
        root = Path(td)
        main.DATA = root / "data"
        main.DB = main.DATA / "stage.sqlite3"
        main.MEDIA = root / "media"
        main.BASE = STAGE_BASE
        folder = main.MEDIA / "localtest"
        folder.mkdir(parents=True)
        (folder / "index.m3u8").write_text(
            "#EXTM3U\n#EXTINF:6,\nsegment001.ts\n#EXT-X-ENDLIST\n")
        (folder / "segment001.ts").write_bytes(b"staging-local-only")

        user, password = "stage-viewer", secrets.token_urlsafe(24)
        salt = secrets.token_hex(16)
        with main.connect() as db:
            ensure_vod_schema(db)
            db.execute(
                "INSERT INTO users(username,salt,password_hash,created_at,disabled)"
                " VALUES (?,?,?,?,0)",
                (user, salt, main.digest(password, salt), int(time.time()))
            )
            db.execute("INSERT INTO vod_movies(id,name,category,folder)"
                       " VALUES (101,'Staging Movie','Stage','localtest')")
            db.execute("INSERT INTO vod_series(id,name,category)"
                       " VALUES (201,'Staging Series','Stage')")
            db.execute("INSERT INTO vod_episodes(id,series_id,season,episode,name,folder)"
                       " VALUES (301,201,1,1,'Staging Episode','localtest')")
            stream_catalog.ensure_mapping_schema(db)
            for kind, item_id, video_uuid in (
                ("movie", 101, MOVIE), ("episode", 301, EPISODE)
            ):
                db.execute(
                    "INSERT INTO vod_external_sources"
                    " (kind,item_id,provider,video_uuid,enabled,last_known_status,updated_at)"
                    " VALUES (?,?,'moobee-stream',?,1,'ready',?)",
                    (kind, item_id, video_uuid, int(time.time()))
                )
            db.commit()

        with TestClient(main.app) as client:
            movie_route = f"/movie/{user}/{password}/101.m3u8"
            episode_route = f"/series/{user}/{password}/301.m3u8"

            old = client.get(movie_route)
            check(old.status_code == 200 and "segment001.ts" in old.text,
                  "Feature flag off preserves local movie")

            os.environ["MOOBEE_STREAM_PLAYBACK_ENABLED"] = "true"
            check(stream_gateway.gateway_enabled(), "Staging gateway enabled")

            for label, route, expected_id in (
                ("Movie", movie_route, 101),
                ("Episode", episode_route, 301),
            ):
                master = client.get(route)
                check(master.status_code == 200 and
                      "segment001.ts" not in master.text and
                      "/hls/" not in master.text and
                      HOST not in master.text and
                      MOVIE not in master.text and
                      EPISODE not in master.text,
                      label + " rewritten master playlist (no upstream paths)")
                media_path = successful_path(master.text, "/index.m3u8")
                check(f"/{expected_id}/" in media_path,
                      label + " staging catalog ID unchanged")
                rendition = client.get(media_path)
                check(rendition.status_code == 200,
                      label + " real HLS rendition")
                segment_path = successful_path(rendition.text, ".ts")
                segment = client.get(segment_path)
                check(segment.status_code == 200 and len(segment.content) > 188
                      and segment.content[0] == 0x47,
                      label + " real MPEG-TS segment through gateway")

            bad = client.get(f"/movie/{user}/not-the-password/101.m3u8")
            check(bad.status_code == 401, "Wrong subscriber password denied")
            with main.connect() as db:
                db.execute("UPDATE users SET disabled=1 WHERE username=?", (user,))
                db.commit()
            check(client.get(segment_path).status_code == 401,
                  "Disabled subscriber denied on next segment")
            with main.connect() as db:
                db.execute("UPDATE users SET disabled=0 WHERE username=?", (user,))
                db.execute("DELETE FROM vod_external_sources WHERE kind='episode' AND item_id=301")
                db.commit()
            check(client.get(segment_path).status_code == 404,
                  "Removed mapping denies next segment")
            os.environ["MOOBEE_STREAM_PLAYBACK_ENABLED"] = "false"
            local_again = client.get(movie_route)
            check(local_again.status_code == 200 and "segment001.ts" in local_again.text,
                  "Playback disabled safely returns to local media")
    print("PASS: staging smoke complete; temporary database destroyed.")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # Exceptions can include private subscriber URLs or credentials in
        # underlying transport messages; never print their string representation.
        print("STAGING FAILED:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)
