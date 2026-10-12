import base64
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main
from epg_core import parse_xmltv, parse_time

ADMIN = ("admin", "this-is-a-test-password-1234567890")
VIEWER = {"username":"epgtester","password":"viewer-password-12345678"}

def xml_sample():
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=15)
    end = start + timedelta(minutes=60)
    fmt = lambda d: d.strftime("%Y%m%d%H%M%S +0000")
    return (f'<?xml version="1.0"?><tv><channel id="demo.tv">'
            f'<display-name>Demo &amp; News</display-name></channel>'
            f'<programme channel="demo.tv" start="{fmt(start)}" stop="{fmt(end)}">'
            f'<title>News &amp; Weather</title><desc>Current programme</desc>'
            f'</programme></tv>')

@pytest.mark.parametrize("source", [
    '<!DOCTYPE tv [<!ENTITY x "boom">]><tv></tv>',
    '<tv><channel id="news"><display-name>Test</display-name></channel>'
      '<programme channel="news" start="20261011200000" stop="20261011210000 +0000">'
      '<title>Missing time zone</title></programme></tv>',
    '<tv><channel id="news"><display-name>Test</display-name></channel>'
      '<programme channel="unknown" start="20261011200000 +0000" stop="20261011210000 +0000">'
      '<title>Unknown channel</title></programme></tv>',
    '<tv><channel id="invalid id"><display-name>Test</display-name></channel></tv>',
    '<tv><channel id="news"><display-name>Test</display-name></channel><channel id="news">'
      '<display-name>Duplicate</display-name></channel></tv>',
    "<tv><channel id='x'><display-name>bad</tv>",
])
def test_parser_rejects_bad_xml(source):
    with pytest.raises(ValueError):
        parse_xmltv(source)

def test_xmltv_bounds_and_timezone():
    with pytest.raises(ValueError):
        parse_xmltv("<tv>" + " " * 1_000_001 + "</tv>")
    assert parse_time("20261011200000 -0400") == parse_time("20261012000000 +0000")
    with pytest.raises(ValueError):
        parse_time("20261011200000")
    with pytest.raises(ValueError):
        parse_time("20261011200000 +9999")

def test_admin_import_and_xtream_guide(monkeypatch):
    with tempfile.TemporaryDirectory() as temp:
        monkeypatch.setattr(main, "DATA", Path(temp))
        monkeypatch.setattr(main, "DB", Path(temp) / "guide.sqlite3")
        monkeypatch.setenv("ADMIN_USERNAME", ADMIN[0])
        monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
        client = TestClient(main.app)
        assert client.get("/admin/api/epg").status_code == 401
        assert client.post("/admin/api/epg/import", json={"xml":xml_sample()}).status_code == 401
        assert client.post("/admin/api/users",auth=ADMIN,json=VIEWER).status_code == 201
        assert client.post("/admin/api/channels",auth=ADMIN,json={
            "id":5,"name":"Demo stream","category":"Personal","folder":"demo"
        }).status_code == 201
        document = xml_sample()
        preview = client.post("/admin/api/epg/import",auth=ADMIN,json={"xml":document,"dry_run":True})
        assert preview.status_code == 200, preview.text
        assert preview.json()["programmes"] == 1
        assert client.get("/admin/api/epg",auth=ADMIN).json()["programmes"] == 0
        committed = client.post("/admin/api/epg/import",auth=ADMIN,json={"xml":document,"dry_run":False})
        assert committed.status_code == 200, committed.text
        assert committed.json()["channels"] == 1
        assert client.put("/admin/api/epg/mappings/5",auth=ADMIN,json={"xmltv_id":"unknown.tv"}).status_code == 404
        mapped = client.put("/admin/api/epg/mappings/5",auth=ADMIN,json={"xmltv_id":"demo.tv"})
        assert mapped.status_code == 200, mapped.text
        params={**VIEWER,"action":"get_live_streams"}
        live=client.get("/player_api.php",params=params).json()
        assert live[0]["epg_channel_id"] == "demo.tv"
        epg=client.get("/player_api.php",params={**VIEWER,"action":"get_short_epg","stream_id":5})
        assert epg.status_code == 200, epg.text
        listings=epg.json()["epg_listings"]
        assert len(listings) == 1
        assert base64.b64decode(listings[0]["title"]).decode() == "News & Weather"
        assert listings[0]["now_playing"] == 1
        assert client.get("/player_api.php",params={**VIEWER,"action":"get_simple_data_table","stream_id":5}).json()["epg_listings"]
        m3u=client.get("/get.php",params=VIEWER)
        assert 'tvg-id="demo.tv"' in m3u.text
        xml=client.get("/xmltv.php",params=VIEWER)
        assert xml.status_code == 200, xml.text
        tree=ET.fromstring(xml.content)
        assert tree.find("channel").attrib["id"] == "demo.tv"
        assert tree.find("programme/title").text == "News & Weather"
        assert client.get("/admin/api/epg/programmes/5",auth=ADMIN).json()[0]["title"] == "News & Weather"
        # An invalid replacement must not corrupt previous guide data.
        invalid=client.post("/admin/api/epg/import",auth=ADMIN,
                            json={"xml":"<tv><channel id='x'></tv>","dry_run":False})
        assert invalid.status_code == 422
        assert client.get("/admin/api/epg",auth=ADMIN).json()["programmes"] == 1
        # Deleting mapping removes the guide from the subscriber endpoints.
        assert client.put("/admin/api/epg/mappings/5",auth=ADMIN,json={"xmltv_id":None}).status_code == 200
        assert client.get("/player_api.php",params={**VIEWER,"action":"get_short_epg","stream_id":5}).json() == {"epg_listings":[]}
        assert ET.fromstring(client.get("/xmltv.php",params=VIEWER).content).find("channel") is None
