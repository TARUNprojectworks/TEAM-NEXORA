"""All settings come from environment variables, with safe local defaults.

Moving from a laptop to the GCP VM should only mean changing env vars.
See .env.example for what each variable does.
"""

import os

from dotenv import load_dotenv

# Locally we keep settings in .env. On the VM, systemd sets real env vars,
# and load_dotenv never overrides those.
load_dotenv()


def env_bool(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value else default


class Config:
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SECRET_SOURCE = os.environ.get("SECRET_SOURCE", "env")  # env | secret_manager
    SECRET_NAME = os.environ.get("SECRET_NAME", "nexora-secret-key")

    # A relative SQLite path is resolved inside Flask's instance/ folder,
    # so this default lands at instance/nexora.db.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///nexora.db")

    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True

    # CSRF tokens last as long as the session, so a long study session
    # doesn't fail on the next answer.
    WTF_CSRF_TIME_LIMIT = None

    # Logging
    LOG_FILE = os.environ.get("LOG_FILE", "")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # "Today" and exam dates are Indian dates; stored times are UTC.
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")

    # AI
    AI_ENABLED = env_bool("AI_ENABLED", False)
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    AI_DAILY_LIMIT = env_int("AI_DAILY_LIMIT", 20)
    AI_TIMEOUT_SECONDS = env_int("AI_TIMEOUT_SECONDS", 15)

    # Storage for note photos
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")  # local | gcs
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "")  # empty = instance/uploads
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # note photos are max 5 MB

    # Google Cloud (only needed on the VM)
    GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
    GCP_LOCATION = os.environ.get("GCP_LOCATION", "asia-south1")
    NOTES_BUCKET = os.environ.get("NOTES_BUCKET", "")
    BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "")
    BQ_DATASET = os.environ.get("BQ_DATASET", "nexora")


def load_secret_key_from_secret_manager(project, secret_name):
    """Read SECRET_KEY from Secret Manager using the VM's service account."""
    # Imported here so the app runs on a laptop without GCP libraries set up.
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8").strip()
