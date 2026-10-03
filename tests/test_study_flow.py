"""Create My Own's three screens with Back links, and the confidence-first study flow."""

from datetime import timedelta

import pytest

from app.models import Card, Deck, Progress, UserDeck, db, today_local
from app.study import shelf_tag


@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


def own_deck(app, user_id, cards=3):
    with app.app_context():
        deck = Deck(title="Bio", folder="personal", owner_id=user_id)
        db.session.add(deck)
        deck.cards.extend(Card(question=f"Q{n}", answer=f"Answer {n}", topic="Cells", owner_id=user_id)
                          for n in range(cards))
        db.session.flush()
        db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode="normal"))
        db.session.commit()
        return deck.id


# ---------- Create My Own ----------

@pytest.mark.parametrize("method, lands_on", [
    ("write", "/cards/new?back=options"), ("paste", "/maker/?deck_id="), ("upload", "/maker/?deck_id="),
])
def test_each_option_opens_its_own_screen(riya_client, method, lands_on):
    response = riya_client.post("/decks/new", data={"title": "Notes", "method": method, "has_exam": "no"})
    assert lands_on in response.headers["Location"]
    if method != "write":
        assert f"mode={method}" in response.headers["Location"]


def test_paste_screen_shows_only_paste(app, riya_client, riya):
    deck_id = own_deck(app, riya)
    page = riya_client.get(f"/maker/?deck_id={deck_id}&mode=paste").get_data(as_text=True)
    assert 'id="notes"' in page and "Quick split" in page and "Make cards with AI" in page
    assert 'id="files"' not in page and 'id="target"' not in page and "Type cards one by one" not in page
    assert f'href="/decks/new?deck_id={deck_id}">← Back</a>' in page


def test_upload_screen_shows_only_upload(app, riya_client, riya):
    deck_id = own_deck(app, riya)
    page = riya_client.get(f"/maker/?deck_id={deck_id}&mode=upload").get_data(as_text=True)
    assert 'id="files"' in page and "Make cards with AI" in page
    assert 'id="notes"' not in page and "Quick split" not in page


def test_write_screen_has_back_to_the_options(app, riya_client, riya):
    deck_id = own_deck(app, riya)
    page = riya_client.get(f"/decks/{deck_id}/cards/new?back=options").get_data(as_text=True)
    assert f'href="/decks/new?deck_id={deck_id}">← Back</a>' in page
    assert 'data-unsaved="You have unsaved cards. Leave anyway?"' in page


def test_back_shows_the_options_for_the_same_deck(app, riya_client, riya):
    deck_id = own_deck(app, riya)
    page = riya_client.get(f"/decks/new?deck_id={deck_id}").get_data(as_text=True)
    assert "Add cards" in page and 'name="title"' not in page  # no second deck is made
    for href in (f"/decks/{deck_id}/cards/new?back=options", f"mode=paste", f"mode=upload"):
        assert href in page


def test_options_for_someone_elses_deck_are_not_found(app, riya_client, make_user):
    other = make_user(name="Arjun", email="arjun@example.com")
    assert riya_client.get(f"/decks/new?deck_id={own_deck(app, other)}").status_code == 404


def test_drafts_page_warns_before_leaving(riya_client):
    page = riya_client.post("/maker/drafts", data={"target": "new", "new_title": "N", "has_exam": "no",
                                                   "action": "split", "notes": "A - b"}).get_data(as_text=True)
    assert 'data-dirty="true"' in page and "← Back" in page


# ---------- Study flow ----------

def test_card_starts_locked_with_the_confidence_question(app, riya_client, riya):
    riya_client.get(f"/study/{own_deck(app, riya)}")
    page = riya_client.get("/study/card").get_data(as_text=True)
    assert 'data-locked="true"' in page
    assert page.index('id="confident-button"') < page.index('id="flip-button"') < page.index('id="know-button"')


@pytest.mark.parametrize("knew_it, before, after, text", [
    (True, 2, 3, "Moved to Good"),
    (True, 2, 2, "Stays on Getting there"),
    (False, 4, 1, "Back to Learning"),
    (False, 1, 1, "Back to Learning"),
])
def test_shelf_tag(knew_it, before, after, text):
    assert shelf_tag(knew_it, before, after) == text


def answer_one(client, confident, knew_it):
    client.get("/study/card")
    with client.session_transaction() as state:
        card_id = state["study"]["current"]
    client.post("/study/answer", data={"card_id": card_id, "confident": confident, "knew_it": knew_it})
    return client.get("/study/card").get_data(as_text=True)


def test_tag_shows_once_after_a_smart_answer(app, riya_client, riya):
    riya_client.get(f"/study/{own_deck(app, riya)}")
    page = answer_one(riya_client, "sure", "1")
    assert 'id="answer-tag"' in page and "Moved to Getting there" in page
    assert 'id="answer-tag"' not in riya_client.get("/study/card").get_data(as_text=True)  # only once


def test_quick_revise_shows_no_tag_and_moves_nothing(app, riya_client, riya):
    deck_id = own_deck(app, riya)
    riya_client.get(f"/study/{deck_id}?style=free")
    page = answer_one(riya_client, "sure", "1")
    assert 'id="answer-tag"' not in page
    with app.app_context():
        assert db.session.query(Progress).count() == 0


# ---------- Today's plan: soft time bar ----------

def start_plan(client, minutes=None):
    if minutes:
        client.post("/plan/length", data={"minutes": minutes})
    client.get("/home")
    client.get("/study/plan")
    return client.get("/study/card").get_data(as_text=True)


def test_plan_session_has_the_time_bar(app, riya_client, riya):
    own_deck(app, riya)
    page = start_plan(riya_client, "15")
    assert 'id="plan-timer" data-minutes="15"' in page
    assert 'data-total="3" data-done="0"' in page and "Hide timer" in page
    assert "plantimer.js" in page


def test_no_limit_plan_has_no_minutes(app, riya_client, riya):
    own_deck(app, riya)
    assert 'id="plan-timer" data-minutes=""' in start_plan(riya_client, "none")


def test_cards_done_count_the_whole_plan(app, riya_client, riya):
    own_deck(app, riya)
    start_plan(riya_client)
    page = answer_one(riya_client, "sure", "1")
    assert 'data-total="3" data-done="1"' in page


@pytest.mark.parametrize("style", ["smart", "free"])
def test_no_time_bar_outside_todays_plan(app, riya_client, riya, style):
    riya_client.get(f"/study/{own_deck(app, riya)}?style={style}")
    assert 'id="plan-timer"' not in riya_client.get("/study/card").get_data(as_text=True)


def test_no_time_bar_when_revising_the_plan(app, riya_client, riya):
    own_deck(app, riya)
    start_plan(riya_client)
    riya_client.get("/study/revise")
    assert 'id="plan-timer"' not in riya_client.get("/study/card").get_data(as_text=True)
