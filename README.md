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
