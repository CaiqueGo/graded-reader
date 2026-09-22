"""Reading and writing the deck.

The queries here are what the profile is built from, which is why they live
behind names that say what they are *for* rather than what they select.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from degrau.store.models import CardState, Word, utcnow

#: Stability, in days, at which a word counts as learned rather than in flight.
#: The dashboard's "mastered" number and the prompt's exception list are the same
#: claim -- a word you will still have in three weeks -- so they use one constant.
MASTERED_STABILITY_DAYS = 21.0


def deck_lemmas(session: Session) -> set[str]:
    """Every lemma already in the deck, at any stage."""
    return set(session.exec(select(Word.lemma)))


def mastered_in_bands(session: Session, bands: list[str]) -> list[str]:
    """Learned words that sit in any of ``bands``, most stable first.

    An empty ``bands`` returns nothing rather than everything. The caller that
    passes an empty list is asking for "words above C2", and the honest answer to
    that is no words, not the whole deck.
    """
    if not bands:
        return []
    statement = (
        select(Word.lemma)
        .where(col(Word.band).in_(bands))
        .where(col(Word.stability) >= MASTERED_STABILITY_DAYS)
        .order_by(col(Word.stability).desc())
    )
    return list(session.exec(statement))


def due_within(session: Session, days: int, *, now: datetime | None = None) -> list[str]:
    """Words still being learned that come up for review within ``days``.

    Only ``learning`` and ``relearning``: a word in the ``review`` state is
    holding on its own, and spending one of the text's few slots on it buys less
    than spending it on one that is about to slip.
    """
    moment = now or utcnow()
    statement = (
        select(Word.lemma)
        .where(col(Word.state).in_([CardState.LEARNING.value, CardState.RELEARNING.value]))
        .where(col(Word.due) <= moment + timedelta(days=days))
        .order_by(col(Word.due))
    )
    return list(session.exec(statement))


def count(session: Session) -> int:
    return len(list(session.exec(select(Word.id))))
