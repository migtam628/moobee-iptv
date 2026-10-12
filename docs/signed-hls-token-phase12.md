# Phase 12 — Replace passwords in proxied HLS playlists with signed tokens

This work remains **draft and isolated from the known-good staging gateway**.

## Model

- StreamBoss's initial Xtream URL `/movie/{username}/{password}/{id}.m3u8` or `/series/{username}/{password}/{id}.m3u8` is **unchanged**, preserving native Xtream compatibility.
- Only after validating the Xtream credentials, operator flags and an enabled/ready upstream mapping does the gateway issue a new **signed bearer token** scoped to `movie|episode + local id + upstream UUID + subscriber/account generation`.
- The master response and all child HLS rendition/segment URLs use `/stream-vod-t/{token}/{kind}/{id}/...`, not raw username/password.
- Tokens are HMAC-SHA256 signed with **a separate operator-owned secret** (`MOOBEE_STREAM_HLS_TOKEN_SECRET`, at least 32 bytes); the private Stream service key is not reused.
- Each rendition/segment request checks token integrity/expiry, live subscriber disabled state, hashed-password/salt account binding, **and** the currently enabled/ready mapped UUID. Changing the password, disabling/recreating a user, changing or removing a mapping, disabling either operator feature gate or rotating the token secret revokes further requests.
- Fixed TTL **4 hours** is chosen for long movie playback; tokens are **bearer credentials** (not encrypted, not single-use or device-bound) and can be replayed by whoever obtains one until revoked/expired. No refresh/renewal flow is implemented, so playback after 4h is expected to fail: a release blocker for long sessions.
- No old `/stream-vod/{username}/{password}/...` routes remain in this branch.

## Known limitations and release gates

- **Initial Xtream playlist request still embeds username/password in its path.** StreamBoss's protocol generates that URL; protecting it end to end needs client/server changes or a gateway exchange. Do not claim passwords have been eliminated from all URLs.
- **Bearer tokens also appear in URL paths.** Disable or redact path logging for the relevant URLs at reverse proxy, app, error reporting and analytics layers, avoid sharing playlists or screenshots with complete URLs. Set `Cache-Control: private, no-store` and `Referrer-Policy: no-referrer`. Avoid public HTTP: only the localhost stage carried over SSH is currently allowed.
- Implement a refresh strategy or a suitably bound session for VOD longer than four hours.
- Session/connection limits are still not enforced. Token scopes one asset only and can be replayed; concurrency monitoring and limits are a separate production blocker.
- Signed tokens do **not** remove current per-segment upstream resolver calls/buffering. CPU, memory, load and latency still require benchmarking.
- Running processes need a stable secret across restarts/replicas. Lost/rotated secret intentionally invalidates active tokenized playlists; seed secret should never be in repo or printed to logs.
- Run CI/security review, one-shot real-upstream smoke test, then a completely separate **port-18001** staging instance for actual StreamBoss macOS playback/seek/revocation. Keep the original known-good staging port 18000 untouched.
- This proposal is **not merged or deployed** to production `vps3584464`.

## First smoke test: ephemeral (new Stream VPS checkout)

```bash
cd /opt
git clone --single-branch --branch feat/streamboss-hls-signed-tokens-20261012 \
  https://github.com/migtam628/moobee-iptv.git moobee-iptv-token-stage
cd /opt/moobee-iptv-token-stage
docker build -t moobee-iptv-token-stage:20261012 .

(
  cd /opt/moobee-stream || exit 1
  source .env || exit 1
  export MOOBEE_STREAM_SERVICE_KEY="$STREAM_SERVICE_API_KEY"
  export MOOBEE_STREAM_BASE_URL="https://moobee-stream-vps.tail08e9d7.ts.net"
  export MOOBEE_STREAM_TAILSCALE_HOSTNAME="moobee-stream-vps.tail08e9d7.ts.net"
  cd /opt/moobee-iptv-token-stage || exit 1
  docker run --rm --network host --read-only \
    --tmpfs /tmp:rw,nosuid,size=256m \
    --cap-drop ALL --security-opt no-new-privileges \
    -e MOOBEE_STREAM_SERVICE_KEY \
    -e MOOBEE_STREAM_BASE_URL \
    -e MOOBEE_STREAM_TAILSCALE_HOSTNAME \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$PWD/scripts/staging_private_stream_smoke.py:/smoke.py:ro" \
    --entrypoint python moobee-iptv-token-stage:20261012 /smoke.py
)
```

The one-shot smoke generates a random signing secret **in memory** and a synthetic user/mapping database under `/tmp` (automatically deleted), then proves real upstream movie+episode HLS retrieval and fail-closed revocation. The Stream service key is injected only to the staging process without printing it. No ports are opened and production containers are untouched.

## Separate live Mac test — port 18001 (ONLY after smoke passes)

After the ephemeral test passes, create a new isolated staging account/database under **this checkout's** `runtime` folder, using `PUBLIC_URL=http://127.0.0.1:18001` in the seed and server commands. Start a new one-shot named Docker container on `127.0.0.1:18001`. Use a stable, independently generated `MOOBEE_STREAM_HLS_TOKEN_SECRET` set on this new staging container (not the Stream service key). Establish a distinct Mac SSH tunnel:

```bash
ssh -o ExitOnForwardFailure=yes -N \
  -L 127.0.0.1:18001:127.0.0.1:18001 root@209.159.148.172
```

Only test StreamBoss against `http://127.0.0.1:18001` with the generated temporary account. The known-good staging instance on port 18000 must remain untouched for comparison and rollback.

The old and new stage ports use **localhost-only** HTTP carried through SSH. Never expose either port directly to the internet or tailnet. Before production use, token URLs require the public-facing HTTPS gateway with path scrubbing in logs.
