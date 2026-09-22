"""Tests for the Anki export.

Section 12 says this stage is done when the file imports without manual
adjustment, so every test here is about a way the file could parse fine and
still be wrong: markup escaped into visible angle brackets, a translation with a
comma splitting into two columns, a tags column that Anki reads as a field.

None of that raises anything. It produces a deck full of broken cards, which the
reader finds out about one card at a time, weeks later.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest

from graded_reader import anki, deck
from graded_reader.store import database, words

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db")

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def card(**fields: object) -> deck.SavedWord:
    base: dict[str, object] = {
        "lemma": "rock",
        "display": "rock",
        "band": "B1",
        "pt": "rocha",
        "example_en": "They put the water under a rock.",
        "example_pt": "",
        "due": NOW,
        "state": "new",
    }
    return deck.SavedWord.model_validate({**base, **fields})


def parse(document: str) -> tuple[list[str], list[list[str]]]:
    """Split a rendered file into its headers and its rows, the way Anki would."""
    lines = document.splitlines()
    headers = [line for line in lines if line.startswith("#")]
    body = "\n".join(lines[len(headers) :])
    return headers, [row for row in csv.reader(io.StringIO(body)) if row]


# --- the headers ----------------------------------------------------------------


def test_the_headers_say_comma_and_html() -> None:
    headers, _rows = parse(anki.render([anki.build_note(card())]))
    assert "#separator:Comma" in headers
    assert "#html:true" in headers


def test_the_third_column_is_declared_as_tags() -> None:
    """Without this Anki imports the column as a third field, not as tags."""
    headers, _rows = parse(anki.render([anki.build_note(card())]))
    assert "#tags column:3" in headers


def test_the_deck_is_named_and_the_notetype_is_not_guessed() -> None:
    """The default note type is called something different in each language."""
    headers, _rows = parse(anki.render([anki.build_note(card())], deck="Graded Reader"))
    assert "#deck:Graded Reader" in headers
    assert not any(line.startswith("#notetype") for line in headers)


def test_a_notetype_is_emitted_when_the_caller_names_one() -> None:
    headers, _rows = parse(anki.render([anki.build_note(card())], notetype="Basic"))
    assert "#notetype:Basic" in headers


def test_the_headers_come_before_any_row() -> None:
    document = anki.render([anki.build_note(card())])
    body_starts = min(i for i, line in enumerate(document.splitlines()) if not line.startswith("#"))
    assert all(line.startswith("#") for line in document.splitlines()[:body_starts])


# --- escaping --------------------------------------------------------------------


def test_the_ampersand_is_escaped_before_the_angle_brackets() -> None:
    """Doing it the other way round turns &lt; into &amp;lt; and prints it."""
    assert anki.escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_content_is_escaped_but_our_own_markup_is_not() -> None:
    """The <br> and <i> this module adds have to survive as real HTML."""
    note = anki.build_note(card(pt="mais & menos", example_en="Use <b> for bold."))
    assert "mais &amp; menos" in note.back
    assert "Use &lt;b&gt; for bold." in note.back
    assert "<br><i>" in note.back, "the separator is markup, not content"
    assert note.back.endswith("</i>")


def test_a_line_break_in_a_field_becomes_html() -> None:
    """A raw newline would end the CSV row halfway through the card."""
    note = anki.build_note(card(example_en="First line.\nSecond line."))
    assert "First line.<br>Second line." in note.back
    assert "\n" not in note.back


def test_windows_line_endings_do_not_become_two_breaks() -> None:
    note = anki.build_note(card(example_en="First.\r\nSecond."))
    assert note.back.count("<br>") == 2, "one for the separator, one for the newline"


# --- the columns -----------------------------------------------------------------


def test_the_back_is_the_translation_then_the_example_in_italics() -> None:
    _headers, rows = parse(anki.render([anki.build_note(card())]))
    assert rows[0][1] == "rocha<br><i>They put the water under a rock.</i>"


def test_a_card_without_an_example_carries_no_stray_separator() -> None:
    note = anki.build_note(card(example_en=""))
    assert note.back == "rocha"


def test_a_card_without_a_translation_still_shows_its_example() -> None:
    note = anki.build_note(card(pt=""))
    assert note.back == "<i>They put the water under a rock.</i>"


def test_a_card_with_neither_is_marked_as_having_nothing_on_the_back() -> None:
    note = anki.build_note(card(pt="", example_en=""))
    assert note.back == ""
    assert not note.usable


def test_the_word_is_the_first_column_so_anki_can_spot_duplicates() -> None:
    """Anki reads the first field as the identity of a note."""
    _headers, rows = parse(anki.render([anki.build_note(card())]))
    assert rows[0][0] == "rock"


# --- csv mechanics ---------------------------------------------------------------


def test_a_comma_in_a_translation_does_not_split_the_row() -> None:
    _headers, rows = parse(anki.render([anki.build_note(card(pt="rocha, pedra"))]))
    assert len(rows[0]) == 3
    assert rows[0][1].startswith("rocha, pedra")


def test_a_quotation_mark_survives_the_round_trip() -> None:
    _headers, rows = parse(anki.render([anki.build_note(card(pt='uma "rocha"'))]))
    assert len(rows[0]) == 3
    assert 'uma "rocha"' in rows[0][1]


def test_every_row_has_exactly_three_columns() -> None:
    notes = [
        anki.build_note(card(lemma="rock")),
        anki.build_note(card(lemma="stone", pt="", example_en="")),
        anki.build_note(card(lemma="thick", pt="grosso, denso; espesso")),
    ]
    _headers, rows = parse(anki.render(notes))
    assert [len(row) for row in rows] == [3, 3, 3]


def test_accented_text_is_preserved() -> None:
    """The file is written as UTF-8, which is what Anki expects."""
    _headers, rows = parse(anki.render([anki.build_note(card(pt="não é só água"))]))
    assert "não é só água" in rows[0][1]


# --- tags -------------------------------------------------------------------------


def test_the_tags_are_the_source_the_kind_and_the_band() -> None:
    """The kind rides along so Anki can give the two of them different settings."""
    note = anki.build_note(card(band="A2"))
    assert note.tags == "graded-reader word A2"


def test_a_sentence_card_is_tagged_as_one() -> None:
    note = anki.build_note(sentence_card())
    assert note.tags == "graded-reader sentence B1"


def test_a_card_with_no_band_still_carries_the_source_tag() -> None:
    assert anki.build_note(card(band="")).tags == "graded-reader word"


def test_a_tag_containing_a_space_is_joined_rather_than_split() -> None:
    """Spaces separate tags, so one with a space in it would become two."""
    note = anki.build_note(card(), extra_tags=["from a text"])
    assert note.tags == "graded-reader word B1 from-a-text"


# --- sentence cards ----------------------------------------------------------------


def sentence_card(**fields: object) -> deck.SavedWord:
    frase = "They put the water deep under a rock."
    base: dict[str, object] = {
        "kind": "sentence",
        "lemma": "rock",
        "display": frase,
        "band": "B1",
        "sentence": frase,
        "sentence_pt": "Eles puseram a agua bem fundo, sob uma rocha.",
        "due": NOW,
        "state": "new",
    }
    return deck.SavedWord.model_validate({**base, **fields})


def test_a_sentence_card_puts_the_sentence_on_the_front() -> None:
    """Exporting the word instead would ship a different card than the one studied."""
    _headers, rows = parse(anki.render([anki.build_note(sentence_card())]))
    assert rows[0][0] == "They put the water deep under a rock."
    assert rows[0][1] == "Eles puseram a agua bem fundo, sob uma rocha."


def test_a_sentence_card_carries_no_italics_wrapper() -> None:
    """The back is the meaning, not an example beneath a translation."""
    note = anki.build_note(sentence_card())
    assert "<i>" not in note.back


def test_a_sentence_saved_without_a_meaning_exports_with_an_empty_back() -> None:
    note = anki.build_note(sentence_card(sentence_pt=""))
    assert note.back == ""
    assert not note.usable


def test_a_sentence_with_a_comma_still_makes_one_row() -> None:
    long_one = "When it rains, the water goes under the rock."
    _headers, rows = parse(anki.render([anki.build_note(sentence_card(sentence=long_one))]))
    assert len(rows[0]) == 3
    assert rows[0][0] == long_one


# --- end to end -------------------------------------------------------------------


def test_the_deck_exports_in_the_order_it_was_built() -> None:
    for lemma in ("rock", "stone", "thick"):
        with database.session() as active:
            deck.save_word(active, lemma, pt=lemma)

    with database.session() as active:
        values = deck.as_values(words.for_export(active))

    _headers, rows = parse(anki.render(anki.build_notes(values)))
    assert [row[0] for row in rows] == ["rock", "stone", "thick"]


def test_exporting_only_one_band_leaves_the_rest_out() -> None:
    with database.session() as active:
        deck.save_word(active, "rock", band="A1")
        deck.save_word(active, "thirst", band="B2")

    with database.session() as active:
        values = deck.as_values(words.for_export(active, band="A1"))

    assert [word.lemma for word in values] == ["rock"]


def test_an_empty_deck_renders_headers_and_nothing_else() -> None:
    headers, rows = parse(anki.render([]))
    assert headers
    assert rows == []


def test_the_rendered_document_uses_one_newline_per_line() -> None:
    """Whatever the platform does to files, render() itself is unambiguous."""
    document = anki.render([anki.build_note(card())])
    assert "\r" not in document
    assert document.endswith("\n")
