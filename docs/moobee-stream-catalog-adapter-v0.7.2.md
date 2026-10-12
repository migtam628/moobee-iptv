# MooBee IPTV ↔ MooBee Stream — catalog adapter v0.7.2

**Implementation, not deployment.** This increment creates a **disabled-by-default, admin-only, read-only preview** of MooBee Stream metadata with **manual UUID mapping** onto existing MooBee IPTV movie/episode records. It does **not** automatically import new movies/episodes, download assets, change Xtream responses, route remote playback, or modify existing local HLS. The only supported client in the active platform is **StreamBoss**.

## Dependency

MooBee Stream's [private API security PR #1](https://github.com/migtam628/moobee-stream/pull/1) provides the proposed endpoints:

- `GET /api/v1/internal/videos` returns up to 100 sanitized videos and an optional `nextCursor` UUID.
- `GET /api/v1/internal/videos/{uuid}` returns one normalized record.
- Both require `x-service-key`, **not** the Stream upload/delete `x-api-key`.

That PR must be merged, tested and privately deployed **before** enabling this adapter. The current stable MooBee Stream `main` does not yet have these endpoints. Do not use its unauthenticated playback resolver as a substitute. The separate [integration plan PR #8](https://github.com/migtam628/moobee-iptv/pull/8) is complementary documentation.

## Disabled by default

```dotenv
# These values are empty/off unless specifically supplied via private .env.
MOOBEE_STREAM_ENABLED=false
MOOBEE_STREAM_BASE_URL=
MOOBEE_STREAM_SERVICE_KEY=
```

`compose.yaml` passes these as optional environment variables with safe empty/off defaults. No change is made to `compose.hostnet.yaml`, Caddy, Tailscale, TLS, host ports, or existing container volumes.

To stage after **both repositories' security code is ready**:
1. Stand up a **separate private** MooBee Stream service. Do not publish its 8080 to the internet; keep PostgreSQL, Redis and worker resources isolated.
2. Create a distinct random 32+-byte `STREAM_SERVICE_API_KEY` in Stream's private `.env`; separately configure the same value as `MOOBEE_STREAM_SERVICE_KEY` in the IPTV backend's private `.env`. Never print, commit or put it in StreamBoss/browser code. This is **not** the Stream admin upload key.
3. Configure only a **fixed numeric private IPv4 origin**, e.g. `https://100.99.174.2:8443` with a TLS certificate valid for **that IP address**. Non-TLS connections are allowed only to `http://127.0.0.1` in the same network namespace. The current IPTV API runs on Docker **bridge** networking: its own `127.0.0.1` is NOT the VPS host, so `http://127.0.0.1:8080` will not work merely because Stream binds a port to the host loopback. Establish a secure and deliberately reachable private interface/overlay first. **Do not weaken the admin network policy** to make it work.
4. Set `MOOBEE_STREAM_ENABLED=true` only in an isolated staging backend after the link and service key are proven. Never pass arbitrary upstream URLs from clients or admin request bodies.
5. Back up the IPTV SQLite DB before enabling writes; validate at least one movie and one episode mapping and existing local playback before any production rollout.

The adapter explicitly **rejects DNS names, public IPs, link-local/cloud metadata, IPv6, credentials in URL, query/fragment/path, redirects, plain HTTP to remote IPs, ambient proxies**, and unexpected JSON/oversized responses. This conservatism is deliberate; the production private-network architecture will be reviewed separately.

## Private admin endpoints

All endpoints start with `/admin/api/integrations/moobee-stream` and use the existing **VPN-only, HTTP Basic authenticated admin** guard. No subscriber endpoint is introduced.

| Method / path | Behavior |
|---|---|
| `GET /status` | Local state: enabled/configured and playback supported = false; never contacts upstream or returns URL/key |
| `POST /preview` | Fetch up to 1–10 catalog pages (100 records/page), redact upstream fields, show mapped/unmapped; **no SQL writes** |
| `GET /mappings` | Read existing mappings (empty if schema not created); **no table creation** |
| `PUT /mappings/{movie|episode}/{item_id}` | Validate local ID + remote UUID/status/type through service endpoint; create/update mapping with explicit admin action and audit |
| `DELETE /mappings/{movie|episode}/{item_id}` | Remove mapping metadata only; no media/catalog deletion |

Preview request: `{"max_pages":3}`. If pagination exceeds the requested cap the response sets `"complete":false` and `"next_cursor"`; this is **not** permission to assume all videos were fetched. Limit is 10 pages / 1,000 records per preview.

Mapping request: `{"video_id":"00000000-0000-4000-8000-000000000101","enabled":false}`. An upstream record of type `movie` can map only to an existing local movie; `tv` only to an episode. A UUID cannot map to two catalog items, retries are idempotent, and only `ready` upstream items may be marked `enabled=true`. **Even an enabled mapping is metadata only; no external playback route exists yet.** `uploaded`/`processing`/`queued` records can be mapped as disabled to prepare later processing.

Mappings are stored in a **new** SQLite table `vod_external_sources`, created only on a successful mapping write. It has a unique `(kind,item_id)` and unique `(provider,video_uuid)` constraint. Mapping status is a last-known snapshot, not a live playback entitlement. Deleting a mapped local movie/episode/series cleans up the mapping entry automatically. Existing local movie folders, subscriber credentials, IPTV stream IDs and EPG remain unchanged.

## Security controls and limits

- No HTTP call occurs unless `MOOBEE_STREAM_ENABLED=true` and a 32+-byte service key and allowable fixed private origin exist.
- Upstream URL comes only from operator environment, not preview/mapping body; extra body keys such as `base_url` are rejected with HTTP 422.
- Explicit `trust_env=False` disables HTTP proxy auto-configuration. `follow_redirects=False` means redirects fail rather than navigate to a new IP. Private non-loopback transport requires TLS with normal certificate verification.
- Tight connect/read/write timeouts, JSON content-type check, 256 KiB response cap per page, max 10 pages, UUID/cursor and metadata validation. Errors produce bounded, generic messages that do not echo upstream secrets or raw responses.
- No automatic media import, deletes, status polling, signed HLS URL fetch, playback gateway or Docker network changes.
- The normal VOD Xtream responses and local playback endpoints **do not read `vod_external_sources`**. Subscribers cannot discover upstream IDs or service configuration through this feature.
- Existing StreamBoss local movie and episode playback remains the regression baseline.

## CI / validation

`pytest -q` covers: disabled mode; preview read-only; admin auth; allowed/denied destinations; malicious redirects; bad JSON; large responses; duplicate IDs; cursor pagination/partial results; movie/TV mismatch; duplicate mapping conflicts; idempotent update; ready vs queued; automatic mapping cleanup; and unchanged local Xtream/HLS playback.

In isolated staging, first confirm the Stream private catalog returns metadata only with the service key, and denies requests missing the key. Then exercise Preview, save a disabled movie mapping, check StreamBoss local VOD still works and delete the mapping.

## Deployment hold and future gateway

**Do not deploy this PR to the live VPS or restart containers/Caddy.** Existing live service relies on `docker compose -f compose.yaml -f compose.hostnet.yaml` and the established Tailscale admin restriction. This PR is additive in GitHub only.

Next phases: private network/TLS between services → approved Stream PR #1 release → adapter-only staging → signed adaptive-HLS gateway with per-subscriber authorization/expiry and revocation → StreamBoss E2E checks with owned media. Use separate PRs and explicit deployment approval.
