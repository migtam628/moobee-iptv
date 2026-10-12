import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main

ADMIN = ("admin", "this-is-a-test-password-1234567890")
VIEWER = {"username": "vodtester", "password": "viewer-password-12345678"}


def test_vod_catalog_xtream_and_authenticated_playback(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for folder in ("movie1", "episode1"):
            media = root / "media" / folder
            media.mkdir(parents=True)
            (media / "index.m3u8").write_text(
                "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:6.0,\nsegment001.ts\n"
                "#EXT-X-ENDLIST\n", encoding="utf-8")
            (media / "segment001.ts").write_bytes(b"authorized-segment")
        monkeypatch.setattr(main, "DATA", root)
        monkeypatch.setattr(main, "DB", root / "test.sqlite3")
        monkeypatch.setattr(main, "MEDIA", root / "media")
        monkeypatch.setenv("ADMIN_USERNAME", ADMIN[0])
        monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
        client = TestClient(main.app)
        assert client.get("/admin/api/vod").status_code == 401
        assert client.post("/admin/api/vod/movies",json={
            "id":7,"name":"Private film","category":"Films","folder":"movie1"
        }).status_code == 401
        u = client.post("/admin/api/users", auth=ADMIN, json=VIEWER)
        assert u.status_code == 201, u.text
        missing = client.post("/admin/api/vod/movies",auth=ADMIN,json={
            "id":1,"name":"Missing","category":"Films","folder":"missing"
        })
        assert missing.status_code == 422
        bad_poster = client.post("/admin/api/vod/movies",auth=ADMIN,json={
            "id":1,"name":"Bad Poster","category":"Films","folder":"movie1",
            "poster":"http://not-https.example.com/img.png"
        })
        assert bad_poster.status_code == 422
        movie = client.post("/admin/api/vod/movies",auth=ADMIN,json={
            "id":7,"name":"Private film","category":"Films","folder":"movie1",
            "description":"Owned test media","release_year":2025
        })
        assert movie.status_code == 201, movie.text
        assert client.post("/admin/api/vod/movies",auth=ADMIN,json={
            "id":7,"name":"Duplicate","category":"Films","folder":"movie1"
        }).status_code == 409
        series = client.post("/admin/api/vod/series",auth=ADMIN,json={
            "id":8,"name":"Private series","category":"Shows"
        })
        assert series.status_code == 201, series.text
        assert client.post("/admin/api/vod/episodes",auth=ADMIN,json={
            "id":9,"series_id":999,"name":"Episode 1",
            "folder":"episode1","season":1,"episode":1
        }).status_code == 404
        episode = client.post("/admin/api/vod/episodes",auth=ADMIN,json={
            "id":9,"series_id":8,"name":"Episode 1",
            "folder":"episode1","season":1,"episode":1
        })
        assert episode.status_code == 201, episode.text
        catalog = client.get("/admin/api/vod",auth=ADMIN).json()
        assert len(catalog["movies"]) == len(catalog["series"]) == len(catalog["episodes"]) == 1

        def action(name, **kwargs):
            return client.get("/player_api.php",params={**VIEWER,"action":name,**kwargs})
        assert action("get_vod_categories").json()[0]["category_id"] == "Films"
        streams = action("get_vod_streams").json()
        assert len(streams) == 1
        assert streams[0]["container_extension"] == "m3u8"
        assert action("get_vod_streams",category_id="other").json() == []
        assert action("get_vod_info",vod_id=7).json()["movie_data"]["stream_id"] == 7
        assert action("get_series_categories").json()[0]["category_id"] == "Shows"
        assert action("get_series").json()[0]["series_id"] == 8
        details = action("get_series_info",series_id=8).json()
        assert details["episodes"]["1"][0]["id"] == "9"
        assert action("get_series_info",series_id=999).status_code == 404
        assert action("get_vod_info",vod_id=999).status_code == 404
        assert client.get("/player_api.php",params={"action":"get_vod_streams"}).status_code == 401

        movie_url="/movie/vodtester/viewer-password-12345678/7.m3u8"
        episode_url="/series/vodtester/viewer-password-12345678/9.m3u8"
        movie_hls=client.get(movie_url)
        assert movie_hls.status_code == 200, movie_hls.text
        assert "/vod-hls/vodtester/viewer-password-12345678/movie/7/segment001.ts" in movie_hls.text
        assert client.get(episode_url).status_code == 200
        segment = client.get("/vod-hls/vodtester/viewer-password-12345678/movie/7/segment001.ts")
        assert segment.status_code == 200
        assert segment.content == b"authorized-segment"
        assert client.get("/movie/vodtester/wrong-password/7.m3u8").status_code == 401
        assert client.get("/vod-hls/vodtester/wrong-password/episode/9/segment001.ts").status_code == 401
        assert client.get("/movie/vodtester/viewer-password-12345678/999.m3u8").status_code == 404
        assert client.get("/vod-hls/vodtester/viewer-password-12345678/movie/7/notthere.ts").status_code == 404
        assert client.get("/vod-hls/vodtester/viewer-password-12345678/movie/7/%2E%2Esecret.ts").status_code in (400, 404)

        # Unsupported remote/nested/encrypted playlists must not be rewritten into public routes.
        manifest = root / "media" / "movie1" / "index.m3u8"
        manifest.write_text("#EXTM3U\nhttps://elsewhere.example/live.ts\n")
        assert client.get(movie_url).status_code == 422
        manifest.write_text("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key\"\nsegment001.ts\n")
        assert client.get(movie_url).status_code == 501
        manifest.write_text("#EXTM3U\nsegment001.ts\n")
        assert client.get(movie_url).status_code == 200

        assert client.delete("/admin/api/vod/episodes/9",auth=ADMIN).status_code == 200
        assert action("get_series_info",series_id=8).json()["episodes"] == {}
        assert client.delete("/admin/api/vod/series/8",auth=ADMIN).status_code == 200
        assert client.delete("/admin/api/vod/movies/7",auth=ADMIN).status_code == 200
        assert action("get_vod_streams").json() == []
        assert (root / "media" / "movie1" / "index.m3u8").exists()


def test_vod_rejects_symlinks_outside_media(monkeypatch):
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp)
        (base/"media").mkdir()
        (base/"outside").mkdir()
        (base/"outside"/"index.m3u8").write_text("#EXTM3U\n")
        (base/"media"/"malicious").symlink_to(base/"outside",target_is_directory=True)
        monkeypatch.setattr(main,"DATA",base)
        monkeypatch.setattr(main,"DB",base/"db.sqlite3")
        monkeypatch.setattr(main,"MEDIA",base/"media")
        monkeypatch.setenv("ADMIN_USERNAME",ADMIN[0])
        monkeypatch.setenv("ADMIN_PASSWORD",ADMIN[1])
        c=TestClient(main.app)
        assert c.post("/admin/api/vod/movies",auth=ADMIN,json={
            "id":1,"name":"Symlink","category":"Films","folder":"malicious"
        }).status_code in (404,422)
