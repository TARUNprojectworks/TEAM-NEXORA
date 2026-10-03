"""The side menu (☰): account details, card theme, feedback. Also the logo preview page.

Each form posts here and returns to the page the student was on (`next`).
The card theme can also be saved with a fetch POST (live preview in the menu
and in the card maker).
"""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth import safe_next_url
from app.models import CARD_THEMES, Feedback, db, today_local
from app.planner import streak_to_show

bp = Blueprint("account", __name__)
log = logging.getLogger("nexora.account")

THEME_NAMES = {"index": "Index card", "clean": "Clean", "night": "Night"}
MIN_PASSWORD = 8
MAX_FEEDBACK = 2000


@bp.app_context_processor
def side_menu_values():
    """Values the side menu needs on every page."""
    if not current_user.is_authenticated:
        return {}
    return {
        "menu_streak": streak_to_show(current_user, today_local()),
        "theme_names": THEME_NAMES,
    }


def back_to_page():
    return redirect(safe_next_url(request.form.get("next")) or url_for("planner.home"))


@bp.post("/account/name")
@login_required
def change_name():
    name = request.form.get("name", "").strip()
    if not name or len(name) > 80:
        flash("Enter a name of up to 80 characters.", "error")
    else:
        current_user.name = name
        db.session.commit()
        flash("Name saved.", "info")
    return back_to_page()


@bp.post("/account/password")
@login_required
def change_password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    if not current_user.check_password(current):
        flash("Your current password isn't right. Try again.", "error")
    elif len(new) < MIN_PASSWORD or len(new) > 128:
        flash("Use at least 8 characters for the new password.", "error")
    elif new != request.form.get("confirm_password", ""):
        flash("The two new passwords don't match.", "error")
    else:
        current_user.set_password(new)
        db.session.commit()
        log.info("password_changed", extra={"fields": {"event": "password_changed"}})
        flash("Password changed.", "info")
    return back_to_page()


@bp.post("/account/theme")
@login_required
def change_theme():
    """Save the card theme. JSON for the live picker, a normal form post without JavaScript."""
    data = request.get_json(silent=True) or request.form
    theme = data.get("card_theme")
    if theme not in CARD_THEMES:
        if request.is_json:
            return jsonify(error="Pick one of the three themes."), 400
        flash("Pick one of the three themes.", "error")
        return back_to_page()
    current_user.card_theme = theme
    db.session.commit()
    if request.is_json:
        return jsonify(card_theme=theme)
    flash(f"Card theme: {THEME_NAMES[theme]}.", "info")
    return back_to_page()


@bp.post("/account/feedback")
@login_required
def send_feedback():
    text = request.form.get("feedback", "").strip()
    if not text:
        flash("Write something first, then send.", "error")
    else:
        db.session.add(Feedback(user_id=current_user.id, text=text[:MAX_FEEDBACK]))
        db.session.commit()
        flash("Thanks! Your feedback was sent.", "info")
    return back_to_page()


@bp.get("/logo-preview")
@login_required
def logo_preview():
    return render_template("logo_preview.html")
