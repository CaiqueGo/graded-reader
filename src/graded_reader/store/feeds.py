"""The news feeds the reader chose, grouped by topic."""

from __future__ import annotations

from sqlmodel import Session, col, select

from graded_reader.store.models import Feed


def all_feeds(session: Session) -> list[Feed]:
    """Every feed, by topic and then in the order they were added."""
    statement = select(Feed).order_by(col(Feed.topic), col(Feed.id))
    return list(session.exec(statement))


def topics(session: Session) -> list[str]:
    """The distinct topics that have at least one feed, alphabetically."""
    return sorted({feed.topic for feed in all_feeds(session)})


def by_url(session: Session, url: str) -> Feed | None:
    return session.exec(select(Feed).where(col(Feed.url) == url)).first()


def add(session: Session, feed: Feed) -> Feed:
    session.add(feed)
    session.flush()
    session.refresh(feed)
    return feed


def remove(session: Session, feed_id: int) -> bool:
    row = session.get(Feed, feed_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
