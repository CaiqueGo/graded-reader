"""The database connection.

One process, one file. There is no pool to tune: the schema is created on first
use and, until the app has a user other than its author, ``create_all`` is
almost the whole story.

The exception is ``_add_missing_columns``, which handles exactly one case --
a new nullable column on an existing table -- because ``create_all`` skips
tables that already exist and the alternative is asking the reader to throw away
their deck every time a field is added. It deliberately does nothing else. A
renamed column, a changed type or a backfill is a real migration, and the honest
move then is Alembic rather than growing this function until it is a worse one.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
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
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)
    _engines[path] = engine
    return engine


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
