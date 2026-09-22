"""Tests for the one migration this project has.

The rows it moves are the reader's entire study history and there is no other
copy of them, so the things worth proving are: nothing is lost, it only runs
once, it leaves a file behind to go back to, and it does not touch a deck that
never had the old shape.

The old schema is written out by hand rather than fetched from anywhere. That is
the point of the test -- it has to keep describing what the database looked like
before, even after nothing in the codebase does.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fsrs import Card

from graded_reader.store import database, words
from graded_reader.store.models import CardKind

#: The word table as it was before sentence cards, UNIQUE index and all.
LEGACY_SCHEMA = """
CREATE TABLE word (
    id INTEGER NOT NULL PRIMARY KEY,
    lemma VARCHAR NOT NULL,
    display VARCHAR NOT NULL,
    pt VARCHAR,
    example_en VARCHAR,
    example_pt VARCHAR,
    band VARCHAR,
    first_text_id INTEGER,
    created_at DATETIME NOT NULL,
    fsrs_json VARCHAR NOT NULL,
    due DATETIME NOT NULL,
    stability FLOAT,
    state VARCHAR NOT NULL
);
CREATE UNIQUE INDEX ix_word_lemma ON word (lemma);
CREATE INDEX ix_word_state ON word (state);
CREATE INDEX ix_word_due ON word (due);
CREATE INDEX ix_word_band ON word (band);
CREATE TABLE review (
    id INTEGER NOT NULL PRIMARY KEY,
    word_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    reviewed_at DATETIME NOT NULL,
    log_json VARCHAR NOT NULL,
    card_before_json VARCHAR NOT NULL
);
"""


def make_legacy(path: Path, *, lemmas: list[str]) -> None:
    """A database in the shape the app used to write."""
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    card = json.dumps(Card().to_dict())
    now = datetime(2026, 9, 1, 9, 0, tzinfo=UTC).isoformat()
    for index, lemma in enumerate(lemmas, start=1):
        connection.execute(
            "INSERT INTO word (id, lemma, display, pt, band, created_at, fsrs_json,"
            " due, stability, state) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (index, lemma, lemma, f"{lemma}-pt", "A1", now, card, now, 12.5, "review"),
        )
    connection.execute(
        "INSERT INTO review (id, word_id, rating, reviewed_at, log_json,"
        " card_before_json) VALUES (1, 1, 3, ?, '{}', ?)",
        (now, card),
    )
    connection.commit()
    connection.close()


@pytest.fixture()
def legacy_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "old.db"
    make_legacy(path, lemmas=["rock", "stone", "thick"])
    monkeypatch.setenv("GRADED_READER_DB", str(path))
    return path


def indexes(path: Path) -> dict[str, str]:
    connection = sqlite3.connect(path)
    rows = connection.execute(
        "SELECT name, COALESCE(sql, '') FROM sqlite_master WHERE type='index' AND tbl_name='word'"
    ).fetchall()
    connection.close()
    return dict(rows)


def columns(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    names = {row[1] for row in connection.execute("PRAGMA table_info(word)")}
    connection.close()
    return names


# --- what it must not lose -------------------------------------------------------


def test_every_card_and_every_review_survives(legacy_db: Path) -> None:
    database.engine_for(legacy_db)

    with database.session(legacy_db) as active:
        assert words.count(active) == 3
        kept = words.all_cards(active)
        assert sorted(word.lemma for word in kept) == ["rock", "stone", "thick"]
        assert all(word.stability == 12.5 for word in kept), "the schedule comes across"
        assert all(word.fsrs_json for word in kept), "and so does the card inside it"

    connection = sqlite3.connect(legacy_db)
    assert connection.execute("SELECT COUNT(*) FROM review").fetchone()[0] == 1
    connection.close()


def test_everything_that_existed_before_is_a_word_card(legacy_db: Path) -> None:
    database.engine_for(legacy_db)
    with database.session(legacy_db) as active:
        assert all(word.kind == CardKind.WORD.value for word in words.all_cards(active))


def test_the_file_is_copied_before_anything_is_touched(legacy_db: Path) -> None:
    """pysqlite commits before DDL, so a rollback would take nothing back."""
    database.engine_for(legacy_db)

    backups = list(legacy_db.parent.glob(f"{legacy_db.name}.*.backup"))
    assert len(backups) == 1

    connection = sqlite3.connect(backups[0])
    assert connection.execute("SELECT COUNT(*) FROM word").fetchone()[0] == 3
    assert "UNIQUE" in str(indexes(backups[0])["ix_word_lemma"]).upper()
    connection.close()


# --- what it must change ---------------------------------------------------------


def test_the_new_columns_arrive(legacy_db: Path) -> None:
    database.engine_for(legacy_db)
    assert {"kind", "sentence", "sentence_pt"} <= columns(legacy_db)


def test_uniqueness_survives_but_only_for_word_cards(legacy_db: Path) -> None:
    """Three sentences may share a target; two word cards may not."""
    database.engine_for(legacy_db)

    unique = {name: sql for name, sql in indexes(legacy_db).items() if "UNIQUE" in sql.upper()}
    assert len(unique) == 1
    assert "kind = 'word'" in next(iter(unique.values()))


def test_the_scratch_table_is_gone(legacy_db: Path) -> None:
    connection = sqlite3.connect(legacy_db)
    database.engine_for(legacy_db)
    leftover = connection.execute("SELECT 1 FROM sqlite_master WHERE name='word_legacy'").fetchone()
    connection.close()
    assert leftover is None


# --- when it must do nothing -----------------------------------------------------


def test_running_twice_migrates_once(legacy_db: Path) -> None:
    database.engine_for(legacy_db)
    database._engines.pop(legacy_db, None)
    database.engine_for(legacy_db)

    assert len(list(legacy_db.parent.glob(f"{legacy_db.name}.*.backup"))) == 1
    with database.session(legacy_db) as active:
        assert words.count(active) == 3


def test_a_database_that_never_had_the_old_shape_is_left_alone(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.db"
    database.engine_for(fresh)

    assert list(tmp_path.glob("fresh.db.*.backup")) == [], "nothing to back up"
    assert {"kind", "sentence"} <= columns(fresh)
