"""Reading and writing the review log."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, col, func, select

from degrau.store.models import Review, Word


def add(session: Session, review: Review) -> Review:
    session.add(review)
    session.flush()
    session.refresh(review)
    return review


def last(session: Session) -> Review | None:
    """The most recent grading, whichever card it was.

    Undo walks backwards through time, not through the queue: the thing the
    reader wants back is the answer they just gave.
    """
    statement = select(Review).order_by(col(Review.reviewed_at).desc(), col(Review.id).desc())
    return session.exec(statement).first()


def drop(session: Session, review: Review) -> None:
    session.delete(review)
    session.flush()


def count_between(session: Session, start: datetime, end: datetime) -> int:
    """How many gradings happened in a window."""
    statement = (
        select(Review.id)
        .where(col(Review.reviewed_at) >= start)
        .where(col(Review.reviewed_at) < end)
    )
    return len(list(session.exec(statement)))


def introduced_between(session: Session, start: datetime, end: datetime) -> int:
    """How many cards were seen for the very first time in a window.

    This is what the daily new-card limit counts. A card reviewed for the fourth
    time today is not a new card, however new it feels.
    """
    first_seen = (
        select(Review.word_id, func.min(col(Review.reviewed_at)).label("first_at"))
        .group_by(col(Review.word_id))
        .subquery()
    )
    statement = (
        select(first_seen.c.word_id)
        .where(first_seen.c.first_at >= start)
        .where(first_seen.c.first_at < end)
    )
    return len(list(session.exec(statement)))


def history(session: Session, word_id: int) -> list[Review]:
    """Every grading of one card, oldest first."""
    statement = select(Review).where(Review.word_id == word_id).order_by(col(Review.reviewed_at))
    return list(session.exec(statement))


def due_words(session: Session, now: datetime, *, limit: int = 200) -> list[Word]:
    """Cards that have been graded before and have come up again.

    Ordered by how overdue they are. A card three days late is losing more than
    one due this hour.
    """
    statement = (
        select(Word)
        .where(col(Word.state) != "new")
        .where(col(Word.due) <= now)
        .order_by(col(Word.due))
        .limit(limit)
    )
    return list(session.exec(statement))


def new_words(session: Session, *, limit: int) -> list[Word]:
    """Cards never graded, oldest saved first.

    Oldest first so the words from the text you read last week are dealt with
    before the ones from this morning, instead of the backlog growing behind a
    stream of fresher arrivals.
    """
    if limit <= 0:
        return []
    statement = (
        select(Word)
        .where(col(Word.state) == "new")
        .order_by(col(Word.created_at), col(Word.id))
        .limit(limit)
    )
    return list(session.exec(statement))


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The local day containing ``now``, as an aware UTC half-open window.

    The day is the reader's, not UTC's: a card studied at 22:00 local belongs to
    that evening, and a UTC day would push it into tomorrow for anyone west of
    Greenwich.
    """
    local = now.astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(now.tzinfo), (start + timedelta(days=1)).astimezone(now.tzinfo)
