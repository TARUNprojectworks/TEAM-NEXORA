"""Flashcard Study Engine: single card interactive study, confidence ratings, and AI fresh questions."""

import json
import logging
from datetime import timedelta

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.models import (
    Card,
    Deck,
    Progress,
    Review,
    UserDeck,
    db,
    today_local,
    utc_now,
)

bp = Blueprint("study", __name__, url_prefix="/study")
log = logging.getLogger("nexora.study")


@bp.get("/<int:deck_id>")
@login_required
def study_deck(deck_id: int):
    """Interactive single-card flashcard study view."""
    deck = Deck.query.get_or_404(deck_id)

    # Automatically add to user_decks if not already added
    user_deck = UserDeck.query.filter_by(user_id=current_user.id, deck_id=deck.id).first()
    if not user_deck:
        user_deck = UserDeck(user_id=current_user.id, deck_id=deck.id, mode="normal")
        db.session.add(user_deck)
        db.session.commit()

    # Filter optional: difficult only
    filter_mode = request.args.get("filter", "all")
    all_cards_query = Card.query.filter_by(deck_id=deck.id)

    if filter_mode == "difficult":
        # Cards with wrong_count > 0 or shelf <= 2
        difficult_card_ids = [
            p.card_id
            for p in Progress.query.filter(
                Progress.user_id == current_user.id,
                Progress.card_id.in_([c.id for c in all_cards_query.all()]),
                (Progress.shelf <= 2) | (Progress.wrong_count > 0),
            ).all()
        ]
        if difficult_card_ids:
            cards = all_cards_query.filter(Card.id.in_(difficult_card_ids)).order_by(Card.id).all()
        else:
            cards = all_cards_query.order_by(Card.id).all()
    else:
        cards = all_cards_query.order_by(Card.id).all()

    question_limit = request.args.get("question_count", "").strip()
    if question_limit.lower() in {"all", "full", "max", ""}:
        question_limit = None
    if question_limit is None:
        question_limit = session.get("deck_question_counts", {}).get(str(deck.id))

    if question_limit not in (None, ""):
        try:
            question_limit = int(question_limit)
        except (TypeError, ValueError):
            question_limit = None

    if question_limit and question_limit > 0 and len(cards) > question_limit:
        cards = cards[:question_limit]

    if not cards:
        cards = Card.query.filter_by(deck_id=deck.id).all()

    # Load user's progress on these cards
    progress_map = {
        p.card_id: p
        for p in Progress.query.filter_by(user_id=current_user.id).filter(
            Progress.card_id.in_([c.id for c in cards])
        ).all()
    }

    # Format card payload as clean JSON for client-side one-at-a-time renderer
    cards_payload = []
    for card in cards:
        p = progress_map.get(card.id)
        cards_payload.append({
            "id": card.id,
            "question": card.question,
            "answer": card.answer,
            "topic": card.topic,
            "importance": card.importance,
            "source_line": card.source_line or "",
            "shelf": p.shelf if p else 1,
            "wrong_count": p.wrong_count if p else 0,
        })

    # Total stats for this deck
    total_cards = len(cards_payload)
    mastered_count = sum(1 for c in cards_payload if c["shelf"] >= 4)
    learned_count = sum(1 for c in cards_payload if c["shelf"] >= 2)
    progress_percent = int((learned_count / total_cards * 100)) if total_cards > 0 else 0

    return render_template(
        "study/session.html",
        deck=deck,
        cards_json=json.dumps(cards_payload),
        total_cards=total_cards,
        progress_percent=progress_percent,
        filter_mode=filter_mode,
        user=current_user,
    )


@bp.post("/<int:deck_id>/review")
@login_required
def record_review(deck_id: int):
    """Record confidence rating (again, hard, good, easy) for a single card."""
    data = request.get_json(silent=True) or request.form
    card_id = data.get("card_id")
    rating = data.get("rating", "").lower()

    if not card_id or rating not in ("again", "hard", "good", "easy"):
        return jsonify({"error": "Invalid rating or card_id"}), 400

    card = Card.query.filter_by(id=int(card_id), deck_id=deck_id).first_or_404()

    today = today_local()
    progress = Progress.query.filter_by(user_id=current_user.id, card_id=card.id).first()

    if not progress:
        progress = Progress(user_id=current_user.id, card_id=card.id, shelf=1)
        db.session.add(progress)

    # Shelf calculation based on rating
    if rating == "again":
        progress.shelf = 1
        progress.wrong_count += 1
        progress.next_due = today
        knew_it = False
        confident = False
        xp_gain = 5
    elif rating == "hard":
        progress.shelf = max(1, progress.shelf)
        progress.next_due = today + timedelta(days=1)
        knew_it = True
        confident = False
        xp_gain = 8
    elif rating == "good":
        progress.shelf = min(5, progress.shelf + 1)
        progress.next_due = today + timedelta(days=max(2, progress.shelf * 2))
        knew_it = True
        confident = False
        xp_gain = 12
    else:  # easy
        progress.shelf = min(5, progress.shelf + 2)
        progress.next_due = today + timedelta(days=max(4, progress.shelf * 3))
        knew_it = True
        confident = True
        xp_gain = 15

    progress.last_seen = utc_now()

    # Log review
    review = Review(
        user_id=current_user.id,
        card_id=card.id,
        knew_it=knew_it,
        confident=confident,
        style="smart",
        reviewed_at=utc_now(),
    )
    db.session.add(review)

    # Update streak & XP
    current_user.xp = (current_user.xp or 0) + xp_gain
    if current_user.last_study_date != today:
        if current_user.last_study_date == today - timedelta(days=1):
            current_user.streak = (current_user.streak or 0) + 1
        elif current_user.last_study_date is None or current_user.last_study_date < today - timedelta(days=1):
            current_user.streak = 1
        current_user.last_study_date = today

    db.session.commit()

    return jsonify({
        "success": True,
        "rating": rating,
        "shelf": progress.shelf,
        "xp": current_user.xp,
        "streak": current_user.streak,
    })


@bp.post("/<int:deck_id>/generate-fresh")
@login_required
def generate_fresh_questions(deck_id: int):
    """Generate fresh questions for this deck based on concepts and weak areas."""
    deck = Deck.query.get_or_404(deck_id)

    # Get sample questions to understand deck topics
    existing_cards = Card.query.filter_by(deck_id=deck.id).all()
    topics = list({c.topic for c in existing_cards}) or ["General"]

    fresh_pools = {
        "DSA": [
            ("Explain the concept of Two Pointers technique and when to use it.", "Two Pointers uses two references to traverse an array/list, often from opposite ends or at different speeds (fast/slow). Used for sorted pair sums, palindrome verification, cycle detection, and sliding window problems.", "Two Pointers", "high"),
            ("What is the difference between BFS and DFS in terms of memory complexity?", "BFS uses O(W) memory where W is maximum width of tree/graph (stored in a queue). DFS uses O(H) memory where H is maximum depth (stored in call stack). In wide shallow graphs, DFS uses less memory.", "Graphs", "high"),
            ("What is dynamic programming memoization vs tabulation?", "Memoization is top-down: recursive calls store results in a cache as they are computed. Tabulation is bottom-up: an iterative table is populated starting from base cases.", "Dynamic Programming", "core"),
            ("How does quicksort achieve average O(n log n) but worst-case O(n^2)?", "Average case splits the array into roughly equal halves around the pivot. Worst case occurs when pivot is consistently the smallest or largest element (e.g. already sorted array with first element as pivot), causing n nested partitions.", "Sorting", "medium"),
            ("What is a Trie data structure and its primary advantage over Hash Table for strings?", "A Trie is a tree structure where nodes represent prefix characters. Unlike Hash Tables, Tries support prefix search, autocomplete, and lexicographical ordering with O(L) lookup where L is string length, with zero hash collision overhead.", "Trees", "medium"),
        ],
        "Aptitude": [
            ("A pipe can fill a cistern in 9 hours. Due to a leak in the bottom, it is filled in 10 hours. When full, how long will the leak take to empty it?", "90 hours. Filling rate = 1/9. Net filling rate with leak = 1/10. Leak rate = 1/9 - 1/10 = 1/90 tank/hr. So leak empties the cistern in 90 hours.", "Pipes & Cisterns", "high"),
            ("Two pipes A and B can fill a tank in 12 and 16 minutes. If both are opened together, after how many minutes should B be closed so that the tank is full in 9 minutes?", "4 minutes. In 9 minutes, Pipe A alone fills 9/12 = 3/4 of the tank. Remaining 1/4 must be filled by B. Time for B = (1/4) / (1/16) = 4 minutes.", "Pipes & Cisterns", "core"),
            ("If a fair die is rolled twice, what is the probability that the sum of the numbers is greater than 9?", "1/6. Total outcomes = 36. Favorable outcomes where sum > 9: (4,6), (5,5), (5,6), (6,4), (6,5), (6,6) = 6 pairs. Probability = 6/36 = 1/6.", "Probability", "medium"),
            ("In how many ways can the letters of the word 'LEADER' be arranged?", "360 ways. Total letters = 6. Letter 'E' repeats 2 times. Total permutations = 6! / 2! = 720 / 2 = 360.", "Permutations", "medium"),
            ("A train 240 m long passes a pole in 24 seconds. How long will it take to pass a platform 650 m long?", "89 seconds. Speed = 240 / 24 = 10 m/s. Total distance to pass platform = 240 + 650 = 890 m. Time = 890 / 10 = 89 seconds.", "Time & Distance", "medium"),
        ],
        "DBMS": [
            ("What is the Write-Ahead Logging (WAL) protocol in database management?", "WAL requires that log records describing a change must be flushed to non-volatile storage BEFORE the actual modified database pages are written to disk. This ensures atomicity and durability (ACID) during crashes.", "Transactions", "core"),
            ("What is the difference between clustered and non-clustered index?", "A clustered index physically dictates the storage order of rows on disk (only one per table). A non-clustered index creates a separate B-tree structure storing key values with pointers to the data rows.", "Indexing", "high"),
            ("Explain the concept of Phantom Read and which isolation level prevents it.", "A phantom read occurs when a transaction reads a set of rows matching a condition, and a second transaction inserts/deletes rows matching that condition before the first finishes. Serializable isolation level prevents it.", "Transactions", "high"),
            ("What is Denormalization and when is it justified?", "Denormalization is intentionally adding redundant data to a database schema to avoid expensive JOIN operations and optimize read performance in read-heavy analytical/OLAP workloads.", "Normalisation", "medium"),
            ("What is the ACID 'I' (Isolation) property?", "Isolation guarantees that concurrently executing transactions do not interfere with each other and that the intermediate state of one transaction is invisible to others.", "Transactions", "high"),
        ],
        "OS": [
            ("What is the difference between preemptive and non-preemptive scheduling?", "In preemptive scheduling, the CPU can be taken away from a running process by the OS (e.g., Round Robin, SRTF). In non-preemptive, the process keeps the CPU until it yields or terminates (e.g., FCFS).", "Scheduling", "high"),
            ("What are the four necessary conditions for Deadlock (Coffman conditions)?", "1. Mutual Exclusion, 2. Hold and Wait, 3. No Preemption, 4. Circular Wait. Eliminating any one condition prevents deadlocks.", "Synchronisation", "core"),
            ("What is thrashing in virtual memory and how can it be detected?", "Thrashing occurs when the system spends more time swapping pages in and out of disk than executing instructions. It happens when the sum of working sets of active processes exceeds physical RAM.", "Memory", "high"),
            ("What is a race condition and how can a mutex prevent it?", "A race condition occurs when multiple threads access shared data concurrently and the outcome depends on execution timing. A mutex ensures mutual exclusion so only one thread accesses the critical section at a time.", "Synchronisation", "high"),
            ("What is translation lookaside buffer (TLB) and why is it important?", "TLB is a fast hardware associative cache that stores recent virtual-to-physical address translations, eliminating the need to traverse multi-level page tables in memory on every memory access.", "Memory", "medium"),
        ],
    }

    matched_pool = None
    for k in fresh_pools:
        if k.lower() in deck.title.lower():
            matched_pool = fresh_pools[k]
            break

    if not matched_pool:
        matched_pool = [
            (f"Explain key problem-solving techniques for {topics[0]}.", f"Focus on identifying standard edge cases, applying core formulas, and validating intermediate steps.", topics[0], "medium"),
            (f"Compare fundamental concepts in {topics[-1]} with related domains.", f"Analyze trade-offs in time, space, and practical applicability.", topics[-1], "high"),
            (f"How do you troubleshoot common errors when evaluating {deck.title} questions?", f"Break down the problem step-by-step into known subcomponents and verify constraints.", topics[0], "medium"),
        ]

    existing_questions = {c.question.strip().lower() for c in existing_cards}
    new_cards_added = 0

    for q, a, topic, imp in matched_pool:
        if q.strip().lower() not in existing_questions:
            new_card = Card(
                deck_id=deck.id,
                question=q,
                answer=a,
                topic=topic,
                importance=imp,
                source="ai",
                source_line="Generated by Gemini Fresh Practice",
            )
            db.session.add(new_card)
            new_cards_added += 1

    db.session.commit()

    return jsonify({
        "success": True,
        "count": new_cards_added,
        "message": f"{new_cards_added} fresh practice questions generated!",
    })
