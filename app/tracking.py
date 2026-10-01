"""Tracking page and the JSON behind its charts.

Everything is worked out here in Python; tracking.js only draws it.
Hidden cards are left out of every number.
"""

from collections import Counter
from datetime import timedelta

from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required

from app import engine
from app.decks import count_cards, count_hidden_cards, shelf_counts
from app.models import (
    Card, Deck, Progress, Review, UserDeck, db, local_date_of, local_day_start_utc, today_local,
)
from app.planner import seen_cards_for, streak_to_show

bp = Blueprint("tracking", __name__, url_prefix="/tracking")

CALENDAR_WEEKS = 8
WEAKEST_SHOWN = 5


def topic_mastery_rows(user_id):
    """Mastery % per topic (deck + topic name): cards on shelf 5 / all cards in the topic."""
    cards = db.session.query(Card.id, Card.deck_id, Card.topic, Deck.title).join(
        Deck, Deck.id == Card.deck_id
    ).join(UserDeck, (UserDeck.deck_id == Card.deck_id) & (UserDeck.user_id == user_id)).all()
    progress = {
        card_id: (shelf, hidden)
        for card_id, shelf, hidden in db.session.query(Progress.card_id, Progress.shelf, Progress.hidden)
        .filter(Progress.user_id == user_id)
    }

    topics = {}
    for card_id, deck_id, topic, deck_title in cards:
        shelf, hidden = progress.get(card_id, (None, False))
        if hidden:
            continue
        entry = topics.setdefault((deck_id, topic), {"deck": deck_title, "topic": topic, "learned": 0, "total": 0})
        entry["total"] += 1
        if shelf == engine.MAX_SHELF:
            entry["learned"] += 1

    rows = list(topics.values())
    for row in rows:
        row["mastery"] = round(engine.topic_mastery(row["learned"], row["total"]) * 100)
    return sorted(rows, key=lambda row: (row["deck"].lower(), row["topic"].lower()))


def weakest_topics(user_id):
    """Topics with the most cards still on shelf 1-2, using the same numbers as the Fixer."""
    seen = seen_cards_for(user_id)
    stats = engine.topic_stats(seen)
    target = engine.find_weak_topic(seen)
    deck_titles = dict(db.session.query(Deck.id, Deck.title).filter(Deck.id.in_([d for d, _ in stats])))

    rows = []
    for (deck_id, topic), entry in stats.items():
        open_misconceptions = entry["open_misconceptions"]
        if entry["seen"] < engine.MIN_CARDS_SEEN and open_misconceptions < engine.MISCONCEPTION_CARDS_TRIGGER:
            continue
        weak_score = entry["weak"] / entry["seen"]
        if weak_score == 0 and open_misconceptions == 0:
            continue
        rows.append({
            "deck": deck_titles.get(deck_id, ""),
            "topic": topic,
            "weak_percent": round(weak_score * 100),
            "seen": entry["seen"],
            "open_misconceptions": open_misconceptions,
            "is_target": (deck_id, topic) == target,
        })
    rows.sort(key=lambda row: (not row["is_target"], -row["weak_percent"], -row["open_misconceptions"]))
    return rows[:WEAKEST_SHOWN]


def deck_progress(user_id):
    """Cards per shelf for each deck in the student's list, plus cards not studied yet."""
    decks = db.session.query(Deck).join(
        UserDeck, (UserDeck.deck_id == Deck.id) & (UserDeck.user_id == user_id)
    ).order_by(Deck.title).all()
    totals = count_cards([deck.id for deck in decks])
    hidden = count_hidden_cards(user_id)

    rows = []
    for deck in decks:
        visible = totals.get(deck.id, 0) - hidden.get(deck.id, 0)
        if visible == 0:
            continue
        shelves = shelf_counts(user_id, deck.id)
        rows.append({"deck": deck.title, "shelves": shelves, "new": visible - sum(shelves), "total": visible})
    return rows


def misconception_counts(user_id):
    """Cards the student was sure about but got wrong, and how many of those are now fixed."""
    found = db.session.query(Progress.misconception_count, Progress.shelf).filter(
        Progress.user_id == user_id, Progress.hidden.is_(False), Progress.misconception_count > 0,
    ).all()
    still_open = sum(1 for count, shelf in found if engine.is_open_misconception(count, shelf))
    return {"found": len(found), "resolved": len(found) - still_open, "open": still_open}


def streak_calendar(user_id, today):
    """Weeks (Monday first) of days, each with how many cards were answered that day."""
    this_monday = today - timedelta(days=today.weekday())
    first_day = this_monday - timedelta(weeks=CALENDAR_WEEKS - 1)
    times = db.session.query(Review.reviewed_at).filter(
        Review.user_id == user_id, Review.reviewed_at >= local_day_start_utc(first_day),
    )
    per_day = Counter(local_date_of(reviewed_at) for (reviewed_at,) in times)

    weeks = []
    for week in range(CALENDAR_WEEKS):
        days = []
        for weekday in range(7):
            day = first_day + timedelta(weeks=week, days=weekday)
            days.append({"date": day.isoformat(), "count": per_day.get(day, 0), "future": day > today})
        weeks.append(days)
    return weeks


def tracking_data(user_id):
    return {
        "mastery": topic_mastery_rows(user_id),
        "decks": deck_progress(user_id),
        "misconceptions": misconception_counts(user_id),
    }


@bp.get("/")
@login_required
def tracking_page():
    today = today_local()
    data = tracking_data(current_user.id)
    return render_template(
        "tracking.html",
        data=data,
        weakest=weakest_topics(current_user.id),
        calendar=streak_calendar(current_user.id, today),
        streak=streak_to_show(current_user, today),
        resolved_shelf=engine.RESOLVED_SHELF,
        has_reviews=db.session.query(Review.id).filter_by(user_id=current_user.id).count() > 0,
    )


@bp.get("/data")
@login_required
def tracking_json():
    """Numbers for the charts on the tracking page."""
    return jsonify(tracking_data(current_user.id))
