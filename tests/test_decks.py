from datetime import timedelta

import pytest

from app import create_app
from app.models import Card, Deck, Progress, Review, UserDeck, db, today_local


# ---------- Helpers ----------

def login(client, email="riya@example.com", password="password123"):
    client.post("/login", data={"email": email, "password": password})
    return client


def make_deck(app, title="Cell Biology", folder="semester", is_ready=True, owner_id=None, cards=3):
    """Create a deck with a few cards. Returns (deck_id, [card_ids])."""
    with app.app_context():
        deck = Deck(title=title, folder=folder, is_ready=is_ready, owner_id=owner_id)
        db.session.add(deck)
        for number in range(cards):
            deck.cards.append(Card(question=f"Q{number}", answer=f"A{number}", topic="Cells"))
        db.session.commit()
        return deck.id, [card.id for card in deck.cards]


def add_to_list(app, user_id, deck_id, mode="normal", exam_date=None):
    with app.app_context():
        db.session.add(UserDeck(user_id=user_id, deck_id=deck_id, mode=mode, exam_date=exam_date))
        db.session.commit()


def user_deck(app, user_id, deck_id):
    with app.app_context():
        return db.session.get(UserDeck, (user_id, deck_id))


@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def arjun(make_user):
    return make_user(name="Arjun", email="arjun@example.com")


@pytest.fixture
def riya_client(client, riya):
    return login(client)


@pytest.fixture
def arjun_client(app, arjun):
    return login(app.test_client(), "arjun@example.com")


@pytest.fixture
def ready_deck(app):
    return make_deck(app)


@pytest.fixture
def riya_deck(app, riya):
    deck_id, card_ids = make_deck(app, title="My Notes", folder="personal", is_ready=False, owner_id=riya)
    add_to_list(app, riya, deck_id)
    return deck_id, card_ids


# ---------- Ready decks ----------

def test_ready_page_lists_decks_in_chosen_folder(app, riya_client, ready_deck, riya):
    make_deck(app, title="DSA Basics", folder="placement")
    make_deck(app, title="Riya's private deck", folder="personal", is_ready=False, owner_id=riya)

    page = riya_client.get("/decks/ready?folder=semester").get_data(as_text=True)
    assert "Cell Biology" in page
    assert "DSA Basics" not in page
    assert "private deck" not in page
    assert ">Add</a>" in page


def test_deck_library_opens_the_first_folder(riya_client, ready_deck):
    page = riya_client.get("/decks/ready").get_data(as_text=True)
    assert "Deck Library" in page
    assert "Cell Biology" in page  # a Semester deck: the first folder is open
    assert "Semester" in page and "Placement" in page and "Competitive" in page


def test_add_ready_deck_in_normal_mode(app, riya_client, ready_deck, riya):
    deck_id, _ = ready_deck
    response = riya_client.post(f"/decks/ready/{deck_id}/add", data={"has_exam": "no"})
    assert response.status_code == 302
    added = user_deck(app, riya, deck_id)
    assert added.mode == "normal"
    assert added.exam_date is None


def test_add_ready_deck_in_exam_mode(app, riya_client, ready_deck, riya):
    deck_id, _ = ready_deck
    exam_day = today_local() + timedelta(days=5)
    riya_client.post(f"/decks/ready/{deck_id}/add", data={"has_exam": "yes", "exam_date": exam_day.isoformat()})
    added = user_deck(app, riya, deck_id)
    assert added.mode == "exam"
    assert added.exam_date == exam_day


def test_exam_mode_needs_a_date(app, riya_client, ready_deck, riya):
    deck_id, _ = ready_deck
    response = riya_client.post(f"/decks/ready/{deck_id}/add", data={"has_exam": "yes", "exam_date": ""})
    assert "Pick the date of your exam" in response.get_data(as_text=True)
    assert user_deck(app, riya, deck_id) is None


def test_exam_date_cannot_be_in_the_past(app, riya_client, ready_deck, riya):
    deck_id, _ = ready_deck
    yesterday = today_local() - timedelta(days=1)
    response = riya_client.post(f"/decks/ready/{deck_id}/add", data={"has_exam": "yes", "exam_date": yesterday.isoformat()})
    assert "That date has passed" in response.get_data(as_text=True)
    assert user_deck(app, riya, deck_id) is None


def test_adding_the_same_deck_twice_keeps_one_entry(app, riya_client, ready_deck, riya):
    deck_id, _ = ready_deck
    riya_client.post(f"/decks/ready/{deck_id}/add", data={"has_exam": "no"})
    riya_client.post(f"/decks/ready/{deck_id}/add", data={"has_exam": "no"})
    with app.app_context():
        assert db.session.query(UserDeck).filter_by(user_id=riya, deck_id=deck_id).count() == 1


def test_cannot_add_someone_elses_deck_as_ready(app, riya_client, arjun):
    deck_id, _ = make_deck(app, title="Arjun's deck", is_ready=False, owner_id=arjun)
    assert riya_client.get(f"/decks/ready/{deck_id}/add").status_code == 404


def test_removing_ready_deck_keeps_progress(app, riya_client, ready_deck, riya):
    deck_id, card_ids = ready_deck
    add_to_list(app, riya, deck_id)
    with app.app_context():
        db.session.add(Progress(user_id=riya, card_id=card_ids[0], shelf=3))
        db.session.commit()

    riya_client.post(f"/decks/{deck_id}/remove")
    assert user_deck(app, riya, deck_id) is None
    with app.app_context():
        assert db.session.get(Progress, (riya, card_ids[0])).shelf == 3


# ---------- My decks ----------

def test_my_decks_shows_weak_spot_deck_and_badges(app, riya_client, ready_deck, riya):
    deck_id, _ = ready_deck
    add_to_list(app, riya, deck_id, mode="exam", exam_date=today_local() + timedelta(days=5))

    page = riya_client.get("/decks/mine").get_data(as_text=True)
    assert "Weak spot practice" in page
    assert "Exam in 5 days" in page
    assert "Normal" in page


@pytest.mark.parametrize("days, text", [(0, "Exam today"), (1, "Exam tomorrow"), (30, "Exam in 30 days")])
def test_exam_badge_wording(app, riya_client, ready_deck, riya, days, text):
    deck_id, _ = ready_deck
    add_to_list(app, riya, deck_id, mode="exam", exam_date=today_local() + timedelta(days=days))
    assert text in riya_client.get("/decks/mine").get_data(as_text=True)


def test_past_exam_switches_deck_to_normal(app, riya_client, ready_deck, riya):
    deck_id, _ = ready_deck
    add_to_list(app, riya, deck_id, mode="exam", exam_date=today_local() - timedelta(days=2))

    page = riya_client.get("/decks/mine").get_data(as_text=True)
    assert "Exam passed. Set a new date?" in page
    assert user_deck(app, riya, deck_id).mode == "normal"


def test_my_decks_hides_other_students_decks(app, riya_client, arjun):
    deck_id, _ = make_deck(app, title="Arjun's secret deck", is_ready=False, owner_id=arjun)
    add_to_list(app, arjun, deck_id)
    assert "secret deck" not in riya_client.get("/decks/mine").get_data(as_text=True)


# ---------- Create my own ----------

def test_create_my_own_then_type_cards(app, riya_client, riya):
    response = riya_client.post("/decks/new", data={"title": "Organic Chemistry", "method": "write", "has_exam": "no"})
    with app.app_context():
        deck = db.session.query(Deck).filter_by(title="Organic Chemistry").one()
        assert deck.owner_id == riya
        assert deck.folder == "personal"
        assert not deck.is_ready
        assert db.session.get(UserDeck, (riya, deck.id)).mode == "normal"
    assert response.headers["Location"].endswith(f"/decks/{deck.id}/cards/new?back=options")


def test_create_my_own_with_paste_goes_to_card_maker(app, riya_client):
    response = riya_client.post("/decks/new", data={"title": "History", "method": "paste", "has_exam": "no"})
    assert "/maker" in response.headers["Location"]


def test_create_my_own_needs_a_name(riya_client):
    response = riya_client.post("/decks/new", data={"title": "", "method": "write", "has_exam": "no"})
    assert "Give your deck a name" in response.get_data(as_text=True)


def test_create_page_offers_write_paste_upload_in_order(riya_client):
    page = riya_client.get("/decks/new").get_data(as_text=True)
    assert page.index("Write cards") < page.index("Paste notes") < page.index("Upload notes")


# ---------- Deck page ----------

def test_ready_deck_page_is_read_only(riya_client, ready_deck):
    deck_id, _ = ready_deck
    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert '<p class="card-front">Q0</p>' in page
    assert "Add card" not in page
    assert "/edit" not in page


def test_own_deck_page_has_card_controls(riya_client, riya_deck):
    deck_id, card_ids = riya_deck
    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert "Add card" in page
    assert f"/cards/{card_ids[0]}/edit" in page


def test_deck_page_shows_shelf_counts(app, riya_client, riya_deck, riya):
    deck_id, card_ids = riya_deck
    with app.app_context():
        db.session.add(Progress(user_id=riya, card_id=card_ids[0], shelf=5))
        db.session.commit()
    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert "Not studied yet: 2" in page


def test_other_students_deck_is_not_found(arjun_client, riya_deck):
    deck_id, _ = riya_deck
    assert arjun_client.get(f"/decks/{deck_id}").status_code == 404


def test_deck_pages_need_login(client, ready_deck):
    deck_id, _ = ready_deck
    for url in ["/decks/ready", "/decks/mine", "/decks/new", f"/decks/{deck_id}"]:
        assert client.get(url).status_code == 302


# ---------- Exam setting ----------

def test_change_and_remove_exam_date(app, riya_client, riya_deck, riya):
    deck_id, _ = riya_deck
    exam_day = today_local() + timedelta(days=10)
    riya_client.post(f"/decks/{deck_id}/exam", data={"has_exam": "yes", "exam_date": exam_day.isoformat()})
    assert user_deck(app, riya, deck_id).mode == "exam"

    riya_client.post(f"/decks/{deck_id}/exam", data={"has_exam": "no"})
    setting = user_deck(app, riya, deck_id)
    assert setting.mode == "normal"
    assert setting.exam_date is None


def test_exam_setting_needs_deck_in_my_list(riya_client, ready_deck):
    deck_id, _ = ready_deck
    assert riya_client.post(f"/decks/{deck_id}/exam", data={"has_exam": "no"}).status_code == 404


# ---------- Cards ----------

def test_add_card_with_empty_topic_uses_the_deck_name(app, riya_client, riya_deck):
    deck_id, _ = riya_deck
    response = riya_client.post(f"/decks/{deck_id}/cards/new", data={
        "question": "What is ATP?", "answer": "The cell's energy currency.", "topic": "", "importance": "high",
    })
    assert response.headers["Location"].endswith(f"/decks/{deck_id}")
    with app.app_context():
        card = db.session.query(Card).filter_by(question="What is ATP?").one()
        assert card.topic == "My Notes"
        assert card.importance == "high"
        assert card.source == "manual"


def test_save_and_add_next_returns_to_the_form(riya_client, riya_deck):
    deck_id, _ = riya_deck
    response = riya_client.post(f"/decks/{deck_id}/cards/new", data={
        "question": "Q", "answer": "A", "importance": "medium", "save_next": "1",
    })
    assert response.headers["Location"].endswith(f"/decks/{deck_id}/cards/new")


def test_long_card_still_saves(app, riya_client, riya_deck):
    deck_id, _ = riya_deck
    riya_client.post(f"/decks/{deck_id}/cards/new", data={
        "question": "x" * 250, "answer": "y" * 400, "importance": "medium",
    })
    with app.app_context():
        assert db.session.query(Card).filter_by(question="x" * 250).count() == 1


def test_card_form_shows_tip_and_existing_topics(riya_client, riya_deck):
    deck_id, _ = riya_deck
    page = riya_client.get(f"/decks/{deck_id}/cards/new").get_data(as_text=True)
    assert "Keep one idea per card." in page
    assert '<option value="Cells">' in page
    assert "Save &amp; add next" in page


def test_card_needs_front_and_back(riya_client, riya_deck):
    deck_id, _ = riya_deck
    response = riya_client.post(f"/decks/{deck_id}/cards/new", data={"question": "", "answer": "", "importance": "medium"})
    page = response.get_data(as_text=True)
    assert "Write something on the front." in page
    assert "Write something on the back." in page


def test_edit_card_updates_it_and_clears_cached_explanation(app, riya_client, riya_deck):
    deck_id, card_ids = riya_deck
    with app.app_context():
        db.session.get(Card, card_ids[0]).explanation = "Old explanation"
        db.session.commit()

    riya_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/edit", data={
        "question": "New Q", "answer": "New A", "topic": "Membranes", "importance": "low",
    })
    with app.app_context():
        card = db.session.get(Card, card_ids[0])
        assert (card.question, card.answer, card.topic, card.importance) == ("New Q", "New A", "Membranes", "low")
        assert card.explanation is None


def test_delete_card(app, riya_client, riya_deck):
    deck_id, card_ids = riya_deck
    riya_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/delete")
    with app.app_context():
        assert db.session.get(Card, card_ids[0]) is None


def test_ready_deck_cards_are_read_only(riya_client, ready_deck):
    deck_id, card_ids = ready_deck
    data = {"question": "Q", "answer": "A", "importance": "medium"}
    assert riya_client.get(f"/decks/{deck_id}/cards/new").status_code == 403
    assert riya_client.post(f"/decks/{deck_id}/cards/new", data=data).status_code == 403
    assert riya_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/edit", data=data).status_code == 403
    assert riya_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/delete").status_code == 403


def test_cannot_touch_another_students_cards(app, arjun_client, riya_deck):
    deck_id, card_ids = riya_deck
    data = {"question": "Hacked", "answer": "A", "importance": "medium"}
    assert arjun_client.post(f"/decks/{deck_id}/cards/new", data=data).status_code == 404
    assert arjun_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/edit", data=data).status_code == 404
    assert arjun_client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/delete").status_code == 404
    with app.app_context():
        assert db.session.get(Card, card_ids[0]).question == "Q0"


def test_card_from_another_deck_is_not_found(app, riya_client, riya_deck, arjun):
    my_deck_id, _ = riya_deck
    _, other_card_ids = make_deck(app, title="Arjun's", is_ready=False, owner_id=arjun)
    url = f"/decks/{my_deck_id}/cards/{other_card_ids[0]}/delete"
    assert riya_client.post(url).status_code == 404
    with app.app_context():
        assert db.session.get(Card, other_card_ids[0]) is not None


# ---------- Rename and delete decks ----------

def test_rename_own_deck(app, riya_client, riya_deck):
    deck_id, _ = riya_deck
    riya_client.post(f"/decks/{deck_id}/edit", data={"title": "Renamed"})
    with app.app_context():
        assert db.session.get(Deck, deck_id).title == "Renamed"


def test_delete_own_deck_removes_cards_progress_and_reviews(app, riya_client, riya_deck, riya):
    deck_id, card_ids = riya_deck
    with app.app_context():
        db.session.add(Progress(user_id=riya, card_id=card_ids[0]))
        db.session.add(Review(user_id=riya, card_id=card_ids[0], knew_it=True, confident=True))
        db.session.commit()

    riya_client.post(f"/decks/{deck_id}/delete")
    with app.app_context():
        assert db.session.get(Deck, deck_id) is None
        assert db.session.query(Card).filter_by(deck_id=deck_id).count() == 0
        assert db.session.query(Progress).count() == 0
        assert db.session.query(Review).count() == 0
        assert db.session.get(UserDeck, (riya, deck_id)) is None


def test_weak_spot_deck_cannot_be_renamed_or_deleted(app, riya_client, riya):
    with app.app_context():
        deck_id = db.session.query(Deck.id).filter_by(owner_id=riya, is_weak_spot=True).scalar()
    assert riya_client.post(f"/decks/{deck_id}/delete").status_code == 403
    assert riya_client.post(f"/decks/{deck_id}/edit", data={"title": "x"}).status_code == 403


def test_ready_deck_cannot_be_renamed_or_deleted(riya_client, ready_deck):
    deck_id, _ = ready_deck
    assert riya_client.post(f"/decks/{deck_id}/delete").status_code == 403
    assert riya_client.post(f"/decks/{deck_id}/edit", data={"title": "x"}).status_code == 403


def test_cannot_delete_another_students_deck(app, arjun_client, riya_deck):
    deck_id, _ = riya_deck
    assert arjun_client.post(f"/decks/{deck_id}/delete").status_code == 404
    with app.app_context():
        assert db.session.get(Deck, deck_id) is not None


# ---------- CSRF ----------

def test_deck_actions_reject_posts_without_csrf_token(tmp_path):
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'csrf.db'}"})
    deck_id, card_ids = make_deck(app, is_ready=False, owner_id=None)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
    assert client.post(f"/decks/{deck_id}/cards/{card_ids[0]}/delete").status_code == 400
    assert client.post("/decks/new", data={"title": "x"}).status_code == 400
