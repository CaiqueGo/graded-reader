"""Putting a word into the deck, and keeping the card in step with the row.

A word enters the deck with a real FSRS card from the first moment. The
alternative -- a placeholder until the review screen exists -- would mean every
word saved before then carries no schedule, and the first run of the reviewer
has to invent one. Creating the card here costs one line and removes that.

``Word.due``, ``stability`` and ``state`` are copies of what lives inside
``fsrs_json``. They exist so the queue and the dashboard are plain SQL. That
makes them a lie waiting to happen, so exactly one function writes them:
``apply_card``. Nothing else in the project assigns those three fields.
"""

from __future__ import annotations

import json
from datetime import datetime

from fsrs import Card
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from degrau.lexicon import Band, band_for
from degrau.store import words
from degrau.store.models import CardState, Word


class DeckError(Exception):
    """Expected failure while changing the deck, with a message for the user."""


class SavedWord(BaseModel):
    """A card, as anything outside the store sees it.

    Deliberately not the ORM row. A row read inside a session stops working the
    moment that session closes -- every attribute access then raises -- so
    handing one to a caller is handing them something that breaks depending on
    where they use it. This is a plain value and works anywhere.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    lemma: str
    display: str
    band: str = ""
    pt: str = ""
    example_en: str = ""
    example_pt: str = ""
    due: datetime
    stability: float | None = None
    state: str


def _as_value(word: Word) -> SavedWord:
    return SavedWord(
        id=word.id,
        lemma=word.lemma,
        display=word.display,
        band=word.band or "",
        pt=word.pt or "",
        example_en=word.example_en or "",
        example_pt=word.example_pt or "",
        due=word.due,
        stability=word.stability,
        state=word.state,
    )


def card_state(card: Card) -> CardState:
    """The app's state for a card, which is the library's plus 'never graded'."""
    if card.last_review is None:
        return CardState.NEW
    return {
        1: CardState.LEARNING,
        2: CardState.REVIEW,
        3: CardState.RELEARNING,
    }.get(int(card.state), CardState.LEARNING)


def apply_card(word: Word, card: Card) -> Word:
    """Write the card into the row, both serialised and denormalised."""
    word.fsrs_json = json.dumps(card.to_dict())
    word.due = card.due
    word.stability = card.stability
    word.state = card_state(card).value
    return word


def load_card(word: Word) -> Card:
    """The FSRS card stored on a row."""
    try:
        return Card.from_dict(json.loads(word.fsrs_json))
    except (ValueError, KeyError, TypeError) as error:
        raise DeckError(f"card data for {word.lemma!r} is unreadable: {error}") from error


def save_word(
    session: Session,
    lemma: str,
    *,
    display: str = "",
    pt: str | None = None,
    example_en: str | None = None,
    example_pt: str | None = None,
    band: Band | str | None = None,
    first_text_id: int | None = None,
) -> tuple[SavedWord, bool]:
    """Add a word to the deck, or return the one already there.

    Returns the row and whether it was created. Saving the same word twice is
    not an error and must not reset its schedule: clicking a highlighted word
    again is a thing a reader does by accident, and losing three weeks of
    progress to a stray click is not a mistake worth allowing.
    """
    normalised = lemma.strip().casefold()
    if not normalised:
        raise DeckError("a flashcard needs a lemma")

    existing = words.by_lemma(session, normalised)
    if existing is not None:
        filled = _fill_gaps(existing, pt=pt, example_en=example_en, example_pt=example_pt)
        session.flush()
        return _as_value(filled), False

    resolved = band if band is not None else band_for(normalised)[0]
    card = Card()
    word = Word(
        lemma=normalised,
        display=display or lemma,
        pt=pt,
        example_en=example_en,
        example_pt=example_pt,
        band=resolved.value if isinstance(resolved, Band) else str(resolved),
        first_text_id=first_text_id,
        fsrs_json="{}",
        due=card.due,
    )
    apply_card(word, card)
    session.add(word)
    session.flush()
    session.refresh(word)
    return _as_value(word), True


def update_word(
    session: Session,
    word_id: int,
    *,
    lemma: str | None = None,
    display: str | None = None,
    pt: str | None = None,
    example_en: str | None = None,
    example_pt: str | None = None,
) -> SavedWord:
    """Edit a card. Unlike saving, this overwrites.

    ``save_word`` only fills blanks, because a second text should not clobber the
    sentence you have been revising against. This is the other case: the reader
    is looking at the card and saying it is wrong, so what they type wins.

    The lemma is editable on purpose. Section 13 of the MVP names lemmatisation
    as a known weak spot -- irregular forms and phrasal verbs produce odd cards --
    and being able to correct the base form is the mitigation it asks for.
    Editing it never touches the schedule: the card is the same card.
    """
    word = words.by_id(session, word_id)
    if word is None:
        raise DeckError(f"no card with id {word_id}")

    if lemma is not None:
        normalised = lemma.strip().casefold()
        if not normalised:
            raise DeckError("a flashcard needs a lemma")
        if normalised != word.lemma:
            clash = words.by_lemma(session, normalised)
            if clash is not None:
                raise DeckError(
                    f"{normalised!r} is already a card in your deck; "
                    "merging two cards is not something this can do for you"
                )
            word.lemma = normalised
            word.band = band_for(normalised)[0].value

    if display is not None:
        word.display = display.strip() or word.lemma
    if pt is not None:
        word.pt = pt.strip() or None
    if example_en is not None:
        word.example_en = example_en.strip() or None
    if example_pt is not None:
        word.example_pt = example_pt.strip() or None

    session.add(word)
    session.flush()
    session.refresh(word)
    return _as_value(word)


def _fill_gaps(
    word: Word,
    *,
    pt: str | None,
    example_en: str | None,
    example_pt: str | None,
) -> Word:
    """Fill in a translation or example the card did not have before.

    Only fills blanks. The second text that uses a word should not overwrite the
    sentence you have already been revising against.
    """
    if pt and not word.pt:
        word.pt = pt
    if example_en and not word.example_en:
        word.example_en = example_en
    if example_pt and not word.example_pt:
        word.example_pt = example_pt
    return word
