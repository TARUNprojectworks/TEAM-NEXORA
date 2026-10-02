"""Weak Spot Fixer: find a weak topic, explain it, give links, and add 3 practice cards.

It runs in two places:
- after a study session: the summary page shows at once, then fetches the box
  from /study/weak-spot, which looks only at the decks just studied;
- from "Fix it" on My Progress, for one topic (/fixer/topic/<deck_id>).

How it decides: engine.find_weak_topic() (open misconceptions only; the
misconception topic wins). Once per topic per day: before calling Gemini we look
in the database for practice cards already made today for that topic, so logging
in again or pressing "Fix it" twice never makes duplicates.

Practice cards go into the student's "Weak spot practice" deck with
source='fixer', importance='high', on shelf 1 and due today, so the planner
puts them first. The last box is also kept in session["weak_spot"] for Home.
"""

import logging

from flask import Blueprint, abort, jsonify, render_template, request, session
from flask_login import current_user, login_required

from app import ai_service, engine
from app.ai_service import AILimitReached
from app.cardmaker import remove_near_duplicates
from app.models import Card, Deck, Progress, UserDeck, db, local_day_start_utc, today_local, visible_to
from app.planner import seen_cards_for, todays_weak_spot, weak_spot_deck_id

bp = Blueprint("fixer", __name__, url_prefix="/fixer")
log = logging.getLogger("nexora.fixer")

MISSED_CARDS_SENT = 5
ALREADY_DONE_NOTE = "You already got practice cards for this topic today. Practise them again below."


def todays_box(today):
    """The weak spot box from earlier today, or None."""
    return todays_weak_spot(today)


def practice_cards_made_today(user_id, topic, today):
    """Fixer cards already made today for this topic. This is the once-a-day check, and it
    lives in the database, so a new login (a new browser session) can't repeat the Fixer."""
    return db.session.query(Card).filter(
        Card.deck_id == weak_spot_deck_id(user_id), Card.source == "fixer", Card.topic == topic,
        Card.created_at >= local_day_start_utc(today),
    ).order_by(Card.id).all()


def missed_cards(user_id, deck_id, topic):
    """The student's worst cards in this topic: misconceptions first, then most wrong."""
    rows = db.session.query(Card).join(Progress, Progress.card_id == Card.id).filter(
        Progress.user_id == user_id, Progress.hidden.is_(False),
        Card.deck_id == deck_id, Card.topic == topic, visible_to(user_id),
        (Progress.wrong_count > 0) | (Progress.shelf <= 2),
    ).order_by(Progress.misconception_count.desc(), Progress.wrong_count.desc()).limit(MISSED_CARDS_SENT)
    return [{"question": card.question, "answer": card.answer} for card in rows]


def save_practice_cards(user_id, topic, cards, today):
    """Add practice cards to Weak spot practice, on shelf 1 and due today. Returns their ids."""
    deck_id = weak_spot_deck_id(user_id)
    existing = [q for (q,) in db.session.query(Card.question).filter_by(deck_id=deck_id)]
    new_cards = []
    for draft in remove_near_duplicates(cards, existing):
        card = Card(deck_id=deck_id, owner_id=user_id, question=draft["question"], answer=draft["answer"],
                    topic=topic, importance="high", source="fixer")
        db.session.add(card)
        new_cards.append(card)
    db.session.flush()  # gives the cards their ids
    for card in new_cards:
        db.session.add(Progress(user_id=user_id, card_id=card.id, shelf=1, next_due=today,
                                wrong_count=0, misconception_count=0))
    db.session.commit()
    return [card.id for card in new_cards]


def resources_for(user, topic, today):
    """Links for the topic. Uses an AI call if one is left today, otherwise plain search links."""
    try:
        ai_service.use_ai_call(user, today)
        db.session.commit()
    except AILimitReached:
        return {"links": ai_service.fallback_links(topic), "search_widget_html": None}
    return ai_service.find_resources(topic)


def make_box(deck_id, topic, today, explanation="", card_ids=(), links=(), note=None):
    box = {
        "date": today.isoformat(),
        "deck_id": deck_id,
        "deck_title": db.session.get(Deck, deck_id).title,
        "topic": topic,
        "explanation": explanation,
        "card_ids": list(card_ids),
        "links": list(links),
        "note": note,
    }
    session["weak_spot"] = box
    return box


def fix_topic(user, deck_id, topic, today):
    """Explanation, practice cards and links for one weak topic. Returns (box, search widget html)."""
    made_today = practice_cards_made_today(user.id, topic, today)
    if made_today:
        earlier = todays_box(today)
        if earlier and (earlier["deck_id"], earlier["topic"]) == (deck_id, topic):
            return earlier, None  # same browser: the full box is still in the session
        return make_box(deck_id, topic, today, card_ids=[c.id for c in made_today],
                        links=ai_service.fallback_links(topic), note=ALREADY_DONE_NOTE), None

    explanation, card_ids, note = "", [], None
    try:
        ai_service.use_ai_call(user, today)
        db.session.commit()
        fix = ai_service.fix_weak_spot(topic, missed_cards(user.id, deck_id, topic))
        explanation = fix["explanation"]
        card_ids = save_practice_cards(user.id, topic, fix["follow_up_cards"], today)
    except AILimitReached as error:
        note = str(error)
    except Exception as error:
        log.warning("fixer_failed", extra={"fields": {"error": type(error).__name__}})
        note = "Couldn't make practice cards right now. Try again after your next session."

    resources = resources_for(user, topic, today)
    box = make_box(deck_id, topic, today, explanation, card_ids, resources["links"], note)
    log.info("fixer_ran", extra={"fields": {"practice_cards": len(card_ids), "links": len(box["links"])}})
    return box, resources["search_widget_html"]


def run_after_session(user, today, deck_ids):
    """The Fixer for the decks just studied. Returns (box, widget html), or (None, None)."""
    target = engine.find_weak_topic(seen_cards_for(user.id, deck_ids))
    if target is None:
        return None, None
    deck_id, topic = target
    return fix_topic(user, deck_id, topic, today)


def render_box(box, widget):
    """The box as HTML, for the summary page and the "Fix it" page to drop in."""
    return render_template("_weak_spot_box.html", box=box, show_links=True, widget=widget)


# ---------- "Fix it" for one topic (from My Progress) ----------

def topic_in_my_deck_or_404(deck_id, topic):
    deck = db.session.get(Deck, deck_id)
    in_list = deck is not None and db.session.get(UserDeck, (current_user.id, deck_id)) is not None
    has_topic = in_list and db.session.query(Card.id).filter(
        Card.deck_id == deck_id, Card.topic == topic, visible_to(current_user.id)
    ).first() is not None
    if not has_topic:
        abort(404)
    return deck


@bp.get("/topic/<int:deck_id>")
@login_required
def topic_page(deck_id):
    topic = request.args.get("topic", "").strip()
    deck = topic_in_my_deck_or_404(deck_id, topic)
    return render_template("fixer/topic.html", deck=deck, topic=topic)


@bp.post("/topic/<int:deck_id>")
@login_required
def fix_topic_now(deck_id):
    topic = str((request.get_json(silent=True) or {}).get("topic", "")).strip()
    topic_in_my_deck_or_404(deck_id, topic)
    box, widget = fix_topic(current_user, deck_id, topic, today_local())
    return jsonify(html=render_box(box, widget))
