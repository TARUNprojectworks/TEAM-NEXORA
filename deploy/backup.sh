#!/usr/bin/env bash
# Nightly backup: a safe copy of the SQLite database (sqlite3 .backup works while the app runs),
# gzipped and uploaded to the backup bucket. Keeps the last 7 copies on the VM too.
# Runs from cron as the nexora user (deploy/crontab.txt). Run by hand: sudo -u nexora /srv/nexora/app/deploy/backup.sh
set -euo pipefail

ENV_FILE=/srv/nexora/app/.env
DB_FILE=/srv/nexora/data/nexora.db
BACKUP_DIR=/srv/nexora/backups

BUCKET=$(grep -E '^BACKUP_BUCKET=' "$ENV_FILE" | cut -d= -f2-)
if [ -z "$BUCKET" ]; then
  echo "$(date -u +%FT%TZ) backup: BACKUP_BUCKET is empty in $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
FILE="$BACKUP_DIR/nexora-$(date -u +%Y-%m-%d-%H%M).db"
sqlite3 "$DB_FILE" ".backup '$FILE'"
gzip -f "$FILE"
gcloud storage cp --quiet "$FILE.gz" "gs://$BUCKET/db/"

# Keep a week of local copies; the bucket keeps the rest.
find "$BACKUP_DIR" -name 'nexora-*.db.gz' -mtime +7 -delete
echo "$(date -u +%FT%TZ) backup: uploaded $(basename "$FILE").gz to gs://$BUCKET/db/"
