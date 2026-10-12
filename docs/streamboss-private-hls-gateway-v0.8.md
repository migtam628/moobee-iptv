# v0.8 — opt-in StreamBoss adaptive HLS gateway (development preview)

**Status: GitHub-only feature; not deployed or connected to a live Stream service.**
The active platform is **StreamBoss ↔ MooBee IPTV ↔ MooBee Stream**. Other media-player repositories are not involved.

## Existing behavior first

- Live TV, XMLTV guide, local HLS movie/episode playback and Xtream catalog IDs work exactly as v0.7 by default.
- Two independent flags must both be explicitly set true before the gateway can serve remote HLS:
  - `MOOBEE_STREAM_ENABLED=true` (the v0.7.2 metadata connector).
  - `MOOBEE_STREAM_PLAYBACK_ENABLED=true` (new, defaults **false**).
- A specific existing local movie/episode must also have an explicit mapping in `vod_external_sources` with `enabled=1` and `last_known_status='ready'`. If there is no eligible mapping, playback stays local. If an eligible mapped Stream request fails, it **fails closed**, not silently falling back to local files.
- Existing Xtream catalog endpoints and media IDs stay unchanged. The gateway replaces only the playback *representation* when all opt-in conditions hold.

## Data and trust flow

```text
StreamBoss  -- Xtream credentials -->  tv.moobee.lol / existing Caddy
                                               |
                                      MooBee IPTV FastAPI
                                       auth(username,password)
                                               |
                                        local movie/episode ID
                                               |
                                matching explicitly enabled UUID mapping?
                                    no / disabled     yes
                                          |            |
                                existing local HLS     |  private API: /api/v1/videos/{uuid}/playback
                                                       |  x-service-key (server only)
                                                       v
                                               MooBee Stream (private)
                                               signed /hls/... prefix
                                                       |
                             fetch master.m3u8, rendition index.m3u8,
                                     worker-generated .ts segments
                                                       |
                                      validate and rewrite manifests
                                                       |
                          IPTV /stream-vod/... URLs with subscriber auth
                                                       |
                                          StreamBoss adaptive HLS playback
```

**Client never receives** `MOOBEE_STREAM_SERVICE_KEY`, a MooBee Stream signed path, its token/expiry, or the private IP. Every manifest and segment makes a new subscriber auth check, mapping check and fresh upstream ready-state/signed-path request. This permits revocation between HLS requests. Signed URLs are bearer credentials **within** the private service and must never be publicly reverse-proxied as a bypass.

The gateway uses the existing Xtream username/password-in-URL contract for StreamBoss compatibility. These URLs must be treated as sensitive: never log, copy, screenshot or cache them, and plan a separate short-lived session-token design before general public rollout. This prototype has no per-title paid entitlement system; access follows the existing authenticated subscriber + explicitly mapped-title policy.

## Routes

- Existing: `/movie/{username}/{password}/{movie_id}.m3u8` and `/series/{username}/{password}/{episode_id}.m3u8`. If remote mapping enabled, serve rewritten upstream **master** playlist; otherwise serve existing local flat playlist.
- New: `/stream-vod/{username}/{password}/{movie|episode}/{item_id}/{rendition}/index.m3u8` for authenticated rendition media playlists.
- New: `/stream-vod/{username}/{password}/{movie|episode}/{item_id}/{rendition}/segment_00001.ts` (worker-named segments) for authenticated proxied media.

All rewritten asset links target `PUBLIC_URL` (IPTV public domain) and never upstream Stream. Paths are anchored and strictly allowlisted; unknown HLS tags, `URI=` attributes, absolute/external/nested references, traversal, redirects, unexpected signed-path fields, stale signatures, malformed metadata, and oversized content are denied rather than passed to clients. This initial implementation only supports HLS output exactly like the existing MooBee Stream worker's `master.m3u8`, one rendition `index.m3u8` and `segment_#####.ts` format; it does not support encryption, fMP4, audio-group playlists, LL-HLS, or arbitrary remote streams.

Playlist cap: 256 KB. Segment cap: 16 MiB in memory per response. This protects against unlimited upstream buffering but consumes CPU, memory and bandwidth per viewer; **benchmark before production**. Request times are bounded by the existing secure catalog client's HTTP timeouts. No automatic uploads/transcoding or disk storage are added to IPTV.

## Security prerequisites

1. MooBee Stream [security PR #1](https://github.com/migtam628/moobee-stream/pull/1) supplies protected `GET /api/v1/videos/:id/playback`, Nginx signed HLS and the fixed private media origin; it is merged in GitHub, **not known deployed**.
2. MooBee IPTV [catalog adapter PR #9](https://github.com/migtam628/moobee-iptv/pull/9) supplies disabled-by-default metadata and manual UUID mapping; merged in GitHub, **not known deployed**.
3. Before enabling playback establish a secure route from the **IPTV container network** to the private MooBee Stream origin. `127.0.0.1` **inside the IPTV container is not the VPS host**. The operator URL policy requires HTTPS with certificate verification for private remote IPv4, or same-namespace loopback HTTP. Avoid opening ports or changing Caddy/Tailscale for convenience.
4. Run separate service stacks with separate Postgres, Redis, FFmpeg worker and storage; only properly licensed/owned video.
5. Generate a private service key distinct from Stream upload/delete admin credentials. Store only in server-side environment. No secrets in StreamBoss builds or client API responses.
6. Keep production `MOOBEE_STREAM_PLAYBACK_ENABLED=false` until an isolated staging E2E on real FFmpeg-generated adaptive HLS passes, including a full movie and episode playback, negative auth, account disabling, mapping removing, token expiration, URL rewriting and throughput. CI mocks alone are not a production-ready gate.

## Suggested isolated staging tests

- Publish zero public ports for the Stream test service; verify only expected private transport works and no direct public `/hls` or `/api` route exists.
- Verify a service-key-free Stream playback request fails `401`.
- Upload authorized sample movie + episode into private Stream API, wait for transcoding ready (workers/DB), and record UUIDs.
- On isolated IPTV copy, enable only catalog connector; preview and save disabled UUID mapping. Confirm live/EPG/local VOD still works.
- Enable mapping + `MOOBEE_STREAM_PLAYBACK_ENABLED=true`, test StreamBoss movie, series/episode, adaptive qualities, and full duration across token renewal.
- Disable a subscriber during playback: subsequent segment should return `401` and upstream should not be contacted.
- Remove or disable mapping: subsequent remote segment must be denied. Confirm local fallback is only available through the ordinary local-playback route when mapping is no longer eligible.
- Try malicious manifest with `#EXT-X-KEY:URI=...`, remote URL, traversal or extra HLS rendition directive: gateway returns `502` rather than fetching attacker URLs.
- Verify the real HTTPS certificate (including IP SAN if configured by IP), bandwidth, CPU, memory and ffmpeg output before considering prod.
- Backup SQLite/Postgres/media; verify both rollback paths. Never use plain `docker compose up` on live IPTV; retain both `-f compose.yaml -f compose.hostnet.yaml`.

## Rollback / compatibility

To disable remote playback in the future use `MOOBEE_STREAM_PLAYBACK_ENABLED=false` in the relevant *isolated stack* and recreate only the API there. This is a documentation example **not an instruction to change the production VPS now**. With flag false, all mapped movie/episode IDs still use local HLS media folders. Do not delete SQLite mappings or move media files for rollback. The private Stream service can be rolled back independently.

**Production hold:** no live VPS access, Caddy/host-network/Tailscale edits, restart, or deployment in this PR.
