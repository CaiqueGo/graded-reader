"""Reading and writing adapted texts."""

from __future__ import annotations

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


def recent(session: Session, limit: int = 20) -> list[Text]:
    """Most recently imported first."""
    statement = select(Text).order_by(col(Text.created_at).desc()).limit(limit)
    return list(session.exec(statement))


def count(session: Session) -> int:
    return len(list(session.exec(select(Text.id))))
