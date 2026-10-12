# MooBee IPTV v0.7 — Local movie and series catalog

## Scope and architecture

This increment is the **first half of v0.7**: an authorized, local HLS VOD catalog integrated with MooBee IPTV's existing subscriber authentication and Xtream-style API. MooBee Stream is **not connected or deployed** by this PR: see the proposed adapter contract below.

The existing public host is `https://tv.moobee.lol`. The administration host `https://admin.moobee.lol/admin` remains private to Tailscale. All three tables (`vod_movies`, `vod_series`, `vod_episodes`) are added to the existing SQLite database on demand. The API does not alter the users, live TV or XMLTV structures.

* Movies: a title, category, optional poster/description/year, and HLS media folder.
* Series: a title, category and metadata, with numbered seasons and episodes.
* Episodes: belong to an existing series and point to their own local HLS media folder.
* Metadata is managed through authenticated `/admin/api/vod` endpoints and a dashboard section.
* The existing `player_api.php` now responds to `get_vod_categories`, `get_vod_streams`, `get_vod_info`, `get_series_categories`, `get_series`, and `get_series_info`. These require existing subscriber credentials.
* Subscriber playback paths are `/movie/<username>/<password>/<movie_id>.m3u8` and `/series/<username>/<password>/<episode_id>.m3u8`; segments are individually authenticated under `/vod-hls/...`. The existing `/live/...` routes remain unchanged.
* The media folder must already have an `index.m3u8` manifest and local `.ts`, `.m4s`, `.mp4`, `.aac` or `.vtt` segments. Nested or absolute manifests, remote media URLs, encryption key tags, and init-map HLS constructs are intentionally unsupported.

**Important compatibility limitation:** This increment supports VOD HLS (`.m3u8`) only, not `.mp4` direct movie delivery or full Xtream implementation. Some IPTV clients require MPEG-TS or MP4 movie URLs and will not yet play VOD. Titles and media must be owned or properly licensed.

## Add your first authorized movie

Generate a static HLS playlist from a source file you are allowed to host, replacing the file path and target folder:

```bash
cd /root/moobee-iptv
mkdir -p media/movie1
ffmpeg -i /path/to/authorized-video.mp4 \
  -c:v libx264 -preset veryfast -c:a aac \
  -hls_time 6 -hls_list_size 0 \
  -hls_segment_filename 'media/movie1/segment%05d.ts' \
  media/movie1/index.m3u8
```

In the Tailscale-only Admin panel use the **Movies & Series · Local VOD** section to create a movie with ID `101`, folder `movie1`, title and category. This operation registers metadata; it does not run FFmpeg.

To add a series, first register the series (ID `200`), then create episodes (individual IDs, e.g. `201` for S1E1), each using its own existing HLS folder.

Deleting an item removes SQLite metadata, **not** media files. Deleting a series also removes its episode metadata, not the episode media folders.

## Security boundaries

Never expose port 8000 to the public internet or route public traffic to `/admin`. The API validates HLS paths against an allowlisted folder and local file extensions, prohibits traversal/symlinks escaping the media root and rejects any HLS directive embedding a URI, encrypted key/media map, or other unrecognized EXT directive. No external fetch is performed. Subscriber passwords are embedded in Xtream-compatible playback URLs: do not paste full playback URLs into logs, screenshots, or tickets.

## Safe staging and rollback

PR #6 (v0.6 EPG) has passed StreamBoss playback and guide validation and is merged into `main`. PR #7 targets `main` and should be merged only after reviewing its latest successful CI run and security checks. Staging was tested on the VPS with a movie and Series S1E1 in StreamBoss. Back up SQLite before future redeployments.

For a fresh staging checkout, after final review (avoid recreating an existing branch):

```bash
cd /root/moobee-iptv
git status --short
git fetch origin
git switch -c test-vod-v0.7 origin/feat/vod-catalog-v0.7  # skip if branch already exists
bash scripts/backup-db.sh
docker compose -f compose.yaml -f compose.hostnet.yaml config --quiet
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --build api
docker compose -f compose.yaml -f compose.hostnet.yaml ps
curl -fsS https://tv.moobee.lol/healthz
```

Inspect server-side HTTP status and verify existing subscribers, live HLS and EPG continue to work. On Mac with Tailscale, confirm public admin **403** and VPN admin without credentials **401**; test movie and episode playback using a client supporting HLS VOD. Never run plain `docker compose up` because this VPS requires the host-network overlay for Caddy.

API-only rollback (do not delete data):

```bash
git switch main
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --build api
```

Older code ignores the VOD tables. Preserve `.env`, `data/`, `media/`, Caddy files, and existing untracked backup files.

## MooBee Stream integration — next increment, not yet implemented

[MooBee Stream](https://github.com/migtam628/moobee-stream) is already a separate Fastify/Postgres/Redis/FFmpeg/Nginx application with `GET /api/v1/videos`, `GET /api/v1/videos/:id`, and short-lived signed HLS paths from `/api/v1/videos/:id/playback`. It supports movies via `/api/v1/movie/:tmdbId` and episodes via `/api/v1/tv/:tmdbId/:season/:episode`.

Planned integration:
1. Deploy MooBee Stream separately, bind its Nginx port to **127.0.0.1**, and preserve private Postgres/Redis/FFmpeg worker networking. Do not open its admin/upload routes to public internet.
2. Create a read-only internal adapter that authenticates to MooBee Stream over an explicitly configured private endpoint and stores only stable video IDs (not signed URLs or `ADMIN_API_KEY`) in the IPTV catalog. Prohibit user-provided upstream URLs and redirects.
3. Gate all movie/episode resolution with **MooBee subscriber authentication** first, then request time-limited playback paths from MooBee Stream. Decide whether to proxy or issue short-lived per-viewer tickets. Nginx signing alone must not permit unauthorized reuse or leak admin keys.
4. Add end-to-end FFmpeg processing, read-only catalogue sync, signed playback expiry, authorization and SSRF/credential-leak regression tests before enabling the adapter.
5. Reconcile rights/ownership and EPG/series metadata independently. External third-party embed wrappers are not a substitute for authorized first-party VOD.

These integration components are **not** present in this initial PR.
