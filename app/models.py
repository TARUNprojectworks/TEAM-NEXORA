"""All 6 tables. Shared file: only the core owner changes it.

Times are stored as naive UTC datetimes (SQLite has no time zones).
Dates such as next_due, exam_date and last_study_date are Asia/Kolkata dates.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash

from app.config import Config

db = SQLAlchemy()

FOLDERS = ("semester", "placement", "competitive", "personal")
MODES = ("exam", "normal")
IMPORTANCE_LEVELS = ("high", "medium", "low")
CARD_SOURCES = ("manual", "ai", "fixer")
STUDY_STYLES = ("smart", "free")

WEAK_SPOT_DECK_TITLE = "Weak spot practice"
DEFAULT_TOPIC = "General"


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today_local():
    """Today's date in the app's time zone (Asia/Kolkata)."""
    return datetime.now(ZoneInfo(Config.APP_TIMEZONE)).date()


@event.listens_for(Engine, "connect")
def set_sqlite_pragmas(dbapi_connection, connection_record):
    # WAL lets readers and one writer work at the same time (several Gunicorn
    # workers). SQLite ignores foreign keys unless we switch them on.
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    xp = db.Column(db.Integer, nullable=False, default=0)
    streak = db.Column(db.Integer, nullable=False, default=0)
    last_study_date = db.Column(db.Date)
    ai_calls_today = db.Column(db.Integer, nullable=False, default=0)
    ai_calls_date = db.Column(db.Date)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    owned_decks = db.relationship("Deck", back_populates="owner", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Deck(db.Model):
    __tablename__ = "decks"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))  # null = ready deck
    title = db.Column(db.String(120), nullable=False)
    folder = db.Column(db.String(20), nullable=False, default="personal")
    is_ready = db.Column(db.Boolean, nullable=False, default=False)
    # Marks each student's auto-created "Weak spot practice" deck,
    # so we never have to find it by title.
    is_weak_spot = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    owner = db.relationship("User", back_populates="owned_decks")
    cards = db.relationship("Card", back_populates="deck", cascade="all, delete-orphan")
    user_decks = db.relationship("UserDeck", back_populates="deck", cascade="all, delete-orphan")


class UserDeck(db.Model):
    """A deck in a student's list, with that student's mode for it."""

    __tablename__ = "user_decks"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    deck_id = db.Column(db.Integer, db.ForeignKey("decks.id", ondelete="CASCADE"), primary_key=True)
    mode = db.Column(db.String(10), nullable=False, default="normal")
    exam_date = db.Column(db.Date)
    added_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    deck = db.relationship("Deck", back_populates="user_decks")


class Card(db.Model):
    __tablename__ = "cards"

    id = db.Column(db.Integer, primary_key=True)
    deck_id = db.Column(db.Integer, db.ForeignKey("decks.id", ondelete="CASCADE"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    topic = db.Column(db.String(80), nullable=False, default=DEFAULT_TOPIC)
    importance = db.Column(db.String(10), nullable=False, default="medium")
    source_line = db.Column(db.Text)
    source = db.Column(db.String(10), nullable=False, default="manual")
    explanation = db.Column(db.Text)  # cached output of Explain This Card
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    deck = db.relationship("Deck", back_populates="cards")
    progress = db.relationship("Progress", cascade="all, delete-orphan", passive_deletes=True)
    reviews = db.relationship("Review", cascade="all, delete-orphan", passive_deletes=True)


class Progress(db.Model):
    """Where one card sits for one student. Created the first time they answer it,
    or when they hide it."""

    __tablename__ = "progress"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    shelf = db.Column(db.Integer, nullable=False, default=1)
    next_due = db.Column(db.Date, nullable=False, default=today_local)
    wrong_count = db.Column(db.Integer, nullable=False, default=0)
    misconception_count = db.Column(db.Integer, nullable=False, default=0)
    last_seen = db.Column(db.DateTime)
    # A student can hide a card from a shared ready deck (they can't delete it).
    # Hidden cards are left out of study, the planner and mastery.
    hidden = db.Column(db.Boolean, nullable=False, default=False)


class Review(db.Model):
    __tablename__ = "reviews"
    __table_args__ = (db.Index("ix_reviews_user_time", "user_id", "reviewed_at"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    knew_it = db.Column(db.Boolean, nullable=False)
    confident = db.Column(db.Boolean, nullable=False)
    style = db.Column(db.String(10), nullable=False, default="smart")  # smart | free
    reviewed_at = db.Column(db.DateTime, nullable=False, default=utc_now)


def hidden_card_ids(user_id, deck_id):
    """Ids of the cards in this deck that the student has hidden."""
    rows = db.session.query(Progress.card_id).join(Card, Card.id == Progress.card_id).filter(
        Progress.user_id == user_id, Progress.hidden.is_(True), Card.deck_id == deck_id,
    )
    return {card_id for (card_id,) in rows}


def create_weak_spot_deck(user):
    """Give a new student their personal deck for Fixer practice cards."""
    deck = Deck(owner=user, title=WEAK_SPOT_DECK_TITLE, folder="personal", is_weak_spot=True)
    db.session.add(deck)
    db.session.flush()  # gives the user and deck their ids
    db.session.add(UserDeck(user_id=user.id, deck_id=deck.id, mode="normal"))
    return deck
