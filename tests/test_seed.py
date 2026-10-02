import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func

from app import engine
from app.models import Card, Deck, Progress, Review, User, UserDeck, db, local_date_of, today_local
from app.planner import seen_cards_for, welcome_back_count
from seed import cards_to_check, create_demo_users, load_ready_decks, read_all_deck_files, read_deck_file


def test_all_eight_ready_decks_are_valid():
    decks = read_all_deck_files()
    assert len(decks) == 8
    for deck in decks:
        assert 20 <= len(deck["cards"]) <= 30, deck["title"]


def test_each_folder_has_two_or_three_decks():
    folders = [deck["folder"] for deck in read_all_deck_files()]
    for folder in ("semester", "placement", "competitive"):
        assert 2 <= folders.count(folder) <= 3


def test_about_two_hundred_cards_in_total():
    total = sum(len(deck["cards"]) for deck in read_all_deck_files())
    assert 180 <= total <= 220


def test_no_duplicate_questions_within_a_deck():
    for deck in read_all_deck_files():
        questions = [card["question"] for card in deck["cards"]]
        assert len(questions) == len(set(questions)), deck["title"]


def test_loading_twice_does_not_duplicate(app):
    decks = read_all_deck_files()
    with app.app_context():
        added, _ = load_ready_decks(decks)
        assert len(added) == 8
        added_again, updated = load_ready_decks(decks)
        assert added_again == []
        assert updated == []
        assert db.session.query(Deck).filter_by(is_ready=True).count() == 8
        assert db.session.query(Card).count() == sum(len(d["cards"]) for d in decks)


def test_ready_decks_have_no_owner(app):
    with app.app_context():
        load_ready_decks(read_all_deck_files())
        assert db.session.query(Deck).filter_by(is_ready=True).filter(Deck.owner_id.isnot(None)).count() == 0


def test_bad_importance_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "title": "Bad", "folder": "semester",
        "cards": [{"question": "Q", "answer": "A", "topic": "T", "importance": "urgent"}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="importance"):
        read_deck_file(path)


# ---------- Demo students ----------

@pytest.fixture
def seeded(app):
    """Ready decks plus demo students, as `python seed.py` makes them."""
    with app.app_context():
        load_ready_decks(read_all_deck_files())
        create_demo_users(today_local())
    return app


def demo_user(email):
    return db.session.query(User).filter_by(email=email).one()


def test_riya_has_biology_in_exam_mode_5_days_out(seeded):
    with seeded.app_context():
        riya = demo_user("riya@example.com")
        biology = db.session.query(Deck).filter_by(title="Cell Biology").one()
        setting = db.session.get(UserDeck, (riya.id, biology.id))
        assert setting.mode == "exam"
        assert setting.exam_date == today_local() + timedelta(days=5)


def test_riya_has_four_weeks_of_history_and_learned_cards(seeded):
    with seeded.app_context():
        riya = demo_user("riya@example.com")
        first = db.session.query(func.min(Review.reviewed_at)).filter_by(user_id=riya.id).scalar()
        assert (today_local() - local_date_of(first)).days == 28
        assert riya.last_study_date == today_local() - timedelta(days=1)
        assert riya.streak >= 9
        learned = db.session.query(Progress).filter_by(user_id=riya.id, shelf=5).count()
        assert learned >= 5


def test_riya_has_a_weak_spot_for_the_demo(seeded):
    with seeded.app_context():
        riya = demo_user("riya@example.com")
        deck_id, topic = engine.find_weak_topic(seen_cards_for(riya.id))
        assert topic == "Cell Division"


def test_arjun_has_been_away_so_home_says_welcome_back(seeded):
    with seeded.app_context():
        arjun = demo_user("arjun@example.com")
        assert arjun.last_study_date == today_local() - timedelta(days=5)
        assert welcome_back_count(arjun, today_local())


def test_demo_history_matches_what_the_engine_would_do(seeded):
    """Replay every demo review through engine.py: the saved shelves must match exactly."""
    with seeded.app_context():
        for user in db.session.query(User).all():
            settings = {ud.deck_id: ud for ud in db.session.query(UserDeck).filter_by(user_id=user.id)}
            replayed = {}
            reviews = db.session.query(Review, Card).join(Card, Card.id == Review.card_id).filter(
                Review.user_id == user.id).order_by(Review.reviewed_at, Review.id)
            for review, card in reviews:
                day = local_date_of(review.reviewed_at)
                state = replayed.setdefault(card.id, SimpleNamespace(
                    shelf=1, next_due=day, wrong_count=0, misconception_count=0, last_seen=None))
                setting = settings[card.deck_id]
                engine.apply_answer(state, review.knew_it, review.confident, day, setting.mode,
                                    setting.exam_date if setting.mode == "exam" else None, review.reviewed_at)

            saved = {p.card_id: p for p in db.session.query(Progress).filter_by(user_id=user.id)}
            assert saved.keys() == replayed.keys()
            for card_id, progress in saved.items():
                expected = replayed[card_id]
                assert (progress.shelf, progress.next_due, progress.wrong_count, progress.misconception_count) == (
                    expected.shelf, expected.next_due, expected.wrong_count, expected.misconception_count)


def test_demo_xp_matches_the_reviews(seeded):
    with seeded.app_context():
        for user in db.session.query(User).all():
            reviews = db.session.query(Review).filter_by(user_id=user.id).all()
            assert user.xp == sum(2 if r.knew_it else 1 for r in reviews)


def test_seeding_demo_users_twice_does_not_duplicate(seeded):
    with seeded.app_context():
        reviews = db.session.query(Review).count()
        assert create_demo_users(today_local()) == []
        assert db.session.query(Review).count() == reviews


def test_check_notes_are_listed():
    decks = [{"title": "D", "cards": [
        {"question": "Sure one"},
        {"question": "Unsure one", "note": "CHECK: confirm the year"},
    ]}]
    assert cards_to_check(decks) == [("D", "Unsure one")]


def test_seeding_again_adds_new_json_cards_to_existing_decks(app):
    decks = read_all_deck_files()
    with app.app_context():
        load_ready_decks(decks)
        biology = next(d for d in decks if d["title"] == "Cell Biology")
        biology["cards"].append({"question": "What is a vacuole?", "answer": "A storage sac in the cell.",
                                 "topic": "Cell Organelles", "importance": "low"})
        added, updated = load_ready_decks(decks)
        assert added == []
        assert updated == [("Cell Biology", 1)]
        card = db.session.query(Card).filter_by(question="What is a vacuole?").one()
        assert card.owner_id is None


def test_private_cards_do_not_block_a_json_card(app, make_user):
    decks = read_all_deck_files()
    riya = make_user()
    with app.app_context():
        load_ready_decks(decks)
        biology_id = db.session.query(Deck.id).filter_by(title="Cell Biology").scalar()
        db.session.add(Card(deck_id=biology_id, owner_id=riya, question="What is a vacuole?", answer="Mine", topic="T"))
        db.session.commit()
        next(d for d in decks if d["title"] == "Cell Biology")["cards"].append(
            {"question": "What is a vacuole?", "answer": "A storage sac.", "topic": "T", "importance": "low"})
        assert load_ready_decks(decks)[1] == [("Cell Biology", 1)]
