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

## Deploy to Google Cloud

Coming in phase 6 (see `deploy/`). On the VM the app starts with
`gunicorn wsgi:app`; locally we use `flask run` because Gunicorn
doesn't run on Windows.
