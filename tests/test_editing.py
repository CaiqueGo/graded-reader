"""Tests for pasting a document in, and for correcting a card.

Both exist because the interface was missing them, and both touch data the
reader typed by hand -- which is the kind that has no second copy anywhere.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fsrs import Rating

from graded_reader import config, deck, library, review
from graded_reader.adapters.inbox import InboxError
from graded_reader.library import ImportAction
from graded_reader.store import database, words

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")


def saved(lemma: str, **fields: str) -> int:
    with database.session() as active:
        word, _ = deck.save_word(active, lemma, **fields)  # type: ignore[arg-type]
    assert word.id is not None
    return word.id


# --- pasting a document -------------------------------------------------------


def test_a_pasted_document_is_imported(drop_in: Callable[..., Path]) -> None:
    raw = drop_in("source.json").read_text(encoding="utf-8")
    Path(config.inbox_dir() / "source.json").unlink()

    result = library.import_pasted(raw)

    assert result.action is ImportAction.IMPORTED
    assert result.title == "Water Under The Rock"


def test_a_pasted_document_is_written_to_the_inbox_before_it_is_read(
    drop_in: Callable[..., Path],
) -> None:
    """It must survive the page navigating away, valid or not."""
    raw = drop_in("source.json").read_text(encoding="utf-8")
    Path(config.inbox_dir() / "source.json").unlink()

    library.import_pasted(raw)

    kept = list(config.processed_dir().glob("pasted-*.json"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == raw


def test_pasted_rubbish_is_rejected_with_its_reason_and_kept() -> None:
    result = library.import_pasted("{not json")

    assert result.action is ImportAction.REJECTED
    assert "not valid JSON" in result.reason

    kept = list(config.rejected_dir().glob("pasted-*.json"))
    assert len(kept) == 1
    assert kept[0].read_text(encoding="utf-8") == "{not json"
    assert kept[0].with_suffix(".json.error.txt").exists()


def test_pasting_nothing_says_so_instead_of_writing_an_empty_file() -> None:
    with pytest.raises(InboxError, match="nothing pasted"):
        library.import_pasted("   \n  ")
    assert list(config.inbox_dir().glob("pasted-*.json")) == []


def test_pasting_the_same_document_twice_does_not_duplicate_it(
    drop_in: Callable[..., Path],
) -> None:
    raw = drop_in("source.json").read_text(encoding="utf-8")
    Path(config.inbox_dir() / "source.json").unlink()

    first = library.import_pasted(raw)
    second = library.import_pasted(raw)

    assert first.action is ImportAction.IMPORTED
    assert second.action is ImportAction.DUPLICATE


# --- correcting a card --------------------------------------------------------


def test_editing_overwrites_where_saving_only_filled_blanks() -> None:
    """The reader looking at the card and saying it is wrong outranks a text."""
    word_id = saved("rock", pt="rocha")

    with database.session() as active:
        deck.save_word(active, "rock", pt="pedra")
    with database.session() as active:
        assert words.by_lemma(active, "rock").pt == "rocha"  # type: ignore[union-attr]

    with database.session() as active:
        edited = deck.update_word(active, word_id, pt="pedra")
    assert edited.pt == "pedra"


def test_editing_fills_in_a_card_that_arrived_bare() -> None:
    """A word clicked in a text has no translation and no example."""
    word_id = saved("rock")

    with database.session() as active:
        edited = deck.update_word(
            active,
            word_id,
            pt="rocha",
            example_en="They put it under a rock.",
            example_pt="Puseram embaixo de uma rocha.",
        )

    assert edited.pt == "rocha"
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        card = review.to_card(row)
    assert card.cloze == f"They put it under a {review.BLANK}."
    assert card.has_example


def test_the_lemma_can_be_corrected_because_the_lemmatiser_gets_it_wrong() -> None:
    """Section 13 names this as the mitigation for odd cards."""
    word_id = saved("give")

    with database.session() as active:
        edited = deck.update_word(active, word_id, lemma="give up")

    assert edited.lemma == "give up"
    with database.session() as active:
        assert words.by_lemma(active, "give") is None
        assert words.by_lemma(active, "give up") is not None


def test_correcting_the_lemma_leaves_the_schedule_alone() -> None:
    """It is the same card. Losing its history to a typo fix would be absurd."""
    word_id = saved("give")
    with database.session() as active:
        review.grade(active, word_id, int(Rating.Good), now=datetime(2026, 9, 21, 9, tzinfo=UTC))

    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        before = (row.due, row.stability, row.state, row.fsrs_json)

    with database.session() as active:
        deck.update_word(active, word_id, lemma="give up")

    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        assert (row.due, row.stability, row.state, row.fsrs_json) == before


def test_correcting_the_lemma_recomputes_the_band() -> None:
    word_id = saved("rock")
    with database.session() as active:
        assert deck.update_word(active, word_id, lemma="thirst").band == "B2"


def test_renaming_onto_a_card_that_exists_is_refused_rather_than_merged() -> None:
    """Merging two schedules is a decision this cannot make for the reader."""
    first = saved("rock")
    saved("stone")

    with database.session() as active, pytest.raises(deck.DeckError, match="already a card"):
        deck.update_word(active, first, lemma="stone")


def test_an_empty_lemma_is_refused() -> None:
    word_id = saved("rock")
    with database.session() as active, pytest.raises(deck.DeckError, match="needs a lemma"):
        deck.update_word(active, word_id, lemma="   ")


def test_blanking_a_field_clears_it_rather_than_storing_whitespace() -> None:
    word_id = saved("rock", pt="rocha")
    with database.session() as active:
        assert deck.update_word(active, word_id, pt="  ").pt == ""

    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None and row.pt is None


def test_editing_a_card_that_does_not_exist_says_so() -> None:
    with database.session() as active, pytest.raises(deck.DeckError, match="no card with id"):
        deck.update_word(active, 999, pt="x")


# --- adding a word with no text behind it -------------------------------------


def test_a_word_can_be_added_straight_to_the_deck() -> None:
    with database.session() as active:
        word, created = deck.save_word(
            active, "Serendipity", pt="acaso feliz", example_en="A serendipity."
        )

    assert created
    assert word.lemma == "serendipity"
    assert word.band == "C2"
    assert word.due is not None, "it is schedulable from the moment it exists"


def test_adding_a_word_already_in_the_deck_does_not_reset_it() -> None:
    word_id = saved("rock")
    with database.session() as active:
        row = words.by_id(active, word_id)
        assert row is not None
        before = row.due

    with database.session() as active:
        word, created = deck.save_word(active, "rock", pt="rocha")

    assert not created
    assert word.due == before


def test_a_directly_added_word_joins_the_queue() -> None:
    moment = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    with database.session() as active:
        assert review.next_word(active, now=moment) is None

    saved("rock")

    with database.session() as active:
        nxt = review.next_word(active, now=moment)
        assert nxt is not None and nxt.lemma == "rock"


def test_correcting_the_lemma_moves_the_shown_form_with_it() -> None:
    """Otherwise the card reads "give" while the deck knows it as "give up"."""
    word_id = saved("give")
    with database.session() as active:
        edited = deck.update_word(active, word_id, lemma="give up")

    assert edited.lemma == "give up"
    assert edited.display == "give up", "the front is what gets reviewed and exported"


def test_a_display_the_reader_chose_survives_a_lemma_correction() -> None:
    """Only a display that was a copy of the old lemma follows it."""
    word_id = saved("give")
    with database.session() as active:
        deck.update_word(active, word_id, display="to give")
    with database.session() as active:
        edited = deck.update_word(active, word_id, lemma="give up")

    assert edited.lemma == "give up"
    assert edited.display == "to give"
