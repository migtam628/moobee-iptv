# MooBee IPTV + MooBee Stream — integration readiness and architecture (v0.7.1)

**Status:** Design and readiness plan only. No integration is enabled by this document, no new container is deployed, and the production Tailscale/host-network configuration is unchanged.

## Current verified baseline

- [MooBee IPTV](https://github.com/migtam628/moobee-iptv) `main` now includes v0.7 local VOD, v0.6 XMLTV, subscriber credentials, SQLite catalog and authenticated local HLS.
- StreamBoss has successfully authenticated to MooBee IPTV, displayed the EPG, and played the demo live channel, movie ID 101 and Series 201 / Episode 301. That confirms local HLS compatibility only, **not** external MooBee Stream processing.
- Production Caddy uses `compose.yaml` **and** `compose.hostnet.yaml`. Caddy runs in host networking; the Python API is reachable on host loopback `127.0.0.1:8000`; admin access through `admin.moobee.lol` is restricted to Tailscale clients by `Caddyfile.private`. Do not change this during integration discovery.
- [MooBee Stream](https://github.com/migtam628/moobee-stream) is a **different service**: Fastify, PostgreSQL, Redis/BullMQ, FFmpeg worker, player and Nginx with Nginx `secure_link` signed HLS.
- Current upstream implementation was inspected at `apps/api/src/server.ts`, `apps/api/src/auth.ts`, `apps/api/src/signer.ts`, `nginx/templates/default.conf.template` and `docker-compose.yml` on MooBee Stream `main`.

## Immediate release blockers identified in MooBee Stream

1. The route `GET /api/v1/videos/:id/playback` maps to Fastify `GET /v1/videos/:id/playback` and **has no `requireAdmin` preHandler**. It returns a signed stream path for any `ready` video by ID/slug; the current public movie and TV resolver endpoints also return video IDs. Hence possession of the ID could allow non-subscriber playback if this origin is internet-accessible. Merely requiring an Xtream login at the IPTV catalog layer is not sufficient.
2. The MooBee Stream Compose `nginx` service publishes `8080:80`, which binds all host interfaces by default. Never deploy that unmodified Compose file on the production VPS or assume UFW alone stops Docker published traffic. The Nginx `/api/` proxy also exposes the upload/admin route (with its API key guard).
3. MooBee Stream returns **short-lived signed HLS paths**, not an authorized IPTV subscriber manifest. Nginx `secure_link` checks token integrity/expiry but does not know the MooBee IPTV subscriber or account-disable state; signed links can be shared until expiry.
4. MooBee Stream produces nested adaptive-HLS renditions under `storage/videos/<uuid>/master.m3u8`; MooBee IPTV v0.7 accepts a simple flat `/media/<folder>/index.m3u8` and rejects nested, URI-bearing and map/key directives. Copying the MooBee Stream URL into an IPTV `folder`, or simply bind-mounting its videos, **will not work**.
5. Current upstream `GET /api/v1/videos` returns at most 200 records; no pagination guarantee. A real sync must detect incomplete listings rather than silently assume the returned catalog is exhaustive.
6. Nginx currently permits `Access-Control-Allow-Origin "*"` on signed HLS. Cross-origin permissions must be reviewed with the final player/browser, and unrestricted CORS must not become an authentication substitute.
7. The Stream service uses separate database, queue, and filesystems. Do not merge this infrastructure into MooBee IPTV's SQLite store or directly expose Postgres, Redis, upload folders or signing key.

These are **code review findings** from the repository, not evidence that any insecure Stream container is currently running on the VPS.

## Proposed trust boundaries (not deployed)

```text
                     StreamBoss / IPTV Player / other Xtream clients
                                       |
                                     HTTPS
                                       |
                        tv.moobee.lol  (existing Caddy)
                                       |
                              MooBee IPTV FastAPI
                         subscriber auth + entitlement
                         SQLite movie/episode ↔ UUID map
                                       |
                          fixed allowlisted service link
                                       |
                       private MooBee Stream adapter
                         metadata / processing status
                                       |
                  +--------------------+-------------------+
                  |                                        |
           MooBee Stream API                        Media delivery gate
             Fastify (private)                  short-lived per-viewer ticket
                  |                                        |
              PostgreSQL                               Nginx HLS
                  |
                Redis -> FFmpeg worker -> Storage
```

**Control plane:** Admins ingest and transcode authorized content in MooBee Stream. A read-only sync layer fetches metadata and associates **stable video UUIDs** with an existing MooBee IPTV movie or episode. It never stores raw signed HLS URLs, upstream API keys, file system source paths, or arbitrary user-supplied upstream hosts in public catalog records.

**Data plane:** A subscriber presents the existing Xtream credentials to MooBee IPTV. IPTV checks the account and media entitlement, then obtains **a short-lived, constrained playback authorization**, and serves/proxies playlist and segments using a hardened gateway. The player must never receive Stream `ADMIN_API_KEY` or `STREAM_SIGNING_SECRET`. Current Stream public playback resolution is unsuitable for subscriber-only access unless the whole upstream is private and all internet-facing media routes enforce a gate. Do not use an unconditional public 302 redirect to unrestricted signed paths as the final design.

**Network:** For initial integration run MooBee Stream in a *separate test environment*, preferably a separate VM or private Tailscale host, with internal API/storage/queue networks. On a colocated lab host, bind only Stream Nginx to `127.0.0.1:8080:80` and use an explicitly allowed **private** connector path for the API container. Container `127.0.0.1` points to the container itself, not the host: establish an actual reachable, firewalled address and verify it before connecting. Avoid publishing any new public port or changing existing Caddy host networking.

## Incremental implementation plan

### Gate A — Upstream security before cross-service playback (MooBee Stream repository)

- Introduce dedicated service-to-service credentials (least privilege, constant-time validation, rotation) for metadata/status and playback authorization. Do not reuse the general upload/delete `ADMIN_API_KEY` for subscriber-facing traffic.
- Enforce authentication on playback resolver and restrict movie/TV resolution exposure. Return generic errors without leaking sensitive media paths or keys.
- Provide a *fixed origin* and short constrained TTL for playback tickets; bind tickets to content and session or enforce them at a gate that can reject disabled users. Decide ticket revocation semantics.
- Limit public access by construction; change 8080 binding for a test overlay, not a live production compose file. Test the reverse proxy's handling of signed URL paths and nested manifests.
- Harden upload validation and permissions, signing URL path normalization, and metrics/log redaction. Independently back up Postgres and original uploads.

### Gate B — Catalog sync only (MooBee IPTV repository)

- Add nullable `moobee_stream_video_id` (UUID) references or a dedicated mapping table for movies and episodes; keep current local folder path and ID intact.
- Add a **disabled-by-default** configuration switch and *fixed, administrator-configured* upstream base address (no per-request URLs, redirects or DNS/IP bypass to loopback/cloud metadata).
- Implement admin-only `Preview sync` and `Commit mapping` operations reading the upstream catalog/status using service credentials. No automatic deletes; preserve existing ID 101, series 201 and episode 301.
- Only expose `ready` items to subscribers. Surface queued/processing/failed jobs in private admin, not as playable Xtream entries. Handle 200-record cap explicitly.
- Add tests against a fake private upstream, including timeouts, bad UUIDs, oversized responses, admin authentication and idempotent mappings.

### Gate C — Playback compatibility

- Implement a properly authenticated playback gateway that rewrites **master, rendition and segment** URIs safely. Reject directory traversal, remote URI injection, localhost/metadata URLs, redirects and unexpected media types.
- Ensure Stream signing, entitlement, expiration and account disable/revocation semantics still work with browser HLS clients and StreamBoss.
- Cover long episodes and token refresh/retry. Track capacity: reverse proxy streaming overhead, bandwidth, CPU/GPU workers and disk.
- Keep the current `/movie/...` and `/series/...` local-HLS paths working while rolling out external-backed catalog items separately.
- Never expose admin API keys or signing keys through response bodies, URLs, client code, response headers or troubleshooting logs.

### Gate D — Controlled staging and promotion

- Import a small, explicitly authorized test movie and a test episode. Inspect worker output, HLS resolution, original uploads, poster generation and media rights.
- Confirm MooBee Stream metadata/status via service auth; verify StreamBoss finds new catalog entries and plays the entire asset.
- Negative tests: invalid/disabled subscriber, missing entitlement, stolen/expired ticket, malformed upstream manifest, timeout, redirect, forged source host and private-IP SSRF attempt.
- Only after isolated staging passes, prepare a separate, reviewed production rollout/rollback. Keep production Caddy host-network/Tailscale arrangement intact and check public admin is 403, VPN admin (no credentials) is 401, IPTV health 200.

## Deployment hold / rollback

**Do not run `docker compose up` for MooBee Stream on the existing live VPS today.** Its current Compose would publish port 8080 on all interfaces and introduce multi-service CPU/memory/disk load. First obtain the VPS capacity and isolation plan, a distinct `.env` with strong untracked secrets, and a private networking diagram. The existing `docker compose -f compose.yaml -f compose.hostnet.yaml` commands remain mandatory for MooBee IPTV.

No changes to `/root/moobee-iptv/data/server.sqlite3`, `/media`, VPN ACLs, Caddy, UFW, or production configuration are part of this planning PR. A later catalog sync must be reversible and additive. Backups and rollback procedures for **both** stacks are release prerequisites.

## Acceptance criteria for next code PR

- [ ] Separate upstream private API auth from admin upload/delete permissions.
- [ ] No unauthenticated endpoint issues subscriber-only playback URLs.
- [ ] Existing local VOD and EPG pass all previous automated tests.
- [ ] External IDs are validated, stable and mapped without breaking movie/episode IDs.
- [ ] Import preview and dry-run do not mutate database or require public Stream exposure.
- [ ] A malicious user cannot choose upstream URL/host.
- [ ] Disabling a subscriber blocks newly requested manifests and segments under the chosen gateway policy.
- [ ] Signed playback expiry and retry behavior are tested.
- [ ] All secrets remain off GitHub and out of client-visible output.
- [ ] No change is needed to Caddy host networking for the metadata-only gate.
