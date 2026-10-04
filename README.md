# Nexora Notebook

A flashcard app for students, built by Team Nexora for the Cognizant NPN GCP
hackathon (Use Case 3). Flask + SQLite, runs on one Compute Engine VM.

## Run it locally (AI off, no GCP needed)

You need Python 3.11 or newer.

Windows (PowerShell):

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
flask run
```

macOS / Linux:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask run
```

Open http://127.0.0.1:5000, sign up, and you land on Home.
The database is created at `instance/nexora.db` on first start.
If `models.py` changes, delete `instance/nexora.db` and start again
(there are no migrations).

Check it's alive: http://127.0.0.1:5000/health returns `{"status": "ok"}`.

## Run the tests

```
pytest
```

## Deploy

The app runs on one Compute Engine VM (Ubuntu 24.04): Caddy serves HTTPS and passes
requests to Gunicorn (`gunicorn wsgi:app` on 127.0.0.1:8000), which systemd keeps running.
Everything in `deploy/`:

| File | What it does |
|---|---|
| `setup_vm.sh` | One-time setup of a fresh VM (safe to run again) |
| `Caddyfile` | HTTPS for your hostname, proxy to Gunicorn |
| `nexora.service` | systemd service: Gunicorn, restarts always |
| `ops-agent.yaml` | Ships `/var/log/nexora/app.log` to Cloud Logging (log name `nexora_app`) |
| `backup.sh` + `crontab.txt` | Nightly `sqlite3 .backup` to the backup bucket at 02:00 IST |
| `update.sh` | Pull new code, install requirements, run migrations, restart |
| `export_bq.sh` | Optional: reviews table to BigQuery (not scheduled) |

### First setup

Before you start: two private buckets (notes and backups), a static external IP, and a VM
with "Allow HTTP/HTTPS traffic" and access scope "Allow full access to all Cloud APIs".
The VM's service account needs write access to the backup bucket (Storage Object Admin on it).
The hostname is the static IP with dashes plus `.sslip.io`, e.g. `34-93-12-34.sslip.io`
(if Let's Encrypt limits hit sslip.io, use `.nip.io` the same way).

On the VM:

```bash
git clone https://github.com/TARUNprojectworks/TEAM-NEXORA.git
cd TEAM-NEXORA
git checkout v1.0-demo        # or stay on main
sudo bash deploy/setup_vm.sh --project <ID> --notes-bucket <B1> --backup-bucket <B2> --host <HOST>
```

It installs Python, git, sqlite3, Caddy and the Ops Agent; creates the user `nexora`; copies the
repo to `/srv/nexora/app`; makes the venv, `/srv/nexora/data` and `/var/log/nexora`; writes
`/srv/nexora/app/.env` (AI off, local photo storage, a random `SECRET_KEY`); runs
`flask db upgrade` and `seed.py`; installs Caddy, the service, the Ops Agent config and the
crontab; starts everything and prints a checklist. Running it again keeps the database and the
secret key.

If the checklist says the notes bucket's 7-day rule isn't set, run this once in Cloud Shell:

```bash
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' > lifecycle.json
gcloud storage buckets update gs://<B1> --lifecycle-file=lifecycle.json
```

### Day to day

```bash
sudo bash /srv/nexora/app/deploy/update.sh             # latest code on the current branch
sudo bash /srv/nexora/app/deploy/update.sh v1.0-demo   # a tag or a branch
sudo systemctl status nexora                          # is it running?
sudo journalctl -u nexora -n 50                       # Gunicorn start-up errors
sudo -u nexora /srv/nexora/app/deploy/backup.sh       # a backup right now
```

Settings live in `/srv/nexora/app/.env` (only `nexora` can read it). After changing it:
`sudo systemctl restart nexora`. Turning on Gemini later means `AI_ENABLED=true`,
`STORAGE_BACKEND=gcs` and `SECRET_SOURCE=secret_manager` there, no code changes.

### Demo day: fresh demo data

Riya's exam is "5 days out" from the day the demo students were made. On the morning of the
demo, re-create only the two demo students (everyone else's data stays):

```bash
sudo -u nexora -H bash -c 'cd /srv/nexora/app && /srv/nexora/venv/bin/python seed.py --reset-demo'
```

Anyone logged in as Riya or Arjun has to log in again afterwards (`nexora-demo` is the password).

### Logs

In Logs Explorer (Logging > Logs Explorer), every app line is in the log `nexora_app` with its
JSON fields under `jsonPayload`. AI calls that failed:

```
logName="projects/<ID>/logs/nexora_app"
jsonPayload.message="ai_call"
jsonPayload.ok=false
```

`jsonPayload.feature` says which feature (make_cards, explain, ...), `jsonPayload.status` the HTTP
status from Google (e.g. 429 or 504), and `jsonPayload.latency_ms` how long it took. We never log
notes, card text, names or emails. Server errors: `logName="projects/<ID>/logs/nexora_app" severity>=ERROR`.

### Optional: BigQuery export

`sudo -u nexora /srv/nexora/app/deploy/export_bq.sh` loads the reviews table (ids, answers and
times only) into `<ID>:nexora.reviews`, replacing it, for Looker Studio. To run it nightly, add a
line like the backup one to `deploy/crontab.txt`.
