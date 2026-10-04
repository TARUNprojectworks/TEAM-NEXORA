from datetime import timedelta

import pytest

from app import create_app
from app.models import Card, Deck, Progress, Review, User, UserDeck, db, today_local
from app.study import next_streak


# ---------- Helpers ----------

@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


def make_study_deck(app, user_id, importances=("medium", "medium", "medium"), mode="normal", exam_date=None):
    """An own deck in the student's list. Returns (deck_id, [card_ids])."""
    with app.app_context():
        deck = Deck(title="Biology", folder="personal", owner_id=user_id)
        db.session.add(deck)
        for number, importance in enumerate(importances):
            deck.cards.append(Card(question=f"Q{number}", answer=f"A{number}", topic="Cells", importance=importance))
        db.session.flush()
        db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode=mode, exam_date=exam_date))
        db.session.commit()
        return deck.id, [card.id for card in deck.cards]


def set_progress(app, user_id, card_id, shelf=1, due_in_days=0):
    with app.app_context():
        db.session.add(Progress(user_id=user_id, card_id=card_id, shelf=shelf,
                                next_due=today_local() + timedelta(days=due_in_days),
                                wrong_count=0, misconception_count=0))
        db.session.commit()


def current_card(client):
    """Open the study page and return the id of the card it shows (None when done)."""
    client.get("/study/card")
    with client.session_transaction() as state:
        return state["study"]["current"]


def answer(client, knew_it=True, sure=True):
    card_id = current_card(client)
    client.post("/study/answer", data={
        "card_id": card_id, "confident": "sure" if sure else "unsure", "knew_it": "1" if knew_it else "0",
    })
    return card_id


def session_stats(client):
    with client.session_transaction() as state:
        return state["study"]["stats"]


def get_progress(app, user_id, card_id):
    with app.app_context():
        return db.session.get(Progress, (user_id, card_id))


# ---------- Starting a session ----------

def test_ready_deck_must_be_added_before_studying(app, riya_client):
    with app.app_context():
        deck = Deck(title="Ready", folder="semester", is_ready=True)
        db.session.add(deck)
        db.session.commit()
        deck_id = deck.id
    response = riya_client.get(f"/study/{deck_id}")
    assert response.headers["Location"].endswith(f"/decks/{deck_id}")


def test_cannot_study_another_students_deck(app, riya_client, make_user):
    arjun = make_user(name="Arjun", email="arjun@example.com")
    deck_id, _ = make_study_deck(app, arjun)
    assert riya_client.get(f"/study/{deck_id}").status_code == 404


def test_past_exam_is_switched_to_normal_before_study(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya, mode="exam", exam_date=today_local() - timedelta(days=1))
    riya_client.get(f"/study/{deck_id}")
    with app.app_context():
        assert db.session.get(UserDeck, (riya, deck_id)).mode == "normal"


def test_deck_page_offers_both_study_styles(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    page = riya_client.get(f"/decks/{deck_id}").get_data(as_text=True)
    assert "Smart Study" in page and "Quick Revise" in page


def test_study_page_has_flip_confidence_and_progress(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    page = riya_client.get("/study/card").get_data(as_text=True)
    for text in ["Think of your answer first.", ">Confident<", "Not sure", "Flip card", "Got it right",
                 "Got it wrong", "I was close", "I didn't know it", "End session", "progressbar", "3 cards left"]:
        assert text in page


# ---------- Smart Study ----------

def test_smart_study_shows_the_most_important_card_first(app, riya_client, riya):
    deck_id, card_ids = make_study_deck(app, riya, importances=("low", "high", "medium"))
    riya_client.get(f"/study/{deck_id}")
    assert current_card(riya_client) == card_ids[1]


def test_know_it_sure_creates_progress_and_moves_up(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    card_id = answer(riya_client, knew_it=True, sure=True)

    progress = get_progress(app, riya, card_id)
    assert progress.shelf == 2
    assert progress.next_due == today_local() + timedelta(days=1)
    with app.app_context():
        review = db.session.query(Review).one()
        assert (review.knew_it, review.confident, review.style) == (True, True, "smart")


def test_review_again_sure_counts_a_misconception(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    card_id = answer(riya_client, knew_it=False, sure=True)
    progress = get_progress(app, riya, card_id)
    assert (progress.shelf, progress.wrong_count, progress.misconception_count) == (1, 1, 1)


def test_review_again_card_returns_after_three_other_cards(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya, importances=("medium",) * 5)
    riya_client.get(f"/study/{deck_id}")
    missed = answer(riya_client, knew_it=False, sure=False)
    others = [answer(riya_client) for _ in range(3)]
    assert missed not in others
    assert current_card(riya_client) == missed


def test_review_again_card_comes_next_when_fewer_than_three_are_left(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya, importances=("medium", "medium"))
    riya_client.get(f"/study/{deck_id}")
    missed = answer(riya_client, knew_it=False, sure=False)
    answer(riya_client)
    assert current_card(riya_client) == missed


def test_know_it_unsure_card_is_not_repeated_in_the_same_session(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya, importances=("medium",))
    riya_client.get(f"/study/{deck_id}")
    card_id = answer(riya_client, knew_it=True, sure=False)
    assert get_progress(app, riya, card_id).next_due == today_local()  # still due on shelf 1...
    assert current_card(riya_client) is None                           # ...but not again this session


def test_cards_that_are_not_due_are_skipped(app, riya_client, riya):
    deck_id, card_ids = make_study_deck(app, riya)
    set_progress(app, riya, card_ids[0], shelf=3, due_in_days=2)
    riya_client.get(f"/study/{deck_id}")
    shown = [answer(riya_client), answer(riya_client)]
    assert card_ids[0] not in shown
    assert current_card(riya_client) is None


def test_only_ten_new_cards_a_day(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya, importances=("medium",) * 12)
    riya_client.get(f"/study/{deck_id}")
    for _ in range(6):
        answer(riya_client)
    riya_client.get(f"/study/{deck_id}")  # a second session the same day shares the limit
    for _ in range(4):
        answer(riya_client)
    assert current_card(riya_client) is None
    with app.app_context():
        assert db.session.query(Progress).count() == 10


def test_exam_mode_caps_the_wait_before_the_exam(app, riya_client, riya):
    deck_id, card_ids = make_study_deck(app, riya, importances=("medium",), mode="exam",
                                        exam_date=today_local() + timedelta(days=2))
    set_progress(app, riya, card_ids[0], shelf=3)
    riya_client.get(f"/study/{deck_id}")
    answer(riya_client, knew_it=True, sure=True)
    progress = get_progress(app, riya, card_ids[0])
    assert progress.shelf == 4
    assert progress.next_due == today_local() + timedelta(days=1)  # not 7 days: the exam is in 2


def test_nothing_due_shows_a_clear_message(app, riya_client, riya):
    deck_id, card_ids = make_study_deck(app, riya, importances=("medium",))
    set_progress(app, riya, card_ids[0], shelf=2, due_in_days=1)
    riya_client.get(f"/study/{deck_id}")
    response = riya_client.get("/study/card", follow_redirects=True)
    assert "Nothing is due in this deck right now" in response.get_data(as_text=True)


# ---------- Free Practice ----------

def test_free_practice_shows_every_card_and_never_changes_shelves(app, riya_client, riya):
    deck_id, card_ids = make_study_deck(app, riya)
    set_progress(app, riya, card_ids[0], shelf=3, due_in_days=5)  # not due, still shown
    riya_client.get(f"/study/{deck_id}?style=free")
    shown = {answer(riya_client, knew_it=True, sure=True) for _ in range(3)}
    assert shown == set(card_ids)
    assert current_card(riya_client) is None

    with app.app_context():
        assert db.session.get(Progress, (riya, card_ids[0])).shelf == 3
        assert db.session.query(Progress).count() == 1  # no new progress rows
        assert {r.style for r in db.session.query(Review)} == {"free"}
        assert db.session.get(User, riya).xp == 6


def test_free_practice_review_again_also_comes_back(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya, importances=("medium", "medium"))
    riya_client.get(f"/study/{deck_id}?style=free")
    missed = answer(riya_client, knew_it=False, sure=False)
    answer(riya_client)
    assert current_card(riya_client) == missed


def test_shuffle_keeps_the_same_cards_left(app, riya_client, riya):
    deck_id, card_ids = make_study_deck(app, riya, importances=("medium",) * 6)
    riya_client.get(f"/study/{deck_id}?style=free")
    first = answer(riya_client)
    assert riya_client.post("/study/shuffle").status_code == 302
    rest = {answer(riya_client) for _ in range(5)}
    assert rest == set(card_ids) - {first}


# ---------- Answer safety ----------

def test_answer_for_a_card_not_on_screen_is_ignored(app, riya_client, riya):
    deck_id, card_ids = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    shown = current_card(riya_client)
    other = next(card_id for card_id in card_ids if card_id != shown)
    riya_client.post("/study/answer", data={"card_id": other, "confident": "sure", "knew_it": "1"})
    with app.app_context():
        assert db.session.query(Review).count() == 0


def test_answer_needs_sure_or_unsure(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    card_id = current_card(riya_client)
    response = riya_client.post("/study/answer", data={"card_id": card_id, "knew_it": "1"}, follow_redirects=True)
    assert "Pick Confident or Not sure first" in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.query(Review).count() == 0


def test_answer_rejected_without_csrf_token(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'csrf.db'}"})
    client = app.test_client()
    with client.session_transaction() as state:
        state["_user_id"] = "1"
    assert client.post("/study/answer", data={"card_id": 1}).status_code == 400


def test_deleted_deck_ends_the_session(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    with app.app_context():
        db.session.delete(db.session.get(Deck, deck_id))
        db.session.commit()
    response = riya_client.get("/study/card")
    assert response.headers["Location"].endswith("/decks/mine")


# ---------- XP, streak and summary ----------

def test_xp_is_2_for_know_it_and_1_for_review_again(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    answer(riya_client, knew_it=True)
    answer(riya_client, knew_it=False)
    with app.app_context():
        assert db.session.get(User, riya).xp == 3


def test_streak_goes_up_once_a_day(app, riya_client, riya):
    with app.app_context():
        user = db.session.get(User, riya)
        user.streak, user.last_study_date = 4, today_local() - timedelta(days=1)
        db.session.commit()
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    answer(riya_client)
    answer(riya_client)
    with app.app_context():
        user = db.session.get(User, riya)
        assert user.streak == 5
        assert user.last_study_date == today_local()


@pytest.mark.parametrize("last_day_offset, streak, expected", [
    (None, 0, 1),  # first day ever
    (0, 3, 3),     # already studied today
    (1, 3, 4),     # studied yesterday
    (2, 3, 1),     # missed a day
])
def test_next_streak(last_day_offset, streak, expected):
    today = today_local()
    last = None if last_day_offset is None else today - timedelta(days=last_day_offset)
    assert next_streak(streak, last, today) == expected


def test_summary_counts_the_session(app, riya_client, riya):
    deck_id, _ = make_study_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    answer(riya_client, knew_it=False, sure=True)   # misconception, +1 XP
    answer(riya_client, knew_it=True, sure=True)    # moves up, +2 XP
    answer(riya_client, knew_it=True, sure=False)   # stays, +2 XP
    answer(riya_client, knew_it=True, sure=True)    # the missed card again: moves up, +2 XP
    assert session_stats(riya_client) == {"studied": 4, "right": 3, "missed": 1, "moved_up": 2, "xp": 7,
                                          "misconceptions": 1, "plan_finished": False}

    page = riya_client.get("/study/summary").get_data(as_text=True)
    for text in ["Cards studied", "Got it right", "Missed", "Misconceptions", "XP earned", 'id="weak-spot"']:
        assert text in page
