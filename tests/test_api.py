import os
import sys
import tempfile
import importlib
from pathlib import Path
from fastapi.testclient import TestClient

def test_stream_and_admin_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        import main
        monkeypatch.setattr(main, "DATA", Path(tmp))
        monkeypatch.setattr(main, "DB", Path(tmp) / "test.sqlite3")
        monkeypatch.setattr(main, "MEDIA", Path(tmp) / "media")
        monkeypatch.setenv("ADMIN_USERNAME", "admin")
        monkeypatch.setenv("ADMIN_PASSWORD", "this-is-a-test-password-1234567890")
        client=TestClient(main.app)
        assert client.get("/healthz").json() == {"status":"ok"}
        assert client.get("/admin/api/users").status_code == 401
        auth=("admin","this-is-a-test-password-1234567890")
        resp=client.post("/admin/api/users",auth=auth,json={"username":"tester","password":"test-password-12345678"})
        assert resp.status_code==201,resp.text
        assert client.get("/player_api.php",params={"username":"tester","password":"test-password-12345678"}).status_code==200
        assert client.post("/admin/api/channels",auth=auth,json={"id":1,"name":"Test","category":"Personal","folder":"test"}).status_code==201
        assert len(client.get("/player_api.php",params={"username":"tester","password":"test-password-12345678","action":"get_live_streams"}).json())==1
        assert client.patch("/admin/api/users/tester",auth=auth,json={"disabled":True}).status_code==200
        assert client.get("/player_api.php",params={"username":"tester","password":"test-password-12345678"}).status_code==401
        assert client.delete("/admin/api/channels/1",auth=auth).status_code==200
        assert client.get("/admin/api/overview",auth=auth).status_code==200
