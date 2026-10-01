from datetime import timedelta

import pytest

from app.models import Card, Deck, Progress, Review, UserDeck, db, local_day_start_utc, today_local, utc_now


@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


def add_deck(app, user_id, topics, title="Biology"):
    """A ready deck in the student's list; `topics` is a list of topic names, one per card."""
    with app.app_context():
        deck = Deck(title=title, folder="semester", is_ready=True)
        db.session.add(deck)
        for number, topic in enumerate(topics):
            deck.cards.append(Card(question=f"Q{number}", answer="A", topic=topic))
        db.session.flush()
        db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode="normal"))
        db.session.commit()
        return deck.id, [card.id for card in deck.cards]


def set_progress(app, user_id, card_id, shelf, misconceptions=0, hidden=False):
    with app.app_context():
        db.session.add(Progress(user_id=user_id, card_id=card_id, shelf=shelf, next_due=today_local(),
                                wrong_count=misconceptions, misconception_count=misconceptions,
                                last_seen=utc_now(), hidden=hidden))
        db.session.commit()


def data(client):
    return client.get("/tracking/data").get_json()


def test_tracking_needs_login(client):
    assert client.get("/tracking/").status_code == 302
    assert client.get("/tracking/data").status_code == 302


def test_tracking_page_has_every_section(riya_client):
    page = riya_client.get("/tracking/").get_data(as_text=True)
    for heading in ["Weakest topics", "Study calendar", "Mastery by topic", "Progress per deck", "Misconceptions"]:
        assert heading in page
    assert "No study history yet" in page


def test_mastery_is_shelf_5_cards_over_all_cards_in_the_topic(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Cells", "Cells", "Cells", "Cells", "DNA"])
    set_progress(app, riya, card_ids[0], shelf=5)
    set_progress(app, riya, card_ids[1], shelf=3)
    mastery = {row["topic"]: row for row in data(riya_client)["mastery"]}
    assert (mastery["Cells"]["learned"], mastery["Cells"]["total"], mastery["Cells"]["mastery"]) == (1, 4, 25)
    assert mastery["DNA"]["mastery"] == 0


def test_mastery_leaves_out_hidden_cards(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Cells", "Cells", "Cells", "Cells"])
    set_progress(app, riya, card_ids[0], shelf=5)
    set_progress(app, riya, card_ids[1], shelf=1, hidden=True)
    set_progress(app, riya, card_ids[2], shelf=5, hidden=True)
    cells = data(riya_client)["mastery"][0]
    assert (cells["learned"], cells["total"]) == (1, 2)


def test_deck_progress_counts_shelves_and_not_studied(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Cells"] * 5)
    set_progress(app, riya, card_ids[0], shelf=1)
    set_progress(app, riya, card_ids[1], shelf=5)
    set_progress(app, riya, card_ids[2], shelf=2, hidden=True)
    deck = data(riya_client)["decks"][0]
    assert deck["shelves"] == [1, 0, 0, 0, 1]
    assert deck["new"] == 2
    assert deck["total"] == 4


def test_misconceptions_found_and_resolved(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Cells"] * 4)
    set_progress(app, riya, card_ids[0], shelf=1, misconceptions=1)   # still open
    set_progress(app, riya, card_ids[1], shelf=3, misconceptions=2)   # resolved
    set_progress(app, riya, card_ids[2], shelf=4, misconceptions=0)
    set_progress(app, riya, card_ids[3], shelf=5, misconceptions=1, hidden=True)  # hidden: not counted
    assert data(riya_client)["misconceptions"] == {"found": 2, "resolved": 1, "open": 1}


def test_weakest_topics_highlights_the_fixer_target(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Cells"] * 3 + ["DNA"] * 3)
    for card_id in card_ids[:3]:
        set_progress(app, riya, card_id, shelf=1)
    for card_id in card_ids[3:]:
        set_progress(app, riya, card_id, shelf=5)
    page = riya_client.get("/tracking/").get_data(as_text=True)
    assert "<mark>Cells</mark>" in page
    assert "100% of 3 seen still on shelf 1–2" in page
    assert "DNA" not in page.split("Weakest topics")[1].split("Study calendar")[0]


def test_resolved_uses_the_same_line_as_the_engine(app, riya_client, riya):
    from app import engine
    _, card_ids = add_deck(app, riya, ["Cells"] * 2)
    set_progress(app, riya, card_ids[0], shelf=engine.RESOLVED_SHELF - 1, misconceptions=1)
    set_progress(app, riya, card_ids[1], shelf=engine.RESOLVED_SHELF, misconceptions=1)
    assert data(riya_client)["misconceptions"] == {"found": 2, "resolved": 1, "open": 1}


def test_weakest_topics_count_only_open_misconceptions(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Cells"] * 4)
    set_progress(app, riya, card_ids[0], shelf=1, misconceptions=1)   # open
    set_progress(app, riya, card_ids[1], shelf=2, misconceptions=1)   # open
    set_progress(app, riya, card_ids[2], shelf=4, misconceptions=3)   # resolved
    set_progress(app, riya, card_ids[3], shelf=5)
    page = riya_client.get("/tracking/").get_data(as_text=True)
    assert "2 open misconceptions" in page


def test_topic_with_only_resolved_misconceptions_is_not_weak(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Processes"] * 3)
    for card_id in card_ids:
        set_progress(app, riya, card_id, shelf=4, misconceptions=1)
    weakest = riya_client.get("/tracking/").get_data(as_text=True).split("Weakest topics")[1].split("Study calendar")[0]
    assert "Processes" not in weakest
    assert "No weak topics yet" in weakest


def test_calendar_counts_answers_per_day(app, riya_client, riya):
    _, card_ids = add_deck(app, riya, ["Cells"])
    yesterday = today_local() - timedelta(days=1)
    with app.app_context():
        for minute in range(3):
            db.session.add(Review(user_id=riya, card_id=card_ids[0], knew_it=True, confident=True,
                                  reviewed_at=local_day_start_utc(yesterday) + timedelta(hours=20, minutes=minute)))
        db.session.commit()
    page = riya_client.get("/tracking/").get_data(as_text=True)
    assert f'aria-label="{yesterday.isoformat()}: 3 cards"' in page


def test_chart_js_comes_from_cdnjs_with_integrity(riya_client):
    page = riya_client.get("/tracking/").get_data(as_text=True)
    assert "cdnjs.cloudflare.com/ajax/libs/Chart.js/" in page
    assert 'integrity="sha512-' in page
