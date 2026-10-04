"""Ready decks, my decks, deck page and card create/edit/delete.

Who can do what:
- Ready decks are shared by everyone. Their shared cards are read-only
  (a student can only hide them), but a student who added the deck can add
  private cards to it (cards.owner_id = them). Only they see those.
- A student's own decks can be renamed, deleted and fully edited.
- Someone else's deck or private card is a 404, so we don't reveal it exists.
"""

import json
import logging
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import DateField, RadioField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.models import (
    Card, Deck, Progress, UserDeck, card_is_visible_to, db, hidden_card_ids, today_local,
    visible_to,
)

bp = Blueprint("decks", __name__, url_prefix="/decks")
log = logging.getLogger("nexora.decks")

READY_FOLDERS = {"semester": "Semester", "placement": "Placement", "competitive": "Competitive"}
# The three ways to add cards, in this order (Create My Own).
ADD_METHODS = (
    ("write", "Write cards", "Type each card yourself: front, back, topic."),
    ("paste", "Paste notes", "Paste text and let AI or Quick split make the cards."),
    ("upload", "Upload notes", "Photos of handwritten notes, a PDF or a .txt file."),
)

# What each shelf means to a student (shelf 1 to 5).
SHELF_NAMES = ("Learning", "Getting there", "Good", "Strong", "Mastered")
IMPORTANCE_CHOICES = [("high", "High"), ("medium", "Medium"), ("low", "Low")]
MAX_EXAM_DAYS_AHEAD = 730  # two years; anything later is almost surely a typo

# Soft limits for the "This card looks long" warning (shown by cardform.js).
LONG_FRONT_CHARS = 200
LONG_BACK_CHARS = 300


# ---------- Forms ----------

class ExamForm(FlaskForm):
    has_exam = RadioField(
        "Do you have an exam for this?",
        choices=[("no", "No"), ("yes", "Yes")],
        default="no",
    )
    exam_date = DateField("Exam date", validators=[Optional()])

    def validate(self, extra_validators=None):
        # The date is only required when the student said Yes, which a
        # single-field validator can't see.
        if not super().validate(extra_validators):
            return False
        if self.has_exam.data != "yes":
            return True
        today = today_local()
        if not self.exam_date.data:
            self.exam_date.errors.append("Pick the date of your exam.")
        elif self.exam_date.data < today:
            self.exam_date.errors.append("That date has passed. Pick today or a later date.")
        elif self.exam_date.data > today + timedelta(days=MAX_EXAM_DAYS_AHEAD):
            self.exam_date.errors.append("That date is more than two years away. Check the year.")
        return not self.exam_date.errors


class NewDeckForm(ExamForm):
    title = StringField("Deck name", validators=[
        DataRequired(message="Give your deck a name."), Length(max=120),
    ])
    method = RadioField(
        "How do you want to add cards?",
        choices=[(value, label) for value, label, _ in ADD_METHODS],
        default="write",
    )


class DeckTitleForm(FlaskForm):
    title = StringField("Deck name", validators=[
        DataRequired(message="Give your deck a name."), Length(max=120),
    ])


class CardForm(FlaskForm):
    question = TextAreaField(
        "Front", description="Question, term or prompt",
        validators=[DataRequired(message="Write something on the front."), Length(max=1000)],
    )
    answer = TextAreaField(
        "Back", description="Answer, definition or explanation",
        validators=[DataRequired(message="Write something on the back."), Length(max=2000)],
    )
    topic = StringField(
        "Topic", description="Optional. Cards with no topic use the deck's name as their topic.",
        validators=[Optional(), Length(max=80)],
    )
    importance = SelectField("Importance", choices=IMPORTANCE_CHOICES, default="medium")


# ---------- Helpers ----------

def viewable_deck_or_404(deck_id):
    """A ready deck, or one of the current student's own decks."""
    deck = db.session.get(Deck, deck_id)
    if deck is None or not (deck.is_ready or deck.owner_id == current_user.id):
        abort(404)
    return deck


def own_deck_or_error(deck_id):
    """A deck the current student may change. Ready decks are read-only."""
    deck = viewable_deck_or_404(deck_id)
    if deck.owner_id != current_user.id:
        abort(403)
    return deck


def deck_card_or_404(deck, card_id):
    # The card must belong to this deck, so a changed URL can't reach
    # a card in some other deck, and it must not be another student's private card.
    card = db.session.get(Card, card_id)
    if card is None or card.deck_id != deck.id or not card_is_visible_to(card, current_user.id):
        abort(404)
    return card


def deck_for_new_cards_or_error(deck_id):
    """A deck the student may add cards to: their own, or a ready deck in their list."""
    deck = viewable_deck_or_404(deck_id)
    if deck.owner_id == current_user.id:
        return deck
    if deck.is_ready and get_user_deck(deck.id) is not None:
        return deck  # new cards here are private to this student
    abort(403)


def editable_card_or_error(deck_id, card_id):
    """A card the student may edit or delete: any card in their own deck,
    or their own private card in a ready deck. Shared ready cards can only be hidden."""
    deck = viewable_deck_or_404(deck_id)
    card = deck_card_or_404(deck, card_id)
    if deck.owner_id != current_user.id and card.owner_id != current_user.id:
        abort(403)
    return deck, card


def new_card_for(deck, **fields):
    """A card owned by the current student (private when the deck is a ready deck)."""
    return Card(deck_id=deck.id, owner_id=current_user.id, **fields)


def get_user_deck(deck_id):
    return db.session.get(UserDeck, (current_user.id, deck_id))


def apply_exam_choice(user_deck, form):
    if form.has_exam.data == "yes":
        user_deck.mode = "exam"
        user_deck.exam_date = form.exam_date.data
    else:
        user_deck.mode = "normal"
        user_deck.exam_date = None


def expire_past_exams(user_id, today):
    """Switch decks whose exam date has passed back to Normal mode.

    We keep the old exam_date so the badge can say "Exam passed".
    Study and the planner call this too, so the engine never sees a past exam.
    """
    past = db.session.query(UserDeck).filter(
        UserDeck.user_id == user_id, UserDeck.mode == "exam", UserDeck.exam_date < today,
    ).all()
    for user_deck in past:
        user_deck.mode = "normal"
    if past:
        db.session.commit()


def mode_badge(user_deck, today):
    """Text and style for the badge on My Decks and the deck page."""
    if user_deck.mode == "exam" and user_deck.exam_date:
        days_left = (user_deck.exam_date - today).days
        if days_left == 0:
            return "Exam today", "exam"
        if days_left == 1:
            return "Exam tomorrow", "exam"
        return f"Exam in {days_left} days", "exam"
    if user_deck.exam_date and user_deck.exam_date < today:
        return "Exam passed. Set a new date?", "passed"
    return "Normal", "normal"


def count_cards(deck_ids, user_id):
    """{deck_id: number of cards this student can see} for the given decks."""
    if not deck_ids:
        return {}
    rows = db.session.query(Card.deck_id, func.count(Card.id)).filter(
        Card.deck_id.in_(deck_ids), visible_to(user_id)
    ).group_by(Card.deck_id).all()
    return dict(rows)


def count_hidden_cards(user_id):
    """{deck_id: number of cards this student has hidden}."""
    rows = db.session.query(Card.deck_id, func.count()).join(
        Progress, Progress.card_id == Card.id
    ).filter(Progress.user_id == user_id, Progress.hidden.is_(True)).group_by(Card.deck_id).all()
    return dict(rows)


def shelf_counts(user_id, deck_id):
    """How many of this deck's cards sit on shelves 1-5 for this student."""
    rows = db.session.query(Progress.shelf, func.count()).join(
        Card, Card.id == Progress.card_id
    ).filter(
        Progress.user_id == user_id, Card.deck_id == deck_id, Progress.hidden.is_(False),
    ).group_by(Progress.shelf).all()
    counts = dict(rows)
    return [counts.get(shelf, 0) for shelf in range(1, 6)]


def deck_topics(deck_id, user_id):
    rows = db.session.query(Card.topic).filter(
        Card.deck_id == deck_id, visible_to(user_id)
    ).distinct().order_by(Card.topic)
    return [topic for (topic,) in rows]


def visible_deck_cards(deck_id, user_id):
    return db.session.query(Card).filter(
        Card.deck_id == deck_id, visible_to(user_id)
    ).order_by(Card.topic, Card.id).all()


def fill_card_from_form(card, form, deck):
    card.question = form.question.data.strip()
    card.answer = form.answer.data.strip()
    card.topic = (form.topic.data or "").strip() or deck.title  # no topic: the deck is the topic
    card.importance = form.importance.data


def exam_form_for(user_deck):
    """An exam form pre-filled with the deck's current setting."""
    if user_deck.mode == "exam":
        return ExamForm(formdata=None, has_exam="yes", exam_date=user_deck.exam_date)
    return ExamForm(formdata=None, has_exam="no")


def first_error(form):
    for errors in form.errors.values():
        if errors:
            return errors[0]
    return "Check the form and try again."


# ---------- Ready decks ----------

@lru_cache(maxsize=1)
def ready_deck_descriptions():
    """{deck title: one-line description}, read once from the seed JSON files.

    The description isn't in the database (no schema change for a line of text);
    it lives next to the deck's cards in seed/ready_decks/.
    """
    folder = Path(current_app.root_path).parent / "seed" / "ready_decks"
    descriptions = {}
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            descriptions[data["title"]] = data.get("description", "")
        except (OSError, ValueError, KeyError):
            continue
    return descriptions


def mastered_counts(user_id, deck_ids):
    """{deck_id: cards on the Mastered shelf (5)} for this student, hidden cards left out."""
    if not deck_ids:
        return {}
    rows = db.session.query(Card.deck_id, func.count()).join(Progress, Progress.card_id == Card.id).filter(
        Progress.user_id == user_id, Card.deck_id.in_(deck_ids), Progress.shelf == 5, Progress.hidden.is_(False),
    ).group_by(Card.deck_id).all()
    return dict(rows)


def my_deck_cards(decks):
    """For My decks: card count only. The topics and preview stay in the Deck Library."""
    counts = count_cards([deck.id for deck in decks], current_user.id)
    return [{"deck": deck, "card_count": counts.get(deck.id, 0)} for deck in decks]


def library_cards(decks):
    """For each deck: card count, its 3 biggest topics, a description and 3 preview cards.
    The Deck Library and My decks show decks with the same card (decks/_deck_card.html)."""
    ids = [deck.id for deck in decks]
    counts = count_cards(ids, current_user.id)
    topic_rows = db.session.query(Card.deck_id, Card.topic, func.count(Card.id)).filter(
        Card.deck_id.in_(ids), visible_to(current_user.id)
    ).group_by(Card.deck_id, Card.topic).all()
    descriptions = ready_deck_descriptions()
    items = []
    for deck in decks:
        topics = sorted((row for row in topic_rows if row[0] == deck.id), key=lambda row: -row[2])
        preview = db.session.query(Card).filter(
            Card.deck_id == deck.id, visible_to(current_user.id)
        ).order_by(Card.id).limit(3).all()
        items.append({
            "deck": deck,
            "card_count": counts.get(deck.id, 0),
            "topics": [topic for _, topic, _ in topics[:3]],
            "description": descriptions.get(deck.title, ""),
            "preview": preview,
        })
    return items


def matches_search(item, query):
    text = " ".join([item["deck"].title, item["description"], *item["topics"]]).lower()
    return all(word in text for word in query.lower().split())


@bp.get("/ready")
@login_required
def ready_decks():
    """The Deck Library: folders of ready decks, with search. The first folder is open by default."""
    query = request.args.get("q", "").strip()[:80]
    folder = request.args.get("folder")
    if folder not in READY_FOLDERS:
        folder = next(iter(READY_FOLDERS))

    folder_counts = dict(
        db.session.query(Deck.folder, func.count(Deck.id))
        .filter(Deck.is_ready.is_(True)).group_by(Deck.folder).all()
    )
    decks = db.session.query(Deck).filter(Deck.is_ready.is_(True)).order_by(Deck.title)
    if not query:
        decks = decks.filter(Deck.folder == folder)
    items = library_cards(decks.all())
    if query:
        items = [item for item in items if matches_search(item, query)]
    added_ids = {
        deck_id for (deck_id,) in db.session.query(UserDeck.deck_id).filter_by(user_id=current_user.id)
    }
    return render_template(
        "decks/ready.html",
        folders=READY_FOLDERS, folder=None if query else folder, folder_counts=folder_counts,
        items=items, added_ids=added_ids, query=query,
    )


@bp.route("/ready/<int:deck_id>/add", methods=["GET", "POST"])
@login_required
def add_ready_deck(deck_id):
    deck = viewable_deck_or_404(deck_id)
    if not deck.is_ready:
        abort(404)
    if get_user_deck(deck.id):
        flash(f"{deck.title} is already in your decks.", "info")
        return redirect(url_for("decks.deck_page", deck_id=deck.id))

    form = ExamForm()
    if form.validate_on_submit():
        user_deck = UserDeck(user_id=current_user.id, deck_id=deck.id)
        apply_exam_choice(user_deck, form)
        db.session.add(user_deck)
        db.session.commit()
        log.info("deck_added", extra={"fields": {"deck_id": deck.id, "mode": user_deck.mode}})
        flash(f"Added {deck.title} to your decks.", "info")
        return redirect(url_for("decks.deck_page", deck_id=deck.id))
    return render_template("decks/add_ready.html", deck=deck, form=form, today=today_local())


@bp.post("/<int:deck_id>/remove")
@login_required
def remove_ready_deck(deck_id):
    """Take a ready deck off the student's list. Their progress is kept."""
    deck = viewable_deck_or_404(deck_id)
    user_deck = get_user_deck(deck.id)
    if not deck.is_ready or user_deck is None:
        abort(404)
    db.session.delete(user_deck)
    db.session.commit()
    flash(f"Removed {deck.title} from your decks. Add it again any time to pick up where you left off.", "info")
    return redirect(url_for("decks.my_decks"))


# ---------- My decks ----------

def my_decks_order(row):
    # Exam decks first (soonest exam on top), then the rest by name,
    # and the Weak spot practice deck at the end.
    user_deck, deck = row
    exam_date = user_deck.exam_date if user_deck.mode == "exam" else date.max
    return (deck.is_weak_spot, exam_date, deck.title.lower())


@bp.get("/mine")
@login_required
def my_decks():
    today = today_local()
    expire_past_exams(current_user.id, today)
    rows = db.session.query(UserDeck, Deck).join(Deck, UserDeck.deck_id == Deck.id).filter(
        UserDeck.user_id == current_user.id
    ).all()
    rows.sort(key=my_decks_order)
    hidden_counts = count_hidden_cards(current_user.id)
    mastered = mastered_counts(current_user.id, [deck.id for _, deck in rows])
    items = my_deck_cards([deck for _, deck in rows])
    for item, (user_deck, deck) in zip(items, rows):
        item["badge"] = mode_badge(user_deck, today)
        item["hidden_count"] = hidden_counts.get(deck.id, 0)
        item["card_count"] -= item["hidden_count"]
        count = item["card_count"]
        item["mastery"] = round(mastered.get(deck.id, 0) * 100 / count) if count else 0
    return render_template("decks/mine.html", items=items, folders=READY_FOLDERS)


def add_cards_url(deck_id, method):
    """The screen for one way of adding cards. Each has a Back link to the three options."""
    if method == "write":
        return url_for("decks.new_card", deck_id=deck_id, back="options")
    return url_for("cardmaker.maker", deck_id=deck_id, mode=method)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_my_own():
    """Create My Own: name the deck and pick Write / Paste / Upload.

    With ?deck_id=, the deck already exists (the student came Back from a screen),
    so we show just the three options for it instead of making another deck.
    """
    if request.args.get("deck_id", "").isdigit():
        deck = deck_for_new_cards_or_error(int(request.args["deck_id"]))
        return render_template(
            "decks/create.html", deck=deck, methods=ADD_METHODS,
            screen_urls={value: add_cards_url(deck.id, value) for value, _, _ in ADD_METHODS},
        )
    form = NewDeckForm()
    if form.validate_on_submit():
        deck = Deck(owner_id=current_user.id, title=form.title.data.strip(), folder="personal")
        db.session.add(deck)
        db.session.flush()  # gives the deck its id
        user_deck = UserDeck(user_id=current_user.id, deck_id=deck.id)
        apply_exam_choice(user_deck, form)
        db.session.add(user_deck)
        db.session.commit()
        log.info("deck_created", extra={"fields": {"deck_id": deck.id, "mode": user_deck.mode}})

        return redirect(add_cards_url(deck.id, form.method.data))
    return render_template("decks/create.html", form=form, deck=None, methods=ADD_METHODS, today=today_local())


# ---------- Deck page ----------

def deck_cards_split(deck):
    """(cards the student studies, cards they have hidden) for this deck."""
    all_cards = visible_deck_cards(deck.id, current_user.id)
    hidden_ids = hidden_card_ids(current_user.id, deck.id)
    cards = [card for card in all_cards if card.id not in hidden_ids]
    hidden_cards = [card for card in all_cards if card.id in hidden_ids]
    return cards, hidden_cards


def cards_url(deck_id, anchor=""):
    return url_for("decks.deck_cards", deck_id=deck_id) + anchor


def settings_url(deck_id, anchor=""):
    return url_for("decks.deck_settings", deck_id=deck_id) + anchor


def managed_deck_or_redirect(deck_id):
    """(deck, user_deck) for the Edit cards and Settings pages, or a redirect to the deck page
    when a Library deck isn't in the student's list yet (add it first)."""
    deck = viewable_deck_or_404(deck_id)
    expire_past_exams(current_user.id, today_local())
    user_deck = get_user_deck(deck.id)
    if user_deck is None and deck.owner_id != current_user.id:
        return deck, None, redirect(url_for("decks.deck_page", deck_id=deck.id))
    return deck, user_deck, None


@bp.get("/<int:deck_id>")
@login_required
def deck_page(deck_id):
    """The study view: title, mode badge, study buttons and one progress bar. Changes live in the edit view."""
    deck = viewable_deck_or_404(deck_id)
    today = today_local()
    expire_past_exams(current_user.id, today)
    user_deck = get_user_deck(deck.id)
    cards, _ = deck_cards_split(deck)
    shelves = shelf_counts(current_user.id, deck.id)
    return render_template(
        "decks/deck.html",
        deck=deck,
        card_count=len(cards),
        user_deck=user_deck,
        badge=mode_badge(user_deck, today) if user_deck else None,
        shelves=shelves,
        shelf_names=SHELF_NAMES,
        not_studied=len(cards) - sum(shelves),
    )


@bp.get("/<int:deck_id>/edit")
@login_required
def edit_deck(deck_id):
    """The old edit view; it is now split into Edit cards and Settings."""
    return redirect(cards_url(deck_id))


@bp.get("/<int:deck_id>/cards")
@login_required
def deck_cards(deck_id):
    """Edit cards: add, paste or upload notes, generate more, and the full card list."""
    deck, user_deck, leave = managed_deck_or_redirect(deck_id)
    if leave:
        return leave
    cards, hidden_cards = deck_cards_split(deck)
    can_edit = deck.owner_id == current_user.id
    return render_template(
        "decks/cards.html",
        deck=deck,
        cards=cards,
        hidden_cards=hidden_cards,
        show_hidden=request.args.get("show_hidden") == "1",
        user_deck=user_deck,
        can_edit=can_edit,
        can_add_cards=can_edit or deck.is_ready,
    )


@bp.get("/<int:deck_id>/settings")
@login_required
def deck_settings(deck_id):
    """Settings: rename (own decks), exam date, and delete or remove the deck."""
    deck, user_deck, leave = managed_deck_or_redirect(deck_id)
    if leave:
        return leave
    can_edit = deck.owner_id == current_user.id
    today = today_local()
    return render_template(
        "decks/settings.html",
        deck=deck,
        user_deck=user_deck,
        can_edit=can_edit,
        card_count=len(deck_cards_split(deck)[0]),
        title_form=DeckTitleForm(obj=deck) if can_edit and not deck.is_weak_spot else None,
        badge=mode_badge(user_deck, today) if user_deck else None,
        exam_form=exam_form_for(user_deck) if user_deck else None,
        today=today,
    )


@bp.post("/<int:deck_id>/exam")
@login_required
def update_exam(deck_id):
    deck = viewable_deck_or_404(deck_id)
    user_deck = get_user_deck(deck.id)
    if user_deck is None:
        abort(404)
    form = ExamForm()
    if form.validate_on_submit():
        apply_exam_choice(user_deck, form)
        db.session.commit()
        flash("Exam setting saved.", "info")
    else:
        flash(first_error(form), "error")
    return redirect(settings_url(deck.id, "#exam"))


@bp.post("/<int:deck_id>/rename")
@login_required
def rename_deck(deck_id):
    deck = own_deck_or_error(deck_id)
    if deck.is_weak_spot:
        abort(403)  # the app manages this deck's name
    form = DeckTitleForm()
    if form.validate_on_submit():
        deck.title = form.title.data.strip()
        db.session.commit()
        flash("Deck renamed.", "info")
    else:
        flash(first_error(form), "error")
    return redirect(settings_url(deck.id))


@bp.post("/<int:deck_id>/delete")
@login_required
def delete_deck(deck_id):
    deck = own_deck_or_error(deck_id)
    if deck.is_weak_spot:
        abort(403)  # the Fixer needs this deck
    title = deck.title
    db.session.delete(deck)  # cards, progress and reviews go with it
    db.session.commit()
    log.info("deck_deleted", extra={"fields": {"deck_id": deck_id}})
    flash(f"Deleted {title} and its cards.", "info")
    return redirect(url_for("decks.my_decks"))


# ---------- Cards ----------

@bp.route("/<int:deck_id>/cards/new", methods=["GET", "POST"])
@login_required
def new_card(deck_id):
    deck = deck_for_new_cards_or_error(deck_id)
    form = CardForm()
    if form.validate_on_submit():
        card = new_card_for(deck, source="manual")
        fill_card_from_form(card, form, deck)
        db.session.add(card)
        db.session.commit()
        if "save_next" in request.form:
            flash("Card saved. Add the next one.", "info")
            return redirect(url_for("decks.new_card", deck_id=deck.id, back=request.args.get("back")))
        flash("Card saved.", "info")
        return redirect(cards_url(deck.id))
    return render_template(
        "decks/card_form.html", deck=deck, form=form, card=None, topics=deck_topics(deck.id, current_user.id),
        long_front=LONG_FRONT_CHARS, long_back=LONG_BACK_CHARS,
    )


@bp.route("/<int:deck_id>/cards/<int:card_id>/edit", methods=["GET", "POST"])
@login_required
def edit_card(deck_id, card_id):
    deck, card = editable_card_or_error(deck_id, card_id)
    if request.method == "GET":
        return redirect(url_for("cards.card_page", card_id=card.id))  # each card has its own page now
    form = CardForm(obj=card)
    if form.validate_on_submit():
        fill_card_from_form(card, form, deck)
        card.explanation = None  # the cached Explain text no longer matches
        db.session.commit()
        flash("Card updated.", "info")
        return redirect(cards_url(deck.id, f"#card-{card.id}"))
    return render_template(
        "decks/card_form.html", deck=deck, form=form, card=card, topics=deck_topics(deck.id, current_user.id),
        long_front=LONG_FRONT_CHARS, long_back=LONG_BACK_CHARS,
    )


def ready_deck_card_or_error(deck_id, card_id):
    """A shared card in a ready deck that is in the student's list. Only these can be hidden."""
    deck = viewable_deck_or_404(deck_id)
    if not deck.is_ready:
        abort(403)  # own decks: delete the card instead
    if get_user_deck(deck.id) is None:
        abort(404)
    card = deck_card_or_404(deck, card_id)
    if card.owner_id is not None:
        abort(403)  # the student's own private card: delete it instead
    return deck, card


@bp.post("/<int:deck_id>/cards/<int:card_id>/hide")
@login_required
def hide_card(deck_id, card_id):
    """Hide a shared card for this student only. Other students still see it."""
    deck, card = ready_deck_card_or_error(deck_id, card_id)
    progress = db.session.get(Progress, (current_user.id, card.id))
    if progress is None:
        progress = Progress(user_id=current_user.id, card_id=card.id, shelf=1, next_due=today_local(),
                            wrong_count=0, misconception_count=0)
        db.session.add(progress)
    progress.hidden = True
    db.session.commit()
    flash("Card hidden. You won't see it when you study this deck.", "info")
    return redirect(cards_url(deck.id))


@bp.post("/<int:deck_id>/cards/<int:card_id>/unhide")
@login_required
def unhide_card(deck_id, card_id):
    deck, card = ready_deck_card_or_error(deck_id, card_id)
    progress = db.session.get(Progress, (current_user.id, card.id))
    if progress is not None and progress.hidden:
        if progress.last_seen is None:
            # Hidden before it was ever answered: drop the row so it counts
            # as a new card again (and uses the daily new-card limit).
            db.session.delete(progress)
        else:
            progress.hidden = False
        db.session.commit()
    flash("Card is back in your study.", "info")
    return redirect(url_for("decks.deck_cards", deck_id=deck.id, show_hidden=1))


@bp.post("/<int:deck_id>/cards/<int:card_id>/delete")
@login_required
def delete_card(deck_id, card_id):
    deck, card = editable_card_or_error(deck_id, card_id)
    db.session.delete(card)
    db.session.commit()
    flash("Card deleted.", "info")
    return redirect(cards_url(deck.id))


# ---------- One card's own page: /cards/<id> ----------

cards_bp = Blueprint("cards", __name__, url_prefix="/cards")


@cards_bp.route("/<int:card_id>", methods=["GET", "POST"])
@login_required
def card_page(card_id):
    """One card: edit fields with Save and Delete when the student may change it,
    or read-only with Hide for a shared Library card."""
    card = db.session.get(Card, card_id)
    if card is None:
        abort(404)
    deck = viewable_deck_or_404(card.deck_id)
    card = deck_card_or_404(deck, card.id)
    user_deck = get_user_deck(deck.id)
    if user_deck is None and deck.owner_id != current_user.id:
        abort(404)
    can_change = deck.owner_id == current_user.id or card.owner_id == current_user.id
    form = CardForm(obj=card) if can_change else None
    if can_change and form.validate_on_submit():
        fill_card_from_form(card, form, deck)
        card.explanation = None  # the cached Explain text no longer matches
        db.session.commit()
        flash("Card saved.", "info")
        return redirect(url_for("cards.card_page", card_id=card.id))
    return render_template(
        "decks/card_page.html", deck=deck, card=card, form=form, can_change=can_change,
        hidden=card.id in hidden_card_ids(current_user.id, deck.id),
        topics=deck_topics(deck.id, current_user.id), long_front=LONG_FRONT_CHARS, long_back=LONG_BACK_CHARS,
    )
