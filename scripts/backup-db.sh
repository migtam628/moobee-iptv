#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
BACKUP_DIR="${MOOBEE_BACKUP_DIR:-/root/moobee-backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/moobee-$STAMP.sqlite3"
TMP="$BACKUP_DIR/.moobee-$STAMP.tmp"
trap 'rm -f "$TMP"' EXIT
# Use sqlite's online backup API through the running application container.
docker compose exec -T api python -c 'import sqlite3,sys; src=sqlite3.connect("/data/server.sqlite3"); dst=sqlite3.connect("/data/backup-tmp.sqlite3"); src.backup(dst); dst.close(); src.close()'
docker compose cp api:/data/backup-tmp.sqlite3 "$TMP" >/dev/null
docker compose exec -T api python -c 'import os; os.unlink("/data/backup-tmp.sqlite3")'
chmod 600 "$TMP"
python3 - "$TMP" <<'PY'
import sqlite3,sys
db=sqlite3.connect(sys.argv[1])
result=db.execute("PRAGMA integrity_check").fetchone()[0]
db.close()
if result!="ok": raise SystemExit("Backup integrity check failed: "+result)
PY
mv "$TMP" "$DEST"
sha256sum "$DEST" > "$DEST.sha256"
chmod 600 "$DEST.sha256"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'moobee-*.sqlite3' -mtime +13 -delete
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'moobee-*.sqlite3.sha256' -mtime +13 -delete
echo "Backup complete: $DEST"
