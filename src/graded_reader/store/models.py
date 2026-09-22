"""The database tables.

Two shapes appear in this project and they are not the same thing. The pydantic
models in ``graded_reader.adapters.base`` describe the *contract* with whatever produced
a text. These describe what is *stored*. Keeping them apart is what lets the
import contract change without a migration, and the storage change without
breaking every file already sitting in the inbox.

Dates are stored as ISO strings, which is what SQLite does with them anyway, and
always in UTC -- see ``utcnow``. The one place that matters is ``Word.due``: the
review queue is a range query over it, and lexicographic order on ISO-8601 in a
single offset is chronological order.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Now, in UTC, with the offset attached.

    Every stored instant is timezone aware and in UTC. Two reasons, and both
    would have bitten later: FSRS schedules in UTC, so a naive local ``due``
    would drift by an hour twice a year and silently reorder the review queue;
    and the v2 phone client cannot be assumed to sit in the same timezone as
    whatever wrote the row. Local time is a display concern, applied on the way
    out.
    """
    return datetime.now(UTC)


class SourceKind(StrEnum):
    """Where the original text came from."""

    URL = "url"
    FILE = "file"
    PASTE = "paste"


class CardKind(StrEnum):
    """What a card is asking you to recall.

    A word card is the older shape: the word on the front, its translation and
    an example on the back. A sentence card is how vocabulary is usually mined
    for spaced repetition -- the sentence is the card, with the word being
    learned marked inside it, and the back is what the sentence means.

    They live in one table because the only thing the scheduler cares about is
    the FSRS card, and both have one. What differs is what gets shown.
    """

    WORD = "word"
    SENTENCE = "sentence"


class CardState(StrEnum):
    """Card state, denormalised out of ``Word.fsrs_json`` for querying.

    ``NEW`` is ours, not the library's. fsrs 6 has only Learning, Review and
    Relearning: a freshly created card is already Learning, due immediately. But
    "created and never graded" is a distinct thing the app has to count, because
    the daily new-card limit is a limit on exactly those, and once a card has
    been graded once it never returns to it. It is derived from the card's
    ``last_review`` being unset, never from the library's own state.
    """

    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


class Text(SQLModel, table=True):
    """One adapted text, with the original it came from and how it measured.

    ``content_hash`` is what makes importing idempotent. It is taken over the
    adapted text alone: re-running the same adaptation prompt and getting the
    same English back is the case worth collapsing, and a changed title or a
    re-recorded source URL should not be enough to create a duplicate.
    """

    __tablename__ = "text"

    id: int | None = Field(default=None, primary_key=True)
    title: str = ""
    level: str = Field(index=True)
    source_kind: str = SourceKind.PASTE.value
    source_value: str = ""
    original_text: str = ""
    adapted_text: str
    glossary_json: str = "[]"
    questions_json: str = "[]"
    prompt_used: str = ""
    coverage_pct: float | None = None
    out_of_level: str = "[]"
    content_hash: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Word(SQLModel, table=True):
    """One flashcard, of either kind.

    The whole FSRS ``Card`` is kept serialised in ``fsrs_json`` so the scheduler
    stays the library's business and this project never reimplements it.
    ``due``, ``stability`` and ``state`` are copies, written on every review, so
    that the queue and the dashboard are plain SQL instead of deserialising
    every card in the deck to find the ones due today.
    """

    __tablename__ = "word"

    __table_args__ = (
        # Uniqueness applies to word cards only. Clicking a word you already
        # saved must not make a second card of it -- but mining sentences means
        # three cards can legitimately share a target word, and a sentence may
        # have no single target at all. A partial index is what says both.
        Index(
            "ix_word_unique_lemma_per_word_card",
            "lemma",
            unique=True,
            sqlite_where=text("kind = 'word'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(default=CardKind.WORD.value, index=True)
    lemma: str = Field(default="", index=True)
    display: str
    pt: str | None = None
    example_en: str | None = None
    example_pt: str | None = None
    band: str | None = Field(default=None, index=True)
    first_text_id: int | None = Field(default=None, foreign_key="text.id")
    created_at: datetime = Field(default_factory=utcnow)

    #: The sentence being learned, and what it means. Both empty on a word card.
    #: The word highlighted inside the sentence is ``lemma``, the same field a
    #: word card uses -- on either kind it answers "what is this card about",
    #: and a second column saying the same thing would only be somewhere for the
    #: two to disagree. A sentence saved without a target leaves it empty.
    sentence: str | None = None
    sentence_pt: str | None = None

    fsrs_json: str
    due: datetime = Field(index=True)
    stability: float | None = None
    state: str = Field(default=CardState.NEW.value, index=True)


class Review(SQLModel, table=True):
    """One grading of one card.

    Every number on the dashboard is derived from this table, so nothing prunes
    it. The single exception is undo, which removes the most recent row: a
    misclick is not history, and leaving it in would mean the retention curve is
    fitted to an answer the reader never gave.

    ``card_before_json`` is what makes that undo possible, and it is an addition
    to the schema in section 9 of the MVP. The reason is that fsrs cannot
    reconstruct it: a ReviewLog carries only the rating and the time, and the
    scheduler fuzzes its intervals, so replaying the same ratings lands on a
    different date than the one the reader actually saw. Restoring a snapshot is
    exact; replaying is not.
    """

    __tablename__ = "review"

    id: int | None = Field(default=None, primary_key=True)
    word_id: int = Field(foreign_key="word.id", index=True)
    rating: int
    reviewed_at: datetime = Field(default_factory=utcnow, index=True)
    log_json: str = ""
    card_before_json: str = ""


class Setting(SQLModel, table=True):
    """Key-value configuration that belongs to the user, not to the code.

    Current level, daily new-card limit. Things that change by using the app,
    as opposed to the thresholds in ``data/``, which change by calibrating it.
    """

    __tablename__ = "setting"

    key: str = Field(primary_key=True)
    value: str
