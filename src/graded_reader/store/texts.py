"""Reading and writing adapted texts."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, col, select

from graded_reader.store.models import Text


def by_hash(session: Session, content_hash: str) -> Text | None:
    """The text with this content hash, if it was already imported."""
    return session.exec(select(Text).where(Text.content_hash == content_hash)).first()


def by_id(session: Session, text_id: int) -> Text | None:
    return session.get(Text, text_id)


def save(session: Session, text: Text) -> Text:
    """Persist a text and return it with its id filled in."""
    session.add(text)
    session.flush()
    session.refresh(text)
    return text


def recent(session: Session, limit: int = 20, *, archived: bool = False) -> list[Text]:
    """Most recently imported first, either the library or its archive."""
    archive_filter = (
        col(Text.archived_at).is_not(None) if archived else col(Text.archived_at).is_(None)
    )
    statement = (
        select(Text).where(archive_filter).order_by(col(Text.created_at).desc()).limit(limit)
    )
    return list(session.exec(statement))


def count(session: Session, *, archived: bool | None = None) -> int:
    """How many texts, all of them or only one side of the archive."""
    statement = select(Text.id)
    if archived is True:
        statement = statement.where(col(Text.archived_at).is_not(None))
    elif archived is False:
        statement = statement.where(col(Text.archived_at).is_(None))
    return len(list(session.exec(statement)))


def set_archived(session: Session, text_id: int, when: datetime | None) -> bool:
    """Archive a text (a moment) or bring it back (None). False if there is no such text."""
    row = session.get(Text, text_id)
    if row is None:
        return False
    row.archived_at = when
    session.add(row)
    session.flush()
    return True


def source_values(session: Session, kind: str) -> set[str]:
    """Every source value of this kind -- for URLs, the articles already read."""
    statement = select(Text.source_value).where(col(Text.source_kind) == kind)
    return {value for value in session.exec(statement) if value}
