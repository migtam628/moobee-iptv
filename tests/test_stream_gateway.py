import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main
import stream_catalog
import stream_gateway

ADMIN = ("admin", "this-is-a-private-test-password-12345")
VIEWER = {"username": "streamviewer", "password": "viewer-password-12345678"}
MOVIE = "00000000-0000-4000-8000-000000000101"
EPISODE = "00000000-0000-4000-8000-000000000301"
KEY = "a-dedicated-example-service-key-over-32-chars"
TOKEN = "AbcdEfghijklmnopqrstuv"
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
        monkeypatch.delenv("MOOBEE_STREAM_ENABLED", raising=False)
        monkeypatch.delenv("MOOBEE_STREAM_PLAYBACK_ENABLED", raising=False)
        monkeypatch.delenv("MOOBEE_STREAM_SERVICE_KEY", raising=False)
        monkeypatch.delenv("MOOBEE_STREAM_BASE_URL", raising=False)
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
    monkeypatch.setenv("MOOBEE_STREAM_PLAYBACK_ENABLED", "true" if playback else "false")


def stage_mock(monkeypatch, *, master=MASTER, media=MEDIA, segment=b"transcoded-segment",
               sign_status=200, playback_expiry=None, segment_status=200):
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
                "stream": f"/hls/{target}/{expiry}/{TOKEN}/master.m3u8",
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


def test_default_gateway_off_does_not_contact_stream_and_preserves_local(stage):
    c, monkeypatch = stage
    monkeypatch.setattr(stream_gateway, "_client", lambda: pytest.fail("remote call"))
    local = c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8")
    assert local.status_code == 200
    assert "segment001.ts" in local.text
    assert c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/"
                 "720p/index.m3u8").status_code == 404
    configure(monkeypatch, playback=False)
    stage_mock(monkeypatch)
    make_mapping(c, "movie", 101, MOVIE)
    assert c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8").text == local.text
    assert not stream_gateway.gateway_enabled()
    status = c.get("/admin/api/integrations/moobee-stream/status", auth=ADMIN)
    assert status.json()["playback_supported"] is False


def test_movie_and_episode_playback_via_authenticated_proxy(stage):
    c, monkeypatch = stage
    configure(monkeypatch)
    calls = stage_mock(monkeypatch)
    make_mapping(c, "movie", 101, MOVIE)
    make_mapping(c, "episode", 301, EPISODE)
    movie = c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8")
    assert movie.status_code == 200, movie.text
    assert "/stream-vod/streamviewer/viewer-password-12345678/movie/101/720p/index.m3u8" in movie.text
    assert TOKEN not in movie.text and KEY not in movie.text
    media = c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/720p/index.m3u8")
    assert media.status_code == 200, media.text
    assert "/stream-vod/streamviewer/viewer-password-12345678/movie/101/720p/segment_00001.ts" in media.text
    segment = c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/720p/segment_00001.ts")
    assert segment.status_code == 200 and segment.content == b"transcoded-segment"
    assert segment.headers["cache-control"] == "private, no-store"
    ep = c.get("/series/streamviewer/viewer-password-12345678/301.m3u8")
    assert ep.status_code == 200 and "/episode/301/720p/index.m3u8" in ep.text
    assert c.get("/stream-vod/streamviewer/viewer-password-12345678/episode/301/"
                 "720p/segment_00001.ts").status_code == 200
    assert all(path.startswith("/api/") or key is None for path, key in calls)
    assert c.get("/admin/api/integrations/moobee-stream/status", auth=ADMIN).json()["playback_supported"] is True


def test_auth_revocation_and_mapping_revocation_fail_closed(stage):
    c, monkeypatch = stage
    configure(monkeypatch)
    calls = stage_mock(monkeypatch)
    make_mapping(c, "movie", 101, MOVIE)
    before = len(calls)
    assert c.get("/movie/streamviewer/wrong/101.m3u8").status_code == 401
    assert c.get("/stream-vod/streamviewer/wrong/movie/101/720p/segment_00001.ts").status_code == 401
    assert len(calls) == before
    assert c.patch("/admin/api/users/streamviewer", auth=ADMIN,
                   json={"disabled": True}).status_code == 200
    assert c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/"
                 "720p/segment_00001.ts").status_code == 401
    assert len(calls) == before
    assert c.patch("/admin/api/users/streamviewer", auth=ADMIN,
                   json={"disabled": False}).status_code == 200
    assert c.delete("/admin/api/integrations/moobee-stream/mappings/movie/101",
                    auth=ADMIN).status_code == 200
    assert c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/"
                 "720p/segment_00001.ts").status_code == 404
    assert len(calls) == before
    # With no mapping, local demo content remains playable.
    assert "segment001.ts" in c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8").text


@pytest.mark.parametrize("payload", [
    "#EXTM3U\nhttps://evil.example/media.m3u8\n",
    "#EXTM3U\n../secret/index.m3u8\n",
    "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"https://evil.example/key\"\n720p/index.m3u8\n",
    "#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI=\"https://evil.example/audio\"\n720p/index.m3u8\n",
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=42\n720p/../../secret/index.m3u8\n",
])
def test_remote_master_blocks_external_references(stage, payload):
    c, monkeypatch = stage
    configure(monkeypatch)
    stage_mock(monkeypatch, master=payload)
    make_mapping(c, "movie", 101, MOVIE)
    out = c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8")
    assert out.status_code == 502
    assert "evil.example" not in out.text


@pytest.mark.parametrize("payload", [
    "#EXTM3U\nhttp://evil.example/segment_00001.ts\n",
    "#EXTM3U\n../../segment_00001.ts\n",
    "#EXTM3U\n#EXT-X-MAP:URI=\"secret\"\nsegment_00001.ts\n",
    "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"elsewhere\"\nsegment_00001.ts\n",
])
def test_remote_media_playlist_blocks_external_references(stage, payload):
    c, monkeypatch = stage
    configure(monkeypatch)
    stage_mock(monkeypatch, media=payload)
    make_mapping(c, "movie", 101, MOVIE)
    out = c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/"
                "720p/index.m3u8")
    assert out.status_code == 502


@pytest.mark.parametrize("status", [302, 401, 404, 500])
def test_signed_resolver_errors_never_fall_back_to_local(stage, status):
    c, monkeypatch = stage
    configure(monkeypatch)
    stage_mock(monkeypatch, sign_status=status)
    make_mapping(c, "movie", 101, MOVIE)
    out = c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8")
    assert out.status_code in (502, 404)
    assert "segment001.ts" not in out.text


def test_signed_path_expiry_and_large_segments_fail_closed(stage):
    c, monkeypatch = stage
    configure(monkeypatch)
    stage_mock(monkeypatch, playback_expiry=int(time.time()) - 100)
    make_mapping(c, "movie", 101, MOVIE)
    assert c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8").status_code == 502
    stage_mock(monkeypatch, segment=b"x" * (stream_gateway.MAX_SEGMENT + 1))
    assert c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/"
                 "720p/segment_00001.ts").status_code == 502


def test_disabled_connector_flag_fails_closed_after_mapping(stage):
    c, monkeypatch = stage
    configure(monkeypatch)
    stage_mock(monkeypatch)
    make_mapping(c, "movie", 101, MOVIE)
    monkeypatch.setenv("MOOBEE_STREAM_ENABLED", "false")
    r = c.get("/movie/streamviewer/viewer-password-12345678/101.m3u8")
    assert r.status_code == 200
    assert "segment001.ts" in r.text
    assert c.get("/stream-vod/streamviewer/viewer-password-12345678/movie/101/"
                 "720p/index.m3u8").status_code == 404
