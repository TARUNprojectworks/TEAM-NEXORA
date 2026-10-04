#!/usr/bin/env bash
# Optional: export the reviews table to BigQuery (dataset from BQ_DATASET, table "reviews"),
# replacing the table each time, for the Looker Studio report. Not in the crontab.
# Run as: sudo -u nexora /srv/nexora/app/deploy/export_bq.sh
# Only ids, true/false answers and times leave the VM: no names, emails or card text.
set -euo pipefail

ENV_FILE=/srv/nexora/app/.env
DB_FILE=/srv/nexora/data/nexora.db
read_env() { grep -E "^$1=" "$ENV_FILE" | cut -d= -f2-; }
PROJECT=$(read_env GCP_PROJECT)
DATASET=$(read_env BQ_DATASET)
DATASET=${DATASET:-nexora}

CSV=$(mktemp --suffix=.csv)
trap 'rm -f "$CSV"' EXIT
sqlite3 -header -csv "$DB_FILE" \
  "SELECT id, user_id, card_id, knew_it, confident, style, reviewed_at FROM reviews ORDER BY id" > "$CSV"

# Make the dataset the first time.
bq --project_id="$PROJECT" show --dataset "$PROJECT:$DATASET" > /dev/null 2>&1 \
  || bq --project_id="$PROJECT" mk --dataset "$PROJECT:$DATASET"

bq --project_id="$PROJECT" load --replace --source_format=CSV --skip_leading_rows=1 \
  "$DATASET.reviews" "$CSV" \
  id:INTEGER,user_id:INTEGER,card_id:INTEGER,knew_it:BOOLEAN,confident:BOOLEAN,style:STRING,reviewed_at:TIMESTAMP
echo "$(date -u +%FT%TZ) export_bq: loaded $(($(wc -l < "$CSV") - 1)) reviews into $DATASET.reviews"
