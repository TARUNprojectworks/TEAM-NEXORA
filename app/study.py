"""Smart Study, Free Practice, the answer endpoint and the session summary.

How a session works:
- /study/<deck_id>?style=smart|free starts a session for one deck.
- /study/card shows the current card. /study/answer saves the answer and
  redirects back to /study/card for the next one (a plain form post, so
  there is no client-side state to get out of sync).
- The session itself (which cards were answered, which are waiting to come
  back, running totals) lives in the Flask session cookie as card ids and
  numbers only. Shelves and reviews live in the database.

Smart Study asks engine.py which card to show. Free Practice shows every
card in the deck in a shuffled order and never changes shelves.
"""

import logging
import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import engine
from app.config import Config
from app.decks import expire_past_exams, get_user_deck, mode_badge, viewable_deck_or_404
from app.models import Card, Deck, Progress, Review, db, hidden_card_ids, today_local, utc_now

bp = Blueprint("study", __name__, url_prefix="/study")
log = logging.getLogger("nexora.study")

STYLES = ("smart", "free")
XP_KNOW_IT = 2
XP_REVIEW_AGAIN = 1


# ---------- Session state (kept in the cookie: ids and numbers only) ----------

def start_session(deck_id, style):
    session["study"] = {
        "deck_id": deck_id,
        "style": style,
        "seed": random.randrange(1_000_000),  # Free Practice order; a new seed = a new shuffle
        "answered": [],    # card ids answered at least once this session
        "returns": [],     # [card_id, cards_still_to_wait] for "Review again" cards
        "current": None,   # the card on screen, so a double submit can't answer twice
        "stats": {"studied": 0, "moved_up": 0, "xp": 0, "misconceptions": 0},
    }


def get_session():
    return session.get("study")


def save_session(state):
    session["study"] = state
    session.modified = True


def load_deck_for(state):
    """The session's deck and the student's settings for it, or (None, None) if gone."""
    deck = db.session.get(Deck, state["deck_id"])
    if deck is None or not (deck.is_ready or deck.owner_id == current_user.id):
        return None, None
    user_deck = get_user_deck(deck.id)
    if user_deck is None:
        return None, None
    return deck, user_deck


# ---------- Choosing cards ----------

def local_day_start_utc(today):
    """Midnight at the start of `today` in India, as a UTC time (how we store times)."""
    start = datetime.combine(today, time.min, tzinfo=ZoneInfo(Config.APP_TIMEZONE))
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def new_cards_started_today(user_id, today):
    """Cards whose first ever Smart Study answer was today. Shared limit with the planner."""
    first_answers = db.session.query(Review.card_id).filter(
        Review.user_id == user_id, Review.style == "smart",
    ).group_by(Review.card_id).having(func.min(Review.reviewed_at) >= local_day_start_utc(today))
    return first_answers.count()


def unanswered_cards(state):
    """Cards not answered yet this session, leaving out cards the student hid."""
    skip = set(state["answered"]) | hidden_card_ids(current_user.id, state["deck_id"])
    return db.session.query(Card).filter(
        Card.deck_id == state["deck_id"], Card.id.notin_(skip)
    ).all()


def drop_hidden_cards(state):
    """If a card was hidden mid-session (say, in another tab), stop showing it."""
    hidden = hidden_card_ids(current_user.id, state["deck_id"])
    state["returns"] = [entry for entry in state["returns"] if entry[0] not in hidden]
    if state["current"] in hidden:
        state["current"] = None


def smart_queue(state, user_deck, today):
    """Card ids for Smart Study, best first: due cards plus today's share of new cards."""
    cards = unanswered_cards(state)
    progress_by_card = {
        p.card_id: p for p in db.session.query(Progress).filter(
            Progress.user_id == current_user.id, Progress.card_id.in_([c.id for c in cards])
        )
    }
    exam_date = user_deck.exam_date if user_deck.mode == "exam" else None

    def candidate(card):
        return engine.Candidate(card, progress_by_card.get(card.id), user_deck.mode, exam_date)

    seen = [candidate(c) for c in cards if c.id in progress_by_card]
    new = [candidate(c) for c in cards if c.id not in progress_by_card]
    new_allowed = max(engine.NEW_CARDS_PER_DAY - new_cards_started_today(current_user.id, today), 0)
    todays_new = engine.rank_candidates(new, today)[:new_allowed]

    return [c.card.id for c in engine.rank_candidates(seen + todays_new, today)]


def free_queue(state):
    """Every unanswered card in the deck, shuffled by the session's seed."""
    card_ids = sorted(card.id for card in unanswered_cards(state))
    random.Random(state["seed"]).shuffle(card_ids)
    return card_ids


def queue_for(state, user_deck, today):
    if state["style"] == "smart":
        return smart_queue(state, user_deck, today)
    return free_queue(state)


def card_in_deck(card_id, deck):
    card = db.session.get(Card, card_id) if card_id else None
    return card if card is not None and card.deck_id == deck.id else None


def pick_next_card(state, queue):
    """A "Review again" card whose turn has come, otherwise the front of the queue."""
    returning = engine.card_coming_back(state["returns"], other_cards_left=len(queue))
    if returning is not None:
        return returning
    return queue[0] if queue else None


def session_progress(state, queue):
    done = len(state["answered"])
    left = len(queue) + len(state["returns"])
    total = done + left
    percent = round(done * 100 / total) if total else 100
    return {"done": done, "left": left, "percent": percent}


# ---------- XP and streak ----------

def xp_for(knew_it):
    return XP_KNOW_IT if knew_it else XP_REVIEW_AGAIN


def next_streak(streak, last_study_date, today):
    """The streak goes up once per day; missing a day starts it again at 1."""
    if last_study_date == today:
        return streak
    if last_study_date == today - timedelta(days=1):
        return streak + 1
    return 1


def reward_student(user, knew_it, today):
    xp = xp_for(knew_it)
    user.xp += xp
    user.streak = next_streak(user.streak, user.last_study_date, today)
    user.last_study_date = today
    return xp


# ---------- Saving an answer ----------

def update_progress(card, user_deck, knew_it, confident, today, now):
    """Smart Study only. Returns True if the card moved up a shelf."""
    progress = db.session.get(Progress, (current_user.id, card.id))
    if progress is None:
        # First time this student answers this card.
        progress = Progress(user_id=current_user.id, card_id=card.id, shelf=1, next_due=today,
                            wrong_count=0, misconception_count=0)
        db.session.add(progress)
    shelf_before = progress.shelf
    exam_date = user_deck.exam_date if user_deck.mode == "exam" else None
    engine.apply_answer(progress, knew_it, confident, today, user_deck.mode, exam_date, seen_at=now)
    return progress.shelf > shelf_before


def update_session_after_answer(state, card_id, knew_it, confident, moved_up, xp):
    if card_id not in state["answered"]:
        state["answered"].append(card_id)
    state["returns"] = engine.after_answer(state["returns"], card_id, knew_it)
    state["current"] = None
    stats = state["stats"]
    stats["studied"] += 1
    stats["xp"] += xp
    if moved_up:
        stats["moved_up"] += 1
    if not knew_it and confident:
        stats["misconceptions"] += 1


# ---------- Routes ----------

@bp.get("/<int:deck_id>")
@login_required
def start(deck_id):
    deck = viewable_deck_or_404(deck_id)
    if get_user_deck(deck.id) is None:
        flash("Add this deck to your decks before you study it.", "info")
        return redirect(url_for("decks.deck_page", deck_id=deck.id))

    style = request.args.get("style", "smart")
    if style not in STYLES:
        style = "smart"
    # A passed exam must not keep capping waits, so check before choosing cards.
    expire_past_exams(current_user.id, today_local())
    start_session(deck.id, style)
    return redirect(url_for("study.show_card"))


@bp.get("/card")
@login_required
def show_card():
    state = get_session()
    if not state:
        return redirect(url_for("decks.my_decks"))
    deck, user_deck = load_deck_for(state)
    if deck is None:
        session.pop("study", None)
        flash("That deck isn't in your list any more.", "info")
        return redirect(url_for("decks.my_decks"))

    today = today_local()
    drop_hidden_cards(state)
    queue = queue_for(state, user_deck, today)
    # Keep showing the same card on a page reload; otherwise take the next one.
    card = card_in_deck(state["current"], deck) or card_in_deck(pick_next_card(state, queue), deck)
    if card is None:
        return redirect(url_for("study.summary"))

    state["current"] = card.id
    save_session(state)
    return render_template(
        "study/card.html",
        deck=deck,
        card=card,
        style=state["style"],
        badge=mode_badge(user_deck, today),
        progress=session_progress(state, queue),
        coming_back=card.id in [entry[0] for entry in state["returns"]],
    )


@bp.post("/answer")
@login_required
def answer():
    state = get_session()
    if not state:
        return redirect(url_for("decks.my_decks"))

    card_id = request.form.get("card_id", type=int)
    if card_id is None or card_id != state["current"]:
        # Double click or an old tab: this card was already answered.
        return redirect(url_for("study.show_card"))

    confidence = request.form.get("confident")
    knew = request.form.get("knew_it")
    if confidence not in ("sure", "unsure") or knew not in ("1", "0"):
        flash("Pick Sure or Unsure first, then Know it or Review again.", "error")
        return redirect(url_for("study.show_card"))

    deck, user_deck = load_deck_for(state)
    card = card_in_deck(card_id, deck) if deck else None
    if card is None:
        return redirect(url_for("study.show_card"))

    knew_it = knew == "1"
    confident = confidence == "sure"
    today, now = today_local(), utc_now()

    moved_up = False
    if state["style"] == "smart":
        moved_up = update_progress(card, user_deck, knew_it, confident, today, now)
    db.session.add(Review(user_id=current_user.id, card_id=card.id, knew_it=knew_it,
                          confident=confident, style=state["style"], reviewed_at=now))
    xp = reward_student(current_user, knew_it, today)
    db.session.commit()

    log.info("review", extra={"fields": {
        "style": state["style"], "knew_it": knew_it, "confident": confident, "moved_up": moved_up,
    }})
    update_session_after_answer(state, card.id, knew_it, confident, moved_up, xp)
    save_session(state)
    return redirect(url_for("study.show_card"))


@bp.post("/shuffle")
@login_required
def shuffle():
    """Free Practice: shuffle the cards that are left."""
    state = get_session()
    if state and state["style"] == "free":
        state["seed"] = random.randrange(1_000_000)
        state["current"] = None
        save_session(state)
        flash("Cards shuffled.", "info")
    return redirect(url_for("study.show_card"))


@bp.get("/summary")
@login_required
def summary():
    state = get_session()
    if not state:
        return redirect(url_for("decks.my_decks"))
    deck = db.session.get(Deck, state["deck_id"])
    state["current"] = None
    save_session(state)
    return render_template(
        "study/summary.html",
        deck=deck if deck and (deck.is_ready or deck.owner_id == current_user.id) else None,
        style=state["style"],
        stats=state["stats"],
    )
