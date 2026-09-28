"""Answers to the exercises of a course day."""

from __future__ import annotations

from sqlmodel import Session, col, select

from graded_reader.store.models import ExerciseAttempt


def add(session: Session, attempt: ExerciseAttempt) -> ExerciseAttempt:
    session.add(attempt)
    session.flush()
    session.refresh(attempt)
    return attempt


def latest_for_day(session: Session, day_id: int) -> dict[tuple[str, str], ExerciseAttempt]:
    """The most recent answer to each exercise of a day, keyed by (kind, target)."""
    statement = (
        select(ExerciseAttempt)
        .where(col(ExerciseAttempt.day_id) == day_id)
        .order_by(col(ExerciseAttempt.id))
    )
    latest: dict[tuple[str, str], ExerciseAttempt] = {}
    for row in session.exec(statement):
        latest[(row.kind, row.target)] = row
    return latest
