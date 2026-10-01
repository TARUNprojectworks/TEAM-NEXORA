"""seed.py — Load ready decks and demo users into the database.

Usage (from project root, with venv active):
    python seed.py

Safe to run multiple times — existing ready decks are skipped.
Set SEED_DEMO_USERS=0 in .env to skip creating demo users.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap Flask app context before importing any app code
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

from app import create_app
from app.models import (
    Card,
    Deck,
    Progress,
    Review,
    User,
    UserDeck,
    create_weak_spot_deck,
    db,
    today_local,
)

SEED_DIR = Path(__file__).parent / "seed" / "ready_decks"

# Demo users created for the hackathon demo
DEMO_USERS = [
    {"name": "Riya Sharma", "email": "riya@demo.nexora", "password": "Demo1234!"},
    {"name": "Arjun Mehta", "email": "arjun@demo.nexora", "password": "Demo1234!"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json_decks() -> list[dict]:
    """Return all *.json files from the ready_decks seed directory."""
    files = sorted(SEED_DIR.glob("*.json"))
    if not files:
        print(f"[seed] No JSON files found in {SEED_DIR}")
        return []
    decks = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_source_file"] = f.name
            decks.append(data)
        except json.JSONDecodeError as exc:
            print(f"[seed] Skipping {f.name} — bad JSON: {exc}")
    return decks


def seed_ready_decks(decks: list[dict]) -> dict[str, Deck]:
    """Insert ready decks + cards that don't already exist. Returns {title: Deck}."""
    existing_titles = {d.title for d in Deck.query.filter_by(is_ready=True).all()}
    created: dict[str, Deck] = {}

    for data in decks:
        title = data.get("title", "Untitled")
        folder = data.get("folder", "personal")
        cards_data = data.get("cards", [])

        if title in existing_titles:
            print(f"[seed] Skipping '{title}' — already exists.")
            # Still return it so demo history works
            deck = Deck.query.filter_by(title=title, is_ready=True).first()
            created[title] = deck
            continue

        deck = Deck(
            owner_id=None,
            title=title,
            folder=folder,
            is_ready=True,
        )
        db.session.add(deck)
        db.session.flush()  # get deck.id

        for c in cards_data:
            card = Card(
                deck_id=deck.id,
                question=c.get("question", ""),
                answer=c.get("answer", ""),
                topic=c.get("topic", "General"),
                importance=c.get("importance", "medium"),
                source_line=c.get("source_line"),
                source="manual",
            )
            db.session.add(card)

        db.session.flush()
        created[title] = deck
        print(f"[seed] Created ready deck '{title}' ({folder}) with {len(cards_data)} cards.")

    return created


def get_or_create_demo_user(name: str, email: str, password: str) -> User:
    """Return existing demo user or create a new one."""
    user = User.query.filter_by(email=email).first()
    if user:
        print(f"[seed] Demo user '{email}' already exists — skipping creation.")
        return user

    user = User(name=name, email=email, xp=0, streak=5)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    create_weak_spot_deck(user)
    print(f"[seed] Created demo user '{name}' ({email}).")
    return user


def add_deck_to_user(user: User, deck: Deck, mode: str = "normal", exam_date: date | None = None):
    """Add a ready deck to a user's collection if not already added."""
    exists = UserDeck.query.filter_by(user_id=user.id, deck_id=deck.id).first()
    if exists:
        return
    ud = UserDeck(user_id=user.id, deck_id=deck.id, mode=mode, exam_date=exam_date)
    db.session.add(ud)


def seed_demo_history(user: User, deck: Deck, shelf_distribution: dict[int, float]):
    """
    Create simulated progress + review history for a user on a deck.

    shelf_distribution: {shelf_number: fraction_of_cards} — fractions should sum to ~1.0
    Example: {1: 0.3, 2: 0.3, 3: 0.2, 4: 0.1, 5: 0.1}
    """
    cards = Card.query.filter_by(deck_id=deck.id).all()
    today = today_local()

    existing_card_ids = {
        p.card_id
        for p in Progress.query.filter_by(user_id=user.id).all()
        if any(c.id == p.card_id for c in cards)
    }

    WAIT = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}
    total = len(cards)
    card_idx = 0

    for shelf, fraction in sorted(shelf_distribution.items()):
        count = round(total * fraction)
        for i in range(count):
            if card_idx >= total:
                break
            card = cards[card_idx]
            card_idx += 1

            if card.id in existing_card_ids:
                continue

            wait_days = WAIT.get(shelf, 0)
            next_due = today - timedelta(days=1) if shelf == 1 else today + timedelta(days=wait_days)

            progress = Progress(
                user_id=user.id,
                card_id=card.id,
                shelf=shelf,
                next_due=next_due,
                wrong_count=max(0, 3 - shelf),
                misconception_count=1 if shelf == 1 and i == 0 else 0,
            )
            db.session.add(progress)

            # Add a few historical reviews
            for day_offset in range(max(1, shelf - 1), 0, -1):
                knew_it = shelf > 2
                review = Review(
                    user_id=user.id,
                    card_id=card.id,
                    knew_it=knew_it,
                    confident=knew_it,
                    style="smart",
                )
                review.reviewed_at = db.func.now()
                db.session.add(review)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = create_app()
    with app.app_context():
        print("[seed] Starting database seed …")

        # 1. Ready decks
        decks_data = load_json_decks()
        if not decks_data:
            print("[seed] Nothing to seed. Exiting.")
            return

        ready_decks = seed_ready_decks(decks_data)
        db.session.commit()
        print(f"[seed] {len(ready_decks)} ready decks in database.")

        # 2. Demo users (skip if SEED_DEMO_USERS=0)
        if os.getenv("SEED_DEMO_USERS", "1") == "0":
            print("[seed] SEED_DEMO_USERS=0 — skipping demo users.")
            db.session.commit()
            print("[seed] Done.")
            return

        riya = get_or_create_demo_user(**DEMO_USERS[0])
        arjun = get_or_create_demo_user(**DEMO_USERS[1])
        db.session.commit()

        # 3. Add decks to demo users + simulated history
        # Riya: Biology (exam mode, 5 days), Physics (normal), DSA (placement prep)
        biology_deck = ready_decks.get("Biology — Cell & Genetics")
        physics_deck  = ready_decks.get("Physics — Mechanics & Waves")
        dsa_deck      = ready_decks.get("DSA — Arrays, Trees & Sorting")
        aptitude_deck = ready_decks.get("Aptitude — Quant & Reasoning")

        if biology_deck:
            exam_date = today_local() + timedelta(days=5)
            add_deck_to_user(riya, biology_deck, mode="exam", exam_date=exam_date)
            seed_demo_history(riya, biology_deck, {1: 0.25, 2: 0.25, 3: 0.25, 4: 0.15, 5: 0.10})

        if physics_deck:
            add_deck_to_user(riya, physics_deck, mode="normal")
            seed_demo_history(riya, physics_deck, {1: 0.15, 2: 0.20, 3: 0.30, 4: 0.20, 5: 0.15})

        if dsa_deck:
            add_deck_to_user(riya, dsa_deck, mode="normal")
            seed_demo_history(riya, dsa_deck, {1: 0.30, 2: 0.30, 3: 0.20, 4: 0.10, 5: 0.10})

        # Arjun: DSA (exam, 3 days), Aptitude, DBMS
        dbms_deck = ready_decks.get("DBMS — SQL & Database Concepts")

        if dsa_deck:
            exam_date = today_local() + timedelta(days=3)
            add_deck_to_user(arjun, dsa_deck, mode="exam", exam_date=exam_date)
            seed_demo_history(arjun, dsa_deck, {1: 0.20, 2: 0.25, 3: 0.25, 4: 0.20, 5: 0.10})

        if aptitude_deck:
            add_deck_to_user(arjun, aptitude_deck, mode="normal")
            seed_demo_history(arjun, aptitude_deck, {1: 0.20, 2: 0.30, 3: 0.25, 4: 0.15, 5: 0.10})

        if dbms_deck:
            add_deck_to_user(arjun, dbms_deck, mode="normal")
            seed_demo_history(arjun, dbms_deck, {1: 0.25, 2: 0.25, 3: 0.25, 4: 0.15, 5: 0.10})

        db.session.commit()
        print("[seed] Demo user history seeded.")
        print("[seed] [OK] All done!")
        print()
        print("  Demo logins:")
        for u in DEMO_USERS:
            print(f"    Email: {u['email']}   Password: {u['password']}")


if __name__ == "__main__":
    main()
