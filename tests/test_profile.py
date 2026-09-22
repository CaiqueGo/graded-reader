"""Tests for the prompt context block.

What matters here is not the wording, which will change. It is that the four
lists carry what they claim: that a word already in the deck is never offered as
new, that the target band is the one above, and that the caps hold. Every one of
those failing produces a prompt that looks fine and teaches the wrong words.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from degrau import config, library
from degrau.lexicon.models import Band
from degrau.profile import (
    MAX_EXCEPTIONS,
    MAX_NEW_WORDS,
    MIN_NEW_WORDS,
    LevelRules,
    ProfileError,
    build,
    clamp_new_words,
    load_levels,
    next_band,
    render,
)
from degrau.store import database
from degrau.store.models import CardState, Word, utcnow

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db")


def add_word(
    lemma: str,
    *,
    band: str,
    stability: float | None = None,
    state: CardState = CardState.REVIEW,
    due_in_days: float = 30.0,
) -> None:
    with database.session() as active:
        active.add(
            Word(
                lemma=lemma,
                display=lemma,
                band=band,
                stability=stability,
                state=state.value,
                fsrs_json="{}",
                due=utcnow() + timedelta(days=due_in_days),
            )
        )


# --- the scale ----------------------------------------------------------------


def test_the_target_band_is_the_one_immediately_above() -> None:
    assert next_band(Band.A1) is Band.A2
    assert next_band(Band.B1) is Band.B2


def test_at_the_top_of_the_scale_there_is_nothing_above() -> None:
    assert next_band(Band.C2) is None


@pytest.mark.parametrize(
    ("asked", "expected"),
    [(1, MIN_NEW_WORDS), (5, 5), (6, 6), (8, 8), (40, MAX_NEW_WORDS)],
)
def test_the_i_plus_one_window_is_held_inside_its_range(asked: int, expected: int) -> None:
    assert clamp_new_words(asked) == expected


# --- assembling ---------------------------------------------------------------


def test_the_exception_list_is_capped() -> None:
    profile = build(
        Band.A1,
        LevelRules(),
        mastered_above=[f"word{n}" for n in range(200)],
    )
    assert len(profile.exceptions) == MAX_EXCEPTIONS


def test_the_targets_honour_the_requested_count() -> None:
    profile = build(
        Band.A1,
        LevelRules(),
        candidates_above=["under", "favorite", "rock", "thick", "thirst", "sign", "deep", "go"],
        new_words=5,
    )
    assert profile.targets == ["under", "favorite", "rock", "thick", "thirst"]


def test_candidate_order_is_preserved_because_it_carries_the_frequency() -> None:
    profile = build(Band.A1, LevelRules(), candidates_above=["c", "a", "b"], new_words=5)
    assert profile.targets[:3] == ["c", "a", "b"]


# --- from the database --------------------------------------------------------


def test_the_targets_are_the_most_frequent_words_of_the_band_above() -> None:
    """A2 in the miniature list is ranks 501..1000: 'under' then 'favorite'."""
    profile = library.build_profile(Band.A1, new_words=5)
    assert profile.targets == ["under", "favorite"]


def test_a_word_already_in_the_deck_is_never_offered_as_new() -> None:
    add_word("under", band="A2")
    profile = library.build_profile(Band.A1, new_words=5)

    assert "under" not in profile.targets
    assert profile.targets == ["favorite"]


def test_only_words_above_the_target_appear_as_exceptions() -> None:
    add_word("rock", band="B1", stability=40.0)
    add_word("sign", band="A1", stability=99.0)

    profile = library.build_profile(Band.A1, new_words=5)

    assert profile.exceptions == ["rock"], "an A1 word is not an exception to A1"


def test_a_word_still_being_learned_is_not_claimed_as_known() -> None:
    """Stability below the mastery threshold means it has not survived anything."""
    add_word("rock", band="B1", stability=3.0)
    assert library.build_profile(Band.A1, new_words=5).exceptions == []


def test_words_fading_soon_are_offered_for_reinforcement() -> None:
    add_word("thick", band="B1", state=CardState.LEARNING, stability=2.0, due_in_days=1)
    add_word("thirst", band="B2", state=CardState.LEARNING, stability=2.0, due_in_days=40)

    profile = library.build_profile(Band.A1, new_words=5)

    assert profile.reinforcement == ["thick"], "only what comes due within the horizon"


def test_a_settled_word_is_not_pushed_for_reinforcement() -> None:
    add_word("thick", band="B1", state=CardState.REVIEW, stability=50.0, due_in_days=1)
    assert library.build_profile(Band.A1, new_words=5).reinforcement == []


def test_the_level_falls_back_to_the_stored_setting() -> None:
    profile = library.build_profile(new_words=5)
    assert profile.level is Band.A1


# --- levels.toml and rendering ------------------------------------------------


def test_levels_are_read_from_the_file() -> None:
    rules = load_levels(config.levels_path())
    assert rules[Band.A1].sentences == "6 to 10 words."
    assert rules[Band.B1].grammar == "Passive voice."


def test_a_missing_levels_file_says_so(tmp_data: object) -> None:
    with pytest.raises(ProfileError, match="levels file not found"):
        load_levels(config.levels_path().with_name("nope.toml"))


def test_the_block_carries_the_level_rules_and_the_four_lists() -> None:
    profile = library.build_profile(Band.A1, new_words=5)
    block = render(profile)

    assert "## Target level: A1" in block
    assert "6 to 10 words." in block
    assert "band A2" in block
    assert "under, favorite" in block


def test_empty_lists_read_as_empty_rather_than_as_a_blank_line() -> None:
    """A bare heading followed by nothing reads to a model as a missing section."""
    assert "(none yet)" in render(library.build_profile(Band.A1, new_words=5))


def test_the_block_never_contains_the_whole_word_list() -> None:
    """Section 8: send the profile, not the level's vocabulary."""
    block = render(library.build_profile(Band.A1, new_words=5))
    assert "thirst" not in block
    assert len(block.splitlines()) < 60
