#!/usr/bin/env bash
# Set up Nexora Notebook on a fresh Ubuntu 24.04 Compute Engine VM.
#
# From the folder you cloned the repo into (check out the tag or branch you want first):
#   sudo bash deploy/setup_vm.sh --project <ID> --notes-bucket <B1> --backup-bucket <B2> --host <HOST>
#
# <HOST> is the name Caddy gets a certificate for, e.g. 34-93-12-34.sslip.io (your static IP with dashes).
# Safe to run twice: it keeps the database and the secret key, and only replaces config files.
set -euo pipefail

APP_USER=nexora
BASE=/srv/nexora
APP=$BASE/app
VENV=$BASE/venv
DATA=$BASE/data
BACKUPS=$BASE/backups
LOGS=/var/log/nexora
ENV_FILE=$APP/.env
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

step() { echo; echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }
as_app() { sudo -u "$APP_USER" -H "$@"; }

# ---------- 0. Arguments ----------

PROJECT="" NOTES_BUCKET="" BACKUP_BUCKET="" HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT=$2; shift 2 ;;
    --notes-bucket) NOTES_BUCKET=$2; shift 2 ;;
    --backup-bucket) BACKUP_BUCKET=$2; shift 2 ;;
    --host) HOST=$2; shift 2 ;;
    *) fail "Unknown option: $1" ;;
  esac
done
[ "$(id -u)" -eq 0 ] || fail "Run with sudo."
for name in PROJECT NOTES_BUCKET BACKUP_BUCKET HOST; do
  [ -n "${!name}" ] || fail "Missing an option. Usage: sudo bash deploy/setup_vm.sh --project <ID> --notes-bucket <B1> --backup-bucket <B2> --host <HOST>"
done
[ -f "$REPO_DIR/wsgi.py" ] || fail "Run this from the cloned repo (deploy/setup_vm.sh inside it)."

# ---------- 1. Packages ----------

step "Installing packages: python3-venv, git, sqlite3, curl"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv git sqlite3 curl gnupg debian-keyring debian-archive-keyring apt-transport-https

step "Installing Caddy (from Caddy's own apt repository)"
if ! command -v caddy > /dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
else
  echo "Caddy is already installed."
fi

step "Installing the Google Cloud Ops Agent"
if [ ! -d /etc/google-cloud-ops-agent ]; then
  curl -sSo /tmp/add-google-cloud-ops-agent-repo.sh https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
  bash /tmp/add-google-cloud-ops-agent-repo.sh --also-install
else
  echo "The Ops Agent is already installed."
fi

# ---------- 2. User and folders ----------

step "Creating user $APP_USER and folders"
if ! id "$APP_USER" > /dev/null 2>&1; then
  useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$BASE" "$DATA" "$DATA/uploads" "$BACKUPS" "$LOGS"
chown "$APP_USER:$APP_USER" "$BASE" "$DATA" "$DATA/uploads" "$BACKUPS" "$LOGS"

# ---------- 3. Code ----------

step "Copying the repo to $APP"
ORIGIN_URL=$(git -c safe.directory='*' -C "$REPO_DIR" config --get remote.origin.url || true)
if [ ! -d "$APP/.git" ]; then
  # A clone of the folder we were run from, at the same commit (branch or tag) that is checked out there.
  git -c safe.directory='*' clone --quiet "$REPO_DIR" "$APP"
  REF=$(git -c safe.directory='*' -C "$REPO_DIR" rev-parse HEAD)
  git -c safe.directory='*' -C "$APP" checkout --quiet "$REF" 2> /dev/null || true
  if [ -n "$ORIGIN_URL" ]; then
    git -c safe.directory='*' -C "$APP" remote set-url origin "$ORIGIN_URL"   # update.sh pulls from GitHub
  fi
  chown -R "$APP_USER:$APP_USER" "$APP"
  echo "Copied at $(as_app git -C "$APP" describe --tags --always)."
else
  echo "$APP already exists; keeping it. To update the code use: sudo bash $APP/deploy/update.sh"
fi
chmod +x "$APP/deploy/"*.sh

# ---------- 4. Python packages ----------

step "Installing Python packages into $VENV"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  chown -R "$APP_USER:$APP_USER" "$VENV"
fi
as_app "$VENV/bin/pip" install --quiet --upgrade pip
as_app "$VENV/bin/pip" install --quiet -r "$APP/requirements.txt"

# ---------- 5. Settings (.env) ----------

step "Writing $ENV_FILE"
# Keep the existing secret key on a second run, so logged-in students stay logged in.
SECRET_KEY=""
if [ -f "$ENV_FILE" ]; then
  SECRET_KEY=$(grep -E '^SECRET_KEY=' "$ENV_FILE" | cut -d= -f2- || true)
fi
if [ -z "$SECRET_KEY" ] || [ "$SECRET_KEY" = "change-me-to-any-long-random-text" ]; then
  SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
fi

cp "$APP/.env.example" "$ENV_FILE"
set_env() {
  # Replace KEY=... in .env, or add it. Our values never contain "|".
  if grep -qE "^$1=" "$ENV_FILE"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
  else
    echo "$1=$2" >> "$ENV_FILE"
  fi
}
set_env FLASK_DEBUG 0
set_env SECRET_KEY "$SECRET_KEY"
set_env SECRET_SOURCE env
set_env SESSION_COOKIE_SECURE true
set_env DATABASE_URL "sqlite:///$DATA/nexora.db"
set_env LOG_FILE "$LOGS/app.log"
set_env AI_ENABLED false
set_env GEMINI_MODEL gemini-3.5-flash
set_env AI_DAILY_LIMIT 20
set_env STORAGE_BACKEND local
set_env UPLOAD_FOLDER "$DATA/uploads"
set_env GCP_PROJECT "$PROJECT"
set_env GCP_LOCATION global
set_env NOTES_BUCKET "$NOTES_BUCKET"
set_env BACKUP_BUCKET "$BACKUP_BUCKET"
chown "$APP_USER:$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ---------- 6. Database ----------

step "Creating or upgrading the database, then loading the ready decks and demo students"
as_app bash -c "cd $APP && $VENV/bin/flask db upgrade"
as_app bash -c "cd $APP && $VENV/bin/python seed.py"

# ---------- 7. Caddy, the app service, logging, cron ----------

step "Installing the Caddyfile for $HOST"
sed "s|NEXORA_HOST|$HOST|" "$APP/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile > /dev/null

step "Installing nexora.service"
cp "$APP/deploy/nexora.service" /etc/systemd/system/nexora.service

step "Installing the Ops Agent config and log rotation"
cp "$APP/deploy/ops-agent.yaml" /etc/google-cloud-ops-agent/config.yaml
cat > /etc/logrotate.d/nexora <<EOF
$LOGS/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF

step "Installing the crontab for $APP_USER"
crontab -u "$APP_USER" "$APP/deploy/crontab.txt"

step "Setting the 7-day delete rule on the notes bucket (best effort)"
LIFECYCLE=$(mktemp)
echo '{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 7}}]}' > "$LIFECYCLE"
if PATH=$PATH:/snap/bin gcloud storage buckets update "gs://$NOTES_BUCKET" --lifecycle-file="$LIFECYCLE" --quiet > /dev/null 2>&1; then
  LIFECYCLE_STATUS="set"
else
  LIFECYCLE_STATUS="NOT set (see the README: run the command from Cloud Shell)"
fi
rm -f "$LIFECYCLE"

step "Starting everything"
systemctl daemon-reload
systemctl enable --now nexora > /dev/null 2>&1
systemctl restart nexora
systemctl enable --now caddy > /dev/null 2>&1
systemctl reload caddy || systemctl restart caddy
systemctl restart google-cloud-ops-agent

HEALTH="no answer"
for _ in $(seq 1 20); do
  if HEALTH=$(curl -fsS http://127.0.0.1:8000/health 2> /dev/null); then break; fi
  HEALTH="no answer"
  sleep 1
done

# ---------- 8. Checklist ----------

is_active() { systemctl is-active --quiet "$1" && echo "running" || echo "NOT running"; }
cat <<EOF

========================================================================
 Nexora Notebook: setup finished
========================================================================
 App service (nexora)      $(is_active nexora)    /health: $HEALTH
 Caddy (HTTPS)             $(is_active caddy)
 Ops Agent                 $(is_active google-cloud-ops-agent)
 Backup cron (02:00 IST)   $(crontab -u "$APP_USER" -l 2> /dev/null | grep -c backup.sh) job installed
 Notes bucket 7-day rule   $LIFECYCLE_STATUS
 VM time zone              $(timedatectl show -p Timezone --value 2> /dev/null || echo unknown) (the crontab assumes UTC)

 Settings: $ENV_FILE   (AI_ENABLED=false, STORAGE_BACKEND=local, SECRET_SOURCE=env)

 Check these by hand:
  [ ] Open https://$HOST  (the first visit can take ~30 s while Caddy gets the certificate)
  [ ] Firewall allows ports 80 and 443 (VM option "Allow HTTP/HTTPS traffic")
  [ ] $HOST points at this VM's static IP
  [ ] Log in as riya@example.com / nexora-demo and open Home
  [ ] Run a backup now:  sudo -u $APP_USER $APP/deploy/backup.sh
  [ ] Logs Explorer shows log "nexora_app" for project $PROJECT
  [ ] Update later with:  sudo bash $APP/deploy/update.sh [tag]
========================================================================
EOF
