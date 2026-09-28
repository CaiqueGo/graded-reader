"""Course days: which text belongs to which day, and whether it is ready."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import or_, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col, select

from graded_reader.store.models import CourseDay, CourseDayStatus, utcnow


def claim(
    session: Session,
    day: date,
    *,
    kind: str,
    situation: str = "",
    topic: str = "",
    now: datetime | None = None,
    stale_before: datetime,
) -> bool:
    """Take the right to prepare ``day``, and say whether it was taken.

    One statement, so there is no gap for a second caller to slip through. It
    inserts the day; if the day already exists, it takes it over only when the
    earlier attempt failed, or has sat in ``preparing`` since before
    ``stale_before`` -- the server was stopped halfway and nobody is coming back
    for it. A day that is ready, or that someone is preparing right now, is left
    alone, and the statement changes no row.

    The affected row count is the answer. Reading the row first and deciding in
    Python is exactly the check-then-act this is here to avoid.
    """
    moment = now or utcnow()
    values = {
        "day": day,
        "kind": kind,
        "situation": situation,
        "topic": topic,
        "status": CourseDayStatus.PREPARING.value,
        "note": "",
        "claimed_at": moment,
        "prepared_at": None,
        "text_id": None,
    }
    statement = (
        insert(CourseDay)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["day"],
            set_={key: value for key, value in values.items() if key != "day"},
            where=or_(
                col(CourseDay.status) == CourseDayStatus.FAILED.value,
                (col(CourseDay.status) == CourseDayStatus.PREPARING.value)
                & (col(CourseDay.claimed_at) < stale_before),
            ),
        )
    )
    result = session.exec(statement)
    return result.rowcount == 1


def mark_ready(
    session: Session,
    day: date,
    *,
    text_id: int,
    kind: str,
    note: str = "",
    now: datetime | None = None,
) -> bool:
    """Record the prepared text. Only a day still being prepared is changed."""
    statement = (
        update(CourseDay)
        .where(col(CourseDay.day) == day)
        .where(col(CourseDay.status) == CourseDayStatus.PREPARING.value)
        .values(
            status=CourseDayStatus.READY.value,
            text_id=text_id,
            kind=kind,
            note=note,
            prepared_at=now or utcnow(),
        )
    )
    result = session.exec(statement)
    return result.rowcount == 1


def mark_failed(session: Session, day: date, reason: str) -> bool:
    """Record why a day could not be prepared, so the next attempt may retry it."""
    statement = (
        update(CourseDay)
        .where(col(CourseDay.day) == day)
        .where(col(CourseDay.status) == CourseDayStatus.PREPARING.value)
        .values(status=CourseDayStatus.FAILED.value, note=reason)
    )
    result = session.exec(statement)
    return result.rowcount == 1


def finish(session: Session, day: date, *, now: datetime | None = None) -> bool:
    """Mark the day's session as done -- once, and only a day whose text is ready."""
    statement = (
        update(CourseDay)
        .where(col(CourseDay.day) == day)
        .where(col(CourseDay.status) == CourseDayStatus.READY.value)
        .where(col(CourseDay.finished_at).is_(None))
        .values(finished_at=now or utcnow())
    )
    result = session.exec(statement)
    return result.rowcount == 1


def by_day(session: Session, day: date) -> CourseDay | None:
    return session.exec(select(CourseDay).where(col(CourseDay.day) == day)).first()


def before(session: Session, day: date, limit: int = 30) -> list[CourseDay]:
    """The days before ``day``, most recent first."""
    statement = (
        select(CourseDay)
        .where(col(CourseDay.day) < day)
        .order_by(col(CourseDay.day).desc())
        .limit(limit)
    )
    return list(session.exec(statement))
