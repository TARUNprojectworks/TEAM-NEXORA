"""Landing page, sign up, log in and log out."""

import logging
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, StringField
from wtforms.validators import DataRequired, EqualTo, Length, Regexp

from app.models import User, create_weak_spot_deck, db

bp = Blueprint("auth", __name__)
log = logging.getLogger("nexora.auth")

# Simple shape check only; we don't send emails, so we don't need more.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class SignupForm(FlaskForm):
    name = StringField("Your name", validators=[DataRequired(), Length(max=80)])
    email = EmailField("Email", validators=[
        DataRequired(), Length(max=255), Regexp(EMAIL_PATTERN, message="Enter an email like name@example.com."),
    ])
    password = PasswordField("Password", validators=[
        DataRequired(), Length(min=8, max=128, message="Use at least 8 characters."),
    ])
    confirm = PasswordField("Type the password again", validators=[
        DataRequired(), EqualTo("password", message="The two passwords don't match."),
    ])


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    remember = BooleanField("Keep me logged in")


def normalise_email(email):
    return email.strip().lower()


def safe_next_url(target):
    """Only follow ?next= links that stay on our own site."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/"):
        return None
    return target


@bp.get("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("planner.home"))
    return render_template("landing.html")


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("planner.home"))
    form = SignupForm()
    if form.validate_on_submit():
        email = normalise_email(form.email.data)
        if db.session.query(User.id).filter_by(email=email).first():
            form.email.errors.append("That email already has an account. Log in instead.")
        else:
            user = User(name=form.name.data.strip(), email=email)
            user.set_password(form.password.data)
            db.session.add(user)
            create_weak_spot_deck(user)
            db.session.commit()
            login_user(user)
            log.info("signup", extra={"fields": {"event": "signup"}})
            return redirect(url_for("planner.home"))
    return render_template("auth/signup.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("planner.home"))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.query(User).filter_by(email=normalise_email(form.email.data)).first()
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data)
            return redirect(safe_next_url(request.args.get("next")) or url_for("planner.home"))
        # Same message either way, so nobody can test which emails exist.
        flash("Wrong email or password. Try again.", "error")
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You're logged out.", "info")
    return redirect(url_for("auth.landing"))
