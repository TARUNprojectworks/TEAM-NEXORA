"""Home page and the daily plan.

The plan is rule-based (no AI). Once a day we pick the cards for today:
  1. due cards from the current weak topic (including Fixer practice cards)
  2. other due cards, best first by the engine's priority score
  3. new cards, at most 10 a day, Exam-mode decks first
We estimate 30 seconds a card, so a 25-minute plan holds 50 cards. If the
due cards don't fit, Exam-mode decks keep their place and the rest move to
tomorrow. The plan is kept in the session, as card ids only.
"""

import math
from collections import namedtuple
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import engine
from app.decks import expire_past_exams
from app.models import (
    Card, Deck, Progress, UserDeck, card_ids_reviewed_on, db, hidden_card_ids,
    new_cards_started_today, today_local, visible_to,
)

bp = Blueprint("planner", __name__)

# Plan sizes in cards: three quick choices, or Custom (5 to 150).
PLAN_SIZES = (10, 20, 30)
DEFAULT_SIZE = 20
CUSTOM_MIN = 5
# The plan lives in the session cookie (4 KB), so 150 cards is the most a plan can hold.
NO_LIMIT_CARD_CAP = 150
CUSTOM_MAX = NO_LIMIT_CARD_CAP
SIZE_COOKIE = "plan_size"  # remembers the last choice, even after logging out
SIZE_COOKIE_DAYS = 365
SECONDS_PER_CARD = 30  # only for the "about N min" estimate on Home
PLAN_XP = 10
LONG_BREAK_DAYS = 3  # away this many days or more: show "Welcome back"

PlanChoice = namedtuple("PlanChoice", "cards moved_to_tomorrow")


# ---------- Choosing the cards (plain Python, no database) ----------

def card_limit(size):
    return NO_LIMIT_CARD_CAP if size is None else size


def is_weak_topic_card(candidate, weak_topic, weak_spot_deck_id):
    if weak_topic is None:
        return False
    deck_id, topic = weak_topic
    card = candidate.card
    # Fixer practice cards live in the Weak spot practice deck under the same topic name.
    return card.topic == topic and card.deck_id in (deck_id, weak_spot_deck_id)


def exam_decks_first(candidates):
    # sorted() keeps the existing order inside each group, so priority order holds.
    return sorted(candidates, key=lambda candidate: candidate.mode != "exam")


def choose_plan_cards(due, new, size, new_allowance, today, weak_topic=None, weak_spot_deck_id=None):
    """Pick today's cards. `due` are seen cards, `new` never-seen ones (engine Candidates)."""
    limit = card_limit(size)

    ranked_due = engine.rank_candidates(due, today)  # leaves out cards that aren't due
    weak = [c for c in ranked_due if is_weak_topic_card(c, weak_topic, weak_spot_deck_id)]
    others = [c for c in ranked_due if not is_weak_topic_card(c, weak_topic, weak_spot_deck_id)]
    if len(weak) + len(others) > limit:
        # Too many due cards: Exam-mode decks keep their place, the rest wait.
        others = exam_decks_first(others)

    due_in_order = weak + others
    chosen_due = due_in_order[:limit]
    moved_to_tomorrow = len(due_in_order) - len(chosen_due)

    room_left = limit - len(chosen_due)
    ranked_new = exam_decks_first(engine.rank_candidates(new, today))
    chosen_new = ranked_new[:min(room_left, new_allowance)]
    return PlanChoice(chosen_due + chosen_new, moved_to_tomorrow)


# ---------- Reading the student's cards ----------

def study_candidates(user_id, today):
    """(seen, new) engine Candidates for every card in the student's decks, minus hidden ones."""
    rows = db.session.query(Card, UserDeck).join(
        UserDeck, (UserDeck.deck_id == Card.deck_id) & (UserDeck.user_id == user_id)
    ).filter(visible_to(user_id)).all()
    progress_by_card = {p.card_id: p for p in db.session.query(Progress).filter_by(user_id=user_id)}

    seen, new = [], []
    for card, user_deck in rows:
        progress = progress_by_card.get(card.id)
        if progress is not None and progress.hidden:
            continue
        exam_date = user_deck.exam_date if user_deck.mode == "exam" else None
        candidate = engine.Candidate(card, progress, user_deck.mode, exam_date)
        (new if progress is None else seen).append(candidate)
    return seen, new


def seen_cards_for(user_id, deck_ids=None):
    """(deck_id, topic, shelf, misconception_count) for every card the student has seen.

    deck_ids: only these decks (the Fixer passes the decks of the session just finished).
    """
    rows = db.session.query(Card.deck_id, Card.topic, Progress.shelf, Progress.misconception_count).join(
        Progress, Progress.card_id == Card.id
    ).join(
        UserDeck, (UserDeck.deck_id == Card.deck_id) & (UserDeck.user_id == user_id)
    ).filter(Progress.user_id == user_id, Progress.hidden.is_(False), Progress.last_seen.isnot(None))
    if deck_ids is not None:
        rows = rows.filter(Card.deck_id.in_(deck_ids))
    return rows.all()


def weak_spot_deck_id(user_id):
    return db.session.query(Deck.id).filter_by(owner_id=user_id, is_weak_spot=True).scalar()


def due_card_count(user_id, today):
    seen, _ = study_candidates(user_id, today)
    return sum(1 for c in seen if engine.is_due(c.progress, today))


# ---------- The plan in the session ----------

def build_plan(user_id, size, today):
    expire_past_exams(user_id, today)  # a passed exam must not count as Exam mode
    seen, new = study_candidates(user_id, today)
    new_allowance = max(engine.NEW_CARDS_PER_DAY - new_cards_started_today(user_id, today), 0)
    choice = choose_plan_cards(
        seen, new, size, new_allowance, today,
        weak_topic=engine.find_weak_topic(seen_cards_for(user_id)),
        weak_spot_deck_id=weak_spot_deck_id(user_id),
    )
    return {
        "date": today.isoformat(),
        "size": size,
        "card_ids": [c.card.id for c in choice.cards],
        "moved": choice.moved_to_tomorrow,
    }


def valid_size(value):
    """The plan size as a whole number from 5 to 150, or None if it isn't one."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if CUSTOM_MIN <= size <= CUSTOM_MAX else None


def chosen_size():
    """The student's last choice (kept in a cookie), or 20 cards."""
    return valid_size(request.cookies.get(SIZE_COOKIE)) or DEFAULT_SIZE


def todays_plan(user_id, today):
    """The plan for today, built the first time it is asked for each day.

    An empty plan is rebuilt each time, so a student who adds their first deck
    (or new cards) later in the day gets a plan straight away.
    """
    plan = session.get("plan")
    # A plan without "size" was built by an older version (minutes): build it again.
    if not plan or plan.get("date") != today.isoformat() or not plan["card_ids"] or "size" not in plan:
        plan = build_plan(user_id, chosen_size(), today)
        session["plan"] = plan
    return plan


def plan_cards_left(user_id, plan, today):
    """Plan card ids, in plan order, not yet answered today (and not deleted or hidden since)."""
    if not plan or plan.get("date") != today.isoformat():
        return []
    reviewed = card_ids_reviewed_on(user_id, today)
    hidden = hidden_card_ids(user_id)
    existing = {card_id for (card_id,) in db.session.query(Card.id).filter(Card.id.in_(plan["card_ids"]))}
    return [
        card_id for card_id in plan["card_ids"]
        if card_id in existing and card_id not in reviewed and card_id not in hidden
    ]


def award_plan_xp_if_finished(user, today):
    """+10 XP once a day when today's reviews cover every card in the plan."""
    plan = session.get("plan")
    if not plan or plan.get("date") != today.isoformat() or not plan["card_ids"]:
        return False
    if session.get("plan_xp_date") == today.isoformat():
        return False  # already given today, even if the plan was rebuilt
    if plan_cards_left(user.id, plan, today):
        return False
    user.xp += PLAN_XP
    db.session.commit()
    session["plan_xp_date"] = today.isoformat()
    return True


# ---------- What Home shows ----------

def minutes_for(card_count):
    return math.ceil(card_count * SECONDS_PER_CARD / 60)


def plan_groups(plan):
    """The plan as short lines: one per deck and topic, with card count and minutes."""
    cards = {card.id: card for card in db.session.query(Card).filter(Card.id.in_(plan["card_ids"]))}
    groups = {}
    for card_id in plan["card_ids"]:
        card = cards.get(card_id)
        if card is None:
            continue
        group = groups.setdefault((card.deck_id, card.topic), {"deck": card.deck.title, "topic": card.topic, "cards": 0})
        group["cards"] += 1
    for group in groups.values():
        group["minutes"] = minutes_for(group["cards"])
    return list(groups.values())


def todays_weak_spot(today):
    """The Weak Spot Fixer's box from earlier today (kept in the session), or None."""
    box = session.get("weak_spot")
    return box if box and box.get("date") == today.isoformat() else None


def topic_is_open(user_id, deck_id, topic):
    """Is this topic still weak right now (by the same rules that picked it)?"""
    entry = engine.topic_stats(seen_cards_for(user_id, [deck_id])).get((deck_id, topic))
    return entry is not None and engine.is_weak(entry)


def open_weak_spot(user_id, today):
    """Today's weak spot box for Home, but only while its topic is still weak."""
    box = todays_weak_spot(today)
    if box and topic_is_open(user_id, box["deck_id"], box["topic"]):
        return box
    return None


def streak_to_show(user, today):
    """The saved streak is only updated on study days, so a missed day must show 0."""
    if user.last_study_date is None or user.last_study_date < today - timedelta(days=1):
        return 0
    return user.streak


def welcome_back_count(user, today):
    """Cards due after a long break, or None if the student hasn't been away."""
    if user.last_study_date is None or (today - user.last_study_date).days < LONG_BREAK_DAYS:
        return None
    return due_card_count(user.id, today) or None


# ---------- Routes ----------

@bp.get("/home")
@login_required
def home():
    today = today_local()
    plan = todays_plan(current_user.id, today)
    award_plan_xp_if_finished(current_user, today)
    cards_left = plan_cards_left(current_user.id, plan, today)

    # Top 3 weak topics for the home page preview
    from app.tracking import weak_spot_rows
    weak_rows = weak_spot_rows(current_user.id)
    weak_preview = weak_rows[:3]

    return render_template(
        "home.html",
        user=current_user,
        plan=plan,
        groups=plan_groups(plan),
        total_minutes=minutes_for(len(plan["card_ids"])),
        cards_done=len(plan["card_ids"]) - len(cards_left),
        cards_left=len(cards_left),
        plan_finished=session.get("plan_xp_date") == today.isoformat(),
        streak=streak_to_show(current_user, today),
        welcome_back=welcome_back_count(current_user, today),
        sizes=PLAN_SIZES,
        custom_min=CUSTOM_MIN,
        custom_max=CUSTOM_MAX,
        weak_spot=open_weak_spot(current_user.id, today),
        weak_preview=weak_preview,
        weak_open_count=sum(row["status"] == "Open" for row in weak_rows),
    )



@bp.post("/plan/refresh")
@login_required
def refresh_plan():
    """Build today's plan again (e.g. after adding a deck). Plan XP is still once a day."""
    session["plan"] = build_plan(current_user.id, chosen_size(), today_local())
    return redirect(url_for("planner.home"))


@bp.post("/plan/length")
@login_required
def change_plan_length():
    """Pick 10 / 20 / 30 cards or a custom number (5 to 150). Rebuilds today's plan and remembers the choice."""
    choice = request.form.get("size")
    size = valid_size(request.form.get("custom_size") if choice == "custom" else choice)
    response = redirect(url_for("planner.home"))
    if size is None:
        flash(f"Pick a number of cards from {CUSTOM_MIN} to {CUSTOM_MAX}.", "error")
        return response
    session["plan"] = build_plan(current_user.id, size, today_local())
    response.set_cookie(SIZE_COOKIE, str(size), max_age=SIZE_COOKIE_DAYS * 24 * 3600,
                        httponly=True, samesite="Lax", secure=current_app.config.get("SESSION_COOKIE_SECURE", False))
    return response
