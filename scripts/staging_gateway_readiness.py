"""Low-impact stage-only HLS readiness probe.

Simulates up to 3 simultaneous viewers requesting one HLS segment each.
Never prints Xtream credentials, HLS URLs, HTTP errors or HTTP response bodies.
Does not write the staging SQLite database or touch production.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import re
from pathlib import Path
from statistics import median, mean
import sys
import time
from urllib.parse import quote, urlsplit

import httpx

BASE = os.environ.get("MOOBEE_STAGE_LOOPBACK_URL", "http://127.0.0.1:18000")
ACCESS = Path("/stage-access.txt")
MAX_BYTES = 16 * 1024 * 1024
MAX_MASTER_BYTES = 256_000


class ProbeError(Exception):
    pass


def require(condition, label):
    if not condition:
        raise ProbeError(label)


def access():
    raw = ACCESS.read_text(encoding="utf-8").splitlines()
    d = dict(line.split(": ", 1) for line in raw if ": " in line)
    require(d.get("Server") == BASE and
            d.get("Username") == "moobee-stage" and
            len(d.get("Password", "")) >= 24, "wrong staging credentials")
    return d["Username"], d["Password"]


def request(client, url, *, max_bytes):
    start = time.monotonic()
    # No redirects. Buffer bounded responses and never print URLs/exceptions.
    with client.stream("GET", url, follow_redirects=False) as r:
        require(r.status_code == 200, "unexpected gateway HTTP status")
        require(r.headers.get("location") is None, "redirect forbidden")
        data = bytearray()
        for chunk in r.iter_bytes():
            data.extend(chunk)
            require(len(data) <= max_bytes, "oversized gateway response")
    return bytes(data), (time.monotonic() - start) * 1000


def first_path(playlist, user, password, *, rendition):
    try:
        s = playlist.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ProbeError("invalid UTF-8 HLS manifest") from e
    require(s.startswith("#EXTM3U"), "invalid HLS header")
    require("moobee-stream-vps" not in s and "/hls/" not in s,
            "upstream origin or signed path leaked")
    require("x-service-key" not in s.lower(), "header name leaked")
    lines = [line.strip() for line in s.splitlines()
             if line.strip() and not line.startswith("#")]
    require(bool(lines), "HLS manifest has no assets")
    require(user not in s and password not in s,
            "subscriber credentials leaked in media playlist")
    # Bearer tokens are credentials too; validate paths without logging them.
    token_pattern = re.compile(r"[A-Za-z0-9_-]{80,700}\.[A-Za-z0-9_-]{43}")
    for line in lines:
        url = urlsplit(line)
        require(url.scheme == "http" and url.netloc == urlsplit(BASE).netloc
                and not url.query and not url.fragment,
                "unexpected media origin")
        if rendition:
            path_pattern = (r"/stream-vod-t/" + token_pattern.pattern
                            + r"/(?:movie/101|episode/301)/[A-Za-z0-9_-]{1,40}/index\.m3u8")
        else:
            path_pattern = (r"/stream-vod-t/" + token_pattern.pattern
                            + r"/(?:movie/101|episode/301)/[A-Za-z0-9_-]{1,40}/segment_[0-9]{5}\.ts")
        require(re.fullmatch(path_pattern, url.path) is not None,
                "unexpected signed-token media path")
    return lines[0]


def attempt(kind, user, password):
    if kind == "movie":
        path = "/movie/" + quote(user, safe="") + "/" + quote(password, safe="") + "/101.m3u8"
    else:
        path = "/series/" + quote(user, safe="") + "/" + quote(password, safe="") + "/301.m3u8"
    timings, total_bytes = [], 0
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=20.0,
                      headers={"User-Agent": "MooBee-Readiness-Probe/1"}) as client:
        master, ms = request(client, BASE + path, max_bytes=MAX_MASTER_BYTES)
        timings.append(ms)
        playlist_url = first_path(master, user, password, rendition=True)
        media, ms = request(client, playlist_url, max_bytes=MAX_MASTER_BYTES)
        timings.append(ms)
        segment_url = first_path(media, user, password, rendition=False)
        segment, ms = request(client, segment_url, max_bytes=MAX_BYTES)
        timings.append(ms)
        require(len(segment) >= 188 and segment[0] == 0x47,
                "invalid MPEG-TS segment")
        total_bytes += len(segment)
    return kind, timings, total_bytes


def run(viewers, rounds):
    require(BASE in ("http://127.0.0.1:18000", "http://127.0.0.1:18001"),
            "unsupported staging port")
    require(os.environ.get("MOOBEE_STAGE_READINESS_ACK") == "isolated",
            "isolated stage acknowledgement missing")
    require(not os.environ.get("MOOBEE_STREAM_SERVICE_KEY"),
            "service key must not be supplied to client probe")
    user, password = access()
    print("Staging URL: loopback-only (credentials hidden)")
    with httpx.Client(trust_env=False, timeout=10) as client:
        healthy = client.get(BASE + "/healthz")
        require(healthy.status_code == 200, "staging health check failed")
        bad_path = ("/movie/" + quote(user, safe="") +
                    "/invalid-password/101.m3u8")
        denied = client.get(BASE + bad_path)
        require(denied.status_code == 401, "bad subscriber password accepted")
    print("PASS: staging health and unauthorized access")

    # All phases are bounded and serial. Each worker fetches only one TS
    # segment and exits. No long-lived streaming or ramp beyond 3 viewers.
    for count in range(1, viewers + 1):
        jobs = [("movie" if i % 2 == 0 else "episode")
                for i in range(count * rounds)]
        durations = []
        segment_bytes = 0
        with ThreadPoolExecutor(max_workers=count) as executor:
            results = list(executor.map(
                lambda kind: attempt(kind, user, password), jobs
            ))
        for kind, elapsed, size in results:
            durations.extend(elapsed)
            segment_bytes += size
        require(bool(durations), "no latency data")
        ordered = sorted(durations)
        p95 = ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]
        print(f"PASS: {count} concurrent simulated viewer(s), "
              f"{len(results)} stream sessions, {segment_bytes} segment bytes, "
              f"requests={len(durations)}, "
              f"median_ms={median(durations):.0f}, "
              f"mean_ms={mean(durations):.0f}, p95_ms={p95:.0f}")
        time.sleep(0.3)
    print("PASS: bounded readiness probe finished; no staging data changed.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-viewers", type=int, choices=(1, 2, 3), default=3)
    p.add_argument("--rounds", type=int, choices=(1, 2), default=2)
    args = p.parse_args()
    try:
        run(args.max_viewers, args.rounds)
    except Exception as e:
        print("READINESS FAIL:", type(e).__name__,
              file=sys.stderr)
        sys.exit(1)
