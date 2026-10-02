"""Daily planner: the card-choosing rules (no database) and Home (with the database)."""

import html
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.engine import Candidate
from app.models import Card, Deck, Progress, Review, User, UserDeck, db, today_local, utc_now
from app.planner import NO_LIMIT_CARD_CAP, card_limit, choose_plan_cards

TODAY = date(2026, 10, 6)


# ---------- Helpers for the pure tests ----------

def candidate(card_id, deck_id=1, topic="Topic", importance="medium", shelf=1,
              overdue=0, mode="normal", exam_in=None, new=False):
    card = SimpleNamespace(id=card_id, deck_id=deck_id, topic=topic, importance=importance)
    progress = None if new else SimpleNamespace(
        shelf=shelf, next_due=TODAY - timedelta(days=overdue), wrong_count=0,
        misconception_count=0, last_seen=None,
    )
    exam_date = TODAY + timedelta(days=exam_in) if exam_in is not None else None
    return Candidate(card, progress, mode, exam_date)


def ids(choice):
    return [c.card.id for c in choice.cards]


# ---------- Session length options ----------

@pytest.mark.parametrize("minutes, cards", [(15, 30), (25, 50), (40, 80), (None, NO_LIMIT_CARD_CAP)])
def test_card_limit_is_30_seconds_a_card(minutes, cards):
    assert card_limit(minutes) == cards


@pytest.mark.parametrize("minutes, planned", [(15, 30), (25, 50), (40, 80)])
def test_each_time_option_fills_its_cards_and_moves_the_rest(minutes, planned):
    due = [candidate(i) for i in range(200)]
    choice = choose_plan_cards(due, [], minutes, new_allowance=10, today=TODAY)
    assert len(choice.cards) == planned
    assert choice.moved_to_tomorrow == 200 - planned


def test_no_limit_takes_all_due_cards_plus_up_to_10_new():
    due = [candidate(i) for i in range(60)]
    new = [candidate(1000 + i, new=True) for i in range(20)]
    choice = choose_plan_cards(due, new, None, new_allowance=10, today=TODAY)
    assert len(choice.cards) == 70
    assert choice.moved_to_tomorrow == 0


def test_no_limit_still_stops_at_the_safety_cap():
    due = [candidate(i) for i in range(NO_LIMIT_CARD_CAP + 20)]
    choice = choose_plan_cards(due, [], None, new_allowance=10, today=TODAY)
    assert len(choice.cards) == NO_LIMIT_CARD_CAP
    assert choice.moved_to_tomorrow == 20


def test_cards_that_are_not_due_stay_out():
    due = [candidate(1), candidate(2, overdue=-3)]  # card 2 is due in 3 days
    assert ids(choose_plan_cards(due, [], 25, 10, TODAY)) == [1]


# ---------- Order ----------

def test_order_is_weak_topic_then_priority_then_new():
    due = [
        candidate(1, topic="Easy", importance="low", shelf=4),     # score 4 + 2 = 6
        candidate(2, topic="Easy", importance="high", shelf=1),    # score 10 + 6 = 16
        candidate(3, topic="Weak", importance="low", shelf=3),     # score 6 + 2 = 8, but weak topic
    ]
    new = [candidate(4, new=True)]
    choice = choose_plan_cards(due, new, 25, 10, TODAY, weak_topic=(1, "Weak"))
    assert ids(choice) == [3, 2, 1, 4]


def test_weak_topic_must_match_deck_and_topic_name():
    due = [candidate(1, deck_id=2, topic="Weak", importance="low"), candidate(2, importance="high")]
    choice = choose_plan_cards(due, [], 25, 10, TODAY, weak_topic=(1, "Weak"))
    assert ids(choice) == [2, 1]  # same name in another deck is not the weak topic


def test_fixer_cards_in_weak_spot_deck_count_as_weak_topic():
    due = [candidate(1, importance="high"), candidate(2, deck_id=99, topic="Weak", importance="low")]
    choice = choose_plan_cards(due, [], 25, 10, TODAY, weak_topic=(1, "Weak"), weak_spot_deck_id=99)
    assert ids(choice) == [2, 1]


def test_new_cards_respect_the_daily_allowance():
    new = [candidate(i, new=True) for i in range(20)]
    assert len(choose_plan_cards([], new, 25, new_allowance=4, today=TODAY).cards) == 4
    assert choose_plan_cards([], new, 25, new_allowance=0, today=TODAY).cards == []


def test_new_cards_from_exam_decks_come_first():
    new = [
        candidate(1, importance="high", new=True),                                 # normal deck
        candidate(2, deck_id=2, importance="low", mode="exam", exam_in=40, new=True),  # exam deck
    ]
    assert ids(choose_plan_cards([], new, 25, 1, TODAY)) == [2]


def test_new_cards_only_fill_the_room_left():
    due = [candidate(i) for i in range(48)]
    new = [candidate(100 + i, new=True) for i in range(10)]
    choice = choose_plan_cards(due, new, 25, 10, TODAY)
    assert len(choice.cards) == 50
    assert ids(choice)[-2:] == [100, 101]


# ---------- Overflow ----------

def test_overflow_keeps_exam_decks_first_and_moves_the_rest():
    # Normal-deck cards score higher (16) than exam-deck cards (4 + 2 + 1 = 7),
    # but when the plan overflows, Exam-mode decks keep their place.
    normal = [candidate(i, importance="high", shelf=1) for i in range(40)]
    exam = [candidate(100 + i, deck_id=2, importance="low", shelf=4, mode="exam", exam_in=30) for i in range(20)]
    choice = choose_plan_cards(normal + exam, [], 15, 10, TODAY)
    assert ids(choice)[:20] == [100 + i for i in range(20)]
    assert len(choice.cards) == 30
    assert choice.moved_to_tomorrow == 30


def test_without_overflow_priority_order_stays():
    normal = [candidate(1, importance="high", shelf=1)]
    exam = [candidate(2, deck_id=2, importance="low", shelf=4, mode="exam", exam_in=30)]
    assert ids(choose_plan_cards(normal + exam, [], 25, 10, TODAY)) == [1, 2]


def test_weak_topic_cards_stay_first_even_when_overflowing():
    weak = [candidate(i, topic="Weak", importance="low", shelf=4) for i in range(5)]
    exam = [candidate(100 + i, deck_id=2, mode="exam", exam_in=2) for i in range(40)]
    choice = choose_plan_cards(weak + exam, [], 15, 10, TODAY, weak_topic=(1, "Weak"))
    assert ids(choice)[:5] == [0, 1, 2, 3, 4]


# ---------- Home (with the database) ----------

@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


def add_deck(app, user_id, cards=4, mode="normal", exam_in=None, title="Biology", topic="Cells"):
    """A ready deck in the student's list. Returns [card_ids]."""
    with app.app_context():
        deck = Deck(title=title, folder="semester", is_ready=True)
        db.session.add(deck)
        for number in range(cards):
            deck.cards.append(Card(question=f"{title} Q{number}", answer="A", topic=topic))
        db.session.flush()
        exam_date = today_local() + timedelta(days=exam_in) if exam_in is not None else None
        db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode=mode, exam_date=exam_date))
        db.session.commit()
        return [card.id for card in deck.cards]


def set_progress(app, user_id, card_id, shelf=2, due_in_days=0, hidden=False):
    with app.app_context():
        db.session.add(Progress(user_id=user_id, card_id=card_id, shelf=shelf,
                                next_due=today_local() + timedelta(days=due_in_days),
                                wrong_count=0, misconception_count=0, last_seen=utc_now(), hidden=hidden))
        db.session.commit()


def page_text(client, url="/home"):
    """The page as plain text: HTML entities decoded, spaces squeezed."""
    return " ".join(html.unescape(client.get(url).get_data(as_text=True)).split())


def plan_in_session(client):
    client.get("/home")
    with client.session_transaction() as state:
        return state["plan"]


def set_user(app, user_id, **fields):
    with app.app_context():
        user = db.session.get(User, user_id)
        for name, value in fields.items():
            setattr(user, name, value)
        db.session.commit()


def test_home_shows_plan_grouped_with_minutes(app, riya_client, riya):
    add_deck(app, riya, cards=4)
    page = page_text(riya_client)
    assert "Today's plan" in page
    assert "Biology · Cells" in page
    assert "4 cards · 2 min" in page
    assert "Start today's plan" in page


def test_empty_plan_says_what_to_do(riya_client):
    page = page_text(riya_client)
    assert "Nothing to study today" in page
    assert "Start today's plan" not in page


def test_plan_is_built_once_a_day(app, riya_client, riya):
    add_deck(app, riya, cards=3)
    first = plan_in_session(riya_client)
    add_deck(app, riya, cards=3, title="Physics")
    assert plan_in_session(riya_client)["card_ids"] == first["card_ids"]


def test_changing_the_length_rebuilds_the_plan(app, riya_client, riya):
    card_ids = add_deck(app, riya, cards=40)
    for card_id in card_ids:
        set_progress(app, riya, card_id)
    assert len(plan_in_session(riya_client)["card_ids"]) == 40

    riya_client.post("/plan/length", data={"minutes": "15"})
    plan = plan_in_session(riya_client)
    assert (plan["minutes"], len(plan["card_ids"]), plan["moved"]) == (15, 30, 10)
    assert "10 cards moved to tomorrow." in riya_client.get("/home").get_data(as_text=True)

    riya_client.post("/plan/length", data={"minutes": "none"})
    assert plan_in_session(riya_client)["minutes"] is None


def test_hidden_cards_are_left_out_of_the_plan(app, riya_client, riya):
    card_ids = add_deck(app, riya, cards=3)
    set_progress(app, riya, card_ids[0], hidden=True)
    assert card_ids[0] not in plan_in_session(riya_client)["card_ids"]


def test_plan_runs_expire_past_exams_first(app, riya_client, riya):
    add_deck(app, riya, mode="exam", exam_in=-1)
    plan_in_session(riya_client)
    with app.app_context():
        assert db.session.query(UserDeck).filter_by(user_id=riya).filter(UserDeck.mode == "exam").count() == 0


def answer_everything(client):
    """Answer every card the study session shows. Returns the ids shown."""
    shown = []
    while True:
        client.get("/study/card")
        with client.session_transaction() as state:
            card_id = state["study"]["current"]
        if card_id is None:
            return shown
        shown.append(card_id)
        client.post("/study/answer", data={"card_id": card_id, "confident": "sure", "knew_it": "1"})


def test_start_plan_studies_exactly_the_plan_cards(app, riya_client, riya):
    add_deck(app, riya, cards=5)
    other = add_deck(app, riya, cards=3, title="Physics")
    for card_id in other:
        set_progress(app, riya, card_id, due_in_days=4)  # not due: not in the plan
    plan = plan_in_session(riya_client)

    riya_client.get("/study/plan")
    shown = answer_everything(riya_client)
    assert sorted(shown) == sorted(plan["card_ids"])


def test_finishing_the_plan_gives_10_xp_once(app, riya_client, riya):
    add_deck(app, riya, cards=2)
    plan_in_session(riya_client)
    riya_client.get("/study/plan")
    answer_everything(riya_client)
    with app.app_context():
        assert db.session.get(User, riya).xp == 2 + 2 + 10
    with riya_client.session_transaction() as state:
        assert state["study"]["stats"]["plan_finished"] is True
    assert "Done for today" in page_text(riya_client)

    # A rebuilt plan on the same day doesn't pay out again.
    riya_client.post("/plan/length", data={"minutes": "40"})
    riya_client.get("/home")
    with app.app_context():
        assert db.session.get(User, riya).xp == 14


def test_plan_counts_reviews_from_deck_sessions_too(app, riya_client, riya):
    card_ids = add_deck(app, riya, cards=2)
    plan_in_session(riya_client)
    with app.app_context():
        deck_id = db.session.get(Card, card_ids[0]).deck_id
    riya_client.get(f"/study/{deck_id}")
    answer_everything(riya_client)
    riya_client.get("/home")
    with app.app_context():
        assert db.session.get(User, riya).xp == 14


def test_start_plan_with_nothing_left_goes_home(riya_client):
    response = riya_client.get("/study/plan")
    assert response.headers["Location"].endswith("/home")


def test_streak_shows_0_after_a_missed_day(app, riya_client, riya):
    set_user(app, riya, streak=6, last_study_date=today_local() - timedelta(days=2))
    assert "Streak: 0 days" in riya_client.get("/home").get_data(as_text=True)


def test_streak_shows_when_studied_yesterday(app, riya_client, riya):
    set_user(app, riya, streak=6, last_study_date=today_local() - timedelta(days=1))
    assert "Streak: 6 days" in riya_client.get("/home").get_data(as_text=True)


def test_welcome_back_after_a_long_break(app, riya_client, riya):
    card_ids = add_deck(app, riya, cards=3)
    for card_id in card_ids:
        set_progress(app, riya, card_id, due_in_days=-4)
    set_user(app, riya, streak=3, last_study_date=today_local() - timedelta(days=5))
    assert "Welcome back. 3 cards due." in riya_client.get("/home").get_data(as_text=True)


def test_no_welcome_back_after_a_short_break(app, riya_client, riya):
    card_ids = add_deck(app, riya, cards=3)
    for card_id in card_ids:
        set_progress(app, riya, card_id)
    set_user(app, riya, streak=3, last_study_date=today_local() - timedelta(days=1))
    assert "Welcome back" not in riya_client.get("/home").get_data(as_text=True)


def test_new_cards_studied_today_use_up_the_plans_allowance(app, riya_client, riya):
    card_ids = add_deck(app, riya, cards=15)
    with app.app_context():
        for card_id in card_ids[:10]:
            db.session.add(Review(user_id=riya, card_id=card_id, knew_it=True, confident=True,
                                  style="smart", reviewed_at=utc_now()))
            db.session.add(Progress(user_id=riya, card_id=card_id, shelf=2, next_due=today_local() + timedelta(days=1),
                                    wrong_count=0, misconception_count=0, last_seen=utc_now()))
        db.session.commit()
    assert plan_in_session(riya_client)["card_ids"] == []
