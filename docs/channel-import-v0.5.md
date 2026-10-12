# MooBee IPTV v0.5 — local channel importer (first increment)

This candidate extends the existing dashboard with import preview and channel health. It does **not** perform external HLS ingestion, automatic restreaming, EPG or transcoding. The API only accepts `moobee-local://<folder>` entries, backed by real HLS files in `media/<folder>/index.m3u8`. This avoids untrusted URL fetching and preserves the original playback logic.

## Example

```m3u
#EXTM3U
#EXTINF:-1 tvg-name="My Authorized Channel" group-title="Personal",My Authorized Channel
moobee-local://channel3
```

The folder `media/channel3/index.m3u8` must already exist on the VPS. Paste the M3U into the new **Local playlist import & health** panel. Choose **Preview import** first, then **Import new channels**. Existing folder entries are skipped; the import is additive, not a destructive sync. Maximum 200 channels per request, maximum ~300 KB input.

The **Check channels** action validates that each channel points to a readable local file beginning with `#EXTM3U`. This is a manifest-readiness check, **not** a real stream or media segment playback check. External stream URL imports, network health probes, source restreaming, and XMLTV remain future milestones.

## Staged VPS deployment

Stay on the currently working v0.4 deployment until GitHub CI and backup validation pass.

```bash
cd /root/moobee-iptv
git status
git fetch origin
git switch -c test-stream-v0.5 origin/feat/stream-management-v0.5
# Run online backup from the existing v0.4 service
bash scripts/backup-db.sh
docker compose -f compose.yaml -f compose.hostnet.yaml config --quiet
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --build api
```

The API container is recreated briefly; existing SQLite data and mounted local media remain in place. Caddy should remain up with host networking. Verify from the Mac that public IPTV health is 200, public admin is 403, and VPN admin is 401 without credentials. With admin credentials over the VPN, check that users/channels persist, import preview works, and local manifest checks return the expected statuses.

Rollback without overwriting data:

```bash
git switch main
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --build api
```

Do not invoke plain `docker compose up` because host networking depends on the overlay. Do not paste `docker compose config` output into support chats; it expands your admin secret.

## Planned follow-up

Before supporting external authorized HLS sources, implement explicit domain allowlists, DNS/IP validation for private-address SSRF, redirects disabled, bounded payload and timeout controls, segment proxy authorization, playlist rewriting, and rate limiting. Existing subscriber URLs are credential-bearing and must remain protected.
