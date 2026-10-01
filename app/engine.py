"""The study engine: shelves, confidence rules, priority score, weak spot trigger.

Everything here is plain Python. Functions take `today` as an argument and
never touch the database or Flask, so each rule is easy to test and explain.
`progress` is anything with shelf, next_due, wrong_count, misconception_count
and last_seen (the Progress model, or a stand-in in tests). A progress of
None means the student has never seen the card.
"""

from collections import namedtuple
from datetime import datetime, timedelta
from types import SimpleNamespace

# Days to wait before a card on each shelf is due again.
SHELF_WAIT_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}
MAX_SHELF = 5

IMPORTANCE_POINTS = {"high": 6, "medium": 4, "low": 2}
MAX_OVERDUE_POINTS = 5
MISCONCEPTION_POINTS = 3

# New (never seen) cards per student per day, shared by Smart Study and the planner.
NEW_CARDS_PER_DAY = 10

# A "Review again" card comes back after this many other cards.
REQUEUE_GAP = 3

# Weak spot trigger
MIN_CARDS_SEEN = 3
WEAK_SCORE_TRIGGER = 0.5
MISCONCEPTION_CARDS_TRIGGER = 2

# One card to choose from in Smart Study, with its deck's mode.
Candidate = namedtuple("Candidate", "card progress mode exam_date")


# ---------- Shelves and waiting ----------

def days_left_until(exam_date, today):
    if exam_date is None:
        return None
    return (exam_date - today).days


def wait_days(shelf, mode, days_left):
    """Days until a card on this shelf is due again."""
    wait = SHELF_WAIT_DAYS[shelf]
    # Exam mode: never schedule a card for after the exam. The last review
    # should be at least a day before it, or today if the exam is that close.
    if mode == "exam" and days_left is not None:
        wait = min(wait, max(days_left - 1, 0))
    return wait


def next_shelf(shelf, knew_it, confident):
    """The 4 confidence rules."""
    if knew_it and confident:
        return min(shelf + 1, MAX_SHELF)  # Know it + Sure: up one shelf
    if knew_it:
        return shelf                      # Know it + Unsure: a lucky guess, stay put
    return 1                              # Review again (Sure or Unsure): start over


def apply_answer(progress, knew_it, confident, today, mode="normal", exam_date=None, seen_at=None):
    """Update one card's progress after an answer, and set when it is due next."""
    progress.shelf = next_shelf(progress.shelf, knew_it, confident)
    if not knew_it:
        progress.wrong_count += 1
        if confident:
            # Sure but wrong: the student believes something false.
            # That is worse than not knowing, so we track it separately.
            progress.misconception_count += 1

    wait = wait_days(progress.shelf, mode, days_left_until(exam_date, today))
    progress.next_due = today + timedelta(days=wait)
    progress.last_seen = seen_at
    return progress


def is_due(progress, today):
    return progress.next_due <= today


def progress_or_new(progress, today):
    """A never-seen card counts as shelf 1, due today, with no mistakes yet."""
    if progress is not None:
        return progress
    return SimpleNamespace(shelf=1, next_due=today, wrong_count=0, misconception_count=0, last_seen=None)


# ---------- Priority score ----------

def exam_urgency(mode, days_left):
    if mode != "exam" or days_left is None:
        return 0
    if days_left <= 3:
        return 5
    if days_left <= 7:
        return 3
    if days_left <= 30:
        return 1
    return 0


def priority_score(progress, card, today, mode="normal", exam_date=None):
    """How useful it is to show this card now. Higher shows first."""
    progress = progress_or_new(progress, today)
    weakness = (6 - progress.shelf) * 2                    # lower shelf = less known
    importance = IMPORTANCE_POINTS[card.importance]
    urgency = exam_urgency(mode, days_left_until(exam_date, today))
    days_overdue = (today - progress.next_due).days
    forgetting_risk = min(max(days_overdue, 0), MAX_OVERDUE_POINTS)
    misconception = MISCONCEPTION_POINTS if progress.misconception_count > 0 else 0
    return weakness + importance + urgency + forgetting_risk + misconception


def rank_candidates(candidates, today):
    """Due cards, best first. Ties: more mistakes first, then the one seen longest ago."""
    def sort_key(candidate):
        progress = progress_or_new(candidate.progress, today)
        score = priority_score(candidate.progress, candidate.card, today, candidate.mode, candidate.exam_date)
        never_seen = progress.last_seen is None
        # A never-seen card has no "last seen" to compare, so on a full tie
        # it goes after cards the student has already met.
        last_seen = progress.last_seen or datetime.min
        return (-score, -progress.wrong_count, never_seen, last_seen)

    due = [c for c in candidates if is_due(progress_or_new(c.progress, today), today)]
    return sorted(due, key=sort_key)


# ---------- "Review again" comes back in the same session ----------
# `returns` is a list of [card_id, cards_still_to_wait]. It lives in the
# Flask session, not the database, because it only matters for this sitting.

def after_answer(returns, card_id, knew_it):
    """Update the waiting list after any answer."""
    updated = [[waiting_id, max(gap - 1, 0)] for waiting_id, gap in returns if waiting_id != card_id]
    if not knew_it:
        updated.append([card_id, REQUEUE_GAP])
    return updated


def card_coming_back(returns, other_cards_left):
    """The waiting card to show now, or None to pick a fresh card instead."""
    for card_id, gap in returns:
        if gap == 0:
            return card_id
    # Fewer than 3 cards were left to put in between: show it next anyway.
    if returns and other_cards_left == 0:
        return min(returns, key=lambda entry: entry[1])[0]
    return None


# ---------- Weak spot trigger ----------

def topic_stats(seen_cards):
    """Group seen cards by topic. A topic is (deck_id, topic name).

    seen_cards: (deck_id, topic, shelf, misconception_count) for every card
    the student has seen.
    """
    stats = {}
    for deck_id, topic, shelf, misconception_count in seen_cards:
        entry = stats.setdefault((deck_id, topic), {"seen": 0, "weak": 0, "misconceptions": 0})
        entry["seen"] += 1
        if shelf <= 2:
            entry["weak"] += 1
        if misconception_count > 0:
            entry["misconceptions"] += 1
    return stats


def find_weak_topic(seen_cards):
    """The topic the Weak Spot Fixer should target, or None."""
    stats = topic_stats(seen_cards)

    # Misconception rule, checked first because it wins when both rules match:
    # 2 or more cards in a topic that the student was sure about but got wrong.
    misconception_topics = [
        (entry["misconceptions"], entry["weak"] / entry["seen"], key)
        for key, entry in stats.items()
        if entry["misconceptions"] >= MISCONCEPTION_CARDS_TRIGGER
    ]
    if misconception_topics:
        return max(misconception_topics)[2]

    # Weak-score rule: at least 3 cards seen, and half or more still on shelf 1 or 2.
    weak_topics = [
        (entry["weak"] / entry["seen"], entry["weak"], key)
        for key, entry in stats.items()
        if entry["seen"] >= MIN_CARDS_SEEN
    ]
    if weak_topics:
        best = max(weak_topics)
        if best[0] >= WEAK_SCORE_TRIGGER:
            return best[2]
    return None


# ---------- Mastery ----------

def topic_mastery(learned, total):
    """Share of a topic's cards on shelf 5. 0 when the topic has no cards."""
    if total == 0:
        return 0
    return learned / total
