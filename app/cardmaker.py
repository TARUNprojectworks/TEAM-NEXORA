"""AI Card Maker: paste notes or upload files, check the drafts, then save.

Flow (three plain form posts, nothing saved until the last one):
  1. /maker/            choose where the cards go, paste notes and/or upload files
  2. /maker/drafts      Gemini (or Quick split) drafts cards; the student edits them
  3. /maker/save        "Save cards" writes the kept drafts to the deck
Drafts travel in the form itself, not in the session, so there's no size limit worry.

Also here: "Generate more cards" for a deck (/maker/more/<deck_id>), Quick split
(no AI: "term - meaning" lines become cards) and the near-duplicate check.
"""

import logging
import re
from difflib import SequenceMatcher

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ai_service, storage
from app.ai_service import AIError, AILimitReached
from app.decks import (
    ExamForm, apply_exam_choice, deck_for_new_cards_or_error, first_error, new_card_for, visible_deck_cards,
)
from app.models import IMPORTANCE_LEVELS, Deck, UserDeck, db, today_local

bp = Blueprint("cardmaker", __name__, url_prefix="/maker")
log = logging.getLogger("nexora.cardmaker")

MAX_NOTE_WORDS = 3000
MAX_SAVED_CARDS = 60
NEAR_DUPLICATE_RATIO = 0.9

# Question words every card shares. We compare what's left, so "What is osmosis?"
# and "What is mitosis?" stay different, while "Define osmosis." matches "What is osmosis?".
QUESTION_WORDS = {
    "a", "an", "the", "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "is", "are", "was", "were", "do", "does", "did", "of", "in", "on", "to", "for", "and",
    "define", "explain", "describe", "name", "state", "give", "meaning", "term",
}


# ---------- Quick split (no AI) ----------

# "term - meaning", "term – meaning" or "term: meaning", with an optional bullet or number in front.
SPLIT_LINE = re.compile(
    r"^\s*(?:[-*•]\s+|\d+[.)]\s+)?(?P<term>[^:\n]+?)\s*(?::|\s[-–—]\s)\s*(?P<meaning>.+?)\s*$"
)


def split_line(line):
    match = SPLIT_LINE.match(line)
    if not match:
        return None
    term, meaning = match.group("term").strip(), match.group("meaning").strip()
    if not term or not meaning or len(term) > 120 or meaning.startswith("//"):  # skip "https://..."
        return None
    return term, meaning


def quick_split(text):
    """Each 'term - meaning' or 'term: meaning' line becomes a card, term on the front.

    The topic is left empty; saving fills it with the deck's name.
    """
    cards = []
    for line in (text or "").splitlines():
        parts = split_line(line)
        if parts:
            cards.append({"question": parts[0], "answer": parts[1], "topic": "",
                          "importance": "medium", "source_line": line.strip(), "source": "manual"})
    return cards


def skipped_lines(text):
    """Non-empty lines Quick split couldn't turn into a card, so the student can see them."""
    return [line.strip() for line in (text or "").splitlines() if line.strip() and not split_line(line)]


def looks_like_term_lines(text):
    """True when most non-empty lines are 'term - meaning' lines."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return len(lines) >= 2 and sum(1 for line in lines if split_line(line)) >= 0.6 * len(lines)


# ---------- Near duplicates ----------

def question_key(question):
    """The words that make a question different: lower case, no punctuation, no question words."""
    words = re.sub(r"[^a-z0-9 ]", " ", question.lower()).split()
    content = [word for word in words if word not in QUESTION_WORDS]
    return " ".join(content or words)


def is_near_duplicate(a, b):
    """Same key, or keys that differ only by a typo or a small word."""
    return a == b or SequenceMatcher(None, a, b).ratio() >= NEAR_DUPLICATE_RATIO


def remove_near_duplicates(drafts, existing_questions):
    """Drop drafts whose question is (almost) the same as a card we already have, or another draft."""
    seen = [question_key(question) for question in existing_questions]
    kept = []
    for draft in drafts:
        question = question_key(draft["question"])
        if any(is_near_duplicate(question, other) for other in seen):
            continue
        seen.append(question)
        kept.append(draft)
    return kept


# ---------- Where the cards go ----------

def deck_choices():
    """Decks the student can add cards to: their own (not Weak spot practice) and ready decks they added."""
    rows = db.session.query(Deck).join(
        UserDeck, (UserDeck.deck_id == Deck.id) & (UserDeck.user_id == current_user.id)
    ).filter(Deck.is_weak_spot.is_(False)).order_by(Deck.title).all()
    return [deck for deck in rows if deck.owner_id == current_user.id or deck.is_ready]


def read_target(form):
    """Where the drafts will be saved: {"deck_id": id} or {"new_title", "has_exam", "exam_date"}.

    Returns (target, error message or None).
    """
    choice = form.get("target", "new")
    if choice != "new":
        if not choice.isdigit():
            return None, "Choose a deck to add the cards to."
        deck_for_new_cards_or_error(int(choice))  # 403/404 if it isn't theirs
        return {"deck_id": int(choice)}, None

    title = (form.get("new_title") or "").strip()[:120]
    if not title:
        return None, "Give your new deck a name."
    exam = ExamForm(form, meta={"csrf": False})
    if not exam.validate():
        return None, first_error(exam)
    return {"new_title": title, "has_exam": exam.has_exam.data,
            "exam_date": exam.exam_date.data.isoformat() if exam.exam_date.data else ""}, None


def target_deck(target):
    return db.session.get(Deck, target["deck_id"]) if "deck_id" in target else None


def create_target_deck(target):
    """Make the new deck the student asked for, with their exam answer."""
    deck = Deck(owner_id=current_user.id, title=target["new_title"], folder="personal")
    db.session.add(deck)
    db.session.flush()
    user_deck = UserDeck(user_id=current_user.id, deck_id=deck.id)
    apply_exam_choice(user_deck, ExamForm(request.form, meta={"csrf": False}))
    db.session.add(user_deck)
    return deck


def existing_questions(deck):
    return [card.question for card in visible_deck_cards(deck.id, current_user.id)] if deck else []


# ---------- Making drafts ----------

def notes_from_request():
    """Pasted notes plus any .txt file, and the photos/PDFs ready for Gemini."""
    uploads = [(f.filename, f.read()) for f in request.files.getlist("files") if f and f.filename]
    checked = storage.check_uploads(uploads)
    texts = [request.form.get("notes", "").strip()]
    files = []
    for mime_type, data in checked:
        if mime_type == "text/plain":
            texts.append(storage.read_text_file(data).strip())
        else:
            reference = storage.save_note_file(data, mime_type)
            files.append(storage.file_for_gemini(reference, data, mime_type))
    return "\n\n".join(t for t in texts if t), files


def drafts_with_ai(text, files, topics):
    """Ask Gemini (or the stand-in). Falls back to Quick split if the AI call fails."""
    try:
        ai_service.use_ai_call(current_user, today_local())
        db.session.commit()
        return ai_service.make_cards(text=text or None, files=files, topics=topics), None
    except AILimitReached as error:
        fallback, message = quick_split(text), str(error)
    except Exception as error:
        log.warning("make_cards_failed", extra={"fields": {"error": type(error).__name__}})
        fallback = quick_split(text)
        message = str(error) if isinstance(error, AIError) else ai_service.COULD_NOT_READ
    if fallback:
        return fallback, "The AI didn't work this time, so we split your lines with Quick split instead."
    raise AIError(message)


def chosen_target():
    """The deck picked in the form or URL: a deck id as text, or "new"."""
    value = request.values.get("target") or request.values.get("deck_id") or "new"
    return value if value.isdigit() else "new"


MODES = ("paste", "upload")


def render_maker(error=None, status=200):
    """The maker page. mode="paste" or "upload" shows only that screen (from Create My Own)."""
    chosen = chosen_target()
    mode = request.values.get("mode")
    return render_template(
        "maker/maker.html",
        decks=deck_choices(),
        chosen=chosen,
        chosen_deck=db.session.get(Deck, int(chosen)) if chosen != "new" else None,
        mode=mode if mode in MODES else "",
        notes=request.form.get("notes", ""),
        new_title=request.form.get("new_title", ""),
        exam_form=ExamForm(request.form if request.method == "POST" else None, meta={"csrf": False}),
        source=request.args.get("source", ""),
        error=error,
        today=today_local(),
    ), status


def render_drafts(drafts, target, origin, note=None, error=None, skipped=()):
    for draft in drafts:
        draft.setdefault("source", "ai" if origin == "ai" else "manual")
    return render_template(
        "maker/drafts.html",
        drafts=drafts, target=target, deck=target_deck(target), origin=origin, note=note, error=error,
        stand_in=any(d["source"] == "ai" for d in drafts) and not ai_service.ai_enabled(),
        importance_levels=IMPORTANCE_LEVELS, skipped=list(skipped),
    )


DRAFT_FIELDS = ("question", "answer", "topic", "importance", "source_line", "source")


def drafts_from_form():
    """The drafts as the student left them on the page (blank rows dropped)."""
    questions = request.form.getlist("question")
    columns = {name: request.form.getlist(name) for name in DRAFT_FIELDS}
    # A draft without its own source (an older page) takes the form's overall origin.
    origin = "ai" if request.form.get("origin") == "ai" else "manual"
    columns["source"] = columns["source"] or [origin] * len(questions)
    drafts = []
    for row in zip(*(columns[name] for name in DRAFT_FIELDS)):
        raw = dict(zip(DRAFT_FIELDS, row))
        card = ai_service.clean_card(raw)
        if card:
            card["source"] = "ai" if raw["source"] == "ai" else "manual"
            drafts.append(card)
    return drafts


# ---------- Routes ----------

@bp.get("/")
@login_required
def maker():
    return render_maker()


@bp.post("/drafts")
@login_required
def make_drafts():
    target, error = read_target(request.form)
    if error:
        return render_maker(error, 400)

    skipped = []
    if request.form.get("action") == "split":
        notes = request.form.get("notes", "")
        drafts, origin, note, skipped = quick_split(notes), "manual", None, skipped_lines(notes)
        if not drafts:
            return render_maker(
                "No “term - meaning” lines found. Put one term per line, like "
                "“Osmosis - water moving across a membrane”, or use Make cards with AI.", 400)
    else:
        try:
            text, files = notes_from_request()
        except storage.UploadError as upload_error:
            return render_maker(str(upload_error), 400)
        if not text and not files:
            return render_maker("Paste some notes or upload a file first.", 400)
        if len(text.split()) > MAX_NOTE_WORDS:
            return render_maker("That's more than 3,000 words. Paste a shorter piece of notes.", 400)
        try:
            deck = target_deck(target)
            topics = sorted({c.topic for c in visible_deck_cards(deck.id, current_user.id)}) if deck else []
            drafts, note = drafts_with_ai(text, files, topics)
        except AIError as ai_error:
            return render_maker(str(ai_error), 400)
        origin = "ai" if note is None else "manual"

    before = len(drafts)
    drafts = remove_near_duplicates(drafts, existing_questions(target_deck(target)))
    if before > len(drafts):
        note = (note + " " if note else "") + f"Left out {before - len(drafts)} that were already in the deck."
    if not drafts:
        return render_maker("Every card from these notes is already in that deck.", 400)
    return render_drafts(drafts, target, origin, note, skipped=skipped)


@bp.post("/drafts/add-ai")
@login_required
def add_ai_drafts():
    """"Make these with AI" for the lines Quick split skipped. Keeps the drafts already on the page."""
    target, error = read_target(request.form)
    if error:
        return render_maker(error, 400)
    drafts = drafts_from_form()
    lines = request.form.get("skipped", "").strip()
    deck = target_deck(target)
    topics = sorted({c.topic for c in visible_deck_cards(deck.id, current_user.id)}) if deck else []
    try:
        new, note = drafts_with_ai(lines, [], topics)
    except AIError as ai_error:
        return render_drafts(drafts, target, "manual", error=str(ai_error), skipped=lines.splitlines())
    for draft in new:
        draft["source"] = "manual" if note else "ai"
    new = remove_near_duplicates(new, [d["question"] for d in drafts] + existing_questions(deck))
    return render_drafts(drafts + new, target, "ai", note=note or f"Added {len(new)} AI drafts from the skipped lines.")


@bp.post("/more/<int:deck_id>")
@login_required
def generate_more(deck_id):
    """Generate 5 or 10 new cards for a deck, leaving out near-duplicates."""
    deck = deck_for_new_cards_or_error(deck_id)
    count = 10 if request.form.get("count") == "10" else 5
    cards = visible_deck_cards(deck.id, current_user.id)
    existing = [{"question": c.question, "answer": c.answer, "topic": c.topic, "importance": c.importance}
                for c in cards]
    topics = sorted({c.topic for c in cards})
    try:
        ai_service.use_ai_call(current_user, today_local())
        db.session.commit()
        drafts = ai_service.generate_more_cards(deck.title, topics, existing, count)
    except AIError as error:
        flash(str(error), "error")
        return redirect(url_for("decks.deck_page", deck_id=deck.id))
    except Exception as error:
        log.warning("generate_more_failed", extra={"fields": {"error": type(error).__name__}})
        flash("Couldn't make new cards right now. Try again in a minute.", "error")
        return redirect(url_for("decks.deck_page", deck_id=deck.id))

    drafts = remove_near_duplicates(drafts, [c["question"] for c in existing])
    if not drafts:
        flash("Couldn't find new cards that aren't already in this deck.", "info")
        return redirect(url_for("decks.deck_page", deck_id=deck.id))
    return render_drafts(drafts, {"deck_id": deck.id}, "ai")


@bp.post("/save")
@login_required
def save_drafts():
    target, error = read_target(request.form)
    if error:
        return render_maker(error, 400)
    origin = "ai" if request.form.get("origin") == "ai" else "manual"
    kept = drafts_from_form()
    if not kept:
        return render_drafts([], target, origin, error="Keep at least one card with a front and a back.")

    deck = target_deck(target) or create_target_deck(target)
    for draft in kept[:MAX_SAVED_CARDS]:
        db.session.add(new_card_for(deck, source=draft["source"], question=draft["question"],
                                    answer=draft["answer"], topic=draft["topic"] or deck.title,
                                    importance=draft["importance"], source_line=draft["source_line"] or None))
    db.session.commit()
    log.info("cards_saved", extra={"fields": {"deck_id": deck.id, "count": min(len(kept), MAX_SAVED_CARDS),
                                              "source": origin}})
    saved = min(len(kept), MAX_SAVED_CARDS)
    flash(f"Saved {saved} {'card' if saved == 1 else 'cards'}.", "info")
    return redirect(url_for("decks.deck_page", deck_id=deck.id))
