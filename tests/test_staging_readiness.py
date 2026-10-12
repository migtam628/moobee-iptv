"""Static and isolated tests for the bounded staging-readiness client."""
import importlib.util
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "staging_gateway_readiness.py"
spec = importlib.util.spec_from_file_location("stage_readiness_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_script_syntax_and_credentials_are_stage_only(tmp_path, monkeypatch):
    compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec")
    path = tmp_path / "stage-access.txt"
    path.write_text("Server: http://127.0.0.1:18000\n"
                    "Username: moobee-stage\n"
                    "Password: " + "a" * 28 + "\n")
    monkeypatch.setattr(probe, "ACCESS", path)
    assert probe.access() == ("moobee-stage", "a" * 28)
    path.write_text("Server: http://example.org\nUsername: moobee-stage\n"
                    "Password: " + "a" * 28 + "\n")
    with pytest.raises(probe.ProbeError):
        probe.access()


def test_manifest_reference_is_loopback_only():
    username = "moobee-stage"
    password = "a" * 28
    token = "A" * 180 + "." + "B" * 43
    prefix = ("http://127.0.0.1:18000/stream-vod-t/"
              + token + "/movie/101/")
    manifest = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=200000\n"
                + prefix + "480p/index.m3u8\n")
    assert probe.first_path(
        manifest.encode(), username, password, rendition=True
    ).endswith("480p/index.m3u8")
    playlist = ("#EXTM3U\n#EXTINF:5,\n" +
                prefix + "480p/segment_00001.ts\n")
    assert probe.first_path(
        playlist.encode(), username, password, rendition=False
    ).endswith("segment_00001.ts")
    for bad in (
        manifest.replace("127.0.0.1:18000", "example.com"),
        manifest.replace(token, "invalid-token"),
        manifest.replace("480p/index.m3u8", "480p/segment_00001.ts"),
        manifest.replace("http://127.0.0.1", "https://127.0.0.1"),
        manifest.replace("#EXTM3U", "#EXT-X-KEY"),
        manifest.replace("movie/101", "movie/999"),
        manifest.replace(token, username + "/" + password),
        manifest + "https://moobee-stream-vps.tail08e9d7.ts.net/hls/x",
    ):
        with pytest.raises(probe.ProbeError):
            probe.first_path(bad.encode(), username, password, rendition=True)


def test_request_limits_and_redirects():
    def client(status=200, content=b"ok", headers=None):
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(status, content=content,
                                            headers=headers or {})
            ), trust_env=False
        )
    with client(content=b"ok") as c:
        body, duration = probe.request(c, "http://127.0.0.1:18000/x",
                                       max_bytes=5)
        assert body == b"ok" and duration >= 0
    with client(content=b"oversized") as c:
        with pytest.raises(probe.ProbeError):
            probe.request(c, "http://127.0.0.1:18000/x", max_bytes=3)
    with client(status=302, headers={"location": "http://example.org"}) as c:
        with pytest.raises(probe.ProbeError):
            probe.request(c, "http://127.0.0.1:18000/x", max_bytes=10)


def test_stage_ack_required(monkeypatch):
    monkeypatch.delenv("MOOBEE_STAGE_READINESS_ACK", raising=False)
    with pytest.raises(probe.ProbeError):
        probe.run(1, 1)
