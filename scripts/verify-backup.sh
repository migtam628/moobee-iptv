#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 1 ]; then echo "Usage: bash scripts/verify-backup.sh /path/to/backup.sqlite3"; exit 2; fi
FILE="$1"
test -f "$FILE"
python3 - "$FILE" <<'PY'
import sqlite3,sys
db=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True)
assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
for name in ("users","channels"):
    db.execute("SELECT COUNT(*) FROM "+name).fetchone()
print("Integrity and required tables OK")
db.close()
PY
