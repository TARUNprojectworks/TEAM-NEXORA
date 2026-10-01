"""The study engine: shelves, confidence rules, priority score, weak spot trigger.

Owner: engine + study. Built in phase 3 with full unit tests.
Functions here are plain Python: they take `today` as an argument and never
touch the database or Flask, so they are easy to test and to explain.

Planned public functions (other modules may call these):
    wait_days(shelf, mode, days_left)              -> int
    apply_answer(progress, knew_it, confident, today, mode, exam_date)
    priority_score(progress, card, today, mode, exam_date) -> int
    find_weak_topic(topic_stats)                   -> (deck_id, topic) or None
    topic_mastery(shelves)                         -> float between 0 and 1
"""

# Days to wait before a card on each shelf is due again.
SHELF_WAIT_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}
MAX_SHELF = 5

# A "Review again" card comes back after this many other cards.
REQUEUE_GAP = 3
