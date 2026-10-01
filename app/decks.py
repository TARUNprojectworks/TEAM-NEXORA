"""Ready decks, my decks, deck page and card create/edit/delete.

Routes
------
GET  /decks/ready              – Browse all ready decks by folder
GET  /decks/ready/<int:id>     – View a single ready deck (card list)
POST /decks/ready/<int:id>/add – Add a ready deck to the student's collection
GET  /decks/mine               – My decks list
GET  /decks/new                – Create my own deck (scaffold only)
"""

import logging
from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.models import Card, Deck, UserDeck, db

bp = Blueprint("decks", __name__, url_prefix="/decks")
log = logging.getLogger("nexora.decks")

# Folder display metadata
FOLDER_META = {
    "semester":    {"label": "Semester",    "icon": "📚", "description": "Core subject decks for your semester exams."},
    "placement":   {"label": "Placement",   "icon": "💼", "description": "DSA, DBMS, and OS — everything for placement rounds."},
    "competitive": {"label": "Competitive", "icon": "🏆", "description": "Aptitude, reasoning, and GK for competitive exams."},
}


# Category metadata mapping
CATEGORY_MAP = {
    "Aptitude": {"icon": "🧮", "color": "#4F46E5", "filter": "aptitude"},
    "Programming": {"icon": "💻", "color": "#7C3AED", "filter": "programming"},
    "DSA": {"icon": "🌲", "color": "#2563EB", "filter": "dsa"},
    "Core CS": {"icon": "🖥️", "color": "#059669", "filter": "core_cs"},
    "Interview": {"icon": "💼", "color": "#D97706", "filter": "interview"},
    "Semester": {"icon": "📚", "color": "#DC2626", "filter": "semester"},
}


def normalize_question_count(deck_id: int, raw_value) -> int:
    """Clamp the requested question count to the actual number of cards."""
    total = Card.query.filter_by(deck_id=deck_id).count()
    if total <= 0:
        return 0

    if raw_value is None or str(raw_value).strip() == "":
        return total

    value = str(raw_value).strip().lower()
    if value in {"all", "full", "max"}:
        return total

    try:
        requested = int(value)
    except (TypeError, ValueError):
        return total

    if requested <= 0:
        return total
    return min(requested, total)


def get_deck_category(deck: Deck) -> str:
    """Determine deck category tag."""
    title_lower = deck.title.lower()
    if any(k in title_lower for k in ("aptitude", "reasoning", "quant", "pipes", "probability")):
        return "Aptitude"
    elif any(k in title_lower for k in ("dsa", "arrays", "trees", "sorting", "graphs")):
        return "DSA"
    elif any(k in title_lower for k in ("java", "python", "oop", "programming")):
        return "Programming"
    elif any(k in title_lower for k in ("dbms", "os", "operating", "database", "networks")):
        return "Core CS"
    elif deck.folder == "placement":
        return "Interview"
    elif deck.folder == "competitive":
        return "Aptitude"
    return "Semester"


def get_deck_difficulty(deck: Deck, cards: list[Card]) -> str:
    """Calculate deck difficulty based on cards."""
    if not cards:
        return "Medium"
    high_count = sum(1 for c in cards if c.importance in ("high", "core", "5"))
    if high_count > len(cards) * 0.4:
        return "Hard"
    elif high_count > len(cards) * 0.2:
        return "Medium"
    return "Beginner"


def get_deck_description(deck: Deck) -> str:
    """Generate or retrieve a concise description for deck cards."""
    t = deck.title.lower()
    if "dsa" in t:
        return "Master core data structures, algorithms, and complexity trade-offs."
    elif "dbms" in t:
        return "SQL joins, ACID properties, indexing, and normalization essentials."
    elif "os" in t:
        return "Processes, CPU scheduling, virtual memory, and deadlocks."
    elif "aptitude" in t or "pipes" in t:
        return "Quantitative shortcuts, probability, percentages, and time & work."
    elif "reasoning" in t:
        return "Syllogisms, logical sequences, arrangements, and pattern deductions."
    elif "gk" in t:
        return "Key science facts, modern technology, and governance milestones."
    elif "biology" in t:
        return "Cell structure, genetics, molecular processes, and evolution."
    elif "physics" in t:
        return "Mechanics, wave equations, thermodynamics, and electromagnetism."
    elif "maths" in t:
        return "Calculus limits, linear algebra, matrices, and differential equations."
    return f"Comprehensive flashcards to test and retain key {deck.title} concepts."


# ---------------------------------------------------------------------------
# Ready Decks — Browse & My Learning Dashboard
# ---------------------------------------------------------------------------

@bp.get("/ready")
@login_required
def ready_decks():
    """Show modern deck selection dashboard with filters, search, and progress."""
    all_ready = Deck.query.filter_by(is_ready=True).order_by(Deck.folder, Deck.title).all()

    # User's added decks
    user_deck_map = {
        ud.deck_id: ud
        for ud in UserDeck.query.filter_by(user_id=current_user.id).all()
    }

    # Gather all cards for ready decks to compute topics, count, and progress
    from app.models import Progress, today_local
    today = today_local()

    decks_data = []
    for deck in all_ready:
        cards = Card.query.filter_by(deck_id=deck.id).all()
        card_count = len(cards)
        category = get_deck_category(deck)
        cat_meta = CATEGORY_MAP.get(category, {"icon": "📖", "color": "#4F46E5", "filter": "all"})

        # Unique topics for "What you'll learn"
        topics = list(dict.fromkeys(c.topic for c in cards if c.topic))[:4]
        if not topics:
            topics = ["Core concepts & definitions", "Standard formulas & rules", "Practice problems"]

        # Calculate user progress on this deck's cards
        card_ids = [c.id for c in cards]
        user_progress = (
            Progress.query
            .filter(Progress.user_id == current_user.id, Progress.card_id.in_(card_ids))
            .all() if card_ids else []
        )
        
        learned_count = sum(1 for p in user_progress if p.shelf >= 2)
        progress_pct = int((learned_count / card_count * 100)) if card_count > 0 else 0

        # Status
        if progress_pct == 0:
            status = "not_started"
            action_label = "Start Learning →"
        elif progress_pct >= 100:
            status = "completed"
            action_label = "Review Again →"
        else:
            status = "in_progress"
            action_label = "Continue Learning →"

        # Last studied info
        last_seen_dates = [p.last_seen for p in user_progress if p.last_seen]
        if last_seen_dates:
            latest = max(last_seen_dates).date()
            if latest == today:
                last_studied_text = "Studied today"
            elif latest == today - timedelta(days=1):
                last_studied_text = "Studied yesterday"
            else:
                last_studied_text = f"Studied {latest.strftime('%d %b')}"
        else:
            last_studied_text = "Never opened"

        decks_data.append({
            "id": deck.id,
            "title": deck.title,
            "category": category,
            "category_icon": cat_meta["icon"],
            "category_filter": cat_meta["filter"],
            "description": get_deck_description(deck),
            "card_count": card_count,
            "difficulty": get_deck_difficulty(deck, cards),
            "est_minutes": max(5, int(card_count * 0.5)),
            "progress_percent": progress_pct,
            "status": status,
            "action_label": action_label,
            "last_studied": last_studied_text,
            "topics": topics,
            "is_added": deck.id in user_deck_map,
        })

    # Available category filters
    filters = [
        {"name": "All", "key": "all", "active": True},
        {"name": "Aptitude", "key": "aptitude", "active": False},
        {"name": "Programming", "key": "programming", "active": False},
        {"name": "DSA", "key": "dsa", "active": False},
        {"name": "Core CS", "key": "core_cs", "active": False},
        {"name": "Interview", "key": "interview", "active": False},
        {"name": "Semester", "key": "semester", "active": False},
    ]

    return render_template(
        "decks/ready_decks.html",
        decks=decks_data,
        filters=filters,
        user=current_user,
    )


# ---------------------------------------------------------------------------
# Ready Deck Detail — View Cards
# ---------------------------------------------------------------------------

@bp.get("/ready/<int:deck_id>")
@login_required
def ready_deck_detail(deck_id: int):
    """View the cards in a single ready deck."""
    deck = Deck.query.filter_by(id=deck_id, is_ready=True).first_or_404()
    cards = Card.query.filter_by(deck_id=deck.id).order_by(Card.topic, Card.id).all()

    already_added = (
        UserDeck.query.filter_by(user_id=current_user.id, deck_id=deck.id).first()
        is not None
    )

    # Group cards by topic for display
    topics: dict[str, list[Card]] = {}
    for card in cards:
        topics.setdefault(card.topic, []).append(card)

    folder_info = FOLDER_META.get(deck.folder, {"label": deck.folder.title(), "icon": "📖"})

    return render_template(
        "decks/ready_deck_detail.html",
        deck=deck,
        topics=topics,
        cards=cards,
        already_added=already_added,
        folder_info=folder_info,
    )


# ---------------------------------------------------------------------------
# Add Ready Deck to My Collection
# ---------------------------------------------------------------------------

@bp.post("/ready/<int:deck_id>/add")
@login_required
def add_ready_deck(deck_id: int):
    """Add a ready deck to current_user's collection."""
    deck = Deck.query.filter_by(id=deck_id, is_ready=True).first_or_404()

    already = UserDeck.query.filter_by(user_id=current_user.id, deck_id=deck.id).first()
    if already:
        flash(f'"{deck.title}" is already in your decks.', "info")
        return redirect(url_for("decks.ready_decks"))

    mode = request.form.get("mode", "normal")
    if mode not in ("normal", "exam"):
        mode = "normal"

    exam_date_str = request.form.get("exam_date", "").strip()
    exam_date = None
    if mode == "exam" and exam_date_str:
        from datetime import date as date_type
        try:
            exam_date = date_type.fromisoformat(exam_date_str)
        except ValueError:
            flash("Invalid exam date — deck added in normal mode.", "warning")
            mode = "normal"

    question_count = normalize_question_count(deck.id, request.form.get("question_count"))
    deck_question_counts = session.get("deck_question_counts", {})
    deck_question_counts[str(deck.id)] = question_count
    session["deck_question_counts"] = deck_question_counts

    ud = UserDeck(user_id=current_user.id, deck_id=deck.id, mode=mode, exam_date=exam_date)
    db.session.add(ud)
    db.session.commit()

    log.info(
        "ready_deck_added",
        extra={"fields": {"deck_id": deck.id, "mode": mode, "question_count": question_count}},
    )
    flash(f'"{deck.title}" added to your decks! {question_count} questions selected.', "success")
    return redirect(url_for("decks.my_decks"))


# ---------------------------------------------------------------------------
# My Decks
# ---------------------------------------------------------------------------

@bp.get("/mine")
@login_required
def my_decks():
    """Show the student's own deck collection."""
    # User's ready decks (added via add_ready_deck)
    user_deck_rows = (
        UserDeck.query
        .filter_by(user_id=current_user.id)
        .join(UserDeck.deck)
        .order_by(Deck.folder, Deck.title)
        .all()
    )

    # Exclude the hidden weak-spot deck from the list
    user_deck_rows = [ud for ud in user_deck_rows if not ud.deck.is_weak_spot]

    # Card counts
    card_counts = {
        ud.deck_id: Card.query.filter_by(deck_id=ud.deck_id).count()
        for ud in user_deck_rows
    }

    from app.models import today_local
    today = today_local()

    return render_template(
        "decks/my_decks.html",
        user_decks=user_deck_rows,
        card_counts=card_counts,
        folder_meta=FOLDER_META,
        today=today,
    )


# ---------------------------------------------------------------------------
# Create My Own (scaffold — AI Card Maker owner fills this in)
# ---------------------------------------------------------------------------

@bp.get("/new")
@login_required
def create_my_own():
    return render_template("placeholder.html", title="Create my own")
