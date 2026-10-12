"""Initialize a separate, loopback-only StreamBoss manual E2E staging instance.

Requires /data and /media to be disposable, separate writable directories.
Never points at production SQLite or the live IPTV media library.
Creates unique staging credentials in /data/stage-access.txt with mode 0600.
"""
import os
from pathlib import Path
import secrets
import sys
import time

sys.path.insert(0, "/srv/app")
import main
from vod_catalog import ensure_vod_schema
from stream_catalog import ensure_mapping_schema

MOVIE = "efd11f2b-fbc9-4259-a713-81afc4c90bda"
EPISODE = "1eb75e2b-b46a-40f9-8d80-eb57747e51b0"
ACCESS = Path("/data/stage-access.txt")
DB = Path("/data/server.sqlite3")
MEDIA = Path("/media/localtest")


def run():
    if os.environ.get("MOOBEE_STAGE_SETUP_ACK") != "isolated":
        raise RuntimeError("Isolated staging acknowledgement missing")
    if DB.exists() or ACCESS.exists():
        raise RuntimeError("Staging files already exist; refusing to overwrite")
    if not Path("/data").is_dir() or not Path("/media").is_dir():
        raise RuntimeError("Disposable data/media mount not found")
    if os.environ.get("PUBLIC_URL") != "http://127.0.0.1:18000":
        raise RuntimeError("Unexpected staging URL")
    if not MEDIA.parent.is_dir():
        raise RuntimeError("Media mount not found")
    MEDIA.mkdir(exist_ok=False)
    (MEDIA / "index.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:6,\nsegment001.ts\n#EXT-X-ENDLIST\n"
    )
    # This is local fallback metadata only, NOT a remotely playable segment.
    (MEDIA / "segment001.ts").write_bytes(b"temporary-staging-placeholder")

    username = "moobee-stage"
    password = secrets.token_urlsafe(24)
    salt = secrets.token_hex(16)
    with main.connect() as db:
        ensure_vod_schema(db)
        db.execute("INSERT INTO users(username,salt,password_hash,created_at,disabled)"
                   " VALUES (?,?,?,?,0)",
                   (username, salt, main.digest(password, salt), int(time.time())))
        db.execute("INSERT INTO vod_movies(id,name,category,folder) VALUES "
                   "(101,'MooBee Test Movie','Staging','localtest')")
        db.execute("INSERT INTO vod_series(id,name,category) VALUES "
                   "(201,'MooBee Test Series','Staging')")
        db.execute("INSERT INTO vod_episodes(id,series_id,season,episode,name,folder)"
                   " VALUES (301,201,1,1,'S01E01','localtest')")
        ensure_mapping_schema(db)
        for kind, item_id, video in (
            ("movie", 101, MOVIE), ("episode", 301, EPISODE)
        ):
            db.execute(
                "INSERT INTO vod_external_sources"
                " (kind,item_id,provider,video_uuid,enabled,last_known_status,updated_at)"
                " VALUES (?,?,'moobee-stream',?,1,'ready',?)",
                (kind, item_id, video, int(time.time()))
            )
        db.commit()
    fd = os.open(str(ACCESS), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write("Server: http://127.0.0.1:18000\n")
        out.write(f"Username: {username}\nPassword: {password}\n")
    print("STAGING INITIALIZED. Temporary database and media only.")
    print("Credentials saved to /data/stage-access.txt (mode 0600).")
    print("Do not paste credentials into chat or share them.")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("STAGING SETUP FAILED:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)
