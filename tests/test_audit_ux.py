"""Audit fixes and UX polish (2 Oct): Fixer once a day on the server, Home weak spot, plan rebuild,
Quick split skipped lines, deck-name topics, Deck Library, Quick Revise, card box, My Progress lists."""

import html
from datetime import timedelta

import pytest

from app.models import Card, Deck, Progress, User, UserDeck, db, today_local, utc_now
from seed import read_all_deck_files

QUESTIONS = [
    ("Which organelle makes ATP?", "Mitochondria, through aerobic respiration."),
    ("What does the Golgi apparatus do?", "It packages and sorts proteins into vesicles."),
    ("Where is rRNA made?", "In the nucleolus."),
]


@pytest.fixture
def riya(make_user):
    return make_user()


def login(client):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


@pytest.fixture
def riya_client(client, riya):
    return login(client)


def text(client, url):
    return " ".join(html.unescape(client.get(url).get_data(as_text=True)).split())


def make_deck(app, user_id, title="Biology", topic="Cells", in_list=True):
    with app.app_context():
        deck = Deck(title=title, folder="semester", is_ready=True)
        db.session.add(deck)
        deck.cards.extend(Card(question=q, answer=a, topic=topic) for q, a in QUESTIONS)
        db.session.flush()
        if in_list:
            db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode="normal"))
        db.session.commit()
        return deck.id, [c.id for c in deck.cards]


def make_weak(app, user_id, card_ids):
    with app.app_context():
        for card_id, misconceptions in zip(card_ids, (1, 1, 0)):
            db.session.add(Progress(user_id=user_id, card_id=card_id, shelf=1, next_due=today_local(),
                                    wrong_count=1, misconception_count=misconceptions, last_seen=utc_now()))
        db.session.commit()


def finish_session(client, deck_id):
    client.get(f"/study/{deck_id}?style=free")
    client.get("/study/card")
    with client.session_transaction() as state:
        card_id = state["study"]["current"]
    client.post("/study/answer", data={"card_id": card_id, "confident": "sure", "knew_it": "0"})
    summary = client.get("/study/summary").get_data(as_text=True)
    return summary, client.post("/study/weak-spot").get_json()["html"]


def practice_cards(app, user_id):
    with app.app_context():
        return db.session.query(Card).join(Deck).filter(Deck.owner_id == user_id, Deck.is_weak_spot.is_(True)).all()


# ---------- 1. Summary first, weak spot after ----------

def test_summary_shows_at_once_and_loads_the_box_after(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak(app, riya, card_ids)
    summary, box = finish_session(riya_client, deck_id)
    assert "Finding your weak spot" in summary and "Weak spot:" not in summary
    assert "Weak spot: <mark>Cells</mark>" in box


# ---------- 2. Once per topic per day, checked in the database ----------

def test_logging_in_again_does_not_rerun_the_fixer(app, riya, riya_client):
    deck_id, card_ids = make_deck(app, riya)
    make_weak(app, riya, card_ids)
    finish_session(riya_client, deck_id)
    first = sorted(c.id for c in practice_cards(app, riya))
    assert len(first) == 3

    second_browser = login(app.test_client())  # a new login: empty browser session
    _, box = finish_session(second_browser, deck_id)
    assert "already got practice cards for this topic today" in html.unescape(box)
    assert sorted(c.id for c in practice_cards(app, riya)) == first
    with app.app_context():
        assert db.session.get(User, riya).ai_calls_today == 2  # only the first Fixer used AI


def test_fix_it_runs_the_fixer_for_one_topic(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak(app, riya, card_ids)
    page = riya_client.get(f"/fixer/topic/{deck_id}?topic=Cells").get_data(as_text=True)
    assert "Fix: Cells" in page and "Getting an explanation" in page
    reply = riya_client.post(f"/fixer/topic/{deck_id}", json={"topic": "Cells"}).get_json()
    assert "Practice these 3 cards" in reply["html"]
    riya_client.post(f"/fixer/topic/{deck_id}", json={"topic": "Cells"})  # pressed twice
    assert len(practice_cards(app, riya)) == 3


def test_fix_it_needs_my_deck_and_a_real_topic(app, riya_client, riya, make_user):
    other = make_user(name="Arjun", email="arjun@example.com")
    my_deck, _ = make_deck(app, riya)
    not_mine, _ = make_deck(app, other, title="Other", in_list=False)
    assert riya_client.get(f"/fixer/topic/{not_mine}?topic=Cells").status_code == 404
    assert riya_client.get(f"/fixer/topic/{my_deck}?topic=Nonsense").status_code == 404
    assert riya_client.post(f"/fixer/topic/{not_mine}", json={"topic": "Cells"}).status_code == 404


# ---------- 3. Home weak spot box ----------

def test_home_box_names_its_deck_and_hides_once_fixed(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak(app, riya, card_ids)
    finish_session(riya_client, deck_id)
    assert "From: Biology" in text(riya_client, "/home")

    with app.app_context():  # the student masters the topic
        for p in db.session.query(Progress).filter(Progress.card_id.in_(card_ids), Progress.user_id == riya):
            p.shelf = 5
        db.session.commit()
    assert "Weak spot:" not in text(riya_client, "/home")


# ---------- 4. Daily plan ----------

def test_empty_plan_fills_in_after_adding_a_deck(app, riya_client, riya):
    assert "Nothing to study today" in text(riya_client, "/home")
    make_deck(app, riya)
    page = text(riya_client, "/home")
    assert "Start today's plan" in page and "3 cards" in page


def test_refresh_plan_rebuilds_it(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    riya_client.get("/home")
    with app.app_context():
        db.session.add(Card(deck_id=deck_id, owner_id=riya, question="New Q", answer="A", topic="Cells"))
        db.session.commit()
    assert riya_client.post("/plan/refresh").status_code == 302
    with riya_client.session_transaction() as state:
        assert len(state["plan"]["card_ids"]) == 4


def answer_all(client):
    while True:
        client.get("/study/card")
        with client.session_transaction() as state:
            card_id = state["study"]["current"]
        if card_id is None:
            return
        client.post("/study/answer", data={"card_id": card_id, "confident": "sure", "knew_it": "1"})


def test_done_for_today_offers_revise_and_keep_going(app, riya_client, riya):
    make_deck(app, riya)
    riya_client.get("/home")
    riya_client.get("/study/plan")
    answer_all(riya_client)
    page = text(riya_client, "/home")
    assert "Done for today" in page and "Revise today's cards again" in page and "Keep going" in page


def test_revising_the_plan_gives_no_xp_and_changes_no_shelves(app, riya_client, riya):
    make_deck(app, riya)
    riya_client.get("/home")
    riya_client.get("/study/plan")
    answer_all(riya_client)
    with app.app_context():
        xp_before = db.session.get(User, riya).xp
        shelves_before = sorted(p.shelf for p in db.session.query(Progress).filter_by(user_id=riya))
    riya_client.get("/study/revise")
    answer_all(riya_client)
    with app.app_context():
        assert db.session.get(User, riya).xp == xp_before
        assert sorted(p.shelf for p in db.session.query(Progress).filter_by(user_id=riya)) == shelves_before
    assert "no XP" in text(riya_client, "/study/summary")


# ---------- 5 and 11. Quick split skipped lines; deck name as topic ----------

def maker_form(**extra):
    return dict({"target": "new", "new_title": "Bio notes", "has_exam": "no"}, **extra)


def test_quick_split_lists_skipped_lines(riya_client):
    notes = "Osmosis - water\nThis line has no separator\nATP: energy\nAnother plain line"
    response = riya_client.post("/maker/drafts", data=maker_form(notes=notes, action="split"))
    page = " ".join(html.unescape(response.get_data(as_text=True)).split())
    assert "2 lines skipped" in page
    assert "This line has no separator" in page and "Another plain line" in page
    assert "Make these with AI" in page


def test_make_these_with_ai_keeps_the_quick_split_drafts(riya_client):
    data = maker_form(origin="manual", skipped="Enzymes are proteins that speed up reactions.",
                      question=["Osmosis"], answer=["water"], topic=[""], importance=["medium"],
                      source_line=["Osmosis - water"], source=["manual"])
    page = html.unescape(riya_client.post("/maker/drafts/add-ai", data=data).get_data(as_text=True))
    assert ">Osmosis</textarea>" in page and "What are enzymes?" in page
    assert 'name="source" value="manual"' in page and 'name="source" value="ai"' in page


def test_each_draft_keeps_its_own_source_and_empty_topics_use_the_deck_name(app, riya_client, riya):
    data = maker_form(origin="ai", question=["Osmosis", "What are enzymes?"], answer=["water", "Proteins."],
                      topic=["", "Biochemistry"], importance=["medium", "high"], source_line=["", ""],
                      source=["manual", "ai"])
    riya_client.post("/maker/save", data=data)
    with app.app_context():
        cards = {c.question: c for c in db.session.query(Card).filter(Card.owner_id == riya)}
        assert (cards["Osmosis"].source, cards["Osmosis"].topic) == ("manual", "Bio notes")
        assert (cards["What are enzymes?"].source, cards["What are enzymes?"].topic) == ("ai", "Biochemistry")


def test_typed_card_without_topic_uses_the_deck_name(app, riya_client, riya):
    riya_client.post("/decks/new", data={"title": "Physics", "method": "type", "has_exam": "no"})
    with app.app_context():
        deck_id = db.session.query(Deck.id).filter_by(title="Physics").scalar()
    riya_client.post(f"/decks/{deck_id}/cards/new", data={"question": "Q", "answer": "A", "importance": "medium"})
    with app.app_context():
        assert db.session.query(Card).filter_by(question="Q").one().topic == "Physics"


# ---------- 7, 8, 9, 10. Names, Quick Revise note, card box, Deck Library ----------

def test_new_names_and_old_urls(app, riya_client, riya):
    page = text(riya_client, "/home")
    assert "Deck Library" in page and "My Progress" in page and "Ready decks" not in page
    assert riya_client.get("/decks/ready").status_code == 200 and riya_client.get("/tracking/").status_code == 200


def test_quick_revise_says_progress_does_not_change(app, riya_client, riya):
    deck_id, _ = make_deck(app, riya)
    riya_client.get(f"/study/{deck_id}?style=free")
    assert "Practice only, your progress doesn't change" in text(riya_client, "/study/card")
    riya_client.get(f"/study/{deck_id}")
    assert "Practice only" not in text(riya_client, "/study/card")


def test_card_box_uses_named_shelves(app, riya_client, riya):
    deck_id, _ = make_deck(app, riya)
    page = text(riya_client, f"/decks/{deck_id}")
    for name in ["Learning", "Getting there", "Good", "Strong", "Mastered", "How this works"]:
        assert name in page


def test_deck_library_cards_and_search(app, riya_client, riya):
    make_deck(app, riya, title="Cell Biology")
    make_deck(app, riya, title="DSA Basics", topic="Graphs", in_list=False)
    page = text(riya_client, "/decks/ready")
    assert "Preview 3 cards" in page and "Which organelle makes ATP?" in page and "Added" in page
    found = text(riya_client, "/decks/ready?q=graphs")
    assert "DSA Basics" in found and "Cell Biology" not in found and "1 deck for “graphs”" in found


def test_every_ready_deck_has_a_description():
    assert all(deck.get("description") for deck in read_all_deck_files())


# ---------- 12 and 13. Misconceptions and weak spots on My Progress ----------

def test_misconception_list_with_explain_and_practice(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak(app, riya, card_ids)
    page = text(riya_client, "/tracking/")
    assert "Which organelle makes ATP?" in page and "Practise all open (2)" in page
    assert f"/study/explain/{card_ids[0]}" in page and f"/study/cards?ids={card_ids[0]}" in page


def test_practice_chosen_cards_only_allows_my_cards(app, riya_client, riya, make_user):
    deck_id, card_ids = make_deck(app, riya)
    other = make_user(name="Arjun", email="arjun@example.com")
    _, other_ids = make_deck(app, other, title="Other", in_list=False)
    riya_client.get(f"/study/cards?ids={card_ids[0]},{other_ids[0]}")
    with riya_client.session_transaction() as state:
        assert state["study"]["card_ids"] == [card_ids[0]]
    response = riya_client.get(f"/study/cards?ids={other_ids[0]}")
    assert response.headers["Location"].endswith("/tracking/")


def test_fixed_weak_spot_is_listed_as_fixed(app, riya_client, riya):
    deck_id, card_ids = make_deck(app, riya)
    make_weak(app, riya, card_ids)
    finish_session(riya_client, deck_id)
    with app.app_context():
        for p in db.session.query(Progress).filter(Progress.card_id.in_(card_ids), Progress.user_id == riya):
            p.shelf, p.misconception_count = 5, 0
        db.session.commit()
    section = text(riya_client, "/tracking/").split("Weak spots")[1].split("Study calendar")[0]
    assert 'status-fixed">Fixed</span> <span class="weak-topic">Cells' in section


def test_study_page_has_the_misconception_note(app, riya_client, riya):
    deck_id, _ = make_deck(app, riya)
    riya_client.get(f"/study/{deck_id}")
    page = text(riya_client, "/study/card")
    assert "You were sure about this one." in page and "Tap Explain to see why." in page
