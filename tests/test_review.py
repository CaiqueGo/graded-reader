"""Tests for the review session.

Section 11 of the MVP asks for three things here: that a sequence of gradings
produces growing intervals, that Again knocks them back down, and that a card
survives a round trip through the database. All three fail silently -- a card
whose schedule is quietly wrong still shows up and still looks fine, and you
find out months later that nothing stuck.

Undo and the daily limit are tested for the same reason. A misclick that cannot
be taken back corrupts the one number the product claims is honest, and a limit
that does not hold is, by section 13, how the whole thing gets abandoned.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import Card, Rating

from degrau import deck, review
from degrau.store import database, reviews, words
from degrau.store import settings as settings_store
from degrau.store.models import CardState

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db")


def add(lemma: str, *, example_en: str = "", pt: str = "") -> int:
    with database.session() as active:
        word, _ = deck.save_word(active, lemma, example_en=example_en or None, pt=pt or None)
    assert word.id is not None
    return word.id


def grade(word_id: int, rating: Rating, at: datetime) -> review.GradeResult:
    with database.session() as active:
        return review.grade(active, word_id, int(rating), now=at)


def word_state(word_id: int) -> tuple[str, datetime, float | None]:
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        return row.state, row.due, row.stability


# --- intervals ----------------------------------------------------------------


def test_repeated_good_produces_growing_intervals() -> None:
    word_id = add("rock")
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

    gaps: list[timedelta] = []
    for _ in range(6):
        _state, due, _stability = word_state(word_id)
        moment = max(due, moment + timedelta(minutes=1))
        grade(word_id, Rating.Good, moment)
        _state, new_due, _stability = word_state(word_id)
        gaps.append(new_due - moment)

    # The first couple are learning steps, measured in minutes. From the moment
    # the card graduates, each interval must exceed the one before it.
    graduated = gaps[2:]
    assert graduated == sorted(graduated), f"intervals did not grow: {gaps}"
    assert graduated[-1] > timedelta(days=30)


def test_again_knocks_a_settled_card_back_down() -> None:
    word_id = add("rock")
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

    for _ in range(4):
        _state, due, _stability = word_state(word_id)
        moment = max(due, moment + timedelta(minutes=1))
        grade(word_id, Rating.Good, moment)

    _state, settled_due, settled_stability = word_state(word_id)
    assert settled_due - moment > timedelta(days=7)

    moment = settled_due
    grade(word_id, Rating.Again, moment)
    state, lapsed_due, lapsed_stability = word_state(word_id)

    assert lapsed_due - moment < timedelta(days=1), "Again must bring the card straight back"
    assert state == CardState.RELEARNING.value
    assert settled_stability is not None and lapsed_stability is not None
    assert lapsed_stability < settled_stability


def test_a_card_survives_the_round_trip_through_the_database() -> None:
    word_id = add("rock")
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    grade(word_id, Rating.Good, moment)

    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        restored = deck.load_card(row)
        assert restored.due == row.due
        assert restored.stability == row.stability
        assert deck.card_state(restored).value == row.state
        # And a second trip changes nothing.
        assert Card.from_dict(json.loads(row.fsrs_json)).to_dict() == restored.to_dict()


def test_a_grade_writes_exactly_one_review_row() -> None:
    word_id = add("rock")
    grade(word_id, Rating.Good, datetime(2026, 9, 21, 9, 0, tzinfo=UTC))

    with database.session() as active:
        history = reviews.history(active, word_id)
        assert len(history) == 1
        assert history[0].rating == int(Rating.Good)
        assert history[0].card_before_json, "the snapshot undo needs must be stored"


def test_an_unknown_rating_is_refused() -> None:
    word_id = add("rock")
    with database.session() as active, pytest.raises(review.ReviewError, match="not a rating"):
        review.grade(active, word_id, 9)


def test_grading_a_card_that_does_not_exist_says_so() -> None:
    with database.session() as active, pytest.raises(review.ReviewError, match="no card with id"):
        review.grade(active, 999, 3)


# --- undo ---------------------------------------------------------------------


def test_undo_puts_the_card_back_exactly_where_it_was() -> None:
    word_id = add("rock")
    before_state, before_due, before_stability = word_state(word_id)

    grade(word_id, Rating.Easy, datetime(2026, 9, 21, 9, 0, tzinfo=UTC))
    assert word_state(word_id) != (before_state, before_due, before_stability)

    with database.session() as active:
        assert review.undo_last(active) == "rock"

    assert word_state(word_id) == (before_state, before_due, before_stability)


def test_undo_removes_the_review_so_it_leaves_no_trace_in_the_numbers() -> None:
    word_id = add("rock")
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    grade(word_id, Rating.Good, moment)

    with database.session() as active:
        assert review.counts(active, now=moment).reviewed_today == 1
        review.undo_last(active)

    with database.session() as active:
        assert reviews.history(active, word_id) == []
        assert review.counts(active, now=moment).reviewed_today == 0


def test_undo_takes_back_the_most_recent_answer_not_the_most_recent_card() -> None:
    first = add("rock")
    second = add("stone")
    start = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

    grade(second, Rating.Good, start)
    grade(first, Rating.Good, start + timedelta(minutes=5))

    with database.session() as active:
        assert review.undo_last(active) == "rock"


def test_undo_twice_walks_back_two_answers() -> None:
    word_id = add("rock")
    start = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    original = word_state(word_id)

    grade(word_id, Rating.Good, start)
    grade(word_id, Rating.Good, start + timedelta(minutes=20))

    with database.session() as active:
        review.undo_last(active)
    with database.session() as active:
        review.undo_last(active)

    assert word_state(word_id) == original


def test_undo_with_nothing_to_undo_is_not_an_error() -> None:
    add("rock")
    with database.session() as active:
        assert review.undo_last(active) is None


# --- the daily limit ----------------------------------------------------------


def test_new_cards_stop_at_the_daily_limit() -> None:
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    ids = [add(lemma) for lemma in ["rock", "stone", "thick", "thirst", "sign"]]

    with database.session() as active:
        settings_store.set_value(active, settings_store.KEY_DAILY_NEW, "2")

    with database.session() as active:
        assert review.counts(active, now=moment).new == 2

    for word_id in ids[:2]:
        grade(word_id, Rating.Easy, moment)

    with database.session() as active:
        state = review.counts(active, now=moment)
        assert state.introduced_today == 2
        assert state.new == 0
        assert state.new_left_today == 0
        assert review.next_word(active, now=moment) is None, "no more new cards today"


def test_the_limit_counts_first_sightings_not_gradings() -> None:
    """A card graded four times today used up one slot, not four."""
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    word_id = add("rock")
    with database.session() as active:
        settings_store.set_value(active, settings_store.KEY_DAILY_NEW, "2")

    for step in range(4):
        grade(word_id, Rating.Again, moment + timedelta(minutes=step))

    with database.session() as active:
        state = review.counts(active, now=moment)
    assert state.introduced_today == 1
    assert state.reviewed_today == 4


def test_the_allowance_comes_back_the_next_day() -> None:
    today = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    tomorrow = today + timedelta(days=1)
    add("rock")
    add("stone")

    with database.session() as active:
        settings_store.set_value(active, settings_store.KEY_DAILY_NEW, "1")
    grade(add("thick"), Rating.Easy, today)

    with database.session() as active:
        assert review.counts(active, now=today).new == 0
        assert review.counts(active, now=tomorrow).new == 1


def test_a_limit_of_zero_introduces_nothing() -> None:
    add("rock")
    with database.session() as active:
        settings_store.set_value(active, settings_store.KEY_DAILY_NEW, "0")
    with database.session() as active:
        assert review.next_word(active, now=datetime(2026, 9, 21, 9, 0, tzinfo=UTC)) is None


def test_a_corrupt_limit_falls_back_instead_of_crashing_the_session() -> None:
    with database.session() as active:
        settings_store.set_value(active, settings_store.KEY_DAILY_NEW, "ten")
        assert review.daily_new_limit(active) == 10


# --- the queue ----------------------------------------------------------------


def test_a_card_that_is_due_comes_before_a_card_never_seen() -> None:
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    due_id = add("rock")
    grade(due_id, Rating.Again, moment)
    add("stone")

    with database.session() as active:
        nxt = review.next_word(active, now=moment + timedelta(hours=2))
        assert nxt is not None and nxt.lemma == "rock"


def test_a_card_not_yet_due_is_not_offered() -> None:
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    word_id = add("rock")
    grade(word_id, Rating.Easy, moment)

    with database.session() as active:
        assert review.next_word(active, now=moment + timedelta(hours=1)) is None


def test_an_empty_deck_has_nothing_to_review() -> None:
    with database.session() as active:
        assert review.next_card(active) is None
        assert review.counts(active).remaining == 0


# --- what the screen shows ----------------------------------------------------


def test_the_front_hides_the_word_in_its_example() -> None:
    assert review.blank_out("They put water under a rock.", "rock") == (
        f"They put water under a {review.BLANK}."
    )


def test_every_inflected_form_is_hidden_not_just_the_exact_spelling() -> None:
    """Hiding 'run' but leaving 'running' two words later gives the answer away."""
    hidden = review.blank_out("He runs, and running is what he did.", "run")
    assert "runs" not in hidden
    assert "running" not in hidden, "the gerund-as-noun lemmatises to itself"
    assert hidden.count(review.BLANK) == 2


@pytest.mark.parametrize(
    ("example", "lemma", "survives"),
    [
        ("The rocket left the rock.", "rock", "rocket"),
        ("She was in bed.", "be", "bed"),
        ("A signal, not a sign.", "sign", "signal"),
    ],
)
def test_a_longer_unrelated_word_is_not_swallowed(example: str, lemma: str, survives: str) -> None:
    """The suffix fallback must not turn into a prefix match."""
    assert survives in review.blank_out(example, lemma)


def test_a_card_without_an_example_has_no_cloze_and_says_so() -> None:
    word_id = add("rock")
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        card = review.to_card(row)
    assert card.cloze == ""
    assert card.has_example is False


def test_the_four_buttons_carry_the_keys_and_an_interval_each() -> None:
    word_id = add("rock", example_en="A rock.")
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        card = review.to_card(row)

    assert [option.key for option in card.options] == ["1", "2", "3", "4"]
    assert [option.label for option in card.options] == ["Again", "Hard", "Good", "Easy"]
    assert all(option.interval for option in card.options)


def test_easy_offers_a_longer_wait_than_again() -> None:
    word_id = add("rock")
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        options = {o.label: o for o in review.options_for(row, now=moment)}

    assert options["Again"].interval.endswith("m")
    assert options["Easy"].interval.endswith("d")


def test_previewing_the_buttons_does_not_change_the_card() -> None:
    """Rendering the answer side must not schedule anything."""
    word_id = add("rock")
    before = word_state(word_id)
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        review.options_for(row, now=datetime(2026, 9, 21, 9, 0, tzinfo=UTC))
    assert word_state(word_id) == before


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=30), "<1m"),
        (timedelta(minutes=10), "10m"),
        (timedelta(hours=5), "5h"),
        (timedelta(days=2), "2d"),
        (timedelta(days=60), "2mo"),
        (timedelta(days=730), "2.0y"),
    ],
)
def test_intervals_read_as_a_person_would_say_them(delta: timedelta, expected: str) -> None:
    assert review.humanize(delta) == expected


# --- three days running, which is the stage's own acceptance test -------------


def test_three_days_of_reviews_behave() -> None:
    """Section 12: M3 is done when three days in a row behave sensibly."""
    word_id = add("rock", example_en="A rock.")
    day_one = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

    grade(word_id, Rating.Good, day_one)
    grade(word_id, Rating.Good, day_one + timedelta(minutes=11))
    _state, after_day_one, _stability = word_state(word_id)
    assert after_day_one > day_one + timedelta(hours=12), "graduated out of the same day"

    day_two = after_day_one
    grade(word_id, Rating.Good, day_two)
    _state, after_day_two, _stability = word_state(word_id)
    assert after_day_two - day_two > after_day_one - day_one

    day_three = after_day_two
    grade(word_id, Rating.Good, day_three)
    state, after_day_three, stability = word_state(word_id)

    assert after_day_three - day_three > after_day_two - day_two
    assert state == CardState.REVIEW.value
    assert stability is not None and stability > 0

    with database.session() as active:
        assert len(reviews.history(active, word_id)) == 4
