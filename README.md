# MooBee IPTV — Private Xtream-Compatible IPTV Server

Self-hosted, private IPTV MVP for media you own or have permission to distribute. This is **not** a complete Xtream Codes implementation.

## Features

- Xtream-style authentication and live channel discovery via `/player_api.php`
- M3U playlists via `/get.php`
- Authenticated local HLS playlists and segments via `/live/...` and `/hls/...`
- SQLite user and channel management through a CLI
- Docker Compose deployment with Caddy automatic HTTPS
- XMLTV placeholder and empty VOD/series responses

**Not yet supported:** direct MPEG-TS streams, VOD/series playback, real EPG, external stream ingestion, transcoding, web admin dashboard, concurrency/session enforcement, and load balancing.

## Requirements

Ubuntu Server 24.04 LTS VPS, DNS A record for your chosen TV hostname, Docker Engine with the Compose plugin, and a domain accessible on TCP 80 and 443. Use SSH keys and restrict SSH at your provider firewall. Install `ffmpeg` on the VPS to generate the optional demo channel.

For installation, follow https://docs.docker.com/engine/install/ubuntu/.

## Deploy

```bash
git clone https://github.com/migtam628/moobee-iptv.git
cd moobee-iptv
cp .env.example .env
nano .env  # Set DOMAIN and PUBLIC_URL to your actual hostname
mkdir -p data media/channel1
sudo chown -R 10001:10001 data
chmod +x scripts/create-demo-media.sh
./scripts/create-demo-media.sh
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=50
```

The demo generator needs `ffmpeg` installed locally. The container runs without root privileges; `data/` must be writable by UID 10001. Media is mounted read-only within the API container.

## Add a user and test channel

```bash
sudo docker compose exec api python admin.py add-user viewer01
sudo docker compose exec api python admin.py add-channel 1 'Private Demo Channel' channel1 --category Personal
sudo docker compose exec api python admin.py list-users
sudo docker compose exec api python admin.py list-channels
```

The generated password is displayed once. Save it securely.

## Verify

```bash
curl -fsS https://tv.example.com/healthz
curl 'https://tv.example.com/player_api.php?username=viewer01&password=YOUR_PASSWORD'
curl 'https://tv.example.com/player_api.php?username=viewer01&password=YOUR_PASSWORD&action=get_live_streams'
curl 'https://tv.example.com/get.php?username=viewer01&password=YOUR_PASSWORD&type=m3u_plus&output=m3u8'
```

Substitute your actual hostname and credentials. Avoid saving credential-containing URLs to shell history, logs, screenshots or tickets.

In an IPTV application such as TiviMate, choose Xtream login, enter your HTTPS hostname, username and generated password, and select HLS/m3u8 when available. Some clients require unsupported MPEG-TS or other API fields. You can also test the generated M3U playlist in VLC.

## Add authorized local media

Each registered channel corresponds to a directory under `media/`, with `index.m3u8` and relative HLS segments.

```bash
mkdir -p media/channel2
ffmpeg -i /path/to/your-authorized-video.mp4 -c:v libx264 -preset veryfast -c:a aac -hls_time 6 -hls_list_size 0 -hls_segment_filename 'media/channel2/segment%05d.ts' media/channel2/index.m3u8
sudo docker compose exec api python admin.py add-channel 2 'My Video' channel2 --category Personal
```

The MVP supports simple local HLS playlists, not remote or nested manifests, encrypted segments or continuous live ingest.

## Security

- Keep `.env`, `data/`, `media/`, credential files, private keys and runtime files out of Git.
- Only Caddy publishes ports 80 and 443. Do not expose the internal API on port 8000 or the SQLite database.
- Use HTTPS and strong, unique account passwords.
- Restrict access with provider firewall rules, keeping the actual SSH port open for administration.
- Beware that Docker published ports may bypass some UFW filtering.
- CLI administration uses SSH/container access rather than a publicly exposed admin endpoint.
- Streaming credentials appear in Xtream-compatible URLs and may be recorded by clients or reverse-proxy logs.
- Add rate limiting, security monitoring and session limits before larger deployment.
- Backup SQLite consistently via the SQLite backup API or while the API is stopped. Test restores.
- Stream only content you own or are licensed to distribute.

## Troubleshooting

```bash
sudo docker compose ps
sudo docker compose logs --tail=100 api
sudo docker compose logs --tail=100 caddy
sudo docker compose config
```

**401:** invalid, missing or disabled account. **404 media:** missing playlist or segment. **400 HLS:** unsupported/unsafe reference. **501 TS:** MPEG-TS playback not implemented. **TLS errors:** check DNS, provider firewall and Caddy logs.

## Layout

```text
app/main.py                    API and stream authorization
app/admin.py                   Users and channels CLI
compose.yaml                   Docker services
Dockerfile                     Application container
Caddyfile                      HTTPS reverse proxy
scripts/create-demo-media.sh   Demo HLS generator
media/                         Local media, excluded from Git
data/                          SQLite state, excluded from Git
```

## Licensing

No open-source license has been selected yet. See [LICENSE-NOTICE.md](LICENSE-NOTICE.md).

## MooBee IPTV v0.2 — Private administration

v0.2 adds an authenticated browser interface at **https://admin.moobee.lol/admin** with account creation/enable/disable, channel creation/editing/deletion, counts, and recent audit entries. The streaming hostname remains **https://tv.moobee.lol**. No real-time viewer metrics, EPG import, or external ingest are implemented yet.

**Important:** Do not deploy without setting an admin password (minimum 20 characters). The admin subdomain is publicly reachable unless you restrict it at your VPS firewall, VPN, or proxy. HTTP Basic authentication is suitable for initial private use over HTTPS, not a complete production identity solution. Admin credentials are kept in the local untracked `.env` file. The server compares them using constant-time comparison. If no password is configured, admin routes return HTTP 503. The existing API still works.

### Upgrade existing server

On the VPS, first back up `data/server.sqlite3` safely (stop the API or use SQLite's backup API) and preserve your local `.env`. To deploy the feature branch for testing:

```bash
cd /root/moobee-iptv
git status
git fetch origin
git switch -c test-admin-v0.2 origin/feat/admin-dashboard-v0.2
```

If `git status` shows your previous local Dockerfile fix, either commit it first or stash it before switching. Do **not** discard changes to `.env`, `data/`, or `media/`. Ensure `admin.moobee.lol` points to your VPS. Configure locally:

```bash
cat >> .env <<'EOF'
ADMIN_DOMAIN=admin.moobee.lol
ADMIN_USERNAME=admin
EOF
# Generate a random password on your VPS; save it in a password manager.
ADMIN_SECRET="$(openssl rand -hex 24)"
printf 'ADMIN_PASSWORD=%s\n' "$ADMIN_SECRET" >> .env
unset ADMIN_SECRET
chmod 600 .env
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl -i https://tv.moobee.lol/healthz
curl -I https://admin.moobee.lol/admin
```

The last command should return HTTP 401 with a Basic authentication challenge. Open `https://admin.moobee.lol/admin` in your browser and enter your configured admin credentials. A 503 indicates an unset or too-short admin password. Use `docker compose logs --tail=100 api caddy` for errors.

### Rollback

Because v0.2 adds only a separate audit table and does not delete existing account/channel data, you can return to the previous application branch if needed:

```bash
git switch main
docker compose up -d --build
```

Keep a tested database backup before every upgrade. Git version control does not back up SQLite data or media.

### Constraints and security

- Admin API and UI exist under `/admin` and `/admin/api/*` on the admin hostname. Caddy blocks those paths on the TV hostname.
- Keep admin access private via WireGuard/Tailscale or a reverse proxy access policy where possible.
- Do not paste the admin password or generated subscriber passwords into screenshots, shell transcripts, or tickets.
- Subscriber passwords are shown once on creation; they are stored as salted scrypt hashes. The current Xtream-compatible playback URLs still expose subscriber credentials to the client and possibly its logs.
- Channel folders must already contain supported local HLS media; creating a channel record does not ingest or transcode content.
- Activity records administrative mutations only; they are not active viewing sessions or detailed analytics.
- These changes require testing on the real VPS; branch commits alone do not mean production deployment.
