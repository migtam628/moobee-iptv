# StreamBoss Mac manual E2E: SSH-tunneled isolated staging

This setup runs a separate staging API **on the MooBee Stream VPS** only.
It never connects to the live MooBee IPTV SQLite database, restarts live
IPTV Caddy, or makes Stream's signed HLS origin public.

Prerequisites:
- Successful `scripts/staging_private_stream_smoke.py` run.
- The Stream VPS is in the same Tailscale network as the upstream Serve hostname.
- Synthetic movie and TV episode are `ready` on the private Stream catalog.
- The user has the StreamBoss macOS desktop app with Xtream source support.

## 1. Prepare staging files on Stream VPS

```bash
cd /opt/moobee-iptv-gateway-stage
git pull --ff-only origin staging/streamboss-tailnet-e2e-20261012
docker build -t moobee-iptv-gateway-stage:20261012 .

# New staging-only folders; stop if /opt/.../runtime already exists.
if [ -e runtime ]; then echo "STOP: runtime already exists"; else
  mkdir -p runtime/data runtime/media
  chown 10001:10001 runtime/data runtime/media
  chmod 700 runtime/data runtime/media
fi

docker run --rm \
  --network host --read-only --tmpfs /tmp:rw,nosuid,size=128m \
  --cap-drop ALL --security-opt no-new-privileges \
  -e MOOBEE_STAGE_SETUP_ACK=isolated \
  -e PUBLIC_URL=http://127.0.0.1:18000 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD/runtime/data:/data" \
  -v "$PWD/runtime/media:/media" \
  -v "$PWD/scripts/prepare_streamboss_stage.py:/staging-setup.py:ro" \
  --entrypoint python moobee-iptv-gateway-stage:20261012 /staging-setup.py
```

Expected: staging files initialized with two mapped ready records and a synthetic subscriber. Access details stored at `runtime/data/stage-access.txt` with permission 0600. Do not paste its content into chat.

## 2. Start staging IPTV on localhost only

```bash
(
  cd /opt/moobee-stream || exit 1
  source .env || exit 1
  export MOOBEE_STREAM_SERVICE_KEY="$STREAM_SERVICE_API_KEY"
  export MOOBEE_STREAM_BASE_URL="https://moobee-stream-vps.tail08e9d7.ts.net"
  export MOOBEE_STREAM_TAILSCALE_HOSTNAME="moobee-stream-vps.tail08e9d7.ts.net"
  cd /opt/moobee-iptv-gateway-stage || exit 1

  docker run --rm -d --name moobee-iptv-stage \
    --network host --read-only --tmpfs /tmp:rw,nosuid,size=128m \
    --cap-drop ALL --security-opt no-new-privileges \
    -e MOOBEE_STREAM_SERVICE_KEY \
    -e MOOBEE_STREAM_BASE_URL \
    -e MOOBEE_STREAM_TAILSCALE_HOSTNAME \
    -e MOOBEE_STREAM_ENABLED=true \
    -e MOOBEE_STREAM_PLAYBACK_ENABLED=true \
    -e PUBLIC_URL=http://127.0.0.1:18000 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$PWD/runtime/data:/data" \
    -v "$PWD/runtime/media:/media:ro" \
    --entrypoint uvicorn \
    moobee-iptv-gateway-stage:20261012 \
    main:app --host 127.0.0.1 --port 18000 --no-access-log
)

curl -fsS http://127.0.0.1:18000/healthz
ss -lnt | grep ':18000 '
```

Check listener is **127.0.0.1:18000** only. If address is any public or tailnet interface, stop the container immediately.

## 3. On your Mac, create a private SSH tunnel

```bash
ssh -N -L 18000:127.0.0.1:18000 root@209.159.148.172
```

Keep the SSH terminal open. On a **second** Mac terminal, check:
```bash
curl -fsS http://127.0.0.1:18000/healthz
```

Display temporary Xtream credentials **only on the Stream VPS terminal**:
```bash
cat /opt/moobee-iptv-gateway-stage/runtime/data/stage-access.txt
```

In StreamBoss on the Mac add a separate Xtream source using the
`http://127.0.0.1:18000` server and temporary username/password.
Expect movie ID 101 and a series episode ID 301. Play both; seek, pause,
resume, monitor player codec and network errors. Do not modify your existing
production source.

The tunnel is essential: the stage API emits absolute `http://127.0.0.1:18000`
HLS URLs, which the Mac resolves through the forwarded port.
SSH tunnel forwards only the staging API's loopback socket.
A different phone/TV won't resolve this to the Mac staging tunnel.

## Stop and cleanup

```bash
docker stop moobee-iptv-stage
```

Tunnel ends when the SSH terminal is closed. After testing, the staging
`runtime/` folders can be destroyed **only** if the isolated test credentials
and records are no longer needed. Never issue `docker compose down -v` on the
MooBee Stream stack; it would delete application volumes.

## Limitations

This is functional E2E testing, **not** a production deployment. It uses
temporary synthetic subscriber credentials in Xtream URL paths and a
loopback-only HTTP test server carried over SSH. The existing production
IPTV server remains on the original compose + host-network overlay.
Before general rollout, verify real StreamBoss playback, credential handling
and access logging, and bridge-network container behavior, including load.
