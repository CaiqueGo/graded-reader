"""Tests for the import contract.

The inbox is the seam between a model and the database, which makes it the place
where a silent failure is most expensive: the text cost a model call to produce,
and a file that vanishes without a note is a call you cannot get back.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from graded_reader import config, library
from graded_reader.adapters.inbox import InboxError, parse, pending
from graded_reader.library import ImportAction, content_hash
from graded_reader.store import database, texts

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

#: The adapted English of the reference document in conftest.
REFERENCE_TEXT = "They put the water deep under a rock."


# --- the contract, in isolation ----------------------------------------------


def test_a_minimal_document_needs_only_schema_level_and_text() -> None:
    adapted = parse(json.dumps({"schema": 1, "level": "A1", "adapted_text": "The water."}))
    assert adapted.level == "A1"
    assert adapted.title == ""
    assert adapted.glossary == []


def test_unknown_fields_are_ignored_rather_than_refused() -> None:
    """A producer that adds a field must not break an importer that predates it."""
    adapted = parse(
        json.dumps({"schema": 1, "level": "A1", "adapted_text": "The water.", "mood": "calm"})
    )
    assert adapted.adapted_text == "The water."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "expected a JSON object"),
        ('{"level": "A1", "adapted_text": "x"}', "missing required field 'schema'"),
        ('{"schema": 99, "level": "A1", "adapted_text": "x"}', "schema 99 is not supported"),
        ('{"schema": 1, "adapted_text": "x"}', "level"),
        ('{"schema": 1, "level": "A1"}', "adapted_text"),
        ('{"schema": 1, "level": "A1", "adapted_text": ""}', "adapted_text"),
        ('{"schema": 1, "level": "B3", "adapted_text": "x"}', "unknown level"),
        ('{"schema": 1, "level": "NA", "adapted_text": "x"}', "not a target level"),
    ],
)
def test_every_rejection_explains_itself(raw: str, expected: str) -> None:
    with pytest.raises(InboxError, match=expected):
        parse(raw)


# --- importing ----------------------------------------------------------------


def test_a_valid_file_lands_in_the_database_with_its_coverage(
    drop_in: Callable[..., Path],
) -> None:
    result = library.import_file(drop_in())

    assert result.action is ImportAction.IMPORTED
    assert result.text_id is not None
    assert result.coverage_pct == pytest.approx(0.75)

    with database.session() as active:
        stored = texts.by_id(active, result.text_id)
        assert stored is not None
        assert stored.title == "Water Under The Rock"
        assert stored.level == "A1"
        assert stored.coverage_pct == pytest.approx(0.75)
        assert stored.original_text == "The original, as it was pasted."
        assert json.loads(stored.glossary_json)[0]["en"] == "rock"
        assert json.loads(stored.questions_json)[0]["q"] == "Where does the water go?"
        assert [entry["lemma"] for entry in json.loads(stored.out_of_level)] == ["rock", "under"]


def test_an_imported_file_moves_out_of_the_inbox(drop_in: Callable[..., Path]) -> None:
    path = drop_in("story.json")
    library.import_file(path)

    assert not path.exists()
    assert (config.processed_dir() / "story.json").exists()
    assert pending(config.inbox_dir()) == []


def test_a_broken_file_is_kept_with_a_note_saying_why(drop_in: Callable[..., Path]) -> None:
    path = drop_in("broken.json", raw="{not json")
    result = library.import_file(path)

    assert result.action is ImportAction.REJECTED
    kept = config.rejected_dir() / "broken.json"
    assert kept.exists(), "the input is never deleted"
    assert kept.read_text(encoding="utf-8") == "{not json"

    note = config.rejected_dir() / "broken.json.error.txt"
    assert "not valid JSON" in note.read_text(encoding="utf-8")


def test_a_rejected_file_never_reaches_the_database(drop_in: Callable[..., Path]) -> None:
    library.import_file(drop_in("broken.json", raw="{not json"))
    with database.session() as active:
        assert texts.count(active) == 0


def test_importing_the_same_text_twice_does_not_duplicate_it(
    drop_in: Callable[..., Path],
) -> None:
    first = library.import_file(drop_in("first.json"))
    second = library.import_file(drop_in("second.json"))

    assert first.action is ImportAction.IMPORTED
    assert second.action is ImportAction.DUPLICATE
    assert second.text_id == first.text_id

    with database.session() as active:
        assert texts.count(active) == 1


def test_a_duplicate_still_leaves_the_inbox(drop_in: Callable[..., Path]) -> None:
    """Otherwise the same file is re-examined on every run, forever."""
    library.import_file(drop_in("first.json"))
    path = drop_in("second.json")
    library.import_file(path)

    assert not path.exists()
    assert (config.processed_dir() / "second.json").exists()


def test_trailing_whitespace_does_not_make_a_new_text() -> None:
    assert content_hash("The water.\n\n") == content_hash("The water.")


def test_a_reworded_sentence_is_a_different_text() -> None:
    assert content_hash("The water is deep.") != content_hash("The water is deeper.")


def test_a_second_file_with_different_english_gets_its_own_row(
    drop_in: Callable[..., Path],
) -> None:
    library.import_file(drop_in("one.json"))
    other = library.import_file(drop_in("two.json", adapted_text="They go under the thick rock."))

    assert other.action is ImportAction.IMPORTED
    with database.session() as active:
        assert texts.count(active) == 2


def test_two_files_with_the_same_name_do_not_overwrite_each_other(
    drop_in: Callable[..., Path],
) -> None:
    library.import_file(drop_in("same.json"))
    library.import_file(drop_in("same.json", adapted_text="A thick rock."))

    kept = sorted(item.name for item in config.processed_dir().glob("same*.json"))
    assert kept == ["same-1.json", "same.json"]


def test_draining_the_inbox_reports_one_result_per_file(drop_in: Callable[..., Path]) -> None:
    drop_in("a.json")
    drop_in("b.json", adapted_text="A thick rock.")
    drop_in("c.json", raw="{not json")

    results = library.import_inbox()
    actions = sorted(result.action.value for result in results)

    assert actions == ["imported", "imported", "rejected"]
    assert pending(config.inbox_dir()) == []


def test_processed_and_rejected_are_not_picked_up_again(drop_in: Callable[..., Path]) -> None:
    drop_in("a.json")
    drop_in("b.json", raw="{not json")
    library.import_inbox()

    assert library.import_inbox() == []


def test_a_text_below_its_threshold_is_imported_anyway_and_says_so(
    drop_in: Callable[..., Path],
) -> None:
    """Section 6 of the MVP: show the number, do not block the import."""
    result = library.import_file(drop_in())

    assert result.action is ImportAction.IMPORTED
    assert result.meets_threshold is False
    assert result.threshold == 0.95
