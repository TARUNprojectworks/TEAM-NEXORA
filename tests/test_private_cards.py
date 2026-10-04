"""cards.owner_id: private cards in ready decks are seen, studied and changed only by their owner."""


import pytest

from app.models import Card, Deck, User, UserDeck, db


@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def arjun(make_user):
    return make_user(name="Arjun", email="arjun@example.com")


def login(client, email="riya@example.com"):
    client.post("/login", data={"email": email, "password": "password123"})
    return client


@pytest.fixture
def riya_client(client, riya):
    return login(client)


@pytest.fixture
def arjun_client(app, arjun):
    return login(app.test_client(), "arjun@example.com")


@pytest.fixture
def ready(app, riya, arjun):
    """A ready deck with 2 shared cards, in both students' lists. Returns (deck_id, [shared card ids])."""
    with app.app_context():
        deck = Deck(title="Cell Biology", folder="semester", is_ready=True)
        db.session.add(deck)
        deck.cards.extend([Card(question="Shared Q1", answer="A", topic="Cells"),
                           Card(question="Shared Q2", answer="A", topic="Cells")])
        db.session.flush()
        for user_id in (riya, arjun):
            db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode="normal"))
        db.session.commit()
        return deck.id, [card.id for card in deck.cards]


def add_private_card(client, deck_id, question="Riya's own Q"):
    return client.post(f"/decks/{deck_id}/cards/new", data={
        "question": question, "answer": "Her answer", "topic": "Cells", "importance": "high",
    })


def riya_private_card_id(app, riya):
    with app.app_context():
        return db.session.query(Card.id).filter_by(owner_id=riya).scalar()


def test_student_can_add_a_private_card_to_a_ready_deck(app, riya_client, riya, ready):
    deck_id, _ = ready
    assert add_private_card(riya_client, deck_id).status_code == 302
    with app.app_context():
        card = db.session.query(Card).filter_by(question="Riya's own Q").one()
        assert card.owner_id == riya
        assert card.deck_id == deck_id
    page = riya_client.get(f"/decks/{deck_id}/edit").get_data(as_text=True)
    assert "Riya&#39;s own Q" in page
    assert "Your card" in page


def test_ready_deck_must_be_in_the_list_to_add_cards(app, riya_client):
    with app.app_context():
        deck = Deck(title="Not added", folder="semester", is_ready=True)
        db.session.add(deck)
        db.session.commit()
        deck_id = deck.id
    assert add_private_card(riya_client, deck_id).status_code == 403


def test_other_students_never_see_a_private_card(app, riya_client, arjun_client, riya, ready):
    deck_id, _ = ready
    add_private_card(riya_client, deck_id)
    page = arjun_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert "own Q" not in page
    assert "2 cards" in " ".join(page.split())
    mine = " ".join(arjun_client.get("/decks/mine").get_data(as_text=True).split())
    assert "Cell Biology" in mine and "2 cards" in mine


def test_my_decks_counts_my_private_cards(riya_client, ready):
    deck_id, _ = ready
    add_private_card(riya_client, deck_id)
    assert "3 cards" in " ".join(riya_client.get("/decks/mine").get_data(as_text=True).split())


def test_other_students_cannot_open_edit_or_delete_a_private_card(app, riya_client, arjun_client, riya, ready):
    deck_id, _ = ready
    add_private_card(riya_client, deck_id)
    card_id = riya_private_card_id(app, riya)
    data = {"question": "Changed", "answer": "A", "importance": "medium"}
    assert arjun_client.get(f"/decks/{deck_id}/cards/{card_id}/edit").status_code == 404
    assert arjun_client.post(f"/decks/{deck_id}/cards/{card_id}/edit", data=data).status_code == 404
    assert arjun_client.post(f"/decks/{deck_id}/cards/{card_id}/delete").status_code == 404
    assert arjun_client.post(f"/study/explain/{card_id}").status_code == 404
    with app.app_context():
        assert db.session.get(Card, card_id).question == "Riya's own Q"


def test_owner_can_edit_and_delete_their_private_card(app, riya_client, riya, ready):
    deck_id, _ = ready
    add_private_card(riya_client, deck_id)
    card_id = riya_private_card_id(app, riya)
    riya_client.post(f"/decks/{deck_id}/cards/{card_id}/edit",
                     data={"question": "Edited", "answer": "A", "importance": "low"})
    with app.app_context():
        assert db.session.get(Card, card_id).question == "Edited"
    riya_client.post(f"/decks/{deck_id}/cards/{card_id}/delete")
    with app.app_context():
        assert db.session.get(Card, card_id) is None


def test_shared_cards_stay_read_only_and_private_cards_cannot_be_hidden(app, riya_client, riya, ready):
    deck_id, shared_ids = ready
    add_private_card(riya_client, deck_id)
    card_id = riya_private_card_id(app, riya)
    data = {"question": "Changed", "answer": "A", "importance": "medium"}
    assert riya_client.post(f"/decks/{deck_id}/cards/{shared_ids[0]}/edit", data=data).status_code == 403
    assert riya_client.post(f"/decks/{deck_id}/cards/{shared_ids[0]}/delete").status_code == 403
    assert riya_client.post(f"/decks/{deck_id}/cards/{card_id}/hide").status_code == 403


def test_cards_in_own_decks_belong_to_their_owner(app, riya_client, riya):
    riya_client.post("/decks/new", data={"title": "My Notes", "method": "write", "has_exam": "no"})
    with app.app_context():
        deck_id = db.session.query(Deck.id).filter_by(title="My Notes").scalar()
    add_private_card(riya_client, deck_id, question="Own deck Q")
    with app.app_context():
        assert db.session.query(Card).filter_by(question="Own deck Q").one().owner_id == riya


def studied(client, deck_id, style="smart"):
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


def test_private_cards_are_studied_only_by_their_owner(app, riya_client, arjun_client, riya, ready):
    deck_id, shared_ids = ready
    add_private_card(riya_client, deck_id)
    card_id = riya_private_card_id(app, riya)
    assert card_id in studied(riya_client, deck_id)
    assert sorted(studied(arjun_client, deck_id, "free")) == sorted(shared_ids)


def test_private_cards_are_only_in_the_owners_plan(app, riya_client, arjun_client, riya, ready):
    deck_id, _ = ready
    add_private_card(riya_client, deck_id)
    card_id = riya_private_card_id(app, riya)
    arjun_client.get("/home")
    with arjun_client.session_transaction() as state:
        assert card_id not in state["plan"]["card_ids"]
    riya_client.get("/home")
    with riya_client.session_transaction() as state:
        assert card_id in state["plan"]["card_ids"]


def test_mastery_counts_only_cards_the_student_can_see(app, riya_client, arjun_client, riya, ready):
    deck_id, _ = ready
    add_private_card(riya_client, deck_id)
    cells = arjun_client.get("/tracking/data").get_json()["mastery"][0]
    assert cells["total"] == 2
    cells = riya_client.get("/tracking/data").get_json()["mastery"][0]
    assert cells["total"] == 3


def test_deleting_a_student_deletes_their_private_cards(app, riya_client, riya, ready):
    deck_id, _ = ready
    add_private_card(riya_client, deck_id)
    with app.app_context():
        db.session.delete(db.session.get(User, riya))
        db.session.commit()
        assert db.session.query(Card).filter_by(deck_id=deck_id).count() == 2
