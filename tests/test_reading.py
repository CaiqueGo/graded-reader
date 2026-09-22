"""Tests for the deck and the reading view.

The MVP says not to test routes or templates, and these are not. They test the
two functions the routes call, which is where every rule on the reading screen
lives: which words are highlighted, what a second click does, and whether the
text survives being turned into markup and back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from graded_reader import deck, library, reading
from graded_reader.lexicon import tokenize
from graded_reader.reading import ReadingError
from graded_reader.store import database, words
from graded_reader.store.models import CardState

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")


def imported(drop_in: Callable[..., Path], **overrides: object) -> int:
    result = library.import_file(drop_in(**overrides))
    assert result.text_id is not None
    return result.text_id


# --- tokenising for display ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "They put the water deep under a rock.",
        "One.\n\nTwo paragraphs, and  a  double  space.",
        "Quotes: 'a' and \"b\" -- plus a dash.",
        "Iceland sent 300 tonnes of CO2.",
    ],
)
def test_the_text_survives_a_round_trip_through_tokens(text: str) -> None:
    """The reading view rebuilds the text from tokens. It must come back whole.

    Paragraph breaks and repeated spaces are the author's; a renderer that
    normalises them is silently editing the text the reader paid to have.
    """
    rebuilt = "".join(token.text + token.whitespace for token in tokenize(text))
    assert rebuilt == text


def test_punctuation_is_kept_for_display_but_carries_no_lemma() -> None:
    kinds = {token.text: token.is_word for token in tokenize("Water, please.")}
    assert kinds["Water"] is True
    assert kinds[","] is False
    assert kinds["."] is False


# --- saving a word ------------------------------------------------------------


def test_saving_a_word_creates_a_card_that_is_due_now() -> None:
    with database.session() as active:
        word, created = deck.save_word(active, "Rock", pt="rocha")

    assert created
    assert word.lemma == "rock", "the deck is keyed by lemma, lowercase"
    assert word.display == "Rock"
    assert word.state == CardState.NEW.value
    assert word.due is not None

    with database.session() as active:
        stored = words.by_lemma(active, "rock")
        assert stored is not None
        assert stored.fsrs_json != "{}"
        assert deck.load_card(stored).due == word.due


def test_a_new_card_counts_as_new_until_it_is_graded() -> None:
    """fsrs has no New state; ours is derived from the card never being reviewed."""
    from fsrs import Card

    assert deck.card_state(Card()) is CardState.NEW


def test_saving_the_same_word_twice_does_not_reset_its_schedule() -> None:
    with database.session() as active:
        first, created_first = deck.save_word(active, "rock")
        original_due = first.due

    with database.session() as active:
        second, created_second = deck.save_word(active, "Rock")

    assert created_first and not created_second
    assert second.due == original_due

    with database.session() as active:
        assert words.count(active) == 1


def test_a_second_sighting_fills_a_gap_but_does_not_overwrite() -> None:
    with database.session() as active:
        deck.save_word(active, "rock", pt="rocha")
    with database.session() as active:
        word, _ = deck.save_word(active, "rock", pt="pedra", example_en="A big rock.")

    assert word.pt == "rocha", "the translation you have been revising against wins"
    assert word.example_en == "A big rock.", "but an empty field is filled"


def test_a_blank_lemma_is_refused() -> None:
    with database.session() as active, pytest.raises(deck.DeckError, match="needs a lemma"):
        deck.save_word(active, "   ")


# --- the reading view ---------------------------------------------------------


def test_opening_a_text_that_does_not_exist_says_so() -> None:
    with database.session() as active, pytest.raises(ReadingError, match="no text with id"):
        reading.build_view(active, 999)


def test_words_already_in_the_deck_are_marked(drop_in: Callable[..., Path]) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        deck.save_word(active, "rock")

    with database.session() as active:
        view = reading.build_view(active, text_id)

    marked = {token.text for token in view.tokens if token.in_deck}
    assert marked == {"rock"}
    assert view.saved_count == 1


def test_an_inflected_form_is_marked_from_its_lemma(drop_in: Callable[..., Path]) -> None:
    """Saving 'rock' must highlight 'rocks' too, or the deck looks broken."""
    text_id = imported(drop_in, adapted_text="They go under the rocks. A rock is thick.")
    with database.session() as active:
        deck.save_word(active, "rock")

    with database.session() as active:
        view = reading.build_view(active, text_id)

    assert {token.text for token in view.tokens if token.in_deck} == {"rocks", "rock"}


def test_words_above_the_level_are_flagged_from_the_stored_measurement(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        view = reading.build_view(active, text_id)

    assert view.above_level == ["rock", "under"]
    assert {token.text for token in view.tokens if token.above_level} == {"rock", "under"}


def test_names_and_numbers_are_not_clickable(drop_in: Callable[..., Path]) -> None:
    """Offering a country as a flashcard is the bug the NER check exists to stop."""
    text_id = imported(drop_in, adapted_text="Iceland put 300 tonnes under a rock.")
    with database.session() as active:
        view = reading.build_view(active, text_id)

    clickable = {token.text for token in view.tokens if token.clickable}
    assert "Iceland" not in clickable
    assert "300" not in clickable
    assert "rock" in clickable


def test_the_glossary_says_which_entries_are_already_saved(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        view = reading.build_view(active, text_id)
    assert [item.in_deck for item in view.glossary] == [False]

    with database.session() as active:
        deck.save_word(active, "rock")
        view = reading.build_view(active, text_id)
    assert [item.in_deck for item in view.glossary] == [True]


def test_questions_carry_their_answers_but_the_index_is_what_reveals_them(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        view = reading.build_view(active, text_id)

    assert [question.index for question in view.questions] == [0]
    assert view.questions[0].q == "Where does the water go?"


def test_a_damaged_glossary_column_does_not_stop_you_reading(
    drop_in: Callable[..., Path],
) -> None:
    """The text is still readable, so refusing to open it helps nobody."""
    text_id = imported(drop_in)
    with database.session() as active:
        from graded_reader.store import texts

        row = texts.by_id(active, text_id)
        assert row is not None
        row.glossary_json = "{not json"
        active.add(row)

    with database.session() as active:
        view = reading.build_view(active, text_id)

    assert view.glossary == []
    assert view.tokens, "the text itself is untouched"


# --- the word card ------------------------------------------------------------


def test_the_card_borrows_the_translation_from_the_text_glossary(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        card = reading.build_card(active, "rock", text_id=text_id)

    assert card.pt == "rocha"
    assert card.example_en == "They put the water under a rock."
    assert card.in_deck is False
    assert card.band == "B1"


def test_the_card_knows_when_the_word_is_already_saved(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)
    with database.session() as active:
        deck.save_word(active, "rock", pt="rocha")
    with database.session() as active:
        card = reading.build_card(active, "rock", text_id=text_id)

    assert card.in_deck is True


def test_a_word_with_no_glossary_entry_still_gets_a_band() -> None:
    with database.session() as active:
        card = reading.build_card(active, "thirst")
    assert card.band == "B2"
    assert card.pt == ""


def test_saving_the_whole_glossary_is_what_gets_you_a_deck(
    drop_in: Callable[..., Path],
) -> None:
    """Section 12: M2 is done when reading a text leaves words in the deck."""
    glossary = [
        {"en": word, "pt": word, "example_en": f"A {word}.", "example_pt": ""}
        for word in ["rock", "under", "thick", "thirst", "favorite", "sign", "deep", "water"]
    ]
    text_id = imported(drop_in, glossary=glossary)

    with database.session() as active:
        view = reading.build_view(active, text_id)
        for item in view.glossary:
            deck.save_word(active, item.lemma, display=item.en, pt=item.pt)

    with database.session() as active:
        assert words.count(active) == 8
        assert reading.build_view(active, text_id).saved_count == 8


def test_a_multi_word_glossary_entry_stays_one_card(
    drop_in: Callable[..., Path],
) -> None:
    """give up is not give. Section 13 names this as the known weak spot."""
    text_id = imported(
        drop_in,
        glossary=[{"en": "give up", "pt": "desistir", "example_en": "", "example_pt": ""}],
    )
    with database.session() as active:
        view = reading.build_view(active, text_id)

    assert view.glossary[0].lemma == "give up"


def test_the_stored_columns_are_json_the_view_can_read(drop_in: Callable[..., Path]) -> None:
    """Guards the seam between what the importer writes and what reading parses."""
    text_id = imported(drop_in)
    with database.session() as active:
        from graded_reader.store import texts

        row = texts.by_id(active, text_id)
        assert row is not None
        assert isinstance(json.loads(row.glossary_json), list)
        assert isinstance(json.loads(row.questions_json), list)
        assert isinstance(json.loads(row.out_of_level), list)
