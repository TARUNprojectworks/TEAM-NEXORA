"""Unit tests for every engine rule. No database, no Flask: plain objects in, answers out."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import engine
from app.engine import Candidate

TODAY = date(2026, 10, 6)


def progress(shelf=1, due=TODAY, wrong=0, misconceptions=0, last_seen=None):
    return SimpleNamespace(
        shelf=shelf, next_due=due, wrong_count=wrong,
        misconception_count=misconceptions, last_seen=last_seen,
    )


def card(card_id=1, importance="medium"):
    return SimpleNamespace(id=card_id, importance=importance)


# ---------- Shelves and wait days ----------

@pytest.mark.parametrize("shelf, days", [(1, 0), (2, 1), (3, 3), (4, 7), (5, 14)])
def test_normal_mode_uses_full_wait(shelf, days):
    assert engine.wait_days(shelf, "normal", days_left=None) == days


def test_normal_mode_ignores_exam_date():
    assert engine.wait_days(5, "normal", days_left=2) == 14


@pytest.mark.parametrize("shelf, days_left, expected", [
    (5, 5, 4),    # 14 days capped to the day before the exam
    (4, 5, 4),    # 7 days capped to 4
    (3, 5, 3),    # 3 days already fits before the exam
    (2, 1, 0),    # exam tomorrow: due again today
    (5, 0, 0),    # exam today: never pushed past it
    (4, 30, 7),   # exam far away: full wait
])
def test_exam_mode_caps_wait_before_the_exam(shelf, days_left, expected):
    assert engine.wait_days(shelf, "exam", days_left) == expected


def test_exam_cap_never_goes_negative():
    assert engine.wait_days(3, "exam", days_left=-2) == 0


def test_days_left_until_exam():
    assert engine.days_left_until(TODAY + timedelta(days=5), TODAY) == 5
    assert engine.days_left_until(None, TODAY) is None


# ---------- The 4 confidence rules ----------

def test_know_it_sure_moves_up_one_shelf():
    p = engine.apply_answer(progress(shelf=2), knew_it=True, confident=True, today=TODAY)
    assert p.shelf == 3
    assert p.next_due == TODAY + timedelta(days=3)
    assert (p.wrong_count, p.misconception_count) == (0, 0)


def test_know_it_sure_stops_at_shelf_5():
    p = engine.apply_answer(progress(shelf=5), knew_it=True, confident=True, today=TODAY)
    assert p.shelf == 5
    assert p.next_due == TODAY + timedelta(days=14)


def test_know_it_unsure_stays_on_same_shelf():
    p = engine.apply_answer(progress(shelf=3), knew_it=True, confident=False, today=TODAY)
    assert p.shelf == 3
    assert p.next_due == TODAY + timedelta(days=3)
    assert (p.wrong_count, p.misconception_count) == (0, 0)


def test_review_again_unsure_goes_to_shelf_1_and_counts_wrong():
    p = engine.apply_answer(progress(shelf=4, wrong=1), knew_it=False, confident=False, today=TODAY)
    assert p.shelf == 1
    assert p.next_due == TODAY
    assert p.wrong_count == 2
    assert p.misconception_count == 0


def test_review_again_sure_is_a_misconception():
    p = engine.apply_answer(progress(shelf=4), knew_it=False, confident=True, today=TODAY)
    assert p.shelf == 1
    assert p.wrong_count == 1
    assert p.misconception_count == 1


def test_apply_answer_records_when_the_card_was_seen():
    seen = datetime(2026, 10, 6, 4, 30)
    p = engine.apply_answer(progress(), knew_it=True, confident=True, today=TODAY, seen_at=seen)
    assert p.last_seen == seen


def test_exam_mode_answer_is_due_before_the_exam():
    exam = TODAY + timedelta(days=5)
    p = engine.apply_answer(progress(shelf=4), knew_it=True, confident=True,
                            today=TODAY, mode="exam", exam_date=exam)
    assert p.shelf == 5
    assert p.next_due == TODAY + timedelta(days=4)
    assert p.next_due < exam


def test_normal_mode_answer_uses_full_wait():
    p = engine.apply_answer(progress(shelf=4), knew_it=True, confident=True,
                            today=TODAY, mode="normal", exam_date=None)
    assert p.next_due == TODAY + timedelta(days=14)


# ---------- Due cards ----------

def test_due_means_next_due_is_today_or_earlier():
    assert engine.is_due(progress(due=TODAY), TODAY)
    assert engine.is_due(progress(due=TODAY - timedelta(days=3)), TODAY)
    assert not engine.is_due(progress(due=TODAY + timedelta(days=1)), TODAY)


# ---------- Priority score ----------

def test_priority_score_adds_up_every_part():
    # weakness (6-2)*2=8, high importance 6, exam in 3 days 5, overdue 2, misconception 3
    p = progress(shelf=2, due=TODAY - timedelta(days=2), misconceptions=1)
    score = engine.priority_score(p, card(importance="high"), TODAY,
                                  mode="exam", exam_date=TODAY + timedelta(days=3))
    assert score == 8 + 6 + 5 + 2 + 3


@pytest.mark.parametrize("importance, points", [("high", 6), ("medium", 4), ("low", 2)])
def test_importance_points(importance, points):
    score = engine.priority_score(progress(shelf=5), card(importance=importance), TODAY)
    assert score == 2 + points  # shelf 5 weakness is (6-5)*2 = 2


@pytest.mark.parametrize("days_left, points", [(0, 5), (3, 5), (4, 3), (7, 3), (8, 1), (30, 1), (31, 0)])
def test_exam_urgency_points(days_left, points):
    assert engine.exam_urgency("exam", days_left) == points


def test_no_exam_urgency_in_normal_mode():
    assert engine.exam_urgency("normal", 2) == 0
    normal = engine.priority_score(progress(), card(), TODAY, mode="normal", exam_date=TODAY + timedelta(days=2))
    exam = engine.priority_score(progress(), card(), TODAY, mode="exam", exam_date=TODAY + timedelta(days=2))
    assert exam - normal == 5


def test_forgetting_risk_is_days_overdue_capped_at_5():
    on_time = engine.priority_score(progress(due=TODAY), card(), TODAY)
    two_late = engine.priority_score(progress(due=TODAY - timedelta(days=2)), card(), TODAY)
    very_late = engine.priority_score(progress(due=TODAY - timedelta(days=40)), card(), TODAY)
    assert two_late - on_time == 2
    assert very_late - on_time == 5


def test_misconception_adds_3_once():
    plain = engine.priority_score(progress(), card(), TODAY)
    one = engine.priority_score(progress(misconceptions=1), card(), TODAY)
    many = engine.priority_score(progress(misconceptions=4), card(), TODAY)
    assert one - plain == 3
    assert many == one


def test_resolved_misconception_gets_no_bonus():
    # Same shelf, one card never wrong, one card a misconception now back on shelf 3.
    never_wrong = engine.priority_score(progress(shelf=3), card(), TODAY)
    resolved = engine.priority_score(progress(shelf=3, misconceptions=2), card(), TODAY)
    assert resolved == never_wrong


def test_open_misconception_on_shelf_2_still_gets_the_bonus():
    plain = engine.priority_score(progress(shelf=2), card(), TODAY)
    still_open = engine.priority_score(progress(shelf=2, misconceptions=1), card(), TODAY)
    assert still_open - plain == 3


@pytest.mark.parametrize("count, shelf, expected", [
    (0, 1, False),   # never a misconception
    (1, 1, True),    # just got it wrong while sure
    (2, 2, True),    # known once since, not yet relearned
    (1, 3, False),   # back on shelf 3: resolved
    (3, 5, False),
])
def test_is_open_misconception(count, shelf, expected):
    assert engine.is_open_misconception(count, shelf) is expected


def test_new_card_scores_like_a_fresh_shelf_1_card():
    assert engine.priority_score(None, card(importance="high"), TODAY) == 10 + 6


# ---------- Ranking and tie-breaks ----------

def test_rank_puts_highest_score_first():
    weak = Candidate(card(1, "low"), progress(shelf=1), "normal", None)        # 10 + 2 = 12
    strong = Candidate(card(2, "high"), progress(shelf=5), "normal", None)     # 2 + 6 = 8
    urgent = Candidate(card(3, "medium"), progress(shelf=2), "exam", TODAY + timedelta(days=2))  # 8+4+5 = 17
    ranked = engine.rank_candidates([weak, strong, urgent], TODAY)
    assert [c.card.id for c in ranked] == [3, 1, 2]


def test_tie_break_by_higher_wrong_count():
    few = Candidate(card(1), progress(wrong=1), "normal", None)
    many = Candidate(card(2), progress(wrong=3), "normal", None)
    assert [c.card.id for c in engine.rank_candidates([few, many], TODAY)] == [2, 1]


def test_tie_break_then_by_older_last_seen():
    recent = Candidate(card(1), progress(last_seen=datetime(2026, 10, 5, 9, 0)), "normal", None)
    older = Candidate(card(2), progress(last_seen=datetime(2026, 10, 1, 9, 0)), "normal", None)
    assert [c.card.id for c in engine.rank_candidates([recent, older], TODAY)] == [2, 1]


def test_never_seen_card_comes_after_seen_cards_on_a_tie():
    seen = Candidate(card(1), progress(last_seen=datetime(2026, 10, 1, 9, 0)), "normal", None)
    new = Candidate(card(2), None, "normal", None)
    assert [c.card.id for c in engine.rank_candidates([new, seen], TODAY)] == [1, 2]


def test_rank_skips_cards_that_are_not_due():
    due = Candidate(card(1), progress(due=TODAY), "normal", None)
    later = Candidate(card(2), progress(due=TODAY + timedelta(days=1)), "normal", None)
    assert [c.card.id for c in engine.rank_candidates([due, later], TODAY)] == [1]


# ---------- "Review again" comes back after 3 other cards ----------

def test_review_again_card_waits_for_3_other_cards():
    returns = engine.after_answer([], card_id=7, knew_it=False)
    assert returns == [[7, 3]]
    assert engine.card_coming_back(returns, other_cards_left=10) is None

    for other in (1, 2, 3):
        returns = engine.after_answer(returns, card_id=other, knew_it=True)
    assert returns == [[7, 0]]
    assert engine.card_coming_back(returns, other_cards_left=10) == 7


def test_review_again_card_comes_next_if_fewer_than_3_cards_are_left():
    returns = engine.after_answer([], card_id=7, knew_it=False)
    returns = engine.after_answer(returns, card_id=1, knew_it=True)
    assert engine.card_coming_back(returns, other_cards_left=0) == 7


def test_knowing_a_returned_card_takes_it_out_of_the_queue():
    returns = [[7, 0]]
    assert engine.after_answer(returns, card_id=7, knew_it=True) == []


def test_missing_a_returned_card_again_restarts_its_wait():
    returns = [[7, 0], [8, 2]]
    assert engine.after_answer(returns, card_id=7, knew_it=False) == [[8, 1], [7, 3]]


def test_first_waiting_card_comes_back_first():
    assert engine.card_coming_back([[5, 0], [6, 0]], other_cards_left=4) == 5
    assert engine.card_coming_back([[5, 2], [6, 1]], other_cards_left=0) == 6


# ---------- Weak spot trigger ----------

def seen(deck_id, topic, shelf, misconceptions=0):
    return (deck_id, topic, shelf, misconceptions)


def test_no_weak_topic_without_enough_cards_seen():
    cards = [seen(1, "Cells", 1), seen(1, "Cells", 1)]  # only 2 seen
    assert engine.find_weak_topic(cards) is None


def test_weak_score_of_half_or_more_triggers():
    cards = [seen(1, "Cells", 1), seen(1, "Cells", 2), seen(1, "Cells", 4), seen(1, "Cells", 5)]
    assert engine.find_weak_topic(cards) == (1, "Cells")  # 2 of 4 on shelf 1-2 = 0.5


def test_weak_score_below_half_does_not_trigger():
    cards = [seen(1, "Cells", 1), seen(1, "Cells", 3), seen(1, "Cells", 4)]
    assert engine.find_weak_topic(cards) is None  # 1 of 3


def test_highest_weak_score_wins():
    cards = [
        seen(1, "Cells", 1), seen(1, "Cells", 2), seen(1, "Cells", 5), seen(1, "Cells", 5),  # 0.5
        seen(1, "DNA", 1), seen(1, "DNA", 1), seen(1, "DNA", 2), seen(1, "DNA", 5),          # 0.75
    ]
    assert engine.find_weak_topic(cards) == (1, "DNA")


def test_two_open_misconception_cards_trigger_even_with_a_low_weak_score():
    cards = [
        seen(1, "Cells", 1, misconceptions=1), seen(1, "Cells", 2, misconceptions=2),
        seen(1, "Cells", 5), seen(1, "Cells", 5), seen(1, "Cells", 5),
    ]  # weak score 2/5 = 0.4, below 0.5, so only the misconception rule can fire
    assert engine.find_weak_topic(cards) == (1, "Cells")


def test_resolved_misconceptions_do_not_trigger():
    # Both cards were misconceptions once, but they're back on shelf 3+.
    cards = [seen(1, "Cells", 3, misconceptions=1), seen(1, "Cells", 4, misconceptions=2), seen(1, "Cells", 5)]
    assert engine.find_weak_topic(cards) is None


def test_one_open_and_one_resolved_misconception_is_not_enough():
    cards = [seen(1, "Cells", 2, misconceptions=1), seen(1, "Cells", 3, misconceptions=1), seen(1, "Cells", 5)]
    assert engine.find_weak_topic(cards) is None


def test_one_misconception_card_is_not_enough():
    cards = [seen(1, "Cells", 3, misconceptions=5), seen(1, "Cells", 4), seen(1, "Cells", 5)]
    assert engine.find_weak_topic(cards) is None


def test_misconception_topic_wins_over_higher_weak_score():
    cards = [
        seen(1, "DNA", 1), seen(1, "DNA", 1), seen(1, "DNA", 1),                       # weak score 1.0
        seen(2, "Friction", 1, 1), seen(2, "Friction", 2, 1), seen(2, "Friction", 5),  # 2 open misconceptions
    ]
    assert engine.find_weak_topic(cards) == (2, "Friction")


def test_same_topic_name_in_two_decks_is_two_topics():
    cards = [
        seen(1, "General", 1), seen(1, "General", 1),
        seen(2, "General", 1), seen(2, "General", 5),
    ]
    assert engine.find_weak_topic(cards) is None  # neither deck has 3 cards seen


# ---------- Mastery ----------

def test_topic_mastery_is_learned_over_all_cards():
    assert engine.topic_mastery(learned=3, total=12) == 0.25
    assert engine.topic_mastery(learned=0, total=0) == 0
