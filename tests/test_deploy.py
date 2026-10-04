"""The deploy files keep the settings the guide and the VM rely on."""

from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def read(name):
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_service_runs_gunicorn_on_localhost_and_always_restarts():
    service = read("nexora.service")
    assert "gunicorn wsgi:app --bind 127.0.0.1:8000" in service
    assert "Restart=always" in service and "User=nexora" in service
    assert "EnvironmentFile=/srv/nexora/app/.env" in service


def test_caddy_proxies_to_gunicorn_for_the_host_placeholder():
    caddyfile = read("Caddyfile")
    assert "NEXORA_HOST {" in caddyfile and "reverse_proxy 127.0.0.1:8000" in caddyfile


def test_backup_runs_at_2am_ist_and_bigquery_is_not_scheduled():
    jobs = [line for line in read("crontab.txt").splitlines() if line and not line.startswith(("#", "PATH="))]
    assert jobs == ["30 20 * * * /srv/nexora/app/deploy/backup.sh >> /var/log/nexora/backup.log 2>&1"]


def test_ops_agent_ships_the_app_log():
    assert "/var/log/nexora/app.log" in read("ops-agent.yaml")


def test_setup_writes_the_production_settings():
    setup = read("setup_vm.sh")
    for line in ["set_env AI_ENABLED false", "set_env STORAGE_BACKEND local", "set_env SECRET_SOURCE env",
                 'set_env DATABASE_URL "sqlite:///$DATA/nexora.db"', 'set_env LOG_FILE "$LOGS/app.log"',
                 "set_env SESSION_COOKIE_SECURE true", "set_env GCP_LOCATION global",
                 "set_env GEMINI_MODEL gemini-3.5-flash", "set_env AI_DAILY_LIMIT 20",
                 "flask db upgrade", "seed.py"]:
        assert line in setup


def test_scripts_have_linux_line_endings():
    for path in DEPLOY.iterdir():
        assert b"\r\n" not in path.read_bytes(), path.name
