"""ai_service: stand-ins, checks on Gemini replies, logging, Explain and the Weak Spot Fixer."""

import logging
from types import SimpleNamespace

import pytest

from app import ai_service
from app.ai_service import AIError
from app.models import Card, Deck, Progress, User, UserDeck, db, today_local, utc_now


# ---------- Checking what Gemini sends back ----------

def test_clean_cards_fixes_missing_fields_and_drops_bad_ones():
    cards = ai_service.clean_cards([
        {"question": " Q1 ", "answer": "A1", "importance": "URGENT"},
        {"question": "", "answer": "no front"},
        "not a card",
        {"question": "Q2", "answer": "A2", "topic": "Cells", "importance": "high", "source_line": "line"},
    ])
    assert cards == [
        {"question": "Q1", "answer": "A1", "topic": "General", "importance": "medium", "source_line": ""},
        {"question": "Q2", "answer": "A2", "topic": "Cells", "importance": "high", "source_line": "line"},
    ]


@pytest.mark.parametrize("reply", [None, "text", [], [{"question": "no answer"}]])
def test_unreadable_replies_give_the_friendly_message(reply):
    with pytest.raises(AIError, match="Couldn't read that"):
        ai_service.clean_cards(reply)


def grounded_response(chunks, widget="<div>suggestions</div>"):
    metadata = SimpleNamespace(
        grounding_chunks=[SimpleNamespace(web=SimpleNamespace(**chunk)) for chunk in chunks],
        search_entry_point=SimpleNamespace(rendered_content=widget),
    )
    return SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=metadata)])


def test_grounding_links_come_from_metadata_without_repeats_or_unsafe_links():
    response = grounded_response([
        {"title": "Khan Academy", "uri": "https://example.org/a", "domain": "example.org"},
        {"title": "Repeat", "uri": "https://example.org/a", "domain": "example.org"},
        {"title": "Bad", "uri": "javascript:alert(1)", "domain": None},
        {"title": None, "uri": "https://video.example/b", "domain": "video.example"},
    ])
    assert ai_service.grounding_links(response) == [
        {"title": "Khan Academy", "uri": "https://example.org/a"},
        {"title": "video.example", "uri": "https://video.example/b"},
    ]
    assert ai_service.search_widget(response) == "<div>suggestions</div>"


def test_fallback_links_are_plain_search_urls():
    links = ai_service.fallback_links("Cell Division")
    assert [link["uri"].split("/")[2] for link in links] == ["www.youtube.com", "www.google.com", "en.wikipedia.org"]
    assert all("Cell+Division" in link["uri"] for link in links)


# ---------- Real-mode paths, with Gemini replaced by a stand-in ----------

@pytest.fixture
def ai_on(app):
    app.config["AI_ENABLED"] = True
    yield app
    app.config["AI_ENABLED"] = False


def test_real_make_cards_validates_gemini_output(ai_on, monkeypatch):
    monkeypatch.setattr(ai_service, "gemini_make_cards", lambda text, files: [
        {"question": "What is ATP?", "answer": "Energy currency.", "topic": "Cells", "importance": "high",
         "source_line": "ATP is the energy currency"}])
    with ai_on.app_context():
        assert ai_service.make_cards(text="notes")[0]["question"] == "What is ATP?"


def test_real_fix_weak_spot_needs_three_cards(ai_on, monkeypatch):
    monkeypatch.setattr(ai_service, "gemini_fix_weak_spot", lambda topic, cards: {
        "explanation": "Clear explanation.", "follow_up_cards": [{"question": "Q", "answer": "A"}]})
    with ai_on.app_context(), pytest.raises(AIError):
        ai_service.fix_weak_spot("Cells", [{"question": "Q", "answer": "A"}])


def test_real_find_resources_falls_back_when_grounding_fails(ai_on, monkeypatch):
    def broken(topic):
        raise RuntimeError("timeout")
    monkeypatch.setattr(ai_service, "gemini_find_resources", broken)
    with ai_on.app_context():
        result = ai_service.find_resources("Osmosis")
    assert result["search_widget_html"] is None
    assert len(result["links"]) == 3


def test_failed_gemini_call_falls_back_to_quick_split_in_the_maker(ai_on, monkeypatch, client, make_user):
    def broken(text, files):
        raise RuntimeError("Gemini timed out")
    monkeypatch.setattr(ai_service, "gemini_make_cards", broken)
    make_user()
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    page = client.post("/maker/drafts", data={"target": "new", "new_title": "Bio", "has_exam": "no",
                                              "action": "ai", "notes": "Osmosis - water\nATP - energy"}).get_data(as_text=True)
    assert "split your lines with Quick split" in page


# ---------- Stand-ins (AI off) ----------

def test_stand_in_make_cards_uses_the_notes(app):
    with app.app_context():
        cards = ai_service.make_cards(text="Osmosis is the movement of water. DNA is the genetic material.")
    assert [c["question"] for c in cards] == ["What is osmosis?", "What is DNA?"]
    assert cards[0]["source_line"] == "Osmosis is the movement of water."


def test_stand_in_returns_the_same_shapes_as_gemini(app):
    card = SimpleNamespace(question="What is ATP?", answer="Energy currency of the cell.")
    with app.app_context():
        assert isinstance(ai_service.explain_card(card), str)
        fix = ai_service.fix_weak_spot("Cells", [{"question": "Q1", "answer": "A1"}, {"question": "Q2", "answer": "A2"}])
        resources = ai_service.find_resources("Cells")
    assert set(fix) == {"explanation", "follow_up_cards"} and len(fix["follow_up_cards"]) == 3
    assert set(fix["follow_up_cards"][0]) == {"question", "answer", "topic", "importance", "source_line"}
    assert set(resources) == {"links", "search_widget_html"}


# ---------- Logging ----------

class Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_ai_calls_are_logged_without_content(app):
    collect = Collect()
    logging.getLogger("nexora").addHandler(collect)
    try:
        with app.app_context():
            ai_service.make_cards(text="Osmosis is the movement of water.")
    finally:
        logging.getLogger("nexora").removeHandler(collect)
    fields = [r.fields for r in collect.records if r.getMessage() == "ai_call"][0]
    assert fields["feature"] == "make_cards" and fields["ok"] is True and "latency_ms" in fields
    assert "Osmosis" not in str(fields)


# ---------- Explain ----------

@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


QUESTIONS = [
    ("Which organelle makes ATP?", "Mitochondria, through aerobic respiration."),
    ("What does the Golgi apparatus do?", "It packages and sorts proteins into vesicles."),
    ("Where is rRNA made?", "In the nucleolus."),
]


def make_deck(app, user_id, topics=("Cells", "Cells", "Cells"), title="Biology"):
    with app.app_context():
        deck = Deck(title=title, folder="semester", is_ready=True)
        db.session.add(deck)
        deck.cards.extend(Card(question=QUESTIONS[n][0], answer=QUESTIONS[n][1], topic=t)
                          for n, t in enumerate(topics))
        db.session.flush()
        db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode="normal"))
        db.session.commit()
        return deck.id, [card.id for card in deck.cards]


def ai_calls(app, user_id):
    with app.app_context():
        return db.session.get(User, user_id).ai_calls_today


def test_explain_is_cached_and_counts_once(app, riya_client, riya):
    _, card_ids = make_deck(app, riya)
    first = riya_client.post(f"/study/explain/{card_ids[0]}").get_json()
    second = riya_client.post(f"/study/explain/{card_ids[0]}").get_json()
    assert first["cached"] is False and second["cached"] is True
    assert first["explanation"] == second["explanation"]
    assert ai_calls(app, riya) == 1
    with app.app_context():
        assert db.session.get(Card, card_ids[0]).explanation == first["explanation"]


def test_explain_from_cache_works_even_at_the_limit(app, riya_client, riya):
    _, card_ids = make_deck(app, riya)
    riya_client.post(f"/study/explain/{card_ids[0]}")
    with app.app_context():
        user = db.session.get(User, riya)
        user.ai_calls_today = 20
        db.session.commit()
    assert riya_client.post(f"/study/explain/{card_ids[0]}").status_code == 200
    response = riya_client.post(f"/study/explain/{card_ids[1]}")
    assert response.status_code == 429
    assert "used today's 20 AI calls" in response.get_json()["error"]


def test_explain_needs_the_deck_in_my_list(app, riya_client, make_user):
    arjun = make_user(name="Arjun", email="arjun@example.com")
    _, card_ids = make_deck(app, arjun)
    assert riya_client.post(f"/study/explain/{card_ids[0]}").status_code == 404


def test_study_page_has_the_explain_button(app, riya_client, riya):
    deck_id, _ = make_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    assert "Explain this card" in riya_client.get("/study/card").get_data(as_text=True)


# ---------- Weak Spot Fixer ----------

def make_weak_topic(app, user_id, deck_id, card_ids):
    """Two open misconceptions in the topic, so the Fixer picks it."""
    with app.app_context():
        for card_id, misconceptions in zip(card_ids, (1, 1, 0)):
            db.session.add(Progress(user_id=user_id, card_id=card_id, shelf=1, next_due=today_local(),
                                    wrong_count=1, misconception_count=misconceptions, last_seen=utc_now()))
        db.session.commit()


def finish_a_session(client, deck_id):
    client.get(f"/study/{deck_id}?style=free")
    client.get("/study/card")
    with client.session_transaction() as state:
        card_id = state["study"]["current"]
    client.post("/study/answer", data={"card_id": card_id, "confident": "sure", "knew_it": "0"})
    return client.get("/study/summary").get_data(as_text=True)


def practice_cards(app, user_id):
    with app.app_context():
        return db.session.query(Card).join(Deck).filter(Deck.owner_id == user_id, Deck.is_weak_spot.is_(True)).all()


def test_fixer_adds_three_practice_cards_and_shows_the_box(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak_topic(app, riya, deck_id, card_ids)
    page = finish_a_session(riya_client, deck_id)
    assert "Weak spot: <mark>Cells</mark>" in page
    assert "Practice these 3 cards" in page
    assert "youtube.com/results?search_query=Cells" in page

    cards = practice_cards(app, riya)
    assert len(cards) == 3
    assert {(c.source, c.importance, c.topic, c.owner_id) for c in cards} == {("fixer", "high", "Cells", riya)}
    with app.app_context():
        for card in cards:
            progress = db.session.get(Progress, (riya, card.id))
            assert (progress.shelf, progress.next_due) == (1, today_local())


def test_no_box_without_a_weak_topic(app, riya_client, riya):
    deck_id, _ = make_deck(app, riya)
    assert "Weak spot:" not in finish_a_session(riya_client, deck_id)
    assert practice_cards(app, riya) == []


def test_fixer_runs_once_per_topic_per_day(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak_topic(app, riya, deck_id, card_ids)
    finish_a_session(riya_client, deck_id)
    riya_client.get("/study/summary")              # reload: same session
    page = finish_a_session(riya_client, deck_id)   # a second session the same day
    assert "Weak spot: <mark>Cells</mark>" in page
    assert len(practice_cards(app, riya)) == 3


def test_practice_these_cards_studies_exactly_those_cards(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak_topic(app, riya, deck_id, card_ids)
    finish_a_session(riya_client, deck_id)
    riya_client.get("/study/practice")
    shown = []
    while True:
        riya_client.get("/study/card")
        with riya_client.session_transaction() as state:
            card_id = state["study"]["current"]
        if card_id is None:
            break
        shown.append(card_id)
        riya_client.post("/study/answer", data={"card_id": card_id, "confident": "sure", "knew_it": "1"})
    assert sorted(shown) == sorted(card.id for card in practice_cards(app, riya))


def test_home_shows_todays_weak_spot(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak_topic(app, riya, deck_id, card_ids)
    finish_a_session(riya_client, deck_id)
    page = riya_client.get("/home").get_data(as_text=True)
    assert "Weak spot: <mark>Cells</mark>" in page
    assert "youtube.com" not in page  # links only on the summary page


def test_fixer_at_the_ai_limit_still_shows_links(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak_topic(app, riya, deck_id, card_ids)
    with app.app_context():
        user = db.session.get(User, riya)
        user.ai_calls_today, user.ai_calls_date = 20, today_local()
        db.session.commit()
    page = finish_a_session(riya_client, deck_id)
    assert "used today&#39;s 20 AI calls" in page
    assert "youtube.com/results" in page
    assert practice_cards(app, riya) == []


def test_resolved_misconceptions_do_not_trigger_the_fixer(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    with app.app_context():
        for card_id in card_ids:
            db.session.add(Progress(user_id=riya, card_id=card_id, shelf=4, next_due=today_local(),
                                    wrong_count=2, misconception_count=2, last_seen=utc_now()))
        db.session.commit()
    assert "Weak spot:" not in finish_a_session(riya_client, deck_id)


def test_stand_in_makes_three_different_practice_cards_from_one_missed_card(app):
    with app.app_context():
        fix = ai_service.fix_weak_spot("Cells", [{"question": "Where is rRNA made?", "answer": "In the nucleolus."}])
    questions = [card["question"] for card in fix["follow_up_cards"]]
    assert len(set(questions)) == 3
