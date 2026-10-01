"""Tracking page and the JSON behind its charts.

Owner: planner + tracking. Built in phase 4.
"""

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("tracking", __name__, url_prefix="/tracking")


@bp.get("/")
@login_required
def tracking_page():
    return render_template("placeholder.html", title="Tracking")
