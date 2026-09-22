"""Tests for sentence cards.

A word on its own is ambiguous and easy to "know" without being able to use.
Mining the sentence it appeared in is how vocabulary is usually rehearsed, and
these are the places where doing it wrong is silent: two cards where one was
meant, a target that highlights nothing, a sentence that comes back reflowed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fsrs import Rating

from graded_reader import deck, library, reading, review
from graded_reader.store import database, words
from graded_reader.store.models import CardKind

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
SENTENCE = "They put the water deep under a rock."


def imported(drop_in: Callable[..., Path], **overrides: object) -> int:
    result = library.import_file(drop_in(**overrides))
    assert result.text_id is not None
    return result.text_id


def saved(sentence: str = SENTENCE, **fields: object) -> deck.SavedWord:
    with database.session() as active:
        card, _created = deck.save_sentence(active, sentence, **fields)  # type: ignore[arg-type]
    return card


# --- saving ------------------------------------------------------------------------


def test_a_sentence_becomes_a_card_of_its_own_kind() -> None:
    card = saved(target="rock", pt="Puseram a agua sob uma rocha.")

    assert card.kind == CardKind.SENTENCE.value
    assert card.is_sentence
    assert card.sentence == SENTENCE
    assert card.lemma == "rock", "the word it is teaching"
    assert card.band == "B1", "taken from the target, not from the sentence"


def test_the_whitespace_of_a_selection_does_not_change_the_card() -> None:
    """A selection dragged across a line break arrives with the break in it."""
    dragged = "They put the water\n  deep under   a rock."
    assert saved(dragged).sentence == SENTENCE


def test_the_same_sentence_saved_twice_is_one_card() -> None:
    first = saved(target="rock")
    with database.session() as active:
        second, created = deck.save_sentence(active, SENTENCE)

    assert not created
    assert second.id == first.id
    with database.session() as active:
        assert words.count(active) == 1


def test_saving_the_same_sentence_again_does_not_reset_its_schedule() -> None:
    card = saved()
    assert card.id is not None
    with database.session() as active:
        review.grade(active, card.id, int(Rating.Good), now=NOW)
    with database.session() as active:
        row = words.by_id(active, card.id)
        assert row is not None
        before = row.due

    with database.session() as active:
        again, _created = deck.save_sentence(active, SENTENCE)
    assert again.due == before


def test_a_second_sighting_fills_a_meaning_that_was_missing() -> None:
    saved(target="rock")
    with database.session() as active:
        card, _created = deck.save_sentence(active, SENTENCE, pt="Sob uma rocha.")
    assert card.sentence_pt == "Sob uma rocha."


def test_two_sentences_may_teach_the_same_word() -> None:
    """The whole reason the unique constraint on lemma had to go."""
    saved("They put the water deep under a rock.", target="rock")
    saved("The rock is very thick.", target="rock")

    with database.session() as active:
        assert words.count(active) == 2


def test_a_sentence_and_a_word_card_can_both_exist_for_one_word() -> None:
    with database.session() as active:
        deck.save_word(active, "rock", pt="rocha")
    saved(target="rock")

    with database.session() as active:
        assert words.count(active) == 2
        kinds = sorted(word.kind for word in words.all_cards(active))
    assert kinds == ["sentence", "word"]


def test_a_word_card_still_cannot_be_saved_twice() -> None:
    """Relaxing the constraint for sentences must not relax it for words."""
    with database.session() as active:
        deck.save_word(active, "rock")
    with database.session() as active:
        _card, created = deck.save_word(active, "rock")

    assert not created
    with database.session() as active:
        assert words.count(active) == 1


def test_a_sentence_may_have_no_target_at_all() -> None:
    """Sometimes the sentence is worth keeping for its shape, not for a word."""
    card = saved(pt="Sem palavra-alvo.")
    assert card.lemma == ""
    assert card.band == ""


def test_an_empty_selection_is_refused() -> None:
    with database.session() as active, pytest.raises(deck.DeckError, match="needs a sentence"):
        deck.save_sentence(active, "   \n  ")


# --- what the review screen shows ----------------------------------------------------


def test_the_target_word_is_marked_inside_the_sentence() -> None:
    card = saved(target="rock", pt="Sob uma rocha.")
    assert card.id is not None
    with database.session() as active:
        row = words.by_id(active, card.id)
        assert row is not None
        shown = review.to_card(row)

    marked = [part.text for part in shown.segments if part.highlight]
    assert marked == ["rock"]
    assert shown.is_sentence


def test_every_form_of_the_target_is_marked_not_just_the_exact_spelling() -> None:
    card = saved("The rocks are thick, and one rock is thin.", target="rock")
    assert card.id is not None
    with database.session() as active:
        row = words.by_id(active, card.id)
        assert row is not None
        marked = [p.text for p in review.to_card(row).segments if p.highlight]
    assert marked == ["rocks", "rock"]


def test_the_sentence_rebuilds_exactly_from_its_pieces() -> None:
    """A card that quietly reflows the sentence is showing a different sentence."""
    card = saved(target="rock")
    assert card.id is not None
    with database.session() as active:
        row = words.by_id(active, card.id)
        assert row is not None
        segments = review.to_card(row).segments

    rebuilt = "".join(part.text + part.whitespace for part in segments)
    assert rebuilt == SENTENCE


def test_a_sentence_with_no_target_highlights_nothing() -> None:
    card = saved()
    assert card.id is not None
    with database.session() as active:
        row = words.by_id(active, card.id)
        assert row is not None
        assert not any(part.highlight for part in review.to_card(row).segments)


def test_the_back_is_the_meaning_and_it_knows_when_there_is_none() -> None:
    with_meaning = saved(pt="Sob uma rocha.")
    without = saved("The rock is thick.")
    assert with_meaning.id is not None and without.id is not None

    with database.session() as active:
        first = words.by_id(active, with_meaning.id)
        second = words.by_id(active, without.id)
        assert first is not None and second is not None
        assert review.to_card(first).has_back
        assert not review.to_card(second).has_back


def test_a_sentence_card_joins_the_queue_like_any_other() -> None:
    saved(target="rock", pt="Sob uma rocha.")
    with database.session() as active:
        nxt = review.next_card(active, now=NOW)
        assert nxt is not None
        assert nxt.is_sentence


# --- choosing the target from a selection ---------------------------------------------


def test_the_excerpt_offers_every_word_in_it_as_a_target(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        excerpt = reading.build_excerpt(active, SENTENCE, text_id=text_id)

    assert excerpt.text == SENTENCE
    assert excerpt.word_count == 8
    assert {choice.lemma for choice in excerpt.words} >= {"rock", "under", "water"}


def test_words_the_text_flagged_as_hard_are_offered_first(
    drop_in: Callable[..., Path],
) -> None:
    """Those are the ones the reader most likely selected the sentence for."""
    text_id = imported(drop_in)
    with database.session() as active:
        excerpt = reading.build_excerpt(active, SENTENCE, text_id=text_id)

    leading = [choice.lemma for choice in excerpt.words if choice.above_level]
    assert leading == [choice.lemma for choice in excerpt.words[: len(leading)]]
    assert set(leading) == {"rock", "under"}


def test_a_name_is_not_offered_as_something_to_learn(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        excerpt = reading.build_excerpt(
            active, "Iceland put the water under a rock.", text_id=text_id
        )
    assert "iceland" not in {choice.lemma for choice in excerpt.words}


def test_the_excerpt_says_when_the_sentence_is_already_saved(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        assert not reading.build_excerpt(active, SENTENCE, text_id=text_id).already_saved

    saved()
    with database.session() as active:
        assert reading.build_excerpt(active, SENTENCE, text_id=text_id).already_saved


def test_selecting_nothing_says_so() -> None:
    with database.session() as active, pytest.raises(reading.ReadingError, match="nothing"):
        reading.build_excerpt(active, "   ")


def test_a_word_already_in_the_deck_is_shown_as_such(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        deck.save_word(active, "rock")
    with database.session() as active:
        excerpt = reading.build_excerpt(active, SENTENCE, text_id=text_id)

    rock = next(choice for choice in excerpt.words if choice.lemma == "rock")
    assert rock.in_deck
