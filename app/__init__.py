"""App factory: builds the Flask app, wires extensions, logging and blueprints."""

import hashlib
import json
import logging
import os
import sys
import time

from flask import Flask, g, jsonify, render_template, request
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config, load_secret_key_from_secret_manager
from app.models import User, db

csrf = CSRFProtect()
login_manager = LoginManager()
log = logging.getLogger("nexora")


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config["UPLOAD_FOLDER"]:
        app.config["UPLOAD_FOLDER"] = os.path.join(app.instance_path, "uploads")

    setup_logging(app)
    load_secret_key(app)

    # Caddy terminates HTTPS and forwards to us; trust its X-Forwarded-* headers
    # so url_for builds https:// links and logs show the real client.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Log in to see that page."

    register_blueprints(app)
    register_request_logging(app)
    register_error_pages(app)
    register_health_check(app)
    create_tables(app)
    check_database_matches_models(app)
    return app


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def load_secret_key(app):
    if app.config["SECRET_SOURCE"] == "secret_manager":
        app.config["SECRET_KEY"] = load_secret_key_from_secret_manager(
            app.config["GCP_PROJECT"], app.config["SECRET_NAME"]
        )
    elif app.config["SECRET_KEY"] == "dev-only-change-me" and app.config["SESSION_COOKIE_SECURE"]:
        log.warning("Running with HTTPS cookies but the default SECRET_KEY. Set a real one.")


def register_blueprints(app):
    from app.auth import bp as auth_bp
    from app.cardmaker import bp as cardmaker_bp
    from app.decks import bp as decks_bp
    from app.fixer import bp as fixer_bp
    from app.planner import bp as planner_bp
    from app.study import bp as study_bp
    from app.tracking import bp as tracking_bp

    for blueprint in (auth_bp, decks_bp, cardmaker_bp, study_bp, planner_bp, fixer_bp, tracking_bp):
        app.register_blueprint(blueprint)


def create_tables(app):
    # No migrations in this project: if models.py changes, delete
    # instance/nexora.db and run seed.py again.
    with app.app_context():
        try:
            db.create_all()
        except OperationalError:
            # Two Gunicorn workers can start at the same moment; if the other
            # one already created the tables, there is nothing left to do.
            db.session.rollback()


class OldDatabaseError(RuntimeError):
    """The database file was made by older code and is missing columns."""


def missing_columns(app):
    """Columns models.py expects that the database doesn't have, like 'cards.owner_id'."""
    with app.app_context():
        inspector = inspect(db.engine)
        missing = []
        for table in db.metadata.sorted_tables:
            have = {column["name"] for column in inspector.get_columns(table.name)}
            missing += [f"{table.name}.{column.name}" for column in table.columns if column.name not in have]
        return missing


def check_database_matches_models(app):
    # create_all() makes missing tables but never adds columns to old ones.
    # Without this check an old database fails later with a confusing
    # "no such column" error on whatever page runs first.
    missing = missing_columns(app)
    if missing:
        raise OldDatabaseError(
            f"The database is older than the code (missing: {', '.join(missing)}). "
            "On a laptop: delete instance/nexora.db, run python seed.py, then start the app again."
        )


def register_health_check(app):
    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            log.exception("Health check failed")
            return jsonify(status="error"), 503
        return jsonify(status="ok")


def register_error_pages(app):
    @app.errorhandler(404)
    def not_found(error):
        message = "We couldn't find that page. Check the link or go back to Home."
        return render_template("error.html", message=message), 404

    @app.errorhandler(403)
    def forbidden(error):
        message = "That isn't yours to open. Go back to your decks."
        return render_template("error.html", message=message), 403

    @app.errorhandler(413)
    def too_large(error):
        message = ("Those files are too big together. Upload up to 5 photos (5 MB each), "
                   "one PDF (10 MB) and one text file at a time.")
        return render_template("error.html", message=message), 413

    @app.errorhandler(CSRFError)
    def csrf_failed(error):
        message = "This form expired. Go back, reload the page and try again."
        return render_template("error.html", message=message), 400

    @app.errorhandler(500)
    def server_error(error):
        message = "Something broke on our side. Try again in a minute."
        return render_template("error.html", message=message), 500


# ---------- Logging ----------

class JsonFormatter(logging.Formatter):
    """One JSON object per line, so Cloud Logging can read the fields."""

    def format(self, record):
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def setup_logging(app):
    # Other modules log with logging.getLogger("nexora.<area>") and pass
    # extra={"fields": {...}}; their lines end up here.
    formatter = JsonFormatter()
    formatter.converter = time.gmtime  # log times in UTC
    handlers = [logging.StreamHandler(sys.stdout)]
    if app.config["LOG_FILE"]:
        handlers.append(logging.FileHandler(app.config["LOG_FILE"]))

    log.handlers.clear()
    for handler in handlers:
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(app.config["LOG_LEVEL"])
    log.propagate = False


def user_hash():
    """A short hash instead of the user id, so logs don't identify students."""
    if not current_user.is_authenticated:
        return None
    return hashlib.sha256(f"user-{current_user.id}".encode()).hexdigest()[:12]


def register_request_logging(app):
    @app.before_request
    def start_timer():
        g.request_started = time.perf_counter()

    @app.after_request
    def log_request(response):
        if request.path.startswith("/static/"):
            return response
        latency_ms = round((time.perf_counter() - g.get("request_started", time.perf_counter())) * 1000)
        log.info(
            "request",
            extra={"fields": {
                "route": request.url_rule.rule if request.url_rule else request.path,
                "method": request.method,
                "status": response.status_code,
                "latency_ms": latency_ms,
                "user": user_hash(),
            }},
        )
        return response
