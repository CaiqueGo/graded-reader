"""Tests for the panel.

Section 12 says M4 is done when the panel answers "how much of A1 do I know"
with a number you believe. These tests are about the believing: that the ladder
divides by the size of the band and not by the size of the deck, that a band
with no size refuses to show a percentage rather than inventing one, and that
retention stays blank until there is something real to measure.

A dashboard that is confidently wrong is worse than no dashboard, because
nothing on the screen tells the reader to doubt it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import Card, Rating

from degrau import dashboard, deck, review
from degrau.store import database, words
from degrau.store.models import Review
from degrau.web import charts

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db")

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def saved(lemma: str, *, band: str | None = None) -> int:
    with database.session() as active:
        word, _ = deck.save_word(active, lemma, band=band)
    assert word.id is not None
    return word.id


def make_mastered(word_id: int, *, stability: float = 40.0, band: str = "A1") -> None:
    """Force a card to look learned, without waiting three weeks for it."""
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        row.stability = stability
        row.band = band
        row.state = "review"
        active.add(row)


def panel() -> dashboard.Dashboard:
    with database.session() as active:
        return dashboard.build(active, now=NOW)


def settled_card() -> str:
    """A serialised card in the review state, for the retention filter."""
    data = Card().to_dict()
    data["state"] = 2
    return json.dumps(data)


# --- the ladder ----------------------------------------------------------------


def test_the_band_sizes_come_from_the_word_list() -> None:
    """A1 is the first 500 of the NGSL, A2 the next 500, and so on."""
    sizes = dashboard.band_sizes()
    assert sizes["A1"] == 500
    assert sizes["A2"] == 500
    assert sizes["B1"] == 1000
    assert sizes["B2"] == 809


def test_the_ladder_divides_by_the_band_not_by_the_deck() -> None:
    """Three A1 words out of 500 is 0.6%, whatever else the deck holds."""
    for lemma in ("rock", "stone", "thick"):
        make_mastered(saved(lemma), band="A1")
    saved("thirst")

    rung = next(r for r in panel().ladder if r.level == "A1")
    assert rung.mastered == 3
    assert rung.total == 500
    assert rung.percent == "0.6%"


def test_a_band_with_no_size_refuses_to_show_a_share() -> None:
    """C1 and C2 come from an open-ended scale. There is no denominator."""
    open_bands = [rung for rung in panel().ladder if rung.level in {"C1", "C2"}]
    assert len(open_bands) == 2
    for rung in open_bands:
        assert not rung.measurable
        assert rung.percent == "-"
        assert rung.total is None


def test_a_card_that_has_not_settled_does_not_count_as_learned() -> None:
    """Learned means it survived. A fresh card has survived nothing."""
    make_mastered(saved("rock"), stability=3.0, band="A1")
    rung = next(r for r in panel().ladder if r.level == "A1")

    assert rung.in_deck == 1, "it is in the deck"
    assert rung.mastered == 0, "but it is not learned"


def test_every_level_gets_a_rung_even_with_an_empty_deck() -> None:
    saved("rock")
    assert [rung.level for rung in panel().ladder] == ["A1", "A2", "B1", "B2", "C1", "C2"]


# --- retention ------------------------------------------------------------------


def test_retention_is_blank_until_there_are_real_recall_attempts() -> None:
    """Learning steps are minutes apart; scoring them would flatter the number."""
    word_id = saved("rock")
    with database.session() as active:
        review.grade(active, word_id, int(Rating.Good), now=NOW)

    built = panel()
    assert built.measured_retention is None
    assert built.recall_attempts == 0
    assert next(s for s in built.stats if s.label == "Retention").value == "-"


def test_retention_measures_settled_cards_once_they_exist() -> None:
    word_id = saved("rock")
    moment = NOW
    for _ in range(4):
        with database.session() as active:
            row = words.by_id(active, word_id)
            assert row is not None
            moment = max(row.due, moment + timedelta(minutes=1))
        with database.session() as active:
            review.grade(active, word_id, int(Rating.Good), now=moment)

    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        assert row.state == "review", "the card must be settled for this to mean anything"
        built = dashboard.build(active, now=moment + timedelta(seconds=1))

    assert built.recall_attempts >= 1
    assert built.measured_retention == 1.0, "nothing was forgotten yet"


def test_hard_still_counts_as_remembered() -> None:
    """Only Again means the word was gone. Hard means it came back slowly."""
    rows = [
        Review(word_id=1, rating=int(Rating.Hard), card_before_json=settled_card()),
        Review(word_id=1, rating=int(Rating.Again), card_before_json=settled_card()),
    ]
    share, attempts = dashboard.measured_retention(rows)
    assert attempts == 2
    assert share == 0.5


def test_a_review_with_no_snapshot_is_not_counted_as_an_attempt() -> None:
    """Rows written before undo support cannot say what state they were in."""
    share, attempts = dashboard.measured_retention(
        [Review(word_id=1, rating=int(Rating.Good), card_before_json="")]
    )
    assert share is None
    assert attempts == 0


# --- history and the curve -------------------------------------------------------


def test_the_history_covers_the_whole_window_including_quiet_days() -> None:
    saved("rock")
    built = panel()
    assert len(built.history) == dashboard.WINDOW_DAYS
    assert built.history[-1].day == NOW.astimezone().date()
    assert all(entry.count == 0 for entry in built.history)


def test_a_review_shows_up_on_the_day_it_happened() -> None:
    word_id = saved("rock")
    with database.session() as active:
        review.grade(active, word_id, int(Rating.Good), now=NOW)

    built = panel()
    today = next(entry for entry in built.history if entry.day == NOW.astimezone().date())
    assert today.count == 1
    assert built.reviewed_in_window == 1


def test_the_curve_only_includes_cards_with_a_memory_to_decay() -> None:
    """A card never graded has no recall probability to average in."""
    saved("rock")
    assert all(point.retention == 0 for point in panel().retention)

    word_id = saved("stone")
    with database.session() as active:
        review.grade(active, word_id, int(Rating.Easy), now=NOW)

    curve = panel().retention
    assert curve[0].retention > 0.9
    assert curve[-1].retention < curve[0].retention, "memory decays"


def test_the_curve_reaches_exactly_one_window_ahead() -> None:
    word_id = saved("rock")
    with database.session() as active:
        review.grade(active, word_id, int(Rating.Easy), now=NOW)

    curve = panel().retention
    assert len(curve) == dashboard.WINDOW_DAYS + 1
    assert curve[0].offset == 0
    assert curve[-1].offset == dashboard.WINDOW_DAYS


# --- the drawing -----------------------------------------------------------------


def test_columns_stay_inside_the_spec() -> None:
    """Capped thickness, and a two-pixel gap that the surface does the work with."""
    word_id = saved("rock")
    with database.session() as active:
        review.grade(active, word_id, int(Rating.Good), now=NOW)

    chart = charts.history_chart(panel())
    assert len(chart.columns) == dashboard.WINDOW_DAYS

    slot = charts.PLOT_WIDTH / dashboard.WINDOW_DAYS
    assert slot - charts.COLUMN_GAP <= charts.MAX_COLUMN
    assert not chart.empty


def test_the_end_ticks_are_anchored_so_nothing_leaves_the_frame() -> None:
    """A label centred on the last column hangs past the right edge."""
    saved("rock")
    chart = charts.history_chart(panel())
    assert chart.x_ticks[0].anchor == "start"
    assert chart.x_ticks[-1].anchor == "end"
    assert chart.x_ticks[-1].x <= charts.WIDTH - charts.PAD_RIGHT


def test_a_deck_with_nothing_graded_draws_no_curve() -> None:
    saved("rock")
    assert charts.retention_chart(panel()).empty


def test_the_retention_axis_runs_the_whole_probability_range() -> None:
    """Cropping it would turn a gentle decline into a cliff."""
    word_id = saved("rock")
    with database.session() as active:
        review.grade(active, word_id, int(Rating.Easy), now=NOW)

    chart = charts.retention_chart(panel())
    assert [tick.value for tick in chart.y_ticks] == ["0%", "50%", "100%"]


def test_an_empty_deck_says_so_instead_of_drawing_empty_charts() -> None:
    built = panel()
    assert built.empty
    assert built.deck_size == 0
