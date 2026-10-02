"""AI Card Maker, Quick split, near-duplicates, uploads and Generate more (AI off: stand-in mode)."""

import io
from datetime import timedelta

import pytest

from app import storage
from app.cardmaker import looks_like_term_lines, quick_split, remove_near_duplicates
from app.models import Card, Deck, User, UserDeck, db, today_local

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 200
HEIC = b"\x00\x00\x00\x18ftypheic" + b"0" * 200


def pdf(pages):
    return b"%PDF-1.4\n" + b"".join(b"1 0 obj << /Type /Page >> endobj\n" for _ in range(pages))


# ---------- Quick split ----------

@pytest.mark.parametrize("line, front, back", [
    ("Osmosis - water moving across a membrane", "Osmosis", "water moving across a membrane"),
    ("Mitochondria: the powerhouse of the cell", "Mitochondria", "the powerhouse of the cell"),
    ("ATP – energy currency of the cell", "ATP", "energy currency of the cell"),
    ("- Ribosome - makes proteins", "Ribosome", "makes proteins"),
    ("3. Nucleolus: makes rRNA", "Nucleolus", "makes rRNA"),
])
def test_quick_split_reads_term_lines(line, front, back):
    card = quick_split(line)[0]
    assert (card["question"], card["answer"]) == (front, back)
    assert card["source_line"] == line.strip()
    assert card["topic"] == "General"


@pytest.mark.parametrize("line", [
    "A well-known fact with no separator",
    "See https://example.com for more",
    "just text",
])
def test_quick_split_skips_lines_without_a_term(line):
    assert quick_split(line) == []


def test_quick_split_keeps_hyphenated_words_together():
    assert quick_split("Self-pollination - pollen from the same flower")[0]["question"] == "Self-pollination"


def test_looks_like_term_lines():
    assert looks_like_term_lines("A - one\nB - two\nC - three")
    assert not looks_like_term_lines("Photosynthesis makes sugar.\nIt needs light.")
    assert not looks_like_term_lines("A - one")


# ---------- Near duplicates ----------

def test_similar_looking_questions_about_different_things_are_kept():
    drafts = [{"question": "What is osmosis?"}, {"question": "What is mitosis?"}, {"question": "What is meiosis?"}]
    assert len(remove_near_duplicates(drafts, [])) == 3


def test_same_question_in_other_words_is_a_duplicate():
    assert remove_near_duplicates([{"question": "Define osmosis."}], ["What is osmosis?"]) == []


def test_near_duplicates_are_removed():
    drafts = [{"question": "What is osmosis?"}, {"question": "What is Osmosis ?"},
              {"question": "Define diffusion."}, {"question": "What does ATP stand for?"}]
    kept = remove_near_duplicates(drafts, ["What does ATP stand for"])
    assert [d["question"] for d in kept] == ["What is osmosis?", "Define diffusion."]


# ---------- Uploads (storage.py) ----------

@pytest.mark.parametrize("name, data, mime", [
    ("a.jpg", JPEG, "image/jpeg"), ("a.png", PNG, "image/png"), ("a.webp", WEBP, "image/webp"),
    ("a.heic", HEIC, "image/heic"), ("a.pdf", pdf(1), "application/pdf"), ("notes.txt", b"hello", "text/plain"),
])
def test_file_type_comes_from_the_bytes(name, data, mime):
    assert storage.detect_type(name, data) == mime


def test_renamed_file_is_not_trusted():
    assert storage.detect_type("photo.jpg", b"not really a photo") is None


def test_pdf_page_count():
    assert storage.count_pdf_pages(pdf(3)) == 3
    assert storage.count_pdf_pages(b"%PDF-1.5 << /Type /Pages /Kids [] /Count 12 >>") == 12


@pytest.mark.parametrize("uploads, message", [
    ([("p.jpg", JPEG)] * 6, "up to 5 photos"),
    ([("big.jpg", JPEG + b"0" * (5 * 1024 * 1024))], "bigger than 5 MB"),
    ([("long.pdf", pdf(11))], "more than 10 pages"),
    ([("a.pdf", pdf(1)), ("b.pdf", pdf(1))], "one PDF"),
    ([("virus.exe", b"MZ\x90\x00")], "isn't a photo"),
])
def test_upload_limits(uploads, message):
    with pytest.raises(storage.UploadError, match=message):
        storage.check_uploads(uploads)


def test_saved_locally_and_sent_as_bytes(app):
    with app.test_request_context():
        reference = storage.save_note_file(JPEG, "image/jpeg")
        assert reference.startswith(app.config["UPLOAD_FOLDER"])
        assert storage.file_for_gemini(reference, JPEG, "image/jpeg") == {"mime_type": "image/jpeg", "data": JPEG}
    assert storage.file_for_gemini("gs://bucket/notes/x.jpg", JPEG, "image/jpeg") == {
        "mime_type": "image/jpeg", "uri": "gs://bucket/notes/x.jpg"}


# ---------- Card maker pages ----------

@pytest.fixture
def riya(make_user):
    return make_user()


@pytest.fixture
def riya_client(client, riya):
    client.post("/login", data={"email": "riya@example.com", "password": "password123"})
    return client


ANSWERS = {
    "What is osmosis?": "Water moving across a membrane from low to high solute concentration.",
    "What is ATP?": "The molecule cells use to store and move energy.",
    "What is DNA?": "The molecule that carries genetic instructions.",
}


def make_deck(app, user_id, ready=False, in_list=True, title="Biology", questions=("What is osmosis?",)):
    with app.app_context():
        deck = Deck(title=title, folder="semester" if ready else "personal", is_ready=ready,
                    owner_id=None if ready else user_id)
        db.session.add(deck)
        deck.cards.extend(Card(question=q, answer=ANSWERS.get(q, "An answer"), topic="Cells") for q in questions)
        db.session.flush()
        if in_list:
            db.session.add(UserDeck(user_id=user_id, deck_id=deck.id, mode="normal"))
        db.session.commit()
        return deck.id


NOTES = "Osmosis is the movement of water across a membrane. Diffusion is the spread of particles."


def new_deck_form(**extra):
    return dict({"target": "new", "new_title": "Bio notes", "has_exam": "no", "action": "ai"}, **extra)


def test_maker_page_has_inputs_in_order(app, riya_client, riya):
    make_deck(app, riya, title="My Notes")
    page = riya_client.get("/maker/").get_data(as_text=True)
    assert page.index("Type cards") < page.index("Paste notes") < page.index("Upload")
    assert "Make cards with AI" in page and "Quick split" in page
    assert "My Notes" in page
    assert "Weak spot practice" not in page.split('id="target"')[1].split("</select>")[0]


def test_make_cards_shows_drafts_and_saves_nothing(app, riya_client):
    page = riya_client.post("/maker/drafts", data=new_deck_form(notes=NOTES)).get_data(as_text=True)
    assert "Check your cards" in page
    assert "What is osmosis?" in page
    assert "From your notes" in page
    assert "AI is off on this computer" in page
    with app.app_context():
        assert db.session.query(Card).count() == 0
        assert db.session.query(Deck).filter_by(title="Bio notes").count() == 0


def save_form(target_fields, cards, origin="ai"):
    data = dict(target_fields, origin=origin)
    for field in ("question", "answer", "topic", "importance", "source_line"):
        data[field] = [card.get(field, "") for card in cards]
    return data


def test_save_cards_creates_the_new_deck_with_edited_cards(app, riya_client, riya):
    exam_day = (today_local() + timedelta(days=9)).isoformat()
    target = {"target": "new", "new_title": "Bio notes", "has_exam": "yes", "exam_date": exam_day}
    cards = [
        {"question": "What is osmosis? (edited)", "answer": "Water moving across a membrane.", "topic": "Cells",
         "importance": "high", "source_line": "Osmosis is the movement of water"},
        {"question": "", "answer": "", "topic": "", "importance": "medium"},  # a removed / empty row
    ]
    response = riya_client.post("/maker/save", data=save_form(target, cards))
    with app.app_context():
        deck = db.session.query(Deck).filter_by(title="Bio notes").one()
        assert response.headers["Location"].endswith(f"/decks/{deck.id}")
        assert db.session.get(UserDeck, (riya, deck.id)).mode == "exam"
        card = db.session.query(Card).filter_by(deck_id=deck.id).one()
        assert (card.question, card.source, card.owner_id, card.importance) == (
            "What is osmosis? (edited)", "ai", riya, "high")
        assert card.source_line == "Osmosis is the movement of water"


def test_quick_split_drafts(app, riya_client):
    notes = "Osmosis - water across a membrane\nATP: energy currency"
    page = riya_client.post("/maker/drafts", data=new_deck_form(notes=notes, action="split")).get_data(as_text=True)
    assert "Osmosis" in page and "energy currency" in page
    assert 'name="origin" value="manual"' in page
    assert "AI is off" not in page


def test_quick_split_explains_when_no_lines_match(riya_client):
    response = riya_client.post("/maker/drafts", data=new_deck_form(notes="Just a sentence.", action="split"))
    assert response.status_code == 400
    assert "No “term - meaning” lines found" in response.get_data(as_text=True)


def test_upload_photos_pdf_and_text(riya_client):
    files = [(io.BytesIO(JPEG), "page1.jpg"), (io.BytesIO(PNG), "page2.png"),
             (io.BytesIO(pdf(2)), "chapter.pdf"), (io.BytesIO(b"Enzymes are proteins that speed up reactions."), "notes.txt")]
    page = riya_client.post("/maker/drafts", data=new_deck_form(files=files),
                            content_type="multipart/form-data").get_data(as_text=True)
    assert "Check your cards" in page
    assert "From your photo 1" in page and "From your photo 2" in page and "From your PDF" in page
    assert "What are enzymes?" in page


def test_too_many_photos_is_a_clear_error(riya_client):
    files = [(io.BytesIO(JPEG), f"p{i}.jpg") for i in range(6)]
    response = riya_client.post("/maker/drafts", data=new_deck_form(files=files), content_type="multipart/form-data")
    assert response.status_code == 400
    assert "Upload up to 5 photos" in response.get_data(as_text=True)


def test_empty_input_and_long_notes_are_rejected(riya_client):
    assert "Paste some notes or upload a file first" in riya_client.post(
        "/maker/drafts", data=new_deck_form(notes="")).get_data(as_text=True)
    assert "more than 3,000 words" in riya_client.post(
        "/maker/drafts", data=new_deck_form(notes="word " * 3001)).get_data(as_text=True)


def test_new_deck_needs_a_name(riya_client):
    response = riya_client.post("/maker/drafts", data=new_deck_form(notes=NOTES, new_title=""))
    assert "Give your new deck a name" in response.get_data(as_text=True)


def test_drafts_leave_out_cards_already_in_the_deck(app, riya_client, riya):
    deck_id = make_deck(app, riya, questions=("What is osmosis?",))
    page = riya_client.post("/maker/drafts", data={"target": str(deck_id), "notes": NOTES, "action": "ai"}).get_data(as_text=True)
    assert "What is diffusion?" in page
    assert "What is osmosis?" not in page.split('id="draft-list"')[1]
    assert "Left out 1 that were already in the deck" in page


def test_cards_saved_to_a_ready_deck_are_private(app, riya_client, riya):
    deck_id = make_deck(app, riya, ready=True, questions=("Shared Q",))
    riya_client.post("/maker/save", data=save_form({"target": str(deck_id)},
                                                   [{"question": "Mine", "answer": "A", "topic": "Cells", "importance": "medium"}]))
    with app.app_context():
        card = db.session.query(Card).filter_by(question="Mine").one()
        assert (card.deck_id, card.owner_id) == (deck_id, riya)


def test_cannot_save_to_a_deck_that_is_not_yours(app, riya_client, make_user):
    arjun = make_user(name="Arjun", email="arjun@example.com")
    other = make_deck(app, arjun, title="Arjun's")
    ready_not_added = make_deck(app, arjun, ready=True, in_list=False, title="Ready")
    card = [{"question": "Q", "answer": "A", "topic": "T", "importance": "medium"}]
    assert riya_client.post("/maker/save", data=save_form({"target": str(other)}, card)).status_code == 404
    assert riya_client.post("/maker/save", data=save_form({"target": str(ready_not_added)}, card)).status_code == 403


# ---------- Generate more ----------

def test_generate_more_shows_new_drafts_without_repeats(app, riya_client, riya):
    deck_id = make_deck(app, riya, questions=("What is osmosis?", "What is ATP?", "What is DNA?"))
    page = riya_client.post(f"/maker/more/{deck_id}", data={"count": "5"}).get_data(as_text=True)
    assert "Check your cards" in page
    drafts = page.split('id="draft-list"')[1].split("</ol>")[0]
    assert drafts.count('class="draft index-card"') == 3
    assert 'name="question" rows="2" maxlength="1000">What is osmosis?</textarea>' not in drafts  # no repeat on a front


def test_generate_more_needs_permission(app, riya_client, make_user):
    arjun = make_user(name="Arjun", email="arjun@example.com")
    assert riya_client.post(f"/maker/more/{make_deck(app, arjun)}", data={"count": "5"}).status_code == 404


# ---------- Daily AI limit ----------

def use_up_ai_calls(app, user_id, used=20):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.ai_calls_today, user.ai_calls_date = used, today_local()
        db.session.commit()


def test_each_ai_request_counts_one_call(app, riya_client, riya):
    riya_client.post("/maker/drafts", data=new_deck_form(notes=NOTES))
    riya_client.post("/maker/drafts", data=new_deck_form(notes="A - b\nC - d", action="split"))  # no AI
    with app.app_context():
        assert db.session.get(User, riya).ai_calls_today == 1


def test_limit_reached_falls_back_to_quick_split(app, riya_client, riya):
    use_up_ai_calls(app, riya)
    page = riya_client.post("/maker/drafts", data=new_deck_form(notes="Osmosis - water\nATP - energy")).get_data(as_text=True)
    assert "split your lines with Quick split" in page
    assert "Osmosis" in page


def test_limit_reached_without_term_lines_says_so(app, riya_client, riya):
    use_up_ai_calls(app, riya)
    page = riya_client.post("/maker/drafts", data=new_deck_form(notes=NOTES)).get_data(as_text=True)
    assert "used today&#39;s 20 AI calls" in page


def test_limit_resets_the_next_day(app, riya_client, riya):
    with app.app_context():
        user = db.session.get(User, riya)
        user.ai_calls_today, user.ai_calls_date = 20, today_local() - timedelta(days=1)
        db.session.commit()
    assert "Check your cards" in riya_client.post("/maker/drafts", data=new_deck_form(notes=NOTES)).get_data(as_text=True)
    with app.app_context():
        assert db.session.get(User, riya).ai_calls_today == 1
