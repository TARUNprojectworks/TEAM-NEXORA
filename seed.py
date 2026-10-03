"""Load the ready decks from seed/ready_decks/*.json into the database.

Run it with:  python seed.py

Safe to run again. A ready deck that is already in the database keeps its
cards (so nobody's progress is lost); only cards from the JSON whose question
isn't in the deck yet are added. Editing an existing card's answer in the JSON
does not change the database: delete instance/nexora.db and seed again for that.

Each JSON file looks like:
    {"title": "...", "folder": "semester" | "placement" | "competitive",
     "cards": [{"question": "...", "answer": "...", "topic": "...",
                "importance": "high" | "medium" | "low",
                "note": "CHECK: ..."}]}      <- note is optional, never loaded

A note starting with "CHECK" marks an answer a teammate still has to verify.

It also creates two demo students (Riya and Arjun) with weeks of study
history, so Home, the planner and Tracking show real numbers in the demo.
Their history is made by running simulated answers through engine.py, the
same code the app uses, so shelves and reviews always agree. Run this on the
morning of the demo: Riya's exam is "5 days out" from the day you seed.
"""

import json
import random
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import inspect

from app import create_app, engine
from app.models import (
    IMPORTANCE_LEVELS, Card, Deck, Progress, Review, User, UserDeck, create_weak_spot_deck, db,
    local_day_start_utc, today_local,
)
from app.planner import exam_decks_first
from app.study import next_streak, xp_for

SEED_DIR = Path(__file__).parent / "seed" / "ready_decks"
READY_FOLDERS = ("semester", "placement", "competitive")
CARD_FIELDS = ("question", "answer", "topic", "importance")


def read_deck_file(path):
    """Read one deck file and check it, so a typo fails loudly instead of half-loading."""
    deck = json.loads(path.read_text(encoding="utf-8"))
    if not deck.get("title"):
        raise ValueError(f"{path.name}: missing title")
    if deck.get("folder") not in READY_FOLDERS:
        raise ValueError(f"{path.name}: folder must be one of {READY_FOLDERS}")
    if not deck.get("cards"):
        raise ValueError(f"{path.name}: no cards")

    for number, card in enumerate(deck["cards"], start=1):
        for field in CARD_FIELDS:
            if not str(card.get(field, "")).strip():
                raise ValueError(f"{path.name}, card {number}: missing {field}")
        if card["importance"] not in IMPORTANCE_LEVELS:
            raise ValueError(f"{path.name}, card {number}: importance must be one of {IMPORTANCE_LEVELS}")
    return deck


def read_all_deck_files(seed_dir=SEED_DIR):
    return [read_deck_file(path) for path in sorted(seed_dir.glob("*.json"))]


def cards_to_check(decks):
    """(deck title, question) for every card marked CHECK."""
    return [
        (deck["title"], card["question"])
        for deck in decks
        for card in deck["cards"]
        if str(card.get("note", "")).upper().startswith("CHECK")
    ]


def shared_card(card):
    """A card from the JSON as a shared ready-deck card (owner_id stays empty)."""
    return Card(
        question=card["question"].strip(),
        answer=card["answer"].strip(),
        topic=card["topic"].strip(),
        importance=card["importance"],
        source="manual",
        owner_id=None,
    )


def load_ready_decks(decks):
    """Add new ready decks, and new cards to ready decks we already have.

    Cards are matched on their question text. Returns (new deck titles,
    [(title, number of cards added)] for decks that were already there).
    """
    added, updated = [], []
    for deck_data in decks:
        deck = db.session.query(Deck).filter_by(is_ready=True, title=deck_data["title"]).first()
        if deck is None:
            deck = Deck(title=deck_data["title"], folder=deck_data["folder"], is_ready=True, owner_id=None)
            db.session.add(deck)
            deck.cards.extend(shared_card(card) for card in deck_data["cards"])
            added.append(deck_data["title"])
            continue

        # Students' private cards don't count: only shared cards are matched.
        have = {
            question.strip() for (question,) in
            db.session.query(Card.question).filter(Card.deck_id == deck.id, Card.owner_id.is_(None))
        }
        new_cards = [shared_card(card) for card in deck_data["cards"] if card["question"].strip() not in have]
        deck.cards.extend(new_cards)
        if new_cards:
            updated.append((deck.title, len(new_cards)))
    db.session.commit()
    return added, updated


# ---------- Demo students ----------

DEMO_PASSWORD = "nexora-demo"  # demo accounts only; shown in the README
DEMO_USERS = [
    {
        "name": "Riya",
        "email": "riya@example.com",
        "history_days": 28,     # four weeks of study
        "rest_days": 0,         # last studied yesterday
        "streak_days": 9,       # studied every one of the last 9 days
        # (deck title, exam in N days from seeding, or None for Normal mode)
        "decks": [("Cell Biology", 5), ("Quantitative Aptitude", None), ("DSA Basics", None)],
        "hard_topics": ["Cell Division"],  # she keeps mixing these up: her weak spot
    },
    {
        "name": "Arjun",
        "email": "arjun@example.com",
        "history_days": 14,
        "rest_days": 4,         # away for 4 days: Home says "Welcome back"
        "streak_days": 5,
        "decks": [("DSA Basics", None), ("HR Interview", None), ("GATE CS Operating Systems", 20)],
        "hard_topics": ["Deadlocks"],
    },
]
CARDS_PER_STUDY_DAY = 30
STUDY_HOUR_LOCAL = 19        # demo students study in the evening
SECONDS_PER_ANSWER = 30
CHANCE_OF_STUDYING = 0.75    # on days outside the current streak


def simulated_answer(rng, shelf, hard):
    """(knew_it, confident) for one card. Higher shelves are better known."""
    chance_known = min(0.62 + 0.08 * shelf, 0.95) - (0.3 if hard else 0)
    knew_it = rng.random() < chance_known
    if knew_it:
        confident = rng.random() < (0.55 if hard else 0.8)
    else:
        # Sure but wrong is a misconception; it happens more on hard topics.
        confident = rng.random() < (0.5 if hard else 0.15)
    return knew_it, confident


def cards_for_the_day(cards, progress, decks, day):
    """What Smart Study would show that day: due cards by priority, then up to 10 new."""
    seen, new = [], []
    for card in cards:
        user_deck = decks[card.deck_id]
        candidate = engine.Candidate(card, progress.get(card.id), user_deck.mode, user_deck.exam_date)
        (new if card.id not in progress else seen).append(candidate)
    due = engine.rank_candidates(seen, day)
    todays_new = exam_decks_first(engine.rank_candidates(new, day))[:engine.NEW_CARDS_PER_DAY]
    return [c.card for c in (due + todays_new)[:CARDS_PER_STUDY_DAY]]


def study_one_day(user, cards, progress, decks, hard_topics, day, rng):
    queue = cards_for_the_day(cards, progress, decks, day)
    asked_twice = set()
    start = local_day_start_utc(day) + timedelta(hours=STUDY_HOUR_LOCAL)
    position = 0
    while position < len(queue):
        card = queue[position]
        seen_at = start + timedelta(seconds=SECONDS_PER_ANSWER * position)
        if card.id not in progress:
            progress[card.id] = Progress(user_id=user.id, card_id=card.id, shelf=1, next_due=day,
                                         wrong_count=0, misconception_count=0)
            db.session.add(progress[card.id])
        knew_it, confident = simulated_answer(rng, progress[card.id].shelf, card.topic in hard_topics)

        user_deck = decks[card.deck_id]
        exam_date = user_deck.exam_date if user_deck.mode == "exam" else None
        engine.apply_answer(progress[card.id], knew_it, confident, day, user_deck.mode, exam_date, seen_at=seen_at)
        db.session.add(Review(user_id=user.id, card_id=card.id, knew_it=knew_it, confident=confident,
                              style="smart", reviewed_at=seen_at))
        user.xp += xp_for(knew_it)
        user.streak = next_streak(user.streak, user.last_study_date, day)
        user.last_study_date = day

        # "Review again" brings the card back after 3 others, like the real session.
        if not knew_it and card.id not in asked_twice:
            asked_twice.add(card.id)
            queue.insert(position + 1 + engine.REQUEUE_GAP, card)
        position += 1


def create_demo_user(profile, today):
    """One demo student with their decks and study history. Skipped if they exist."""
    if db.session.query(User.id).filter_by(email=profile["email"]).first():
        return None
    user = User(name=profile["name"], email=profile["email"])
    user.set_password(DEMO_PASSWORD)
    db.session.add(user)
    create_weak_spot_deck(user)

    decks = {}
    for title, exam_in_days in profile["decks"]:
        deck = db.session.query(Deck).filter_by(is_ready=True, title=title).one()
        user_deck = UserDeck(user_id=user.id, deck_id=deck.id, mode="exam" if exam_in_days else "normal",
                             exam_date=today + timedelta(days=exam_in_days) if exam_in_days else None)
        db.session.add(user_deck)
        decks[deck.id] = user_deck
    cards = db.session.query(Card).filter(Card.deck_id.in_(decks)).order_by(Card.id).all()

    rng = random.Random(profile["email"])  # same history every time we seed
    progress = {}
    last_day = today - timedelta(days=1 + profile["rest_days"])
    day = today - timedelta(days=profile["history_days"])
    while day <= last_day:
        in_current_streak = (last_day - day).days < profile["streak_days"]
        if in_current_streak or rng.random() < CHANCE_OF_STUDYING:
            study_one_day(user, cards, progress, decks, set(profile["hard_topics"]), day, rng)
        day += timedelta(days=1)
    db.session.commit()
    return user


def demo_summary(user):
    reviews = db.session.query(Review).filter_by(user_id=user.id).count()
    learned = db.session.query(Progress).filter_by(user_id=user.id, shelf=engine.MAX_SHELF).count()
    return (f"Demo user {user.name} ({user.email} / {DEMO_PASSWORD}): {reviews} reviews, "
            f"{learned} cards on shelf 5, streak {user.streak}, {user.xp} XP")


def create_demo_users(today):
    """Create every demo student that isn't there yet. Returns the new users."""
    created = [create_demo_user(profile, today) for profile in DEMO_USERS]
    return [user for user in created if user is not None]


def prepare_database():
    """Bring the database up to date with the migrations in migrations/.

    A database made before we used migrations (by db.create_all) has the tables
    but no alembic_version table: we mark it as migration 0001, then upgrade, so
    nobody's data is lost.
    """
    from flask_migrate import stamp, upgrade

    tables = inspect(db.engine).get_table_names()
    if "users" in tables and "alembic_version" not in tables:
        stamp(revision="0001")
    upgrade()


def main():
    try:
        decks = read_all_deck_files()
    except (ValueError, json.JSONDecodeError) as error:
        print(f"Seed file problem: {error}")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        prepare_database()
        added, updated = load_ready_decks(decks)
        demo_lines = [demo_summary(user) for user in create_demo_users(today_local())]

    total_cards = sum(len(deck["cards"]) for deck in decks)
    print(f"Read {len(decks)} decks with {total_cards} cards.")
    print(f"Added: {', '.join(added) or 'none'}")
    for title, count in updated:
        print(f"Added {count} new {'card' if count == 1 else 'cards'} to {title}")

    for line in demo_lines:
        print(line)

    to_check = cards_to_check(decks)
    if to_check:
        print(f"\n{len(to_check)} answers marked CHECK, please verify:")
        for title, question in to_check:
            print(f"  - {title}: {question}")


if __name__ == "__main__":
    main()
