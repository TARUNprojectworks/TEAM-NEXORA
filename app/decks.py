"""Ready decks, my decks, deck page and card create/edit/delete.

Owner: auth + decks. Built in phase 2. Every route must check that the
deck belongs to current_user; ready decks are read-only for students.
"""

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("decks", __name__, url_prefix="/decks")


@bp.get("/ready")
@login_required
def ready_decks():
    return render_template("placeholder.html", title="Ready decks")


@bp.get("/mine")
@login_required
def my_decks():
    return render_template("placeholder.html", title="My decks")


@bp.get("/new")
@login_required
def create_my_own():
    return render_template("placeholder.html", title="Create my own")
