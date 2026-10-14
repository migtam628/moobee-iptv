# Signed HLS renewal protocol — v0.8 draft

This branch is stacked on permanent-revocation PR #14. No staging or production deployment has been made.

## Authenticated client hint
When **both** `MOOBEE_STREAM_ENABLED=true` and
`MOOBEE_STREAM_PLAYBACK_ENABLED=true`, the authenticated Xtream
`player_api.php` response advertises
`user_info.moobee_hls_refresh_after_seconds: 10800` (3 hours).

Signed child URLs remain valid for at most four hours. This hint is not a
credential and does not change subscriber authentication, token length or
HMAC verification. Normal Xtream integrations are unchanged.

## Native client behavior
An opted-in StreamBoss native player can fetch a new original
`/movie/{username}/{password}/{id}.m3u8` or episode entry *before* four
hours, then re-open the media with its saved position and play/pause state.
The old HLS token is never extended, and revocation still permanently
invalidates the old session. Other Xtream providers do not request renewal.

Renewal requires the client to have its original Xtream credentials.
Initial entry URLs and bearer paths must be redacted in server/access logs;
neither endpoint should be exposed by a public CORS wildcard.

## Release requirements
- New native macOS build tested against isolated **localhost 18001**
  staging with the opt-in hint present (production and port 18000 unchanged)
- Current video, playback position, paused state and seek behavior verified
  across a forced short clock interval or accelerated test
- No credential/token leakage in player logs, diagnostics or reverse proxy
- HLS expires normally for independent clients that do not renew
- Mobile, watch/fullscreen handoff and existing providers covered by tests
- Account disable/re-enable and token-revocation regression repeated
- Production concurrency and capacity limits still not implemented

This is a **client-coordinated renewal**, not a server-side token extension,
not a refresh token, and not a guarantee of gapless playback across re-open.
