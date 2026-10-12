# MooBee Stream adapter — proposed v1 contract and test matrix

**Proposal only:** no API changes have been made. Fields and routes listed as "current" were read from [moobee-stream](https://github.com/migtam628/moobee-stream) `main`. Proposed adapter routes and fields are not implemented.

**Client scope:** StreamBoss is the **only** player being integrated and validated. MooBee IPTV remains the central backend, and MooBee Stream provides ingest/transcoding/HLS. `iptv-player` and `vidplus-movie-player` are explicitly optional, outside this program, and must not be added as dependencies or acceptance targets. All playback compatibility tests in this document refer to StreamBoss.


## Upstream routing as implemented

MooBee Stream Nginx rewrites `/api/...` to Fastify `/...`:

| Nginx URL (current) | Fastify route (current) | Auth (current) | Integration use |
| --- | --- | --- | --- |
| `GET /api/health` | `GET /health` | none | Private-only readiness probe |
| `GET /api/v1/videos` | `GET /v1/videos` | `x-api-key` | Admin-only catalog sync, max 200 returned |
| `GET /api/v1/videos/{uuid}` | `GET /v1/videos/:id` | `x-api-key` | Private metadata/status lookup |
| `GET /api/v1/videos/{uuid}/playback` | `GET /v1/videos/:id/playback` | **none** | **NOT approved for internet-facing subscriber service** |
| `GET /api/v1/movie/{tmdbId}` | `GET /v1/movie/:tmdbId` | none | Public resolver; reveals video ID |
| `GET /api/v1/tv/{tmdbId}/{season}/{episode}` | `GET /v1/tv/:tmdbId/:season/:episode` | none | Public resolver; reveals video ID |
| `POST /api/v1/admin/videos` | `POST /v1/admin/videos` | `x-api-key` | Upload/queue; private admin path only |
| `DELETE /api/v1/admin/videos/{uuid}` | `DELETE /v1/admin/videos/:id` | `x-api-key` | Destructive; explicitly OUT of adapter |

The current upstream signed-path response looks like this **shape** (example values only):

```json
{
  "id": "55c350e1-6a6f-49c2-beda-5a3d3d0f5c18",
  "slug": "owned-demo",
  "title": "Authorized Demo",
  "stream": "/hls/55c350e1-6a6f-49c2-beda-5a3d3d0f5c18/1234567890/EXAMPLE-TOKEN/master.m3u8",
  "expiresAt": 1234567890,
  "poster": "/media/posters/example.webp",
  "width": 1280,
  "height": 720,
  "durationSeconds": 3600
}
```

An `expiresAt` field **alone** cannot enforce subscriber authorization or prevent sharing. Do not store `stream` in SQLite or surface it to a client as a substitute for an entitlement check.

## Proposed IPTV-side entity mapping

Current v0.7 movies and episodes have local IDs, titles and a `folder`. Maintain their stable IDs and local playback unchanged. For new external-backed items store mapping data in a **separate** table in a later PR:

```sql
-- Proposed, not migrated or executed.
CREATE TABLE IF NOT EXISTS vod_external_sources (
  kind TEXT NOT NULL CHECK (kind IN ('movie', 'episode')),
  item_id INTEGER NOT NULL,
  provider TEXT NOT NULL CHECK (provider = 'moobee-stream'),
  video_uuid TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  last_known_status TEXT NOT NULL DEFAULT 'unknown',
  last_checked_at INTEGER,
  PRIMARY KEY(kind, item_id),
  UNIQUE(provider, video_uuid)
);
```

Avoid a SQLite foreign key to a different DB. Enforce that `kind+item_id` references an existing movie or episode in application logic, and clean mapping when the catalog entry is deleted. The map must be disabled by default until readiness and playback authorization are verified. Keep the authoritative video status in Stream PostgreSQL; cached status is for admin UX only, never a substitute for authorization.

### Proposed normalized metadata (private admin only)

```json
{
  "provider": "moobee-stream",
  "videoId": "55c350e1-6a6f-49c2-beda-5a3d3d0f5c18",
  "mediaType": "movie",
  "title": "Authorized Demo",
  "status": "ready",
  "durationSeconds": 3600,
  "width": 1280,
  "height": 720,
  "checkedAt": "2026-10-12T01:30:00Z"
}
```

Only keep fields required for the IPTV catalog. Never return `source_path`, `source_filename`, `error_message` with sensitive stack traces, storage root, secret values, or raw third-party API response to a subscriber.

## Proposed sync API (private; not implemented)

- `POST /admin/api/integrations/moobee-stream/preview`: admin Basic auth + VPN restriction; fetch only configured private upstream; return 0..200 normalized valid rows and explicit `has_more_unknown` if no pagination available; never modify SQLite.
- `POST /admin/api/integrations/moobee-stream/mappings`: admin Basic auth; map a stable upstream UUID to an existing movie/episode ID with kind and explicit allow/disable; idempotent; audit entries without secrets.
- `GET /admin/api/integrations/moobee-stream/health`: private API readiness summary (no upstream URLs or credentials), queue status when available; bounded timeout and error information.
- There is **no** public subscriber endpoint until the playback gateway, session tokens and revocation behavior pass their security tests.

## Transport requirements

- Upstream base address must be a trusted configuration value, **never** accepted in request JSON, playlist URLs, TMDB metadata, or user-submitted redirects.
- Prefer a private route between containers/VMs; enforce network firewall, DNS/IP pinning or direct trusted private addresses, TLS when crossing hosts, and explicit certificate validation. Fail closed on redirects, unexpected ports and non-JSON responses.
- Use small response-size limits, timeouts, concurrency limits and restricted JSON fields. Avoid logging `x-api-key` or response headers.
- Use a **read-only, narrow-scoped service key** on MooBee Stream for catalog status, not its existing all-powerful upload/delete admin key. Upstream must implement this new role before the adapter sends credentials.
- API error semantics: 401/403 means authorization denied; 404 means nonexistent/removed; 429 means back off; 5xx/network failure marks status unknown but **does not delete an IPTV catalog entry**.
- Sync must not automatically deactivate existing local VOD when upstream is unavailable.

## Security acceptance tests

| Scenario | Expected |
| --- | --- |
| Public request to Stream playback resolver without service auth | 401/403 after upstream change |
| Invalid IPTV subscriber | 401, no upstream request |
| Disabled IPTV subscriber | 401, no signed playback response |
| Expired or replayed ticket | Denied per final ticket policy |
| Public request to private Stream 8080 | Connection refused/blocked |
| Request supplies `http://127.0.0.1` as source or `169.254.169.254` | 422 or ignored; no HTTP fetch |
| Stream sends 30x redirect to private or public URL | Reject; do not follow redirects |
| Stream sends invalid/giant JSON | Fail closed, no state mutation |
| Stream status moves `ready` -> `failed` | Playback fails safely, mapping remains for review |
| Metadata list returns 200 entries (possible truncation) | Show incomplete/pagination warning |
| Upstream media signature expires during playback | Renew safely or fail with clear client outcome |
| Existing v0.7 local movie 101 and episode 301 | Unchanged discovery and playback |
| Unauthenticated public admin request | 403 at Caddy boundary |
| Admin via Tailscale without login | 401 at app boundary |

## Suggested sequence and ownership

1. **Stream-side PR**: private Compose overlay, least-privilege internal auth for metadata and playback, signed delivery threat model and tests.
2. **IPTV-side PR**: new mapping table, disabled flag, read-only preview with mock upstream and strict input validation. No playback.
3. **Gateway PR**: authenticated manifests/master/media playlist rewriting with per-subscriber token lifecycle and network architecture.
4. **Staging verification**: video upload -> worker ready -> sync preview -> explicit mapping -> StreamBoss HLS movie & episode -> invalid/disabled account negative cases -> rollback.

Keep these phases in separate pull requests with standalone CI and docs. **Do not merge or deploy them solely because the v0.7 local playback worked.**
