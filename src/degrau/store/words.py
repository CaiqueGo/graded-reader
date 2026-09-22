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


def by_lemma(session: Session, lemma: str) -> Word | None:
    """The card for a lemma, if it is in the deck."""
    return session.exec(select(Word).where(Word.lemma == lemma)).first()


def lemmas_in(session: Session, lemmas: set[str]) -> set[str]:
    """Which of ``lemmas`` are already in the deck.

    One query instead of one per word: the reading screen asks this about every
    distinct word of a text at once, and doing it word by word is how a page
    that renders in 20ms starts taking a second.
    """
    if not lemmas:
        return set()
    return set(session.exec(select(Word.lemma).where(col(Word.lemma).in_(lemmas))))


def by_id(session: Session, word_id: int) -> Word | None:
    """One card by its id."""
    return session.get(Word, word_id)


def all_cards(session: Session) -> list[Word]:
    """Every card in the deck. The dashboard reads the whole thing.

    A personal deck is hundreds of rows, not millions, and the retention curve
    needs each card's own schedule -- there is no SQL for "average probability
    of recall in eleven days".
    """
    return list(session.exec(select(Word).order_by(col(Word.id))))


def count_by_band(session: Session) -> dict[str, int]:
    """How many cards sit in each band."""
    counts: dict[str, int] = {}
    for band in session.exec(select(Word.band)):
        key = band or "NA"
        counts[key] = counts.get(key, 0) + 1
    return counts


def mastered_by_band(session: Session) -> dict[str, int]:
    """How many cards in each band have survived long enough to count."""
    statement = select(Word.band).where(col(Word.stability) >= MASTERED_STABILITY_DAYS)
    counts: dict[str, int] = {}
    for band in session.exec(statement):
        key = band or "NA"
        counts[key] = counts.get(key, 0) + 1
    return counts


def count_in_states(session: Session, states: list[str]) -> int:
    return len(list(session.exec(select(Word.id).where(col(Word.state).in_(states)))))


def for_export(
    session: Session, *, band: str | None = None, learned_only: bool = False
) -> list[Word]:
    """The cards to export, oldest first so the file is stable between runs."""
    statement = select(Word)
    if band:
        statement = statement.where(col(Word.band) == band)
    if learned_only:
        statement = statement.where(col(Word.stability) >= MASTERED_STABILITY_DAYS)
    return list(session.exec(statement.order_by(col(Word.id))))
