import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main
import stream_catalog as stream

ADMIN = ("admin", "long-test-admin-password-1234567890")
KEY = "stream-service-key-is-private-0123456789ABCDEF"
MOVIE_UUID = "00000000-0000-4000-8000-000000000101"
EPISODE_UUID = "00000000-0000-4000-8000-000000000301"


@pytest.fixture
def setup(monkeypatch):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        monkeypatch.setattr(main, "DATA", root / "data")
        monkeypatch.setattr(main, "DB", root / "data" / "stream-test.sqlite3")
        monkeypatch.setattr(main, "MEDIA", root / "media")
        monkeypatch.setenv("ADMIN_USERNAME", ADMIN[0])
        monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
        monkeypatch.delenv("MOOBEE_STREAM_ENABLED", raising=False)
        monkeypatch.delenv("MOOBEE_STREAM_BASE_URL", raising=False)
        monkeypatch.delenv("MOOBEE_STREAM_SERVICE_KEY", raising=False)
        client = TestClient(main.app)
        yield client, root, monkeypatch


def configure(monkeypatch):
    monkeypatch.setenv("MOOBEE_STREAM_ENABLED", "true")
    monkeypatch.setenv("MOOBEE_STREAM_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("MOOBEE_STREAM_SERVICE_KEY", KEY)


def movie_record(identifier=MOVIE_UUID, *, status="ready"):
    return {"id": identifier, "title": "My authorized movie", "mediaType": "movie",
            "status": status, "tmdbId": None, "season": None, "episode": None,
            "durationSeconds": 120.3, "width": 720, "height": 480,
            "source_path": "/private/NEVER-IN-RESPONSE", "x-api-key": "NEVER"}


def episode_record(status="queued"):
    return {"id": EPISODE_UUID, "title": "Authorized pilot", "mediaType": "tv",
            "status": status, "season": 1, "episode": 1,
            "durationSeconds": 40, "width": 640, "height": 360}


def attach(monkeypatch, handler):
    def secure(request):
        assert request.method == "GET"
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 8080
        assert request.headers["x-service-key"] == KEY
        assert request.headers.get("x-api-key") is None
        return handler(request)
    monkeypatch.setattr(stream, "_client", lambda: httpx.Client(
        transport=httpx.MockTransport(secure), trust_env=False))


def init_catalog(client, root):
    (root / "media" / "movie1").mkdir(parents=True)
    (root / "media" / "movie1" / "index.m3u8").write_text(
        "#EXTM3U\n#EXTINF:6,\nsegment001.ts\n#EXT-X-ENDLIST\n")
    (root / "media" / "movie1" / "segment001.ts").write_bytes(b"movie")
    result = client.post("/admin/api/vod/movies", auth=ADMIN, json={
        "id": 101, "name": "My Local Movie", "category": "Test", "folder": "movie1"})
    assert result.status_code == 201, result.text
    result = client.post("/admin/api/vod/series", auth=ADMIN, json={
        "id": 201, "name": "My Local Series", "category": "Shows"})
    assert result.status_code == 201, result.text
    result = client.post("/admin/api/vod/episodes", auth=ADMIN, json={
        "id": 301, "series_id": 201, "name": "Pilot", "folder": "movie1",
        "season": 1, "episode": 1})
    assert result.status_code == 201, result.text
    viewer = client.post("/admin/api/users", auth=ADMIN,
                         json={"username": "vodviewer", "password": "viewer-password-12345678"})
    assert viewer.status_code == 201, viewer.text


def mapping_table_exists():
    with main.connect() as db:
        return bool(db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                                  AND name='vod_external_sources'""").fetchone())


@pytest.mark.parametrize("url", [
    "http://example.com:8080", "http://169.254.169.254", "http://8.8.8.8",
    "http://0.0.0.0:8080", "http://localhost:8080",
    "http://127.0.0.2:8080", "http://10.0.0.1:8080",
    "http://127.0.0.1@evil.example:8080",
    "http://127.0.0.1:8080/private", "http://127.0.0.1:8080/?url=x",
    "https://user:pass@10.1.1.1:8080", "ftp://10.1.1.1",
    "http://127.0.0.1:0", "http://127.0.0.1:99999",
    "http://[::1]:8080", "http://127.0.0.1:8080\\@evil.test",
])
def test_private_origin_rejects_untrusted_hosts_and_paths(url):
    with pytest.raises(ValueError):
        stream.fixed_private_origin(url)


def test_private_origin_allows_local_or_tls_private_only():
    assert stream.fixed_private_origin("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    assert stream.fixed_private_origin("https://100.99.174.2:9443") == "https://100.99.174.2:9443"
    assert stream.fixed_private_origin("https://10.3.4.5") == "https://10.3.4.5:443"


def test_disabled_connector_does_not_call_upstream_or_create_tables(setup):
    client, root, monkeypatch = setup
    monkeypatch.setattr(stream, "_client", lambda: pytest.fail("Connector must not open a network client"))
    status = client.get("/admin/api/integrations/moobee-stream/status", auth=ADMIN)
    assert status.json() == {"enabled": False, "configured": False,
                             "playback_supported": False,
                             "catalog_write_mode": "manual-mapping-only"}
    assert client.post("/admin/api/integrations/moobee-stream/preview", auth=ADMIN,
                       json={"max_pages": 2}).status_code == 503
    assert client.get("/admin/api/integrations/moobee-stream/mappings", auth=ADMIN).json() == {
        "mappings": [], "playback_supported": False}
    assert not mapping_table_exists()
    assert client.get("/admin/api/integrations/moobee-stream/status").status_code == 401
    assert client.post("/admin/api/integrations/moobee-stream/preview").status_code == 401


def test_preview_is_paginated_read_only_and_redacts_private_upstream_data(setup):
    client, root, monkeypatch = setup
    configure(monkeypatch)
    requests = []
    def handler(req):
        requests.append(str(req.url))
        if req.url.params.get("cursor") is None:
            return httpx.Response(200, json={"videos": [movie_record()], "nextCursor": MOVIE_UUID})
        if req.url.params.get("cursor") == MOVIE_UUID:
            return httpx.Response(200, json={
                "videos": [episode_record()], "nextCursor": None})
        return httpx.Response(404)
    attach(monkeypatch, handler)
    data = client.post("/admin/api/integrations/moobee-stream/preview",
                       auth=ADMIN, json={"max_pages": 2})
    assert data.status_code == 200, data.text
    body = data.json()
    assert body["complete"] is True
    assert body["count"] == 2 and body["pages"] == 2
    assert body["next_cursor"] is None
    assert body["videos"][0]["video_id"] == MOVIE_UUID
    assert body["videos"][0]["mapping"] is None
    assert "source_path" not in data.text and "NEVER" not in data.text
    assert len(requests) == 2
    assert not mapping_table_exists()
    partial = client.post("/admin/api/integrations/moobee-stream/preview",
                          auth=ADMIN, json={"max_pages": 1})
    assert partial.status_code == 200
    assert partial.json()["complete"] is False
    assert partial.json()["next_cursor"] == MOVIE_UUID
    assert not mapping_table_exists()
    assert client.post("/admin/api/integrations/moobee-stream/preview",
                       auth=ADMIN, json={"max_pages": 11}).status_code == 422
    assert client.post("/admin/api/integrations/moobee-stream/preview",
                       auth=ADMIN, json={"max_pages": 1,
                                         "base_url": "http://169.254.169.254"}).status_code == 422


def test_explicit_uuid_mapping_is_idempotent_and_never_changes_local_playback(setup):
    client, root, monkeypatch = setup
    init_catalog(client, root)
    configure(monkeypatch)
    def handler(req):
        if req.url.path.endswith("/" + MOVIE_UUID):
            return httpx.Response(200, json=movie_record())
        if req.url.path.endswith("/" + EPISODE_UUID):
            return httpx.Response(200, json=episode_record())
        return httpx.Response(200, json={"videos": [movie_record(), episode_record()],
                                         "nextCursor": None})
    attach(monkeypatch, handler)
    login = {"username": "vodviewer", "password": "viewer-password-12345678"}
    original_movies = client.get("/player_api.php", params={**login, "action": "get_vod_streams"}).json()
    original_series = client.get("/player_api.php", params={
        **login, "action": "get_series_info", "series_id": 201}).json()
    movie_url = "/movie/vodviewer/viewer-password-12345678/101.m3u8"
    original_hls = client.get(movie_url)
    assert original_hls.status_code == 200

    payload = {"video_id": MOVIE_UUID}
    url = "/admin/api/integrations/moobee-stream/mappings/movie/101"
    first = client.put(url, auth=ADMIN, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["enabled"] is False
    assert first.json()["playback_supported"] is False
    second = client.put(url, auth=ADMIN, json=payload)
    assert second.status_code == 200
    episode_url = "/admin/api/integrations/moobee-stream/mappings/episode/301"
    queued = client.put(episode_url, auth=ADMIN, json={"video_id": EPISODE_UUID})
    assert queued.status_code == 200, queued.text
    assert queued.json()["upstream_status"] == "queued"
    assert client.put(episode_url, auth=ADMIN, json={
        "video_id": EPISODE_UUID, "enabled": True}).status_code == 422
    enabled = client.put(url, auth=ADMIN, json={"video_id": MOVIE_UUID, "enabled": True})
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True

    maps = client.get("/admin/api/integrations/moobee-stream/mappings", auth=ADMIN).json()
    assert len(maps["mappings"]) == 2
    assert maps["playback_supported"] is False
    # A video UUID may be linked to at most one local item.
    (root / "media" / "movie2").mkdir(parents=True)
    (root / "media" / "movie2" / "index.m3u8").write_text("#EXTM3U\n")
    assert client.post("/admin/api/vod/movies", auth=ADMIN, json={
        "id": 102, "name": "Other movie", "category": "Test", "folder": "movie2"
    }).status_code == 201
    assert client.put("/admin/api/integrations/moobee-stream/mappings/movie/102",
                      auth=ADMIN, json=payload).status_code == 409
    assert client.put("/admin/api/integrations/moobee-stream/mappings/episode/301",
                      auth=ADMIN, json=payload).status_code == 422
    assert client.put("/admin/api/integrations/moobee-stream/mappings/movie/9999",
                      auth=ADMIN, json=payload).status_code == 404
    assert client.put(url, auth=ADMIN, json={"video_id": "http://127.0.0.1:8080"}).status_code == 422
    assert client.put(url, auth=ADMIN, json={
        "video_id": MOVIE_UUID, "url": "http://169.254.169.254"}).status_code == 422
    assert client.get("/player_api.php", params={
        **login, "action": "get_vod_streams"}).json()[:1] == original_movies
    assert client.get("/player_api.php", params={
        **login, "action": "get_series_info", "series_id": 201}).json() == original_series
    assert client.get(movie_url).text == original_hls.text

    preview = client.post("/admin/api/integrations/moobee-stream/preview",
                          auth=ADMIN, json={"max_pages": 1}).json()
    assert preview["videos"][0]["mapping"] == {"kind": "movie", "item_id": 101}
    assert client.delete(episode_url, auth=ADMIN).status_code == 200
    assert client.delete(episode_url, auth=ADMIN).status_code == 404
    assert client.delete("/admin/api/vod/movies/101", auth=ADMIN).status_code == 200
    assert client.get("/admin/api/integrations/moobee-stream/mappings",
                      auth=ADMIN).json()["mappings"] == []


def test_series_delete_cleans_episode_mapping(setup):
    client, root, monkeypatch = setup
    init_catalog(client, root)
    configure(monkeypatch)
    attach(monkeypatch, lambda req: httpx.Response(200, json=episode_record("ready")))
    uri = "/admin/api/integrations/moobee-stream/mappings/episode/301"
    assert client.put(uri, auth=ADMIN, json={
        "video_id": EPISODE_UUID, "enabled": True}).status_code == 200
    assert client.delete("/admin/api/vod/series/201", auth=ADMIN).status_code == 200
    assert client.get("/admin/api/integrations/moobee-stream/mappings",
                      auth=ADMIN).json()["mappings"] == []


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"}),
    httpx.Response(200, text="html", headers={"Content-Type": "text/html"}),
    httpx.Response(200, content=b"{" + b"A" * (stream.MAX_PAGE_BYTES + 1),
                   headers={"Content-Type": "application/json"}),
    httpx.Response(200, text="{oops", headers={"Content-Type": "application/json"}),
    httpx.Response(200, json={"videos": [], "nextCursor": MOVIE_UUID}),
    httpx.Response(200, json={"videos": [movie_record(), movie_record()], "nextCursor": None}),
    httpx.Response(200, json={"videos": [{"id": "invalid", "title": "x",
                                          "mediaType": "movie", "status": "ready"}],
                              "nextCursor": None}),
])
def test_malicious_or_broken_upstream_fails_closed(monkeypatch, response):
    configure(monkeypatch)
    requests = []
    def handler(req):
        requests.append(req)
        return response
    attach(monkeypatch, handler)
    with pytest.raises(HTTPException) as caught:
        stream.fetch_catalog(max_pages=1)
    assert caught.value.status_code == 502
    assert len(requests) == 1


def test_missing_or_weak_service_configuration_fails_closed(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("MOOBEE_STREAM_BASE_URL", "http://127.0.0.1:8080/admin")
    with pytest.raises(HTTPException) as caught:
        stream.configured_client()
    assert caught.value.status_code == 503
    monkeypatch.setenv("MOOBEE_STREAM_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("MOOBEE_STREAM_SERVICE_KEY", "short")
    with pytest.raises(HTTPException) as caught:
        stream.configured_client()
    assert caught.value.status_code == 503
    monkeypatch.setenv("MOOBEE_STREAM_ENABLED", "false")
    with pytest.raises(HTTPException) as caught:
        stream.configured_client()
    assert caught.value.status_code == 503
