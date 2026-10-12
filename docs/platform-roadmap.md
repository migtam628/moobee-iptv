# MooBee Platform — phased integration roadmap

## Repository assessment (2026-10-11)

These are separate codebases. Do **not** combine Docker Compose stacks or blindly copy their authentication/player code.

| Repo | Current technical role | Integration decision |
| --- | --- | --- |
| [moobee-iptv](https://github.com/migtam628/moobee-iptv) | Python FastAPI Xtream-style auth, local HLS, SQLite admin, host-network Caddy/Tailscale | Source of truth for subscribers, live channel catalog, HLS authorization |
| [iptv-player](https://github.com/migtam628/iptv-player) | React shared UI/core, Tauri desktop, Capacitor web/mobile/TV, XMLTV parser, Xtream client | Preferred initial interoperable client; test against server's documented Xtream subset |
| [moobee-stream](https://github.com/migtam628/moobee-stream) | Fastify PostgreSQL Redis/BullMQ FFmpeg pipeline, signed HLS via Nginx | Standalone future VOD/transcoding component; integrate through internal service boundary only |
| [vidplus-movie-player](https://github.com/migtam628/vidplus-movie-player) | TypeScript wrapper around external VidUp iframe URLs | Keep independent; not a trusted self-hosted media source or backend integration |
| [StreamBoss](https://github.com/migtam628/StreamBoss) | Flutter player for mobile, desktop, web plus Roku/tvOS projects | Alternate client; retain as separate app, run interoperability tests rather than merging UI stacks |

Assessment based on repository READMEs; implementation, dependency, license, and device-specific reviews are prerequisites to production integration.

## Integration contract — first incremental delivery

Public `GET /.well-known/moobee-capabilities` returns versioned metadata (schema v1) describing server functionality. It reports only capabilities already on main; in particular XMLTV endpoint exists but **program data is not yet implemented**. Do not expose private admin address, proxy upstream, subscriber identifiers or passwords. This endpoint is not an authenticated discovery of channels.

Existing player support stays on HTTPS `/player_api.php`, `/get.php`, `/live/{username}/{password}/{stream_id}.m3u8` and `/xmltv.php`. Player must use an HLS-capable backend and tolerate incomplete Xtream actions. Account passwords are embedded in playback URLs, so never add these URLs to telemetry, analytics, application logs, or bug reports.

## Ordered releases

- **v0.5 — Catalog/source controls:** validate PR #4 local M3U importer, then guarded remote HLS ingestion with SSRF prevention, DNS rebinding/redirect defenses and bounded fetches. Add health status, preview, safe category/logo management. Do not mistake local manifest checks for segment playback.
- **v0.6 — EPG:** XMLTV import from explicitly authorized providers, bounded XML parser, timezone-aware schedule indexing, channel ID mapping, stale-guide handling, and actual `get_short_epg` data. Test malformed/oversized feeds.
- **v0.7 — VOD:** add authorized media metadata and separate VOD API. Prototype an internal integration adapter for MooBee Stream behind VPN/private service networking; preserve its independent Postgres/Redis/worker stack; no direct exposing upload credentials.
- **v0.8 — Players:** pilot `iptv-player` via Xtream/HLS/EPG integration tests on web/desktop/Android TV. Evaluate StreamBoss as an optional client; decide branding and platform priority after hardware tests. Do not merge Flutter and React apps.
- **v0.9 — Operations:** rate limiting, viewer/concurrency accounting, metrics and alerts, tokenized playback migration, scheduled backup and restore drills, signed delivery, resource sizing.
- **v1.0 — Release:** threat model, dependency scanning, load testing, release tagging, CI with smoke tests, disaster recovery, compatibility matrix, reproducible deployment.

## Non-negotiable host-network configuration

Production VPS currently requires **both** Compose files:
```bash
docker compose -f compose.yaml -f compose.hostnet.yaml ps
```
Public `tv.moobee.lol` is served on 443, public `admin.moobee.lol` must return **403**, while VPN-admin without credentials must return **401**. FastAPI binds `127.0.0.1:8000`; never publish 8000 publicly. Keep `.env`, data and media untracked. Keep UFW port 80/443 open for Caddy while relying on Caddy's VPN-only admin restriction. Validate configuration with `docker compose ... config --quiet`, not plaintext output that reveals secrets.

## Safe evaluation/deployment (only after CI)

```bash
cd /root/moobee-iptv
git status --short
git fetch origin
# Switch only if existing local tracked changes permit
git switch -c test-platform-capabilities origin/feat/platform-integration-v0.5
bash scripts/backup-db.sh
docker compose -f compose.yaml -f compose.hostnet.yaml config --quiet
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --build api
```

Expected:
```bash
curl -fsS https://tv.moobee.lol/healthz
curl -fsS https://tv.moobee.lol/.well-known/moobee-capabilities
```
From a Mac with Tailscale, verify public admin 403 and private VPN admin 401. Do not merge or restart Caddy during this API-only milestone. Roll back by switching to `main` and rebuilding only `api` with both Compose files, preserving `.env` and data.
