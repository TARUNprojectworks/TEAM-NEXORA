import json

import pytest

from app.models import Card, Deck, db
from seed import cards_to_check, load_ready_decks, read_all_deck_files, read_deck_file


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
        added_again, skipped = load_ready_decks(decks)
        assert added_again == []
        assert len(skipped) == 8
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


def test_check_notes_are_listed():
    decks = [{"title": "D", "cards": [
        {"question": "Sure one"},
        {"question": "Unsure one", "note": "CHECK: confirm the year"},
    ]}]
    assert cards_to_check(decks) == [("D", "Unsure one")]
