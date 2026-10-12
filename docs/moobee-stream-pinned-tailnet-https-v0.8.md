# Pinned Tailscale Serve HTTPS origin (staging-only design)

## Verified environment (October 12, 2026)

- Stream VPS: Tailscale IPv4 `100.72.201.46`; HTTPS hostname `moobee-stream-vps.tail08e9d7.ts.net`.
- IPTV VPS: Tailscale IPv4 `100.99.174.2`.
- A host-level HTTPS GET to `https://moobee-stream-vps.tail08e9d7.ts.net/api/health` returned 200 from **both** VPS hosts, with a trusted TLS certificate valid for the hostname.
- MooBee Stream origin continues to bind Docker Nginx to `127.0.0.1:8080`. Tailscale Serve routes private HTTPS/443 to that local port. No Funnel or public media origin.

## Opt-in connector configuration

Set the following **only in a staging IPTV backend**, never commit the service key:

```dotenv
MOOBEE_STREAM_ENABLED=true
MOOBEE_STREAM_BASE_URL=https://moobee-stream-vps.tail08e9d7.ts.net
MOOBEE_STREAM_TAILSCALE_HOSTNAME=moobee-stream-vps.tail08e9d7.ts.net
MOOBEE_STREAM_SERVICE_KEY=<distinct-private-stream-service-key>
MOOBEE_STREAM_PLAYBACK_ENABLED=false
```

`MOOBEE_STREAM_TAILSCALE_HOSTNAME` must match the exact Tailscale Serve hostname; it's not a URL. The parser permits only the exact matching HTTPS host on port 443; arbitrary DNS, plain HTTP, redirects, userinfo, URL paths, public IPs and alternate ports remain blocked. Normal TLS certificate validation is required (`verify=True`); proxies remain disabled and redirects are not followed.

## Deployment preconditions

1. In a disposable/isolated API container sharing the intended Docker **bridge** networking, verify DNS resolution, 443 reachability, TLS certificate validation, and authenticated catalog preview. Host-only `curl` proves connectivity of the host, **not** of the future API container.
2. Tailnet DNS must resolve only to the intended private node, and tailnet ACLs should restrict access to the Stream node's port 443. Origin validation constrains the hostname but does not cryptographically pin the resolved IP; review DNS rebinding and egress policy before production.
3. When integration is enabled, use a distinct 32+-byte key from the Stream service, matching the IPTV backend server-side only. It must never reach StreamBoss, browser code, logs, commits, or public URL parameters.
4. Confirm the IPTV API image includes this change. Never just edit `.env` on the live IPTV server and restart it.
5. Until real-movie and real-episode adaptive HLS tests, account revocation, per-viewer controls and load tests pass, keep `MOOBEE_STREAM_PLAYBACK_ENABLED=false` and do **not** merge/deploy the draft playback gateway.
6. Before *any separately approved* IPTV production change, back up SQLite and preserve `docker compose -f compose.yaml -f compose.hostnet.yaml`. No Caddy/Tailscale/firewall modifications on the existing IPTV VPS.

## Rollback

Leave `MOOBEE_STREAM_ENABLED=false` and `MOOBEE_STREAM_PLAYBACK_ENABLED=false` on production. The change only widens a disabled-by-default **operator-configured** origin allowlist and does not introduce new public routes or alter existing local playback.
