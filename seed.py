"""Load the ready decks from seed/ready_decks/*.json into the database.

Run it with:  python seed.py

Safe to run again: a ready deck whose title is already in the database is
skipped, so nobody's progress on it is lost. To reload a deck after editing
its JSON, delete instance/nexora.db and run this again.

Each JSON file looks like:
    {"title": "...", "folder": "semester" | "placement" | "competitive",
     "cards": [{"question": "...", "answer": "...", "topic": "...",
                "importance": "high" | "medium" | "low",
                "note": "CHECK: ..."}]}      <- note is optional, never loaded

A note starting with "CHECK" marks an answer a teammate still has to verify.
Demo users with study history are added in phase 4.
"""

import json
import sys
from pathlib import Path

from app import create_app
from app.models import IMPORTANCE_LEVELS, Card, Deck, db

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


def load_ready_decks(decks):
    """Add ready decks that aren't in the database yet. Returns (added, skipped) titles."""
    added, skipped = [], []
    for deck_data in decks:
        exists = db.session.query(Deck.id).filter_by(is_ready=True, title=deck_data["title"]).first()
        if exists:
            skipped.append(deck_data["title"])
            continue

        deck = Deck(title=deck_data["title"], folder=deck_data["folder"], is_ready=True, owner_id=None)
        db.session.add(deck)
        for card in deck_data["cards"]:
            deck.cards.append(Card(
                question=card["question"].strip(),
                answer=card["answer"].strip(),
                topic=card["topic"].strip(),
                importance=card["importance"],
                source="manual",
            ))
        added.append(deck_data["title"])
    db.session.commit()
    return added, skipped


def main():
    try:
        decks = read_all_deck_files()
    except (ValueError, json.JSONDecodeError) as error:
        print(f"Seed file problem: {error}")
        sys.exit(1)

    app = create_app()
    with app.app_context():
        added, skipped = load_ready_decks(decks)

    total_cards = sum(len(deck["cards"]) for deck in decks)
    print(f"Read {len(decks)} decks with {total_cards} cards.")
    print(f"Added: {', '.join(added) or 'none'}")
    if skipped:
        print(f"Already there, skipped: {', '.join(skipped)}")

    to_check = cards_to_check(decks)
    if to_check:
        print(f"\n{len(to_check)} answers marked CHECK, please verify:")
        for title, question in to_check:
            print(f"  - {title}: {question}")


if __name__ == "__main__":
    main()
