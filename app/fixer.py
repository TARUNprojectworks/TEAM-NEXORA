"""Weak Spot Fixer: at the end of a study session, find the weakest topic,
explain it, give links, and add 3 practice cards.

How it decides: engine.find_weak_topic() (open misconceptions only; the
misconception topic wins). At most one Fixer per session, and at most one per
topic per day, so studying twice doesn't pile up practice cards.

Practice cards go into the student's "Weak spot practice" deck with
source='fixer', importance='high', on shelf 1 and due today, so the planner
puts them first. The box is kept in session["weak_spot"] so Home can show it.
The Google search widget is shown only on the summary page right after the
Fixer runs (it's too big for the session cookie).
"""

import logging

from flask import Blueprint, session

from app import ai_service, engine
from app.ai_service import AILimitReached
from app.cardmaker import remove_near_duplicates
from app.models import Card, Deck, Progress, db, visible_to
from app.planner import seen_cards_for, todays_weak_spot, weak_spot_deck_id

bp = Blueprint("fixer", __name__, url_prefix="/fixer")
log = logging.getLogger("nexora.fixer")

MISSED_CARDS_SENT = 5


def todays_box(today):
    """The weak spot box from earlier today, or None."""
    return todays_weak_spot(today)


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


def run_after_session(user, today):
    """Run the Fixer once at the end of a session. Returns (box, search widget html), or (None, None)."""
    target = engine.find_weak_topic(seen_cards_for(user.id))
    if target is None:
        return None, None
    deck_id, topic = target
    earlier = todays_box(today)
    if earlier and (earlier["deck_id"], earlier["topic"]) == (deck_id, topic):
        return earlier, None  # this topic already got its practice cards today

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
        note = "Couldn't make practice cards right now. They'll come after your next session."

    resources = resources_for(user, topic, today)
    box = {
        "date": today.isoformat(),
        "deck_id": deck_id,
        "deck_title": db.session.get(Deck, deck_id).title,
        "topic": topic,
        "explanation": explanation,
        "card_ids": card_ids,
        "links": resources["links"],
        "note": note,
    }
    session["weak_spot"] = box
    log.info("fixer_ran", extra={"fields": {"practice_cards": len(card_ids), "links": len(box["links"])}})
    return box, resources["search_widget_html"]
