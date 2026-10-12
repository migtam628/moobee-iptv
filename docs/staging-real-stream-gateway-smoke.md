# Isolated real-upstream smoke test

This is **not** a production deployment or StreamBoss client UI test.
The script `scripts/staging_private_stream_smoke.py` runs FastAPI's
`TestClient` against an ephemeral SQLite DB and disposable local media.
The origin is the real private MooBee Stream Tailscale Serve endpoint.
It does not create listening sockets, change production containers, or use
existing IPTV subscriber credentials.

## Prerequisites

- The Stream VPS is already authenticated to the same tailnet.
- API, database, HLS and signed segment delivery are healthy on Stream.
- Synthetic movie `efd11f2b-fbc9-4259-a713-81afc4c90bda` and episode
  `1eb75e2b-b46a-40f9-8d80-eb57747e51b0` have status `ready`.
- Use the unique `STREAM_SERVICE_API_KEY` from Stream, without printing it.

## Run on separate MooBee Stream VPS only

Clone this staging branch to **a new directory** and build the API image:

```bash
cd /opt
git clone --branch staging/streamboss-tailnet-e2e-20261012 --single-branch \
  https://github.com/migtam628/moobee-iptv.git moobee-iptv-gateway-stage
cd /opt/moobee-iptv-gateway-stage
docker build -t moobee-iptv-gateway-stage:20261012 .
```

Run one disposable staging test container with no published ports:

```bash
(
  cd /opt/moobee-stream
  source .env
  export MOOBEE_STREAM_SERVICE_KEY="$STREAM_SERVICE_API_KEY"
  export MOOBEE_STREAM_BASE_URL="https://moobee-stream-vps.tail08e9d7.ts.net"
  export MOOBEE_STREAM_TAILSCALE_HOSTNAME="moobee-stream-vps.tail08e9d7.ts.net"
  cd /opt/moobee-iptv-gateway-stage
  docker run --rm --network host \
    --read-only --tmpfs /tmp:rw,nosuid,size=256m \
    --cap-drop ALL --security-opt no-new-privileges \
    -e MOOBEE_STREAM_SERVICE_KEY \
    -e MOOBEE_STREAM_BASE_URL \
    -e MOOBEE_STREAM_TAILSCALE_HOSTNAME \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$PWD/scripts/staging_private_stream_smoke.py:/staging-smoke.py:ro" \
    --entrypoint python \
    moobee-iptv-gateway-stage:20261012 /staging-smoke.py
)
```

The test only binds the real Stream's private HTTPS origin for outbound
requests. `--network host` has no listening service here: TestClient runs
in-process. Docker host networking is selected only for this one-shot smoke,
not as a prescription for the deployed IPTV service.

## What passes mean

- Separate opt-in flags preserve ordinary local VOD until enabled.
- Both mapping types retrieve real signed upstream playlists and at least
  one real MPEG-TS segment using the subscriber-authenticated gateway.
- Rewritten manifests omit upstream URLs, signing tokens and service keys.
- Incorrect/disabled users fail, removing a mapping stops subsequent remote
  segment access, and disabling playback restores local movie behavior.

No API port is exposed, no browser is used, and StreamBoss itself is not
exercised. For deployment, separately verify the bridge-network container
route and perform real StreamBoss client E2E, account revocation and load
testing. Production MooBee IPTV remains unchanged, especially its
`compose.yaml + compose.hostnet.yaml` host-network Caddy overlay.
