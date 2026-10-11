import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from main import app

def test_public_capabilities_are_versioned_and_truthful():
    response = TestClient(app).get("/.well-known/moobee-capabilities")
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == 1
    assert data["service"] == "moobee-iptv"
    assert data["streaming"]["xtream_player_api"] == "/player_api.php"
    assert data["features"]["live_tv"] is True
    assert data["features"]["vod"] is False
    assert data["features"]["xmltv_program_data"] is False
    assert data["streaming"]["external_hls_ingest"] is False
    assert data["administration"]["discovery_url"] is None
    forbidden = ("password", "secret", "100.99.", "127.0.0.1", "admin.moobee.lol")
    assert not any(x in response.text.lower() for x in forbidden)
