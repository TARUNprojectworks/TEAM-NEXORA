#!/usr/bin/env bash
# Update the running app to the latest main, or to a tag/branch you name:
#   sudo bash /srv/nexora/app/deploy/update.sh             # latest of the current branch
#   sudo bash /srv/nexora/app/deploy/update.sh v1.0-demo   # a tag (or a branch)
# Steps: get the code, install requirements, run migrations, restart, check /health.
set -euo pipefail

APP=/srv/nexora/app
VENV=/srv/nexora/venv
REF=${1:-}
as_nexora() { sudo -u nexora -H "$@"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash $0 [tag-or-branch]" >&2
  exit 1
fi

cd "$APP"
as_nexora git fetch --tags --prune origin
if [ -n "$REF" ]; then
  as_nexora git checkout "$REF"
fi
# On a branch, take its newest commits; a tag is a fixed point, so nothing to pull.
if as_nexora git symbolic-ref -q HEAD > /dev/null; then
  as_nexora git pull --ff-only
fi
echo "Code is now at: $(as_nexora git describe --tags --always) ($(as_nexora git log -1 --format=%s))"

as_nexora "$VENV/bin/pip" install --quiet -r requirements.txt
as_nexora bash -c "cd $APP && $VENV/bin/flask db upgrade"
systemctl restart nexora

for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/health > /dev/null 2>&1; then
    echo "OK: the app is running ($(curl -fsS http://127.0.0.1:8000/health))."
    exit 0
  fi
  sleep 1
done
echo "The app did not answer /health. Look at: journalctl -u nexora -n 50" >&2
exit 1
