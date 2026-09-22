"""Tests for the vocabulary layer.

Only the places where a mistake is silent: a wrong lemma quietly splits one
flashcard into three, and a wrong band quietly passes a text that is too hard.
Neither raises anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graded_reader.lexicon import (
    Band,
    LexiconError,
    band_for,
    coverage,
    lemmatize,
    load_bands,
    load_ngsl,
)
from graded_reader.lexicon.models import is_within, level_index, parse_level


def lemmas(text: str) -> list[str]:
    return [lemma for lemma, _, is_na in lemmatize(text) if not is_na]


# --- lemmatisation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("They went home.", "go"),
        ("The children played.", "child"),
        ("She ran fast.", "run"),
        ("Two men arrived.", "man"),
        ("It was better.", "well"),
        ("He is running.", "run"),
        ("The mice hid.", "mouse"),
    ],
)
def test_irregular_forms_reduce_to_their_base_form(surface: str, expected: str) -> None:
    assert expected in lemmas(surface)


def test_inflections_of_one_verb_collapse_into_a_single_lemma() -> None:
    found = lemmas("He runs. He ran. He is running. They run.")
    assert found.count("run") == 4


def test_punctuation_and_whitespace_are_not_counted_as_words() -> None:
    assert lemmas("Water, water --- water!\n\n  water?") == ["water"] * 4


def test_proper_nouns_numbers_and_acronyms_are_marked_na() -> None:
    tagged = lemmatize("Reykjavik sent 300 tonnes of CO2 to NASA.")
    na = {display for _, display, is_na in tagged if is_na}
    assert {"Reykjavik", "300", "CO2", "NASA"} <= na


def test_a_name_at_the_start_of_a_sentence_is_still_a_name() -> None:
    """The tagger calls sentence-initial Iceland a common NOUN. NER does not.

    Without the entity check, a country lands on the flashcard pile and counts
    against the text's coverage -- and nothing anywhere raises.
    """
    assert ("iceland", "Iceland", True) in lemmatize("Iceland turns carbon into stone.")


def test_a_common_noun_at_the_start_of_a_sentence_is_not_mistaken_for_a_name() -> None:
    """The other direction of the same risk: over-tagging silently hides words."""
    tagged = lemmatize("Water is wet. Carbon goes underground.")
    assert {display for _, display, is_na in tagged if is_na} == set()


# --- bands -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lemma", "expected"),
    [
        ("the", Band.A1),
        ("deep", Band.A1),
        ("sign", Band.A1),
        ("under", Band.A2),
        ("favorite", Band.A2),
        ("rock", Band.B1),
        ("thick", Band.B1),
        ("thirst", Band.B2),
    ],
)
@pytest.mark.usefixtures("tmp_data")
def test_ngsl_rank_lands_on_the_band_its_ceiling_declares(lemma: str, expected: Band) -> None:
    band, zipf = band_for(lemma)
    assert band is expected
    assert zipf is None, "an NGSL hit never consulted wordfreq, so it reports no zipf"


@pytest.mark.usefixtures("tmp_data")
def test_word_outside_the_ngsl_falls_back_to_the_zipf_scale() -> None:
    band, zipf = band_for("machine")
    assert band is Band.B2
    assert zipf is not None and zipf >= 4.0


@pytest.mark.usefixtures("tmp_data")
def test_a_word_nobody_ever_counted_is_c2_not_a_crash() -> None:
    band, zipf = band_for("zzzzqqq")
    assert band is Band.C2
    assert zipf == 0.0


@pytest.mark.usefixtures("tmp_data")
def test_thresholds_come_from_the_file() -> None:
    assert load_bands().threshold_for(Band.A1) == 0.95
    assert load_bands().threshold_for(Band.B1) == 0.92


@pytest.mark.usefixtures("tmp_data")
def test_missing_ngsl_says_where_to_get_it_instead_of_raising_filenotfound(
    tmp_data: Path,
) -> None:
    (tmp_data / "ngsl.csv").unlink()
    with pytest.raises(LexiconError, match="newgeneralservicelist"):
        load_ngsl(tmp_data / "ngsl.csv")


# --- the scale ---------------------------------------------------------------


def test_na_is_not_a_position_on_the_scale() -> None:
    with pytest.raises(ValueError, match="no position"):
        level_index(Band.NA)


def test_na_words_never_count_as_above_the_target() -> None:
    assert is_within(Band.NA, Band.A1)


def test_parse_level_accepts_lowercase_but_refuses_na() -> None:
    assert parse_level("a2") is Band.A2
    with pytest.raises(ValueError, match="not a target level"):
        parse_level("NA")
    with pytest.raises(ValueError, match="unknown level"):
        parse_level("B3")


# --- coverage ----------------------------------------------------------------

# Every word here is in the miniature NGSL, so the arithmetic is checkable by
# hand: 8 counted tokens, of which under (rank 501, A2) and rock (rank 1001, B1)
# sit above A1. 6/8 = 0.75.
REFERENCE = "They put the water deep under a rock."


@pytest.mark.usefixtures("tmp_data")
def test_coverage_counts_only_words_and_divides_by_the_countable_ones() -> None:
    report = coverage(REFERENCE, "A1")
    assert report.counted_tokens == 8
    assert report.na_tokens == 0
    assert report.within_tokens == 6
    assert report.coverage_pct == pytest.approx(6 / 8)
    # Equal counts, so the tie breaks alphabetically: a stable order.
    assert [word.lemma for word in report.out_of_level] == ["rock", "under"]
    assert [word.band for word in report.out_of_level] == [Band.B1, Band.A2]


@pytest.mark.usefixtures("tmp_data")
def test_raising_the_target_level_brings_the_hard_words_inside() -> None:
    report = coverage(REFERENCE, "B1")
    assert report.out_of_level == []
    assert report.coverage_pct == 1.0
    assert report.meets_threshold


@pytest.mark.usefixtures("tmp_data")
def test_names_and_numbers_leave_the_fraction_alone() -> None:
    plain = coverage(REFERENCE, "A1")
    named = coverage("Iceland put 300 tonnes into the water deep under a rock.", "A1")

    assert named.na_tokens == 2
    assert named.total_tokens == named.counted_tokens + 2
    # tonne and into are outside the miniature list and do land above A1. What
    # this asserts is that Iceland and 300 never reached the denominator.
    lemmas_above = {word.lemma for word in named.out_of_level}
    assert "iceland" not in lemmas_above
    assert "300" not in lemmas_above
    # put, tonne, into, the, water, deep, under, a, rock. Iceland replaced They,
    # and neither Iceland nor 300 is in here.
    assert named.counted_tokens == 9
    assert named.counted_tokens == plain.counted_tokens - 1 + 2


@pytest.mark.usefixtures("tmp_data")
def test_known_words_drop_out_of_the_candidates_but_not_out_of_the_score() -> None:
    plain = coverage(REFERENCE, "A1")
    with_known = coverage(REFERENCE, "A1", known={"Rock"})

    assert with_known.coverage_pct == plain.coverage_pct
    assert [word.lemma for word in with_known.out_of_level] == ["rock", "under"]
    assert [word.lemma for word in with_known.candidates] == ["under"]


@pytest.mark.usefixtures("tmp_data")
def test_repeated_hard_word_is_one_candidate_carrying_its_count() -> None:
    report = coverage("The rock is a rock, a rock.", "A1")
    assert len(report.candidates) == 1
    assert report.candidates[0].lemma == "rock"
    assert report.candidates[0].count == 3
    assert report.candidates[0].display == "rock"


@pytest.mark.usefixtures("tmp_data")
def test_a_text_below_its_threshold_is_reported_as_such() -> None:
    report = coverage(REFERENCE, "A1")
    assert report.coverage_pct == pytest.approx(0.75)
    assert report.threshold == 0.95
    assert not report.meets_threshold


@pytest.mark.usefixtures("tmp_data")
def test_empty_text_scores_zero_without_dividing_by_zero() -> None:
    report = coverage("...", "A1")
    assert report.counted_tokens == 0
    assert report.coverage_pct == 0.0
