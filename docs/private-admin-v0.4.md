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
