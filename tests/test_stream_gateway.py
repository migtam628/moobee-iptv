import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main
import stream_catalog
import stream_gateway
import hls_access_tokens

ADMIN = ("admin", "this-is-a-private-test-password-12345")
VIEWER = {"username": "streamviewer", "password": "viewer-password-12345678"}
MOVIE = "00000000-0000-4000-8000-000000000101"
EPISODE = "00000000-0000-4000-8000-000000000301"
KEY = "a-dedicated-example-service-key-over-32-chars"
SECRET = "unrelated-staging-token-signature-secret-0123456789"
UPSTREAM_TOKEN = "AbcdEfghijklmnopqrstuv"
MASTER = (
    "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-INDEPENDENT-SEGMENTS\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=3200000\n720p/index.m3u8\n"
)
MEDIA = (
    "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n"
    "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n"
    "#EXT-X-INDEPENDENT-SEGMENTS\n#EXTINF:6.000,\n"
    "segment_00001.ts\n#EXT-X-ENDLIST\n"
)


@pytest.fixture
def stage(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monkeypatch.setattr(main, "DATA", root / "data")
        monkeypatch.setattr(main, "DB", root / "data" / "stage.sqlite3")
        monkeypatch.setattr(main, "MEDIA", root / "media")
        monkeypatch.setenv("ADMIN_USERNAME", ADMIN[0])
        monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
        for name in ("MOOBEE_STREAM_ENABLED", "MOOBEE_STREAM_PLAYBACK_ENABLED",
                     "MOOBEE_STREAM_SERVICE_KEY", "MOOBEE_STREAM_BASE_URL",
                     "MOOBEE_STREAM_HLS_TOKEN_SECRET"):
            monkeypatch.delenv(name, raising=False)
        folder = root / "media" / "movie1"
        folder.mkdir(parents=True)
        (folder / "index.m3u8").write_text("#EXTM3U\n#EXTINF:6,\nsegment001.ts\n")
        (folder / "segment001.ts").write_bytes(b"local")
        c = TestClient(main.app)
        assert c.post("/admin/api/vod/movies", auth=ADMIN, json={
            "id": 101, "name": "Local demo movie", "category": "Movies", "folder": "movie1",
        }).status_code == 201
        assert c.post("/admin/api/vod/series", auth=ADMIN, json={
            "id": 201, "name": "Demo Series", "category": "Shows",
        }).status_code == 201
        assert c.post("/admin/api/vod/episodes", auth=ADMIN, json={
            "id": 301, "series_id": 201, "name": "Pilot", "folder": "movie1",
            "season": 1, "episode": 1,
        }).status_code == 201
        assert c.post("/admin/api/users", auth=ADMIN, json=VIEWER).status_code == 201
        yield c, monkeypatch


def configure(monkeypatch, playback=True):
    monkeypatch.setenv("MOOBEE_STREAM_ENABLED", "true")
    monkeypatch.setenv("MOOBEE_STREAM_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("MOOBEE_STREAM_SERVICE_KEY", KEY)
    monkeypatch.setenv("MOOBEE_STREAM_HLS_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("MOOBEE_STREAM_PLAYBACK_ENABLED", "true" if playback else "false")


def stage_mock(monkeypatch, *, master=MASTER, media=MEDIA,
               segment=b"transcoded-segment", sign_status=200,
               playback_expiry=None, segment_status=200):
    logs = []
    def handle(req):
        logs.append((req.url.path, req.headers.get("x-service-key")))
        assert req.url.host == "127.0.0.1" and req.url.port == 8080
        if req.url.path.startswith("/api/v1/internal/videos/"):
            target = req.url.path.split("/")[-1]
            return httpx.Response(200, json={
                "id": target, "title": "Upstream asset",
                "mediaType": "movie" if target == MOVIE else "tv",
                "status": "ready", "season": 1 if target == EPISODE else None,
                "episode": 1 if target == EPISODE else None,
            })
        if req.url.path.startswith("/api/v1/videos/"):
            assert req.headers["x-service-key"] == KEY
            if sign_status != 200:
                return httpx.Response(sign_status)
            target = req.url.path.split("/")[-2]
            expiry = (int(time.time()) + 7200
                      if playback_expiry is None else playback_expiry)
            return httpx.Response(200, json={
                "id": target,
                "stream": f"/hls/{target}/{expiry}/{UPSTREAM_TOKEN}/master.m3u8",
                "expiresAt": expiry,
            })
        assert req.url.path.startswith("/hls/")
        assert req.headers.get("x-service-key") is None
        if req.url.path.endswith("/master.m3u8"):
            return httpx.Response(200, text=master)
        if req.url.path.endswith("/index.m3u8"):
            return httpx.Response(200, text=media)
        if req.url.path.endswith("/segment_00001.ts"):
            return httpx.Response(segment_status, content=segment,
                                  headers={"Content-Type": "video/mp2t"})
        return httpx.Response(404)
    def factory():
        return httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
    monkeypatch.setattr(stream_catalog, "_client", factory)
    monkeypatch.setattr(stream_gateway, "_client", factory)
    return logs


def make_mapping(c, kind, local_id, upstream_id):
    r = c.put(f"/admin/api/integrations/moobee-stream/mappings/{kind}/{local_id}",
              auth=ADMIN, json={"video_id": upstream_id, "enabled": True})
    assert r.status_code == 200, r.text


def initial(c, kind, ident, password=VIEWER["password"]):
    path = ("movie" if kind == "movie" else "series")
    return c.get(f"/{path}/{VIEWER['username']}/{password}/{ident}.m3u8")


def referenced_path(content):
    urls = [line.strip() for line in content.splitlines()
            if line.strip() and not line.startswith("#")]
    assert urls
    url = urlsplit(urls[0])
    assert url.scheme == "https" and url.netloc == "tv.example.com"
    assert not url.query and not url.fragment
    return url.path


def token_paths(c, kind, ident):
    master = initial(c, kind, ident)
    assert master.status_code == 200, master.text
    assert VIEWER["password"] not in master.text
    assert VIEWER["username"] not in master.text
    assert KEY not in master.text and SECRET not in master.text
    assert "/stream-vod-t/" in master.text
    assert "/hls/" not in master.text and UPSTREAM_TOKEN not in master.text
    rendition_path = referenced_path(master.text)
    media = c.get(rendition_path)
    assert media.status_code == 200, media.text
    assert VIEWER["password"] not in media.text
    assert VIEWER["username"] not in media.text
    segment_path = referenced_path(media.text)
    assert "/stream-vod-t/" in segment_path
    assert VIEWER["password"] not in segment_path
    assert KEY not in segment_path and SECRET not in segment_path
    return rendition_path, segment_path


def test_gateway_off_preserves_local_vod_and_never_uses_remote(stage):
    c, m = stage
    m.setattr(stream_gateway, "_client", lambda: pytest.fail("unexpected upstream request"))
    original = initial(c, "movie", 101)
    assert original.status_code == 200 and "segment001.ts" in original.text
    configure(m, playback=False)
    stage_mock(m)
    make_mapping(c, "movie", 101, MOVIE)
    assert initial(c, "movie", 101).text == original.text
    assert c.get("/stream-vod-t/not-a-token/movie/101/720p/index.m3u8").status_code == 404
    assert c.get("/stream-vod/streamviewer/x/movie/101/720p/index.m3u8").status_code == 404


def test_movie_and_episode_real_proxy_routes_contain_no_subscriber_password(stage):
    c, m = stage
    configure(m)
    calls = stage_mock(m)
    make_mapping(c, "movie", 101, MOVIE)
    make_mapping(c, "episode", 301, EPISODE)
    movie_rendition, movie_segment = token_paths(c, "movie", 101)
    episode_rendition, episode_segment = token_paths(c, "episode", 301)
    assert c.get(movie_segment).content == b"transcoded-segment"
    assert c.get(episode_segment).content == b"transcoded-segment"
    assert c.get(movie_segment).headers["cache-control"] == "private, no-store"
    assert c.get(episode_rendition).headers["referrer-policy"] == "no-referrer"
    assert all(path.startswith("/api/") or key is None for path, key in calls)
    # The old credential-bearing HLS routes have been removed.
    assert c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/"
                 "720p/segment_00001.ts").status_code == 404


def test_invalid_cross_item_tampered_and_bad_user_tokens_fail_before_upstream(stage):
    c, m = stage
    configure(m)
    calls = stage_mock(m)
    make_mapping(c, "movie", 101, MOVIE)
    make_mapping(c, "episode", 301, EPISODE)
    rendition, segment = token_paths(c, "movie", 101)
    before = len(calls)
    assert initial(c, "movie", 101, password="wrong").status_code == 401
    assert len(calls) == before
    assert c.get(segment.replace("/movie/101/", "/episode/301/")).status_code == 401
    assert c.get(segment.replace("/movie/101/", "/movie/102/")).status_code == 401
    token = segment.split("/")[2]
    bad = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert c.get(segment.replace(token, bad)).status_code == 401
    assert c.get(segment.replace(token, "invalid")).status_code == 401
    assert len(calls) == before


def test_token_expiry_and_signing_secret_rotation_fail_closed(stage):
    c, m = stage
    configure(m)
    stage_mock(m)
    make_mapping(c, "movie", 101, MOVIE)
    rendition, segment = token_paths(c, "movie", 101)
    real_now = int(time.time())
    m.setattr(hls_access_tokens, "time",
              type("FakeTime", (), {"time": staticmethod(lambda: real_now + 15000)}))
    assert c.get(segment).status_code == 401
    m.setattr(hls_access_tokens, "time",
              type("FakeTime", (), {"time": staticmethod(lambda: real_now)}))
    m.setenv("MOOBEE_STREAM_HLS_TOKEN_SECRET", "another-strong-rotated-secret-01234567890")
    assert c.get(segment).status_code == 401


def test_disable_user_password_reset_mapping_removal_all_revoke_segments(stage):
    c, m = stage
    configure(m)
    calls = stage_mock(m)
    make_mapping(c, "movie", 101, MOVIE)
    make_mapping(c, "episode", 301, EPISODE)
    _, movie_segment = token_paths(c, "movie", 101)
    _, episode_segment = token_paths(c, "episode", 301)
    before = len(calls)
    assert c.patch("/admin/api/users/streamviewer", auth=ADMIN,
                   json={"disabled": True}).status_code == 200
    assert c.get(movie_segment).status_code == 401
    assert len(calls) == before
    assert c.patch("/admin/api/users/streamviewer", auth=ADMIN,
                   json={"disabled": False}).status_code == 200
    # Disabling permanently revokes the old token even after account re-enable.
    assert c.get(movie_segment).status_code == 401
    _, renewed_movie_segment = token_paths(c, "movie", 101)
    assert c.get(renewed_movie_segment).status_code == 200
    with main.connect() as db:
        db.execute("UPDATE users SET salt=?,password_hash=? WHERE username=?",
                   ("ab" * 16, "cd" * 64, VIEWER["username"]))
        db.commit()
    assert c.get(movie_segment).status_code == 401
    # Re-creating a former user identity or changing password also revokes.
    with main.connect() as db:
        row = db.execute("SELECT salt,password_hash FROM users WHERE username=?",
                         (VIEWER["username"],)).fetchone()
        assert row
    # Restore test subscriber only to verify mapping revocation with a fresh token.
    with main.connect() as db:
        salt = "ef" * 16
        db.execute("UPDATE users SET salt=?,password_hash=? WHERE username=?",
                   (salt, main.digest(VIEWER["password"], salt), VIEWER["username"]))
        db.commit()
    _, fresh_episode_segment = token_paths(c, "episode", 301)
    assert c.delete("/admin/api/integrations/moobee-stream/mappings/episode/301",
                    auth=ADMIN).status_code == 200
    before = len(calls)
    assert c.get(fresh_episode_segment).status_code == 404
    assert len(calls) == before
    assert c.get(episode_segment).status_code == 401


@pytest.mark.parametrize("payload", [
    "#EXTM3U\nhttps://evil.example/media.m3u8\n",
    "#EXTM3U\n../secret/index.m3u8\n",
    "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"https://evil.example/key\"\n720p/index.m3u8\n",
    "#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI=\"https://evil.example/audio\"\n720p/index.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=42\n720p/../../secret/index.m3u8\n",
])
def test_remote_master_blocks_external_references(stage, payload):
    c, m = stage
    configure(m)
    stage_mock(m, master=payload)
    make_mapping(c, "movie", 101, MOVIE)
    r = initial(c, "movie", 101)
    assert r.status_code == 502 and "evil.example" not in r.text


@pytest.mark.parametrize("payload", [
    "#EXTM3U\nhttp://evil.example/segment_00001.ts\n",
    "#EXTM3U\n../../segment_00001.ts\n",
    "#EXTM3U\n#EXT-X-MAP:URI=\"secret\"\nsegment_00001.ts\n",
    "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"elsewhere\"\nsegment_00001.ts\n",
])
def test_remote_media_playlist_blocks_external_references(stage, payload):
    c, m = stage
    configure(m)
    stage_mock(m, media=payload)
    make_mapping(c, "movie", 101, MOVIE)
    master = initial(c, "movie", 101)
    assert master.status_code == 200
    r = c.get(referenced_path(master.text))
    assert r.status_code == 502


@pytest.mark.parametrize("status", [302, 401, 404, 500])
def test_signed_resolver_errors_fail_closed(stage, status):
    c, m = stage
    configure(m)
    stage_mock(m, sign_status=status)
    make_mapping(c, "movie", 101, MOVIE)
    out = initial(c, "movie", 101)
    assert out.status_code in (502, 404)
    assert "segment001.ts" not in out.text


def test_missing_token_secret_fails_closed(stage):
    c, m = stage
    configure(m)
    calls = stage_mock(m)
    make_mapping(c, "movie", 101, MOVIE)
    m.delenv("MOOBEE_STREAM_HLS_TOKEN_SECRET")
    before = len(calls)
    assert initial(c, "movie", 101).status_code == 503
    assert len(calls) == before
    assert c.get("/stream-vod-t/invalid/movie/101/720p/index.m3u8").status_code == 401


def test_upstream_expiry_segment_size_limit_and_flag_disabled(stage):
    c, m = stage
    configure(m)
    stage_mock(m, playback_expiry=int(time.time()) - 100)
    make_mapping(c, "movie", 101, MOVIE)
    assert initial(c, "movie", 101).status_code == 502
    stage_mock(m, segment=b"x" * (stream_gateway.MAX_SEGMENT + 1))
    rendition, segment = token_paths(c, "movie", 101)
    assert c.get(segment).status_code == 502
    m.setenv("MOOBEE_STREAM_ENABLED", "false")
    assert c.get(segment).status_code == 404
    assert "segment001.ts" in initial(c, "movie", 101).text


def test_direct_sql_account_disable_reenable_cannot_reactivate_token(stage):
    # Matches the standalone Phase 13 validator, which updates the staging DB.
    c, m = stage
    configure(m)
    stage_mock(m)
    make_mapping(c, "movie", 101, MOVIE)
    _, old_segment = token_paths(c, "movie", 101)
    with main.connect() as db:
        previous = db.execute(
            "SELECT token_generation FROM users WHERE username=?",
            (VIEWER["username"],)
        ).fetchone()["token_generation"]
        db.execute("UPDATE users SET disabled=1 WHERE username=?",
                   (VIEWER["username"],))
        db.commit()
    assert c.get(old_segment).status_code == 401
    with main.connect() as db:
        revoked = db.execute(
            "SELECT token_generation FROM users WHERE username=?",
            (VIEWER["username"],)
        ).fetchone()["token_generation"]
        assert revoked == previous + 1
        db.execute("UPDATE users SET disabled=0 WHERE username=?",
                   (VIEWER["username"],))
        db.commit()
    assert c.get(old_segment).status_code == 401
    _, fresh_segment = token_paths(c, "movie", 101)
    assert c.get(fresh_segment).status_code == 200


def test_legacy_users_table_is_upgraded_without_losing_credentials(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "DB", tmp_path / "legacy.sqlite3")
    salt = "ab" * 16
    password = "test-legacy-password-0123456789"
    with sqlite3.connect(main.DB) as db:
        db.execute("""CREATE TABLE users (
            username TEXT PRIMARY KEY, salt TEXT NOT NULL,
            password_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0)""")
        db.execute("INSERT INTO users VALUES (?,?,?,?,0)",
                   ("legacyuser", salt, main.digest(password, salt), 123456))
        db.commit()
    with main.connect() as db:
        row = db.execute("SELECT token_generation,disabled FROM users WHERE username=?",
                         ("legacyuser",)).fetchone()
        assert row["token_generation"] == 0 and row["disabled"] == 0
        db.execute("UPDATE users SET disabled=1 WHERE username=?", ("legacyuser",))
        db.execute("UPDATE users SET disabled=0 WHERE username=?", ("legacyuser",))
        db.commit()
    with main.connect() as db:
        row = db.execute("SELECT token_generation FROM users WHERE username=?",
                         ("legacyuser",)).fetchone()
        assert row["token_generation"] == 1
    assert main.auth("legacyuser", password)["username"] == "legacyuser"


def test_hls_renewal_hint_is_opt_in_and_authenticated(stage):
    c, m = stage
    uri = "/player_api.php"
    p = {"username": VIEWER["username"], "password": VIEWER["password"]}
    assert "moobee_hls_refresh_after_seconds" not in c.get(uri, params=p).json()["user_info"]
    configure(m)
    enabled = c.get(uri, params=p)
    assert enabled.status_code == 200
    assert enabled.json()["user_info"]["moobee_hls_refresh_after_seconds"] == 10800
    assert c.get(uri, params={"username": VIEWER["username"], "password": "wrong"}).status_code == 401
    m.setenv("MOOBEE_STREAM_PLAYBACK_ENABLED", "false")
    assert "moobee_hls_refresh_after_seconds" not in c.get(uri, params=p).json()["user_info"]
