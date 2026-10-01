"""Home page and the daily plan.

Owner: planner + tracking. Phase 4 fills in build_daily_plan() and the
plan / weak spot / "Welcome back" boxes on Home.
"""

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.engine import NEW_CARDS_PER_DAY  # noqa: F401  (one limit for Smart Study and the plan)

bp = Blueprint("planner", __name__)

# Session lengths the student can pick. None = no limit.
SESSION_MINUTES = (15, 25, 40, None)
DEFAULT_SESSION_MINUTES = 25
SECONDS_PER_CARD = 30


@bp.get("/home")
@login_required
def home():
    return render_template("home.html", user=current_user)
