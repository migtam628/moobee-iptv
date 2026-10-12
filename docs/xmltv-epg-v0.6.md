# MooBee IPTV v0.6 — XMLTV guide import and channel mapping

## Included

- Authenticated admin import: paste an authorized XMLTV `<tv>` document in the dashboard; preview without database writes, then import atomically.
- Persist guide channel names and programme schedule to the **existing SQLite database**; retain users, channels, and channel-to-guide mappings.
- Associate one imported XMLTV channel ID with each existing live stream; unmap without deleting channels.
- Real data from subscriber-authenticated `/xmltv.php`, `player_api.php?action=get_short_epg`, and `action=get_simple_data_table`. `get_live_streams` includes mapped `epg_channel_id`; `/get.php` M3U includes `tvg-id`.
- Guide browsing in the VPN-only administration dashboard.

**Not included:** scheduled feed updates, direct URL/remote XML fetching, inference or auto-matching, time shifting, playback sessions, catch-up, and VOD. Import is manual and replaces the previous imported programme dataset. Existing stream URLs and media remain unchanged.

## Security and limits

XMLTV is untrusted input. Limit 1,000,000 UTF-8 bytes / 400 channel entries / 5,000 programmes; reject DTDs/ENTITY declarations, malformed XML, duplicate/unsafe channel IDs, unknown programme channels, invalid durations, missing or invalid timezone offsets, and excessive text fields. Programmes must have an explicit numeric timezone (e.g. `+0000`, `-0400`); times are stored in UTC. Invalid input does not remove existing EPG data. Admin endpoints are protected by the existing Basic auth and network policy.

Guide export includes **only mapped** channels, and programmes from up to 24 hours ago to 14 days ahead. Xtream EPG replies have a 24-hour forward window, support a 1–50 listing limit, and encode titles/descriptions as base64 for typical Xtream clients. The XMLTV URL itself requires subscriber authentication; do not expose subscriber passwords in tickets, logs, or screenshots.

## Example XMLTV for manual testing

Use a current or future timestamp with the explicit timezone:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="personal.demo">
    <display-name>My Licensed Channel</display-name>
  </channel>
  <programme channel="personal.demo" start="20261012000000 +0000" stop="20261012010000 +0000">
    <title>Example programme</title>
    <desc>Content provided with permission.</desc>
  </programme>
</tv>
```

Open `https://admin.moobee.lol/admin` **through Tailscale**, paste XML, **Preview guide**, **Import guide**, choose a MooBee channel and `personal.demo`, then save mapping. For live display, replace the example programme times with your actual current schedule. XML is data, not a remote stream.

## Merge order and CI

This PR is based on `main` after the tested v0.5 local importer (PR #4) was merged. PR #5 (client capability discovery) remains a separate change. Recheck conflicts if the base branch changes again.

Run tests in CI (`pytest -q`) and smoke-test with authenticated local media. The new EPG schema is created on demand in `data/server.sqlite3`. **Rollback to older application code does not drop schema/data** but older clients will no longer receive real guide responses.

## Safe VPS test deployment — only after CI

This repo's *running* production v0.4 uses host networking for Caddy plus API bound to loopback. Do not change Caddy, UFW, provider firewall, or `.env` while evaluating this PR. On the VPS:

```bash
cd /root/moobee-iptv
git status --short
git fetch origin
# If worktree is clean or only contains known untracked backups:
git switch -c test-epg-v0.6 origin/feat/xmltv-epg-v0.6
# Back up the live SQLite database before schema changes
bash scripts/backup-db.sh
docker compose -f compose.yaml -f compose.hostnet.yaml config --quiet
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --build api
docker compose -f compose.yaml -f compose.hostnet.yaml ps
```

Use a **Mac connected to Tailscale** to validate:

```bash
curl -fsS https://tv.moobee.lol/healthz
curl -4 --connect-timeout 5 --max-time 10 -sS -o /dev/null -w 'Public admin: %{http_code}\n' \
  --resolve admin.moobee.lol:443:162.35.165.218 https://admin.moobee.lol/admin
curl -4 --connect-timeout 5 --max-time 10 -sS -o /dev/null -w 'VPN admin: %{http_code}\n' \
  --resolve admin.moobee.lol:443:100.99.174.2 https://admin.moobee.lol/admin
```

Expected: streaming **200**, public administration **403**, VPN administration **401** without credentials. Confirm existing subscribers and local HLS playback, import a test guide, map it to a channel, and verify guide appears in your IPTV client. Do not paste `docker compose config` output, admin credentials, or credential-bearing subscriber playlists.

### Rollback (API only)

```bash
cd /root/moobee-iptv
git switch main
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --build api
```

This reverts application code but preserves the SQLite database, backups, Caddy host networking, and existing content. For complete data rollback, verify and restore the backup separately after planning downtime.
