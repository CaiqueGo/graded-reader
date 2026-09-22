"""The database connection.

One process, one file. There is no pool to tune and no migration tool: the
schema is created on first use and, until the app has a user other than its
author, ``create_all`` is the whole story. When that stops being true, the
honest move is Alembic, not a hand-written patcher.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from degrau import config
from degrau.store import models as _models  # noqa: F401  -- registers the tables

_engines: dict[Path, Engine] = {}


def engine_for(path: Path | None = None) -> Engine:
    """The engine for a database file, created once per path.

    Cached per path rather than globally so a test can point DEGRAU_DB at a
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
    _engines[path] = engine
    return engine


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
