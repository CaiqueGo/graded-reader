"""The course: what today holds, and what the reader did with it.

Until the course, the app was a tool that waited to be fed -- every day the
reader had to find an article, judge it, choose a level and decide what to
study. This module is the other side of that: it decides. It plans the day's
text, builds the exercises from it, checks the answers, and says how the day
went. Preparing the text itself is slow and calls outside the process, so it
lives in ``preparation``; everything here is quick and takes a session.

Every check in here is deterministic. Whether an answer used the target word is
decided by the lemmatiser, the same one that measures coverage -- not by asking
a model, which would sometimes say yes to be encouraging.
"""

from __future__ import annotations

import json
import tomllib
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from graded_reader import config, review
from graded_reader.lexicon import LEVELS, Band, LexiconError, parse_level, tokenize
from graded_reader.reading import lemma_of
from graded_reader.review import QueueCounts, blank_out, is_inflection
from graded_reader.sources import SourceError, check_url
from graded_reader.store import attempts, days, feeds, reviews, texts, words
from graded_reader.store import settings as settings_store
from graded_reader.store.models import (
    CourseDay,
    CourseDayKind,
    CourseDayStatus,
    ExerciseAttempt,
    Feed,
    utcnow,
)

#: How fast the browser reads the text aloud, by level. Slower at the bottom,
#: where every word is still being decoded; natural speed from B2, which is what
#: an interviewer will speak at.
SPEECH_RATE = {"A1": 0.8, "A2": 0.85, "B1": 0.92, "B2": 1.0, "C1": 1.0, "C2": 1.0}

MIN_MINUTES = 5
MAX_MINUTES = 240

#: Exercises per day. A handful done properly beats a page of them skimmed.
MAX_CLOZE = 5
MAX_SENTENCES = 2

#: Below this a "sentence" is a word with a full stop, and proves nothing.
MIN_SENTENCE_WORDS = 4

MAX_TOPIC_LENGTH = 40


class CourseError(Exception):
    """Expected failure in the course, with a message for the user."""


class ExerciseKind(StrEnum):
    #: The example sentence with the word taken out; type it back in.
    CLOZE = "cloze"
    #: Write a sentence of your own that uses the word.
    SENTENCE = "sentence"


class Plan(BaseModel):
    """What to prepare for a day, decided before anything slow happens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    kind: CourseDayKind
    level: Band
    situation: str
    topic: str = ""


class Exercise(BaseModel):
    """One exercise, and the latest answer to it if there is one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ExerciseKind
    target: str
    word: str
    prompt: str
    hint: str = ""
    reference: str = ""
    answer: str = ""
    answered: bool = False
    correct: bool = False
    feedback: str = ""


class DaySummary(BaseModel):
    """How the day went, in numbers that each say what they count."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewed_today: int
    exercises_total: int
    exercises_answered: int
    exercises_right: int
    words_saved_today: int


class FeedView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    topic: str
    url: str


class SettingsView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: str
    levels: list[str]
    minutes: int
    feeds: list[FeedView]


class TodayView(BaseModel):
    """Everything the Today screen shows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    level: str
    minutes: int
    #: ``missing`` when nothing has been started for the day yet.
    status: str
    kind: str = ""
    situation: str = ""
    topic: str = ""
    note: str = ""
    text_id: int | None = None
    title: str = ""
    spoken_text: str = ""
    speech_rate: float = 1.0
    reviews: QueueCounts
    exercises: list[Exercise]
    summary: DaySummary
    finished: bool = False
    tomorrow_status: str = "missing"

    @property
    def ready(self) -> bool:
        return self.status == CourseDayStatus.READY.value and self.text_id is not None

    @property
    def reviews_done(self) -> bool:
        return self.reviews.remaining == 0

    @property
    def date_label(self) -> str:
        return self.day.strftime("%A, %d %B")


# --- the day ------------------------------------------------------------------------


def local_day(now: datetime | None = None) -> date:
    """The reader's calendar day, which is the one the course counts in."""
    return (now or utcnow()).astimezone().date()


def current_level(session: Session) -> Band:
    raw = settings_store.get(session, settings_store.KEY_LEVEL)
    try:
        return parse_level(raw)
    except (LexiconError, ValueError):
        return Band.A1


def set_level(session: Session, raw: str) -> Band:
    try:
        level = parse_level(raw)
    except (LexiconError, ValueError) as error:
        raise CourseError(f"{raw!r} is not a level; use A1 to C2") from error
    settings_store.set_value(session, settings_store.KEY_LEVEL, level.value)
    return level


def minutes_per_day(session: Session) -> int:
    try:
        value = int(settings_store.get(session, settings_store.KEY_MINUTES))
    except ValueError:
        return 30
    return max(MIN_MINUTES, min(MAX_MINUTES, value))


def set_minutes(session: Session, minutes: int) -> int:
    value = max(MIN_MINUTES, min(MAX_MINUTES, minutes))
    settings_store.set_value(session, settings_store.KEY_MINUTES, str(value))
    return value


# --- planning -----------------------------------------------------------------------


def load_situations(path: Path | None = None) -> dict[Band, list[str]]:
    """The work situations the course writes about, by level."""
    source = path or config.situations_path()
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CourseError(f"the list of work situations is missing: {source}") from error
    except tomllib.TOMLDecodeError as error:
        raise CourseError(f"{source} is not valid TOML: {error}") from error

    table: dict[Band, list[str]] = {}
    for key, entry in raw.items():
        try:
            band = parse_level(key)
        except (LexiconError, ValueError):
            continue
        items = entry.get("situations", []) if isinstance(entry, dict) else []
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        if cleaned:
            table[band] = cleaned
    return table


def situations_for(level: Band, table: dict[Band, list[str]]) -> list[str]:
    """The level's situations, or the nearest level below that has some."""
    order = list(LEVELS)
    for band in reversed(order[: order.index(level) + 1]):
        if table.get(band):
            return table[band]
    for band in order:  # nothing at or below: take the lowest there is
        if table.get(band):
            return table[band]
    raise CourseError("the list of work situations is empty")


def _last_used(history: list[CourseDay], field: str, kind: CourseDayKind) -> dict[str, date]:
    """When each value of ``field`` was last used on a finished-preparing day of ``kind``."""
    seen: dict[str, date] = {}
    for row in history:  # most recent first
        if row.kind != kind.value or row.status != CourseDayStatus.READY.value:
            continue
        value = getattr(row, field)
        if value and value not in seen:
            seen[value] = row.day
    return seen


def choose_least_recent(options: list[str], last_used: dict[str, date]) -> str:
    """The first option never used, or else the one used longest ago."""
    never = [option for option in options if option not in last_used]
    if never:
        return never[0]
    return min(options, key=lambda option: last_used[option])


def choose_kind(history: list[CourseDay], *, has_feeds: bool) -> CourseDayKind:
    """Alternate between a work text and the news, starting with work.

    Work first because it is the course's syllabus; news the next day because it
    is what makes the reader come back. Alternation follows what was actually
    prepared, so a news day that fell back to a work text is followed by news
    again rather than by a second work day.
    """
    if not has_feeds:
        return CourseDayKind.WORK
    for row in history:
        if row.status == CourseDayStatus.READY.value:
            if row.kind == CourseDayKind.WORK.value:
                return CourseDayKind.NEWS
            return CourseDayKind.WORK
    return CourseDayKind.WORK


def plan_for(session: Session, day: date, *, table: dict[Band, list[str]] | None = None) -> Plan:
    """Decide what the day's text will be, from the history and the settings."""
    level = current_level(session)
    history = days.before(session, day)
    topics = feeds.topics(session)
    kind = choose_kind(history, has_feeds=bool(topics))

    options = situations_for(level, table if table is not None else load_situations())
    situation = choose_least_recent(options, _last_used(history, "situation", CourseDayKind.WORK))
    topic = (
        choose_least_recent(topics, _last_used(history, "topic", CourseDayKind.NEWS))
        if kind is CourseDayKind.NEWS
        else ""
    )
    return Plan(day=day, kind=kind, level=level, situation=situation, topic=topic)


# --- exercises ----------------------------------------------------------------------


def _glossary(raw: str) -> list[dict[str, Any]]:
    try:
        loaded = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return (
        [entry for entry in loaded if isinstance(entry, dict)] if isinstance(loaded, list) else []
    )


def build_exercises(
    glossary: list[dict[str, Any]],
    latest: dict[tuple[str, str], ExerciseAttempt] | None = None,
) -> list[Exercise]:
    """The day's exercises, built from the words the text was written to teach.

    Cloze first -- recognising the word in context -- then a sentence of your
    own, which is the harder direction: finding the word rather than knowing it
    when you see it. An entry whose example does not contain its own word, or a
    multi-word expression the gap cannot find, makes no cloze rather than a
    broken one.
    """
    answers = latest or {}
    built: list[Exercise] = []

    cloze = 0
    for entry in glossary:
        if cloze >= MAX_CLOZE:
            break
        word = str(entry.get("en", "")).strip()
        target = lemma_of(word)
        example = str(entry.get("example_en", "")).strip()
        if not target or not example or " " in target:
            continue
        gapped = blank_out(example, target)
        if gapped == example:
            continue
        built.append(
            _with_answer(
                Exercise(
                    kind=ExerciseKind.CLOZE,
                    target=target,
                    word=word,
                    prompt=gapped,
                    hint=str(entry.get("pt", "")).strip(),
                ),
                answers,
            )
        )
        cloze += 1

    sentences = 0
    for entry in glossary:
        if sentences >= MAX_SENTENCES:
            break
        word = str(entry.get("en", "")).strip()
        target = lemma_of(word)
        if not target:
            continue
        built.append(
            _with_answer(
                Exercise(
                    kind=ExerciseKind.SENTENCE,
                    target=target,
                    word=word,
                    prompt=f"Write a sentence of your own with “{word}”.",
                    hint=str(entry.get("pt", "")).strip(),
                    reference=str(entry.get("example_en", "")).strip(),
                ),
                answers,
            )
        )
        sentences += 1
    return built


def _with_answer(exercise: Exercise, answers: dict[tuple[str, str], ExerciseAttempt]) -> Exercise:
    attempt = answers.get((exercise.kind.value, exercise.target))
    if attempt is None:
        return exercise
    return exercise.model_copy(
        update={
            "answer": attempt.answer,
            "answered": True,
            "correct": attempt.correct,
            "feedback": feedback(exercise, attempt.answer, correct=attempt.correct),
        }
    )


def _uses(text: str, target: str) -> bool:
    """Whether ``text`` contains the target word, in any of its forms."""
    target = target.casefold()
    if " " in target:
        folded = " ".join(text.casefold().split())
        lemmas = " ".join(token.lemma for token in tokenize(text) if token.is_word)
        return target in folded or target in lemmas
    for token in tokenize(text):
        if not token.is_word:
            continue
        surface = token.text.casefold()
        if token.lemma == target or surface == target or is_inflection(surface, target):
            return True
    return False


def _normalised(text: str) -> str:
    return " ".join("".join(c for c in text.casefold() if c.isalnum() or c.isspace()).split())


def check(exercise: Exercise, answer: str) -> bool:
    """Whether ``answer`` is right for ``exercise``. Deterministic, see the module."""
    text = answer.strip()
    if not text:
        return False
    if exercise.kind is ExerciseKind.CLOZE:
        return _uses(text, exercise.target) and len(text.split()) <= 3
    word_count = sum(1 for token in tokenize(text) if token.is_word)
    if word_count < MIN_SENTENCE_WORDS:
        return False
    if exercise.reference and _normalised(text) == _normalised(exercise.reference):
        return False  # copying the example is not writing a sentence
    return _uses(text, exercise.target)


def feedback(exercise: Exercise, answer: str, *, correct: bool) -> str:
    """One line saying why, written for the reader."""
    if correct:
        return "Right."
    text = answer.strip()
    if exercise.kind is ExerciseKind.CLOZE:
        return f"The word was “{exercise.word}”."
    if sum(1 for token in tokenize(text) if token.is_word) < MIN_SENTENCE_WORDS:
        return f"Write a whole sentence -- at least {MIN_SENTENCE_WORDS} words."
    if exercise.reference and _normalised(text) == _normalised(exercise.reference):
        return "That is the example. Write one of your own."
    return f"Use “{exercise.word}” in it -- any form of the word counts."


def answer_exercise(
    session: Session,
    kind: str,
    target: str,
    answer: str,
    *,
    now: datetime | None = None,
) -> Exercise:
    """Check an answer to one of today's exercises, and record it."""
    day = local_day(now)
    row = days.by_day(session, day)
    if row is None or row.id is None or row.text_id is None:
        raise CourseError("today's text is not ready yet")
    text = texts.by_id(session, row.text_id)
    if text is None:
        raise CourseError("today's text is missing")

    try:
        wanted = ExerciseKind(kind)
    except ValueError as error:
        raise CourseError(f"there is no exercise of kind {kind!r}") from error
    exercise = next(
        (
            item
            for item in build_exercises(_glossary(text.glossary_json))
            if item.kind is wanted and item.target == target
        ),
        None,
    )
    if exercise is None:
        raise CourseError("that exercise is not part of today")

    correct = check(exercise, answer)
    attempts.add(
        session,
        ExerciseAttempt(
            day_id=row.id,
            kind=wanted.value,
            target=target,
            answer=answer.strip(),
            correct=correct,
            created_at=now or utcnow(),
        ),
    )
    return exercise.model_copy(
        update={
            "answer": answer.strip(),
            "answered": True,
            "correct": correct,
            "feedback": feedback(exercise, answer, correct=correct),
        }
    )


# --- today --------------------------------------------------------------------------


def today(session: Session, *, now: datetime | None = None) -> TodayView:
    """The Today screen, as it stands right now."""
    moment = now or utcnow()
    day = local_day(moment)
    row = days.by_day(session, day)
    level = current_level(session)
    counts = review.counts(session, now=moment)

    exercises: list[Exercise] = []
    title = spoken = ""
    if row is not None and row.text_id is not None:
        text = texts.by_id(session, row.text_id)
        if text is not None:
            title, spoken = text.title, text.adapted_text
            latest = attempts.latest_for_day(session, row.id) if row.id is not None else {}
            exercises = build_exercises(_glossary(text.glossary_json), latest)

    start, end = reviews.day_bounds(moment)
    summary = DaySummary(
        reviewed_today=counts.reviewed_today,
        exercises_total=len(exercises),
        exercises_answered=sum(1 for item in exercises if item.answered),
        exercises_right=sum(1 for item in exercises if item.correct),
        words_saved_today=words.created_between(session, start, end),
    )
    tomorrow = days.by_day(session, day + timedelta(days=1))

    return TodayView(
        day=day,
        level=level.value,
        minutes=minutes_per_day(session),
        status=row.status if row is not None else "missing",
        kind=row.kind if row is not None else "",
        situation=row.situation if row is not None else "",
        topic=row.topic if row is not None else "",
        note=row.note if row is not None else "",
        text_id=row.text_id if row is not None else None,
        title=title,
        spoken_text=spoken,
        speech_rate=SPEECH_RATE.get(level.value, 1.0),
        reviews=counts,
        exercises=exercises,
        summary=summary,
        finished=row is not None and row.finished_at is not None,
        tomorrow_status=tomorrow.status if tomorrow is not None else "missing",
    )


def finish(session: Session, *, now: datetime | None = None) -> bool:
    """Close today's session. False if there was nothing to close, or it was closed."""
    return days.finish(session, local_day(now), now=now)


# --- settings -----------------------------------------------------------------------


def settings_view(session: Session) -> SettingsView:
    return SettingsView(
        level=current_level(session).value,
        levels=[band.value for band in LEVELS],
        minutes=minutes_per_day(session),
        feeds=[
            FeedView(id=feed.id or 0, topic=feed.topic, url=feed.url)
            for feed in feeds.all_feeds(session)
        ],
    )


def add_feed(session: Session, topic: str, url: str) -> FeedView:
    """Add a feed under a topic. The topic is created by using it."""
    name = " ".join(topic.split())
    if not name:
        raise CourseError("a feed needs a topic, such as games or football")
    if len(name) > MAX_TOPIC_LENGTH:
        raise CourseError(f"keep the topic under {MAX_TOPIC_LENGTH} characters")
    try:
        checked = check_url(url)
    except SourceError as error:
        raise CourseError(str(error)) from error
    if feeds.by_url(session, checked) is not None:
        raise CourseError("that feed is already there")
    added = feeds.add(session, Feed(topic=name.casefold(), url=checked))
    return FeedView(id=added.id or 0, topic=added.topic, url=added.url)


def remove_feed(session: Session, feed_id: int) -> None:
    if not feeds.remove(session, feed_id):
        raise CourseError(f"there is no feed {feed_id}")
