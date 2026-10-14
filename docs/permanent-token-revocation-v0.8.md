# MooBee IPTV v0.8 — Permanent signed-HLS token revocation

**Scope:** Isolated branch stacked on `feat/streamboss-hls-signed-tokens-20261012`. **Not deployed to staging or production.** The upstream token-refresh and concurrency work remains separate.

## Confirmed Phase 13 finding

The standalone security validator reported:

```
RISK: Old token works again after re-enable (nonpermanent revocation)
```

Old signed HLS bearer tokens were bound to subscriber password/salt and created_at but not to a revocation generation. Flipping `disabled` back to 0 restored an otherwise valid token.

## Fix

- Add `users.token_generation INTEGER NOT NULL DEFAULT 0` using an additive, idempotent SQLite upgrade in `main.connect()`.
- Create a database trigger that increments `token_generation` whenever `disabled` transitions from 0 to 1, including the admin UI, CLI and direct-SQL update paths. Enabling a user does not reset the generation.
- Include the generation in the account-derived HMAC fingerprint (`account-v2`) used for both issuing and verifying HLS tokens.
- Require the generation column when looking up the current subscriber during token verification.
- Regression tests cover browser admin disable/re-enable, direct SQL, fresh token issuance and legacy user schema migration.

Old tokens signed by the previous account-v1 fingerprint become invalid on application upgrade. This is intentional; users may need to reopen playback to receive fresh tokens.

## Validation

After CI passes, test on the isolated port `18001` staging instance only. Back up `runtime/data/server.sqlite3` first and preserve the current port-18001 staging image/container for rollback. Deploy by building a **new staging image** and recreating **only** the disposable port-18001 test container with the **same** private `runtime/stage.env` and database. Do not run the production Compose stack or change Caddy/Tailscale rules.

Expected standalone validator result after fix:

```
PASS: Disabled subscriber denied existing token (HTTP 401)
PASS: Disabled subscriber denied new entry (HTTP 401)
PASS: Old token remains denied after account re-enable (HTTP 401)
PASS: Expired token denied using isolated simulated clock
```

A database restore from *before* a revocation can also restore an older generation. **Rotate `MOOBEE_STREAM_HLS_TOKEN_SECRET` after restoring account data** if previously revoked tokens must remain invalid. Keep secrets out of Git, logs, screenshots and test output.

## Open release blockers

- Playback refresh/renewal after the four-hour token lifetime.
- Per-viewer concurrent session enforcement and capacity/load measurements.
- Subscriber credentials still appear in the initial Xtream playback URL.
- Replayable bearer-token URLs require log/path redaction and HTTPS in production.
- Real StreamBoss macOS seek/reconnect/regression after code change.
- No production merge/deployment until isolated staging checks, security review and CI are green.

## CI-budget blocked: isolated local test procedure

On 2026-10-14, GitHub Actions run 38518298763 **did not start**:
`The job was not started because an Actions budget is preventing further use.`
This does not indicate failed application tests. The first manual Docker test attempt
was interrupted at collection because only `tests/` was mounted while
`tests/test_staging_readiness.py` imports `scripts/staging_gateway_readiness.py`.

Run instead from the isolated test checkout (not from production or running stage):
```bash
cd /opt/moobee-iptv-revocation-test
git fetch origin
git status --short
# Switch only if the worktree is clean; preserve uncommitted local changes.
git pull --ff-only
bash scripts/test-local-isolated.sh
```
This helper builds a separate test image, installs pytest at build time,
then runs **all** tests in a network-isolated, read-only container with the
`tests/` and `scripts/` directories mounted read-only. It opens no ports,
does not use running containers, and does not touch production or staging
SQLite state. Do not claim success until the terminal prints the final
`PASS: all isolated tests passed`. Once the Actions budget is restored,
rerun normal GitHub CI separately before merge.
