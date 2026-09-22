"""The numbers the reader is allowed to believe.

Section 10 of the MVP asks for four things, and section 2 says why: the deck is
the only honest indicator of progress. Not streaks, not texts read -- words that
survived being forgotten.

The ladder is the point of the screen. "How much of A1 do I have" is a different
question from "how many cards do I have", and only the first one means anything:
a hundred cards spread across five bands is not the same as a hundred A1 words.
So each rung is mastered-over-the-size-of-the-band, and the size comes from the
word list rather than from the deck.

Two of these numbers refuse to answer when they cannot. C1 and C2 have no rung,
because those bands are derived from a frequency scale with no end and there is
no denominator to divide by; printing one would be inventing it. Retention stays
blank until there are real recall attempts to measure, because the share of a
handful of learning steps is noise wearing a percentage sign.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from fsrs import Card, Rating
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from degrau.lexicon import LEVELS, Band, load_bands
from degrau.review import scheduler
from degrau.store import reviews, words
from degrau.store.models import CardState, Review, utcnow
from degrau.store.words import MASTERED_STABILITY_DAYS

#: How far back the history goes, and how far ahead the forecast does.
WINDOW_DAYS = 30

#: A card is only a real recall attempt once it has left the learning steps.
#: Counting those steps as retention would flatter the number badly: they are
#: minutes apart, and almost nobody forgets a word in ten minutes.
_REVIEW_STATE = 2


class Stat(BaseModel):
    """One headline number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    value: str
    hint: str = ""


class Rung(BaseModel):
    """One level of the ladder: how much of this band is actually yours."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str
    mastered: int
    in_deck: int
    total: int | None = None

    @property
    def measurable(self) -> bool:
        """Whether the band has a size to divide by."""
        return self.total is not None and self.total > 0

    @property
    def share(self) -> float:
        return (self.mastered / self.total) if self.measurable and self.total else 0.0

    @property
    def percent(self) -> str:
        if not self.measurable:
            return "-"
        return f"{self.share * 100:.1f}%"


class DayCount(BaseModel):
    """Reviews done on one day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    count: int

    @property
    def label(self) -> str:
        return self.day.strftime("%d/%m")


class RetentionPoint(BaseModel):
    """The deck's average chance of recall on a future day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    offset: int
    day: date
    retention: float
    due: int


class Dashboard(BaseModel):
    """Everything the panel shows."""

    model_config = ConfigDict(extra="forbid")

    stats: list[Stat] = Field(default_factory=list)
    ladder: list[Rung] = Field(default_factory=list)
    history: list[DayCount] = Field(default_factory=list)
    retention: list[RetentionPoint] = Field(default_factory=list)
    deck_size: int = 0
    measured_retention: float | None = None
    recall_attempts: int = 0

    @property
    def empty(self) -> bool:
        return self.deck_size == 0

    @property
    def busiest_day(self) -> int:
        return max((entry.count for entry in self.history), default=0)

    @property
    def reviewed_in_window(self) -> int:
        return sum(entry.count for entry in self.history)


def band_sizes() -> dict[str, int]:
    """How many words the list puts in each band.

    Derived from the ceilings in ``bands.toml``, so recalibrating the bands
    moves the ladder with them instead of leaving it measuring the old shape.
    """
    sizes: dict[str, int] = {}
    previous = 0
    for band, ceiling in load_bands().ngsl_ceilings:
        sizes[band.value] = ceiling - previous
        previous = ceiling
    return sizes


def _card_was_a_recall(review: Review) -> bool:
    """Whether this grading was a real attempt rather than a learning step."""
    if not review.card_before_json:
        return False
    try:
        before = Card.from_dict(json.loads(review.card_before_json))
    except (ValueError, KeyError, TypeError):
        return False
    return int(before.state) == _REVIEW_STATE


def measured_retention(rows: list[Review]) -> tuple[float | None, int]:
    """The share of real recall attempts that were remembered, and how many.

    ``Again`` is the only rating that means forgotten; Hard still means the word
    came back. Returns ``None`` when there is nothing to measure, because a
    percentage computed from two reviews is a number that will mislead whoever
    reads it, and this panel exists to be believed.
    """
    attempts = [row for row in rows if _card_was_a_recall(row)]
    if not attempts:
        return None, 0
    remembered = sum(1 for row in attempts if row.rating != int(Rating.Again))
    return remembered / len(attempts), len(attempts)


def _history(rows: list[Review], *, today: date) -> list[DayCount]:
    counts: dict[date, int] = {}
    for row in rows:
        counts[row.reviewed_at.astimezone().date()] = (
            counts.get(row.reviewed_at.astimezone().date(), 0) + 1
        )
    days = [today - timedelta(days=offset) for offset in range(WINDOW_DAYS - 1, -1, -1)]
    return [DayCount(day=day, count=counts.get(day, 0)) for day in days]


def _retention_curve(
    cards: list[Card], dues: list[datetime], *, now: datetime
) -> list[RetentionPoint]:
    """Average chance of recall, and cards falling due, for each of the next days.

    Only cards that have been graded are in here. A card never reviewed has no
    memory to decay, and averaging it in as either 0 or 100 would move the line
    without meaning anything.
    """
    engine = scheduler()
    points: list[RetentionPoint] = []
    for offset in range(WINDOW_DAYS + 1):
        moment = now + timedelta(days=offset)
        day = moment.astimezone().date()
        if cards:
            total = sum(engine.get_card_retrievability(card, moment) for card in cards)
            average = total / len(cards)
        else:
            average = 0.0
        due = sum(1 for when in dues if when.astimezone().date() == day)
        points.append(RetentionPoint(offset=offset, day=day, retention=average, due=due))
    return points


def build(session: Session, *, now: datetime | None = None) -> Dashboard:
    """Assemble the panel."""
    moment = now or utcnow()
    today = moment.astimezone().date()
    window_start = (moment - timedelta(days=WINDOW_DAYS)).astimezone(UTC)

    deck = words.all_cards(session)
    in_deck_by_band = words.count_by_band(session)
    mastered_by_band = words.mastered_by_band(session)
    sizes = band_sizes()

    rows = reviews.between(session, window_start, moment + timedelta(days=1))
    day_start, day_end = reviews.day_bounds(moment)
    today_count = reviews.count_between(session, day_start, day_end)

    retention, attempts = measured_retention(rows)

    graded = [
        Card.from_dict(json.loads(word.fsrs_json))
        for word in deck
        if word.state != CardState.NEW.value and word.fsrs_json
    ]
    dues = [word.due for word in deck if word.state != CardState.NEW.value]

    mastered_total = sum(mastered_by_band.values())
    learning = words.count_in_states(
        session, [CardState.LEARNING.value, CardState.RELEARNING.value]
    )

    stats = [
        Stat(label="In the deck", value=str(len(deck)), hint="words saved so far"),
        Stat(
            label="Learned",
            value=str(mastered_total),
            hint=f"still there in {MASTERED_STABILITY_DAYS:.0f} days",
        ),
        Stat(label="In flight", value=str(learning), hint="not settled yet"),
        Stat(label="Reviewed today", value=str(today_count), hint="gradings since midnight"),
        Stat(
            label="Retention",
            value="-" if retention is None else f"{retention * 100:.0f}%",
            hint=(
                "no recall attempts yet"
                if retention is None
                else f"of {attempts} attempts in {WINDOW_DAYS} days"
            ),
        ),
    ]

    ladder = [
        Rung(
            level=band.value,
            mastered=mastered_by_band.get(band.value, 0),
            in_deck=in_deck_by_band.get(band.value, 0),
            total=sizes.get(band.value),
        )
        for band in LEVELS
    ]

    return Dashboard(
        stats=stats,
        ladder=ladder,
        history=_history(rows, today=today),
        retention=_retention_curve(graded, dues, now=moment),
        deck_size=len(deck),
        measured_retention=retention,
        recall_attempts=attempts,
    )


def ladder_for(level: Band, dashboard: Dashboard) -> Rung | None:
    return next((rung for rung in dashboard.ladder if rung.level == level.value), None)
