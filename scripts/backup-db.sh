#!/usr/bin/env bash
# A consistent copy of data/sprout.db into ~/sprout_backups (on the SD card, so
# the weekly USB clone carries it too) and /mnt/hdd/sprout_backups.
#
# A plain cp while the bot writes can catch the file half-way. SQLite's backup
# API takes a consistent snapshot, and integrity_check proves the copy opens.
# The Pi has no sqlite3 CLI, so this goes through the venv's Python.
#
# Cron: 50 4 * * * nice -n 10 ionice -c3 $HOME/personal/sprout/scripts/backup-db.sh 2>&1 | logger -t sprout-db-backup
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/data/sprout.db"
DEST="$HOME/sprout_backups"
HDD_DEST="/mnt/hdd/sprout_backups"
KEEP=14

if [ ! -f "$DB" ]; then
    echo "!! no database at $DB" >&2
    exit 1
fi

mkdir -p "$DEST"
chmod 700 "$DEST"
OUT="$DEST/sprout_$(date +%F_%H%M).db"
TMP="$OUT.tmp"
# A half-written copy must never look like a backup.
trap 'rm -f "$TMP"' EXIT

"$ROOT/.venv/bin/python" - "$DB" "$TMP" <<'PY'
import sqlite3
import sys

src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
check = dst.execute("PRAGMA integrity_check").fetchone()[0]
nodes = dst.execute("SELECT count(*) FROM nodes").fetchone()[0]
dst.close()
src.close()
if check != "ok":
    sys.exit(f"!! integrity_check: {check}")
print(f"nodes: {nodes}")
PY
chmod 600 "$TMP"
mv "$TMP" "$OUT"
echo "OK: $OUT ($(du -h "$OUT" | cut -f1))"

# The HDD copy outlives the SD card. A missing HDD is not a failed backup.
if mountpoint -q /mnt/hdd 2>/dev/null; then
    mkdir -p "$HDD_DEST"
    cp "$OUT" "$HDD_DEST/"
    ls -1t "$HDD_DEST"/sprout_*.db | tail -n +$((KEEP + 1)) | xargs -r rm -f
    echo "HDD copy: $HDD_DEST/$(basename "$OUT")"
else
    echo "!! /mnt/hdd is not mounted, no HDD copy" >&2
fi

ls -1t "$DEST"/sprout_*.db | tail -n +$((KEEP + 1)) | xargs -r rm -f
