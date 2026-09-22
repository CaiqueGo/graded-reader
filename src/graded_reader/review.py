"""The review session: what to show next, what a grade does, and how to take it back.

The scheduling itself belongs to fsrs and is not reimplemented here. What this
module owns is everything around it: which cards are offered today, how many new
ones are allowed in, what the four buttons will cost, and how to undo the last
answer.

One thing to know about the intervals shown on the buttons. The scheduler fuzzes
its intervals on purpose -- eight words saved from the same text would otherwise
come due on the same day for the rest of their lives, and the reader would meet
them as a wall. So the numbers on the buttons are computed without fuzz and
shown as approximate, while the grade that actually lands has the fuzz applied.
Telling the reader "about 10 days" and giving them 11 is honest; telling them
"10 days" and giving them 11 is not.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fsrs import Card, Rating, Scheduler
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from graded_reader.deck import DeckError, apply_card, card_state, load_card
from graded_reader.lexicon import tokenize
from graded_reader.store import reviews, words
from graded_reader.store import settings as settings_store
from graded_reader.store.models import Review, Word, utcnow

#: What the reader sees where the word was. Long enough to look like a gap.
BLANK = "____"

#: Ratings, in the order the keyboard expects them.
RATINGS: tuple[Rating, ...] = (Rating.Again, Rating.Hard, Rating.Good, Rating.Easy)

RATING_LABELS = {
    Rating.Again: "Again",
    Rating.Hard: "Hard",
    Rating.Good: "Good",
    Rating.Easy: "Easy",
}


class ReviewError(Exception):
    """Expected failure during a review, with a message for the user."""


class Option(BaseModel):
    """One of the four buttons."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rating: int
    label: str
    key: str
    interval: str


class QueueCounts(BaseModel):
    """What is left today."""

    model_config = ConfigDict(extra="forbid")

    due: int = 0
    new: int = 0
    reviewed_today: int = 0
    introduced_today: int = 0
    daily_new_limit: int = 0

    @property
    def remaining(self) -> int:
        return self.due + self.new

    @property
    def new_left_today(self) -> int:
        return max(0, self.daily_new_limit - self.introduced_today)


class ReviewCard(BaseModel):
    """One card, as the review screen shows it."""

    model_config = ConfigDict(extra="forbid")

    word_id: int
    lemma: str
    display: str
    band: str = ""
    pt: str = ""
    example_en: str = ""
    example_pt: str = ""
    cloze: str = ""
    is_new: bool = False
    state: str = ""
    options: list[Option] = Field(default_factory=list)

    @property
    def has_example(self) -> bool:
        return bool(self.example_en.strip())


class GradeResult(BaseModel):
    """What one grading did."""

    model_config = ConfigDict(extra="forbid")

    lemma: str
    rating: int
    interval: str
    state: str


def scheduler(*, fuzz: bool = True) -> Scheduler:
    """The fsrs scheduler. Its defaults are the point of using the library."""
    return Scheduler(enable_fuzzing=fuzz)


def daily_new_limit(session: Session) -> int:
    """How many unseen cards may be introduced in a day.

    Section 13 of the MVP names this as the way the project fails: twelve words
    per text becomes three hundred due cards in a month, and then abandonment.
    """
    raw = settings_store.get(session, settings_store.KEY_DAILY_NEW)
    try:
        return max(0, int(raw))
    except ValueError:
        return 10


def humanize(delta: timedelta) -> str:
    """A span as the button shows it: 10m, 2d, 3mo, 1.4y."""
    seconds = max(0.0, delta.total_seconds())
    if seconds < 60:
        return "<1m"
    minutes = seconds / 60
    if minutes < 60:
        return f"{round(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{round(hours)}h"
    days = hours / 24
    if days < 30:
        return f"{round(days)}d"
    months = days / 30.4
    if months < 12:
        return f"{round(months)}mo"
    return f"{days / 365.25:.1f}y"


#: Suffixes checked when the lemmatiser and the target disagree. All two letters
#: or longer on purpose -- a bare "s" would let a three-letter target swallow an
#: unrelated word, and the lemmatiser already handles plain plurals.
_SUFFIXES = frozenset({"ing", "ed", "es", "er", "est", "ings", "ers"})


def _is_inflection(surface: str, lemma: str) -> bool:
    """Whether ``surface`` looks like an inflected form of ``lemma``.

    A fallback, not the main path. spaCy resolves "is running" to ``run``, but a
    gerund used as a noun -- "running is what he did" -- lemmatises to itself,
    and that form would otherwise survive into the gap sentence and hand over
    the answer.

    The bias is deliberately towards hiding too much. A word covered that did
    not need to be makes the sentence slightly harder; a word left showing makes
    the card pointless.
    """
    if len(lemma) < 3:
        return False
    if lemma.endswith("y") and surface.startswith(lemma[:-1] + "i"):
        return surface[len(lemma) + 1 :] in _SUFFIXES
    if not surface.startswith(lemma):
        return False
    rest = surface[len(lemma) :]
    if rest in _SUFFIXES:
        return True
    # A doubled final consonant: run -> running, sit -> sitting.
    return rest[:1] == lemma[-1:] and rest[1:] in _SUFFIXES


def blank_out(example: str, lemma: str) -> str:
    """The example with the target word replaced by a gap.

    Every inflected form goes, not just the exact spelling: an example that
    hides ``run`` but leaves ``running`` in the next clause has given the answer
    away. The lemma comparison is the same one the reading screen highlights
    with, so the two agree about what counts as the word.
    """
    if not example.strip():
        return ""
    target = lemma.strip().casefold()
    pieces = []
    for token in tokenize(example):
        surface = token.text.casefold()
        hide = token.is_word and (token.lemma == target or _is_inflection(surface, target))
        pieces.append(BLANK if hide else token.text)
        pieces.append(token.whitespace)
    return "".join(pieces)


def options_for(word: Word, *, now: datetime | None = None) -> list[Option]:
    """What each of the four buttons would cost, without applying anything."""
    moment = now or utcnow()
    card = load_card(word)
    plain = scheduler(fuzz=False)
    built: list[Option] = []
    for index, rating in enumerate(RATINGS, start=1):
        after, _log = plain.review_card(card, rating, moment)
        built.append(
            Option(
                rating=int(rating),
                label=RATING_LABELS[rating],
                key=str(index),
                interval=humanize(after.due - moment),
            )
        )
    return built


def counts(session: Session, *, now: datetime | None = None) -> QueueCounts:
    """What is left to do today."""
    moment = now or utcnow()
    start, end = reviews.day_bounds(moment)
    limit = daily_new_limit(session)
    introduced = reviews.introduced_between(session, start, end)
    return QueueCounts(
        due=len(reviews.due_words(session, moment)),
        new=len(reviews.new_words(session, limit=max(0, limit - introduced))),
        reviewed_today=reviews.count_between(session, start, end),
        introduced_today=introduced,
        daily_new_limit=limit,
    )


def next_word(session: Session, *, now: datetime | None = None) -> Word | None:
    """The next card to show, or None when today is done.

    Cards already in flight come before new ones. A word you have met and are
    about to forget is worth more than a word you have never met, and letting
    new cards jump the queue is how a backlog becomes permanent.
    """
    moment = now or utcnow()
    due = reviews.due_words(session, moment, limit=1)
    if due:
        return due[0]

    start, end = reviews.day_bounds(moment)
    allowance = daily_new_limit(session) - reviews.introduced_between(session, start, end)
    fresh = reviews.new_words(session, limit=max(0, min(1, allowance)))
    return fresh[0] if fresh else None


def to_card(word: Word, *, now: datetime | None = None) -> ReviewCard:
    """One word as the screen shows it."""
    if word.id is None:
        raise ReviewError("card has not been saved yet")
    return ReviewCard(
        word_id=word.id,
        lemma=word.lemma,
        display=word.display,
        band=word.band or "",
        pt=word.pt or "",
        example_en=word.example_en or "",
        example_pt=word.example_pt or "",
        cloze=blank_out(word.example_en or "", word.lemma),
        is_new=word.state == "new",
        state=word.state,
        options=options_for(word, now=now),
    )


def next_card(session: Session, *, now: datetime | None = None) -> ReviewCard | None:
    """The next card to show, ready for the template."""
    word = next_word(session, now=now)
    return None if word is None else to_card(word, now=now)


def card_by_id(session: Session, word_id: int, *, now: datetime | None = None) -> ReviewCard | None:
    """One specific card, for the edit form to fill itself from."""
    word = words.by_id(session, word_id)
    return None if word is None else to_card(word, now=now)


def grade(
    session: Session,
    word_id: int,
    rating: int,
    *,
    now: datetime | None = None,
) -> GradeResult:
    """Apply a rating to a card and write down that it happened.

    The card as it stood *before* the grade is stored on the review row. That is
    what undo restores, and it is stored rather than recomputed because the
    scheduler fuzzes: replaying the same ratings does not land on the same date.
    """
    moment = now or utcnow()
    try:
        value = Rating(rating)
    except ValueError:
        raise ReviewError(f"{rating!r} is not a rating; expected 1, 2, 3 or 4") from None

    word = words.by_id(session, word_id)
    if word is None:
        raise ReviewError(f"no card with id {word_id}")

    before = load_card(word)
    after, log = scheduler().review_card(before, value, moment)

    apply_card(word, after)
    session.add(word)
    reviews.add(
        session,
        Review(
            word_id=word_id,
            rating=int(value),
            reviewed_at=moment,
            log_json=json.dumps(log.to_dict()),
            card_before_json=json.dumps(before.to_dict()),
        ),
    )
    session.flush()

    return GradeResult(
        lemma=word.lemma,
        rating=int(value),
        interval=humanize(after.due - moment),
        state=card_state(after).value,
    )


def undo_last(session: Session) -> str | None:
    """Take back the most recent grading. Returns the word, or None if there was none.

    The review row goes with it. An answer the reader retracted is not history:
    leaving it in would put a misclick into the retention curve and into the
    count of what was studied today, and both of those are meant to be numbers
    you can trust.
    """
    latest = reviews.last(session)
    if latest is None:
        return None

    word = words.by_id(session, latest.word_id)
    if word is None:
        reviews.drop(session, latest)
        return None

    if not latest.card_before_json:
        raise DeckError(
            f"the review of {word.lemma!r} has no snapshot to restore; it predates undo support"
        )

    restored = Card.from_dict(json.loads(latest.card_before_json))
    apply_card(word, restored)
    session.add(word)
    reviews.drop(session, latest)
    session.flush()
    return word.lemma
