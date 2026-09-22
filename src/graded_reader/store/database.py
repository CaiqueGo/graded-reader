"""The database connection.

One process, one file. There is no pool to tune: the schema is created on first
use and, until the app has a user other than its author, ``create_all`` is
almost the whole story.

Two functions do the little that ``create_all`` cannot, because it skips tables
that already exist and the alternative is asking the reader to throw away their
deck every time the schema moves.

``_add_missing_columns`` adds a new column. That is all it does.

``_relax_word_uniqueness`` is a real migration, and the only one. SQLite cannot
drop a constraint with ALTER TABLE, so removing the UNIQUE that used to sit on
``word.lemma`` means rebuilding the table and copying the rows across. It had to
go: one card per word is right for word cards and wrong for sentences, where
three cards can share a target. The uniqueness now lives in a partial index that
applies to word cards only.

It copies the file first, and that is not belt-and-braces. The obvious
protection would be a transaction, and it does not work here: pysqlite issues an
implicit COMMIT before a DDL statement, so by the time the INSERT runs, the
rename and the CREATE are already on disk and a rollback takes nothing back.
This was not a guess -- an early version of this function claimed to be atomic,
failed halfway on a real deck, and left an empty table where ten cards had been.
A copy of the file is the only thing that actually holds.

It runs once. Afterwards it detects its own work and does nothing, so it is safe
on every start. Anything more than this -- a renamed column, a changed type, a
backfill -- is where Alembic earns its place rather than this file growing into
a worse version of it.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from graded_reader import config
from graded_reader.store import models as _models  # noqa: F401  -- registers the tables

_engines: dict[Path, Engine] = {}


def engine_for(path: Path | None = None) -> Engine:
    """The engine for a database file, created once per path.

    Cached per path rather than globally so a test can point GRADED_READER_DB at a
    temporary file and get its own database instead of the previous test's.
    """
    path = path or config.db_path()
    existing = _engines.get(path)
    if existing is not None:
        return existing

    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    _relax_word_uniqueness(engine, path)
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    _engines[path] = engine
    return engine


#: The columns the old table had, in the order it had them. Copying by name
#: rather than with SELECT * is what keeps the migration honest if the model
#: gains a column between someone's last run and this one.
_LEGACY_WORD_COLUMNS = (
    "id",
    "lemma",
    "display",
    "pt",
    "example_en",
    "example_pt",
    "band",
    "first_text_id",
    "created_at",
    "fsrs_json",
    "due",
    "stability",
    "state",
)


def _word_lemma_is_unique(engine: Engine) -> bool:
    """Whether the file still has the table-level UNIQUE on word.lemma."""
    inspector = inspect(engine)
    if "word" not in set(inspector.get_table_names()):
        return False
    for index in inspector.get_indexes("word"):
        if not index.get("unique"):
            continue
        # The replacement index is partial and SQLAlchemy reports its filter.
        # Checking that first also steps around a trap: for a partial index the
        # column list comes back holding SQL expression objects, and comparing
        # one with == builds a clause instead of answering a question -- which
        # then raises when it is used as a boolean.
        if index.get("dialect_options", {}).get("sqlite_where") is not None:
            continue
        names = [str(name) for name in (index.get("column_names") or [])]
        if names == ["lemma"]:
            return True
    with engine.connect() as connection:
        sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='word'")
        ).scalar()
    return bool(sql) and "UNIQUE" in str(sql).upper()


def _backup_before_migrating(path: Path) -> Path | None:
    """Copy the database beside itself, and say where it went.

    The rows about to be moved are the reader's whole study history and there is
    no other copy of them. An in-memory database has no file to copy and needs
    none.
    """
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.{stamp}.backup")
    shutil.copy2(path, backup)
    return backup


def _relax_word_uniqueness(engine: Engine, path: Path | None = None) -> None:
    """Rebuild the word table so a lemma may repeat across sentence cards.

    Not atomic, and it cannot be -- see the module docstring. The file is copied
    first instead, and a failure points at the copy rather than leaving the
    reader to work out what happened to their deck.
    """
    if not _word_lemma_is_unique(engine):
        return

    backup = _backup_before_migrating(path) if path else None
    try:
        _rebuild_word_table(engine)
    except Exception as error:
        where = f" The deck as it was is at {backup}." if backup else ""
        raise RuntimeError(
            f"migrating the deck to allow sentence cards failed: {error}.{where}"
        ) from error


def _rebuild_word_table(engine: Engine) -> None:
    columns = ", ".join(_safe_identifier(name) for name in _LEGACY_WORD_COLUMNS)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE word RENAME TO word_legacy"))

        # Renaming a table in SQLite does not rename its indexes. They follow
        # the table under their original names and would collide with the ones
        # the new table declares, so they go first. Dropping them loses nothing:
        # they belong to a table this function is about to delete.
        stale = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='word_legacy' AND sql IS NOT NULL"
            )
        ).scalars()
        for name in list(stale):
            connection.execute(text(f"DROP INDEX {_safe_identifier(name)}"))

        SQLModel.metadata.tables["word"].create(bind=connection)
        # kind is declared NOT NULL with a default on the Python side, not in
        # the DDL, so a raw INSERT has to say what it is. Everything that
        # existed before sentences did is a word card.
        connection.execute(
            text(f"INSERT INTO word (kind, {columns}) SELECT 'word', {columns} FROM word_legacy")
        )
        connection.execute(text("DROP TABLE word_legacy"))


#: A bare SQL identifier. DDL cannot be parameterised -- a placeholder binds a
#: value, never a table or column name -- so the statement below has to be
#: assembled as text. This is what keeps that from being a hole: the names come
#: from the model metadata in this repository, and they are checked against this
#: before they reach a statement rather than trusted because of where they came
#: from.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: A rendered SQL type, which is not an identifier: VARCHAR(50), DECIMAL(9, 2),
#: DOUBLE PRECISION. Checked the same way and for the same reason.
_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]*(\([0-9]+(, ?[0-9]+)?\))?$")


def _safe_identifier(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"refusing to build DDL with the identifier {name!r}")
    return name


def _safe_type(rendered: str) -> str:
    if not _TYPE.match(rendered):
        raise ValueError(f"refusing to build DDL with the type {rendered!r}")
    return rendered


def _add_missing_columns(engine: Engine) -> None:
    """Add columns the models declare and the file does not have.

    Only additions. A NOT NULL column is added with its declared default so the
    rows already in the table stay valid; one with no usable default is left
    alone, which surfaces as a loud error instead of a quiet wrong answer.
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            clause = _column_clause(column, engine)
            if clause is None:
                continue
            statement = f"ALTER TABLE {_safe_identifier(table.name)} ADD COLUMN {clause}"
            with engine.begin() as connection:
                connection.execute(text(statement))


def _column_clause(column: Any, engine: Engine) -> str | None:
    """The ADD COLUMN clause for a column, or None when it cannot be added."""
    name = _safe_identifier(column.name)
    kind = _safe_type(column.type.compile(engine.dialect))
    if column.nullable:
        return f"{name} {kind}"

    default = getattr(column.default, "arg", None)
    if isinstance(default, str):
        escaped = default.replace("'", "''")
        return f"{name} {kind} NOT NULL DEFAULT '{escaped}'"
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        return f"{name} {kind} NOT NULL DEFAULT {default}"
    return None


@contextmanager
def session(path: Path | None = None) -> Iterator[Session]:
    """A session that commits on success and rolls back on any exception.

    Callers never commit by hand. A half-written import is worse than a failed
    one: the file has already moved out of the inbox by then.
    """
    with Session(engine_for(path)) as active:
        try:
            yield active
            active.commit()
        except Exception:
            active.rollback()
            raise
