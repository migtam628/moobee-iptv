# MooBee IPTV v0.4 — optional VPN-only admin access

This is an **opt-in** hardening change; by default `compose.yaml` still mounts the original `Caddyfile` and existing public admin access remains unchanged. Nothing is automatically installed or enabled on your VPS. Existing Xtream client traffic at `tv.moobee.lol` remains public over HTTPS.

## Before enabling

1. Back up your SQLite database and preserve `.env`.
2. Set up Tailscale on your VPS and on your laptop/mobile administrator device. Refer to https://tailscale.com/kb/1031/install-linux. Authenticate both into the **same tailnet**. Do not reveal auth keys or reusable login tokens.
3. Verify connectivity from your laptop to the VPS's Tailscale address (typically `100.x.y.z`) while connected to Tailscale. Record the VPS tailnet IPv4 using `tailscale ip -4` on the VPS.
4. **Test over the tunnel before flipping the policy.** From your laptop (replace `TAILSCALE_IP`), run `curl -k -I --resolve admin.moobee.lol:443:TAILSCALE_IP https://admin.moobee.lol/admin`. Before the flip, you should get HTTP 401 without credentials. Do not use `-k` in ongoing tests; use `curl -I --resolve ...` with normal certificate checks once DNS and Caddy TLS are working.
5. Since public DNS still points at the VPS public IP, configure split DNS or a device-specific `hosts` override for `admin.moobee.lol` to point to the VPS **Tailscale IPv4**. Otherwise your browser will use the public IP and receive 403 after the flip. The domain's existing public DNS A record may remain unchanged for Caddy certificate issuance.
6. Confirm that Tailscale ACLs/grants restrict access to the VPS to trusted administrator devices/users; the Caddy matcher accepts any tailnet source IP in `100.64.0.0/10`, not solely your personal device.

## Opt-in switch (only after tunnel access is working)

On the VPS:
```bash
cd /root/moobee-iptv
git status
git fetch origin
git switch -c test-security-v0.4 origin/feat/admin-security-v0.4
echo 'CADDY_CONFIG_FILE=./Caddyfile.private' >> .env
docker compose config --quiet
docker compose up -d --no-deps --force-recreate caddy
docker compose ps
```

Expected:
- From the public Internet using public DNS: `curl -I https://admin.moobee.lol/admin` returns HTTP **403**.
- From your Tailscale-connected device using hostname resolution to the VPS tailnet IP: `curl -I https://admin.moobee.lol/admin` returns HTTP **401** without admin credentials.
- With credentials over the VPN, the admin panel loads as before.
- `curl -fsS https://tv.moobee.lol/healthz` still reports `{"status":"ok"}`.

**If the admin panel stops working**: from SSH (keep the terminal open while testing), roll back:
```bash
cd /root/moobee-iptv
sed -i '/^CADDY_CONFIG_FILE=/d' .env
docker compose up -d --no-deps --force-recreate caddy
```

Rollback restores the previous password-protected publicly reachable admin site; it does not remove the administrator login requirement. Confirm `CADDY_CONFIG_FILE` appears only once in `.env`, and do not share the contents of `.env`.

### Security notes

- Private routing protects only the administration hostname/path; Xtream clients still use `tv.moobee.lol`.
- IP matching assumes Caddy receives the original remote client's tailnet IP after Docker NAT. Test it; some Docker or reverse-proxy topologies obscure it.
- This does **not** implement rate limiting, credential rotation, audit alerting or per-user SSO. Those are separate future tasks.
- Keep SSH accessible while experimenting. Never lock down port 22 or remove your console access without confirming an alternative.
- Test automatic Caddy certificate renewals under your network/firewall configuration; public TCP 80/443 remain configured.

## Host-network workaround for Docker source NAT (recommended on this VPS)

Packet capture on the VPS showed Tailscale source `100.117.19.100` becoming Docker bridge `172.18.0.1` in the Caddy container. Do not trust the bridge IP. An optional `compose.hostnet.yaml` overlay runs Caddy with Linux host networking, while FastAPI stays in the bridge and publishes *only* `127.0.0.1:8000`. The private Caddyfile has a configurable upstream.

**Do not run until tested with installed Docker Compose (support for `!reset` required).** Keep SSH connected. Before cutover, verify `docker compose -f compose.yaml -f compose.hostnet.yaml config` shows `network_mode: host` for Caddy, no Caddy `ports`, and API port binding host_ip `127.0.0.1`. Abort if any API port is exposed as 0.0.0.0.

Install/test from a clean working tree after fetching the branch:

```bash
cd /root/moobee-iptv
git fetch origin
git status
git switch -c test-hostnet-v0.4 origin/feat/admin-security-v0.4
# Keep an existing SSH session open, and make a database backup before testing.
# Ensure exactly one .env line selects the private Caddyfile:
sed -i '/^CADDY_CONFIG_FILE=/d' .env
echo 'CADDY_CONFIG_FILE=./Caddyfile.private' >> .env
docker compose -f compose.yaml -f compose.hostnet.yaml config > /tmp/moobee-hostnet-rendered.yaml
grep -A16 '^  api:' /tmp/moobee-hostnet-rendered.yaml
grep -A16 '^  caddy:' /tmp/moobee-hostnet-rendered.yaml
```

Validate private Caddyfile syntax with both variables specified:

```bash
docker run --rm -v "$PWD/Caddyfile.private:/etc/caddy/Caddyfile:ro" -e DOMAIN=tv.moobee.lol -e ADMIN_DOMAIN=admin.moobee.lol -e CADDY_UPSTREAM=127.0.0.1:8000 caddy:2 caddy validate --config /etc/caddy/Caddyfile
```

After verifying the rendered config, stop only Caddy first to release 80/443; recreate API with loopback-bound 8000, then create host-network Caddy:

```bash
docker compose stop caddy
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --force-recreate api
docker compose -f compose.yaml -f compose.hostnet.yaml up -d --no-deps --force-recreate caddy
docker compose -f compose.yaml -f compose.hostnet.yaml ps
```

Check `ss -lntp | grep -E ':(80|443|8000)\\b'`. Port 8000 must listen only on 127.0.0.1, and Caddy must own 80/443. Public admin GET should return 403; a Tailscale-resolved admin GET should return 401 before credentials. Public `tv.moobee.lol/healthz` must remain 200. Note that `curl -I` uses HEAD and can return 405; use `curl -sS -o /dev/null -w '%{http_code}\\n'` instead.

### Host-network rollback

If any checks fail, retain SSH and execute:

```bash
cd /root/moobee-iptv
docker compose -f compose.yaml -f compose.hostnet.yaml stop caddy
docker compose -f compose.yaml -f compose.hostnet.yaml stop api
# Recreate both containers with the original bridge networking.
docker compose -f compose.yaml up -d --no-deps --force-recreate api
docker compose -f compose.yaml up -d --no-deps --force-recreate caddy
docker compose ps
```

If you need to restore public password-protected admin access instead of VPN-only, first remove the `CADDY_CONFIG_FILE` line from .env, then recreate Caddy with the original compose file.
