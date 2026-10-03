"""Card and deck actions: edit/delete on own decks, hide/unhide on ready decks,
and the buttons on the My Decks list."""

import pytest

from app.models import Card, Deck, Progress, UserDeck, db, hidden_card_ids, today_local


# ---------- Helpers ----------

@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


def make_deck(app, user_id, is_ready, cards=3, in_list=True, title="Deck"):
    with app.app_context():
        deck = Deck(title=title, folder="semester" if is_ready else "personal",
                    is_ready=is_ready, owner_id=None if is_ready else user_id)
        db.session.add(deck)
        for number in range(cards):
            deck.cards.append(Card(question=f"Q{number}", answer=f"A{number}", topic="Cells"))
        db.session.flush()
        if in_list:
            db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode="normal"))
        db.session.commit()
        return deck.id, [card.id for card in deck.cards]


def hide(client, deck_id, card_id):
    return client.post(f"/decks/{deck_id}/cards/{card_id}/hide")


def get_progress(app, user_id, card_id):
    with app.app_context():
        return db.session.get(Progress, (user_id, card_id))


def studied_cards(client, deck_id, style):
    """Answer every card in a session and return the ids that were shown."""
    client.get(f"/study/{deck_id}?style={style}")
    shown = []
    while True:
        client.get("/study/card")
        with client.session_transaction() as state:
            card_id = state["study"]["current"]
        if card_id is None:
            return shown
        shown.append(card_id)
        client.post("/study/answer", data={"card_id": card_id, "confident": "sure", "knew_it": "1"})


# ---------- Own decks: edit and delete ----------

def test_every_own_card_row_has_edit_and_delete_with_confirm(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=False)
    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    for card_id in card_ids:
        assert f"/decks/{deck_id}/cards/{card_id}/edit" in page
        assert f"/decks/{deck_id}/cards/{card_id}/delete" in page
    assert page.count('data-confirm="Delete this card?') == len(card_ids)
    assert "Hide this card" not in page


def test_my_decks_buttons_depend_on_the_kind_of_deck(app, riya_client, riya):
    own_id, _ = make_deck(app, riya, is_ready=False, title="My Notes")
    ready_id, _ = make_deck(app, riya, is_ready=True, title="Cell Biology")
    with app.app_context():
        weak_id = db.session.query(Deck.id).filter_by(owner_id=riya, is_weak_spot=True).scalar()

    page = riya_client.get("/decks/mine").get_data(as_text=True)
    assert f'action="/decks/{own_id}/delete"' in page
    assert f'action="/decks/{ready_id}/remove"' in page
    assert f'action="/decks/{ready_id}/delete"' not in page
    assert f'action="/decks/{weak_id}/delete"' not in page
    assert f'action="/decks/{weak_id}/remove"' not in page


def test_delete_deck_from_my_decks_returns_to_my_decks(app, riya_client, riya):
    own_id, _ = make_deck(app, riya, is_ready=False)
    response = riya_client.post(f"/decks/{own_id}/delete")
    assert response.headers["Location"].endswith("/decks/mine")
    with app.app_context():
        assert db.session.get(Deck, own_id) is None


# ---------- Ready decks: hide and unhide ----------

def test_ready_card_rows_offer_hide_not_delete(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert page.count('aria-label="Hide this card"') == len(card_ids)
    for card_id in card_ids:
        assert f"/cards/{card_id}/edit" not in page
        assert f"/cards/{card_id}/delete" not in page


def test_hiding_a_never_seen_card_creates_a_hidden_progress_row(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    hide(riya_client, deck_id, card_ids[0])
    progress = get_progress(app, riya, card_ids[0])
    assert progress.hidden is True
    assert progress.last_seen is None


def test_hidden_card_moves_out_of_the_list(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    hide(riya_client, deck_id, card_ids[0])

    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert "Show hidden cards (1)" in page
    assert '<p class="card-front">Q0</p>' not in page  # not "Q0": it can turn up inside the CSRF token
    assert "2 cards · 1 hidden" in " ".join(page.split())

    page = riya_client.get(f"/decks/{deck_id}?show_hidden=1").get_data(as_text=True)
    assert "Hidden cards (1)" in page
    assert '<p class="card-front">Q0</p>' in page
    assert "Show this card again" in page


def test_my_decks_count_leaves_out_hidden_cards(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    hide(riya_client, deck_id, card_ids[0])
    page = " ".join(riya_client.get("/decks/mine").get_data(as_text=True).split())
    assert "2 cards · 1 hidden" in page


def test_hiding_is_only_for_that_student(app, riya_client, riya, make_user):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    hide(riya_client, deck_id, card_ids[0])
    arjun = make_user(name="Arjun", email="arjun@example.com")
    with app.app_context():
        assert hidden_card_ids(arjun, deck_id) == set()
        assert hidden_card_ids(riya, deck_id) == {card_ids[0]}


def test_unhiding_a_never_seen_card_makes_it_new_again(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    hide(riya_client, deck_id, card_ids[0])
    riya_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/unhide")
    assert get_progress(app, riya, card_ids[0]) is None


def test_unhiding_a_studied_card_keeps_its_shelf(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    with app.app_context():
        db.session.add(Progress(user_id=riya, card_id=card_ids[0], shelf=4, next_due=today_local(),
                                wrong_count=0, misconception_count=0, last_seen=today_local()))
        db.session.commit()
    hide(riya_client, deck_id, card_ids[0])
    riya_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/unhide")
    progress = get_progress(app, riya, card_ids[0])
    assert (progress.hidden, progress.shelf) == (False, 4)


def test_hidden_cards_are_left_out_of_the_card_box(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    with app.app_context():
        db.session.add(Progress(user_id=riya, card_id=card_ids[0], shelf=3, next_due=today_local(),
                                wrong_count=0, misconception_count=0, last_seen=today_local()))
        db.session.commit()
    hide(riya_client, deck_id, card_ids[0])
    hide(riya_client, deck_id, card_ids[1])
    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert "Not studied yet: 1" in page
    assert '<span class="shelf-count">1</span>' not in page  # shelf 3 no longer counts the hidden card


def test_cannot_hide_cards_in_own_deck(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=False)
    assert hide(riya_client, deck_id, card_ids[0]).status_code == 403


def test_ready_deck_must_be_in_my_list_to_hide(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True, in_list=False)
    assert hide(riya_client, deck_id, card_ids[0]).status_code == 404


def test_cannot_hide_a_card_from_another_deck(app, riya_client, riya):
    deck_id, _ = make_deck(app, riya, is_ready=True)
    _, other_card_ids = make_deck(app, riya, is_ready=True, title="Other")
    assert hide(riya_client, deck_id, other_card_ids[0]).status_code == 404


# ---------- Hidden cards are skipped in study ----------

def test_smart_study_skips_hidden_cards(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    hide(riya_client, deck_id, card_ids[1])
    assert sorted(studied_cards(riya_client, deck_id, "smart")) == [card_ids[0], card_ids[2]]


def test_free_practice_skips_hidden_cards(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    hide(riya_client, deck_id, card_ids[1])
    assert sorted(studied_cards(riya_client, deck_id, "free")) == [card_ids[0], card_ids[2]]


def test_card_hidden_mid_session_is_not_shown(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya, is_ready=True)
    riya_client.get(f"/study/{deck_id}")
    riya_client.get("/study/card")
    with riya_client.session_transaction() as state:
        on_screen = state["study"]["current"]
    hide(riya_client, deck_id, on_screen)

    riya_client.get("/study/card")
    with riya_client.session_transaction() as state:
        assert state["study"]["current"] != on_screen
