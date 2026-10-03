"""Create My Own's three screens with Back links, and the confidence-first study flow."""

from datetime import timedelta

import pytest

from app.models import Card, Deck, Progress, UserDeck, db, today_local


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
    assert page.index('id="confident-button"') < page.index('id="flip-button"') < page.index('id="step-answer"')
    for text in [">Confident<", ">Not sure<", ">Got it right<", ">Got it wrong<", ">I was close<", ">I didn't know it<"]:
        assert text in page


def answer_one(client, confident, knew_it):
    client.get("/study/card")
    with client.session_transaction() as state:
        card_id = state["study"]["current"]
    client.post("/study/answer", data={"card_id": card_id, "confident": confident, "knew_it": knew_it})
    return client.get("/study/card").get_data(as_text=True)


@pytest.mark.parametrize("confident, knew_it, shelf, misconceptions", [
    ("sure", "1", 2, 0),    # Confident + Got it right: up a shelf
    ("sure", "0", 1, 1),    # Confident + Got it wrong: shelf 1 and a misconception
    ("unsure", "1", 1, 0),  # Not sure + I was close: stays
    ("unsure", "0", 1, 0),  # Not sure + I didn't know it: shelf 1
])
def test_each_answer_pair_reaches_the_engine_unchanged(app, riya_client, riya, confident, knew_it, shelf, misconceptions):
    riya_client.get(f"/study/{own_deck(app, riya)}")
    answer_one(riya_client, confident, knew_it)
    with app.app_context():
        progress = db.session.query(Progress).one()
        assert (progress.shelf, progress.misconception_count) == (shelf, misconceptions)


def test_moved_up_tag_only_when_the_card_can_move_up(app, riya_client, riya):
    deck_id = own_deck(app, riya, cards=1)
    riya_client.get(f"/study/{deck_id}")
    assert 'data-can-move-up="true"' in riya_client.get("/study/card").get_data(as_text=True)
    with app.app_context():
        card = db.session.query(Card).one()
        db.session.add(Progress(user_id=riya, card_id=card.id, shelf=5, next_due=today_local(),
                                wrong_count=0, misconception_count=0))
        db.session.commit()
    riya_client.get(f"/study/{deck_id}")
    assert 'data-can-move-up="false"' in riya_client.get("/study/card").get_data(as_text=True)


def test_quick_revise_moves_nothing_and_never_says_moved_up(app, riya_client, riya):
    deck_id = own_deck(app, riya)
    riya_client.get(f"/study/{deck_id}?style=free")
    page = answer_one(riya_client, "sure", "1")
    assert 'data-can-move-up="false"' in page
    with app.app_context():
        assert db.session.query(Progress).count() == 0


def test_end_session_and_back_ask_first(app, riya_client, riya):
    deck_id = own_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    page = riya_client.get("/study/card").get_data(as_text=True)
    assert "End this session? Your answers so far are saved." in page
    assert ">Keep studying<" in page and page.count("data-end-session") == 2
    assert f'href="/decks/{deck_id}" data-end-session' in page


def test_summary_shows_right_and_missed(app, riya_client, riya):
    riya_client.get(f"/study/{own_deck(app, riya)}")
    answer_one(riya_client, "sure", "1")
    answer_one(riya_client, "sure", "0")
    answer_one(riya_client, "unsure", "0")
    page = riya_client.get("/study/summary").get_data(as_text=True)
    order = ["Cards studied", "Got it right", "Missed", "Misconceptions", "XP earned", 'id="weak-spot"']
    positions = [page.index(text) for text in order]
    assert positions == sorted(positions)
    with riya_client.session_transaction() as state:
        stats = state["study"]["stats"]
    assert (stats["studied"], stats["right"], stats["missed"], stats["misconceptions"]) == (3, 1, 2, 1)


# ---------- Today's plan: no time bar any more ----------

def test_plan_session_has_no_time_bar(app, riya_client, riya):
    own_deck(app, riya)
    riya_client.get("/home")
    riya_client.get("/study/plan")
    page = riya_client.get("/study/card").get_data(as_text=True)
    assert "plan-timer" not in page and "plantimer.js" not in page
