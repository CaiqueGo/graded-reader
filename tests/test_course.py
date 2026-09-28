"""Tests for the course: the day's plan, its exercises, and the claim on a day.

The expensive mistakes here are silent ones. Two threads both deciding they
should write today's text spend the reader's plan twice and leave two texts for
one day. An exercise that accepts a wrong answer teaches nothing; one that
rejects "rocks" for "rock" teaches the reader to distrust it. A plan that never
alternates turns the course back into the dull graded reader it was built to
replace.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from graded_reader import course, library, reading
from graded_reader.course import CourseError, Exercise, ExerciseKind
from graded_reader.lexicon import Band
from graded_reader.store import database, days, texts
from graded_reader.store.models import CourseDay, CourseDayKind, CourseDayStatus

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DAY = course.local_day(NOW)
STALE = NOW - timedelta(minutes=15)

GLOSSARY: list[dict[str, Any]] = [
    {"en": "rock", "pt": "rocha", "example_en": "They put the water under a rock."},
    {"en": "water", "pt": "agua", "example_en": "The water is cold."},
    {"en": "deep", "pt": "fundo", "example_en": "The lake is very deep."},
]


def imported(drop_in: Callable[..., Path], **overrides: Any) -> int:
    result = library.import_file(drop_in(**overrides))
    assert result.text_id is not None
    return result.text_id


def ready_day(text_id: int, day: date = DAY, *, kind: str = "work", situation: str = "") -> None:
    with database.session() as active:
        assert days.claim(active, day, kind=kind, situation=situation, now=NOW, stale_before=STALE)
        days.mark_ready(active, day, text_id=text_id, kind=kind, now=NOW)


def history(*rows: tuple[str, str, str]) -> list[CourseDay]:
    """Course days, most recent first, as (kind, situation, topic)."""
    return [
        CourseDay(
            day=DAY - timedelta(days=offset + 1),
            kind=kind,
            status=CourseDayStatus.READY.value,
            situation=situation,
            topic=topic,
        )
        for offset, (kind, situation, topic) in enumerate(rows)
    ]


# --- the claim --------------------------------------------------------------------


def claim(day: date = DAY, *, now: datetime = NOW) -> bool:
    with database.session() as active:
        return days.claim(
            active, day, kind="work", now=now, stale_before=now - timedelta(minutes=15)
        )


def test_the_first_claim_on_a_day_wins_and_the_second_does_not() -> None:
    assert claim()
    assert not claim(), "someone is preparing it right now"


def test_a_ready_day_is_never_claimed_again() -> None:
    with database.session() as active:
        days.claim(active, DAY, kind="work", now=NOW, stale_before=STALE)
        days.mark_ready(active, DAY, text_id=1, kind="work")

    assert not claim(now=NOW + timedelta(days=1)), "not even long after"


def test_a_failed_day_can_be_claimed_again() -> None:
    assert claim()
    with database.session() as active:
        days.mark_failed(active, DAY, "the plan limit was reached")

    assert claim()
    with database.session() as active:
        row = days.by_day(active, DAY)
        assert row is not None
        assert row.status == CourseDayStatus.PREPARING.value
        assert row.note == "", "the old failure does not linger on the retry"


def test_a_claim_abandoned_by_a_stopped_server_is_taken_over() -> None:
    assert claim(now=NOW)
    assert not claim(now=NOW + timedelta(minutes=5)), "still fresh"
    assert claim(now=NOW + timedelta(minutes=20)), "stale: nobody is coming back for it"


def test_two_threads_claiming_the_same_day_at_once_get_one_winner() -> None:
    """Two real connections, so the database -- not Python -- decides."""
    database.engine_for()  # create the schema before the race, not during it
    start = threading.Barrier(2)
    results: list[bool] = []

    def contender() -> None:
        start.wait()
        results.append(claim())

    threads = [threading.Thread(target=contender) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]


def test_finishing_a_day_happens_once() -> None:
    claim()
    with database.session() as active:
        days.mark_ready(active, DAY, text_id=1, kind="work")
    with database.session() as active:
        assert days.finish(active, DAY, now=NOW)
    with database.session() as active:
        assert not days.finish(active, DAY, now=NOW)


def test_a_day_still_being_written_cannot_be_finished() -> None:
    """Finishing starts tomorrow's text; two at once would run the CLI twice."""
    claim()
    with database.session() as active:
        assert not days.finish(active, DAY, now=NOW)


# --- the plan ---------------------------------------------------------------------


def test_the_first_day_is_a_work_text() -> None:
    assert course.choose_kind([], has_feeds=True) is CourseDayKind.WORK


def test_with_feeds_a_work_day_is_followed_by_news_and_news_by_work() -> None:
    after_work = history(("work", "x", ""))
    after_news = history(("news", "", "games"))

    assert course.choose_kind(after_work, has_feeds=True) is CourseDayKind.NEWS
    assert course.choose_kind(after_news, has_feeds=True) is CourseDayKind.WORK


def test_without_feeds_every_day_is_a_work_text() -> None:
    assert course.choose_kind(history(("work", "x", "")), has_feeds=False) is CourseDayKind.WORK


def test_alternation_follows_what_was_prepared_not_what_was_planned() -> None:
    """A news day that fell back to a work text is recorded as work -- so news is next."""
    fell_back = history(("work", "Explaining a small bug", "games"))
    assert course.choose_kind(fell_back, has_feeds=True) is CourseDayKind.NEWS


def test_a_failed_day_does_not_count_for_alternation() -> None:
    failed = CourseDay(day=DAY - timedelta(days=1), kind="news", status="failed")
    earlier = history(("work", "x", ""))[0].model_copy(update={"day": DAY - timedelta(days=2)})
    assert course.choose_kind([failed, earlier], has_feeds=True) is CourseDayKind.NEWS


def test_a_situation_never_used_comes_before_any_used_one() -> None:
    options = ["one", "two", "three"]
    last_used = {"one": DAY - timedelta(days=1)}
    assert course.choose_least_recent(options, last_used) == "two"


def test_when_every_situation_was_used_the_oldest_comes_back() -> None:
    options = ["one", "two"]
    last_used = {"one": DAY - timedelta(days=1), "two": DAY - timedelta(days=5)}
    assert course.choose_least_recent(options, last_used) == "two"


def test_the_plan_does_not_repeat_yesterdays_situation(drop_in: Callable[..., Path]) -> None:
    ready_day(
        imported(drop_in), DAY - timedelta(days=1), situation="Introducing yourself to the team"
    )

    with database.session() as active:
        plan = course.plan_for(active, DAY)

    assert plan.situation == "Describing your working day"


def test_a_level_without_situations_borrows_the_nearest_one_below() -> None:
    table = course.load_situations()
    assert course.situations_for(Band.B2, table) == table[Band.A2]


def test_a_missing_situations_file_is_said_plainly(tmp_path: Path) -> None:
    with pytest.raises(CourseError, match="missing"):
        course.load_situations(tmp_path / "nowhere.toml")


# --- exercises --------------------------------------------------------------------


def test_a_cloze_hides_the_word_and_gives_the_translation() -> None:
    cloze = course.build_exercises(GLOSSARY)[0]

    assert cloze.kind is ExerciseKind.CLOZE
    assert cloze.target == "rock"
    assert "rock" not in cloze.prompt
    assert cloze.hint == "rocha"


def test_an_example_that_does_not_contain_its_word_makes_no_cloze() -> None:
    broken = [{"en": "rock", "pt": "rocha", "example_en": "The water is cold."}]
    assert not [e for e in course.build_exercises(broken) if e.kind is ExerciseKind.CLOZE]


def test_a_phrase_gets_a_sentence_to_write_but_no_cloze() -> None:
    phrase = [{"en": "give up", "pt": "desistir", "example_en": "Never give up."}]
    kinds = [exercise.kind for exercise in course.build_exercises(phrase)]
    assert kinds == [ExerciseKind.SENTENCE]


def test_the_number_of_exercises_is_bounded() -> None:
    many = [
        {"en": word, "pt": word, "example_en": f"The {word} is here."}
        for word in ("rock", "water", "sign", "child", "thirst", "favorite", "deep")
    ]
    built = course.build_exercises(many)
    assert sum(1 for e in built if e.kind is ExerciseKind.CLOZE) == course.MAX_CLOZE
    assert sum(1 for e in built if e.kind is ExerciseKind.SENTENCE) == course.MAX_SENTENCES


def cloze(target: str = "rock") -> Exercise:
    return Exercise(kind=ExerciseKind.CLOZE, target=target, word=target, prompt="____")


def sentence(target: str = "rock", reference: str = "They put the water under a rock.") -> Exercise:
    return Exercise(
        kind=ExerciseKind.SENTENCE, target=target, word=target, prompt="", reference=reference
    )


@pytest.mark.parametrize("answer", ["rock", "Rock", " rocks ", "rock."])
def test_a_cloze_accepts_the_word_in_any_form(answer: str) -> None:
    assert course.check(cloze(), answer)


@pytest.mark.parametrize("answer", ["", "stone", "a rock is a stone here"])
def test_a_cloze_refuses_another_word_or_a_whole_sentence(answer: str) -> None:
    assert not course.check(cloze(), answer)


def test_a_sentence_has_to_use_the_word() -> None:
    assert course.check(sentence(), "I climbed a big rock yesterday.")
    assert not course.check(sentence(), "I climbed a big stone yesterday.")


def test_a_sentence_counts_any_form_of_the_word() -> None:
    assert course.check(sentence("run"), "She was running to work.")


def test_a_word_on_its_own_is_not_a_sentence() -> None:
    assert not course.check(sentence(), "rock")


def test_copying_the_example_is_not_writing_a_sentence() -> None:
    exercise = sentence()
    assert not course.check(exercise, "They put the water under a rock")
    assert course.feedback(exercise, "They put the water under a rock", correct=False).startswith(
        "That is the example"
    )


def test_a_wrong_cloze_tells_the_word() -> None:
    assert "rock" in course.feedback(cloze(), "stone", correct=False)


# --- answering, and the day's view ------------------------------------------------


def test_nothing_can_be_answered_before_the_text_is_ready() -> None:
    with database.session() as active, pytest.raises(CourseError, match="not ready"):
        course.answer_exercise(active, "cloze", "rock", "rock", now=NOW)


def test_an_answer_is_checked_recorded_and_shown_on_the_day(
    drop_in: Callable[..., Path],
) -> None:
    ready_day(imported(drop_in, glossary=GLOSSARY))

    with database.session() as active:
        checked = course.answer_exercise(active, "cloze", "rock", "rocks", now=NOW)
    assert checked.correct

    with database.session() as active:
        view = course.today(active, now=NOW)
    shown = next(e for e in view.exercises if e.kind is ExerciseKind.CLOZE and e.target == "rock")
    assert shown.answered and shown.correct
    assert view.summary.exercises_right == 1


def test_the_latest_try_is_the_one_that_counts(drop_in: Callable[..., Path]) -> None:
    ready_day(imported(drop_in, glossary=GLOSSARY))

    with database.session() as active:
        course.answer_exercise(active, "cloze", "rock", "stone", now=NOW)
    with database.session() as active:
        course.answer_exercise(active, "cloze", "rock", "rock", now=NOW)
    with database.session() as active:
        view = course.today(active, now=NOW)

    assert view.summary.exercises_right == 1
    assert view.summary.exercises_answered == 1


def test_an_exercise_that_is_not_part_of_today_is_refused(
    drop_in: Callable[..., Path],
) -> None:
    ready_day(imported(drop_in, glossary=GLOSSARY))
    with database.session() as active, pytest.raises(CourseError, match="not part of today"):
        course.answer_exercise(active, "cloze", "thirst", "thirst", now=NOW)


def test_a_day_with_nothing_started_says_so() -> None:
    with database.session() as active:
        view = course.today(active, now=NOW)
    assert view.status == "missing"
    assert not view.ready
    assert view.exercises == []


def test_a_ready_day_brings_its_text_and_the_speed_for_the_level(
    drop_in: Callable[..., Path],
) -> None:
    ready_day(imported(drop_in, glossary=GLOSSARY))

    with database.session() as active:
        view = course.today(active, now=NOW)

    assert view.ready
    assert view.spoken_text == "They put the water deep under a rock."
    assert view.speech_rate == course.SPEECH_RATE["A1"]
    assert len(view.exercises) == len(GLOSSARY) + course.MAX_SENTENCES


# --- settings ---------------------------------------------------------------------


def test_the_level_must_be_a_real_one() -> None:
    with database.session() as active, pytest.raises(CourseError, match="not a level"):
        course.set_level(active, "Z9")


def test_the_minutes_are_kept_within_reason() -> None:
    with database.session() as active:
        assert course.set_minutes(active, 1) == course.MIN_MINUTES
        assert course.set_minutes(active, 10_000) == course.MAX_MINUTES


def test_a_feed_needs_a_topic_and_a_web_address() -> None:
    with database.session() as active:
        with pytest.raises(CourseError, match="topic"):
            course.add_feed(active, "  ", "https://example.com/feed")
        with pytest.raises(CourseError):
            course.add_feed(active, "games", "file:///etc/passwd")


def test_the_same_feed_is_not_added_twice() -> None:
    with database.session() as active:
        course.add_feed(active, "Games", "https://example.com/feed")
    with database.session() as active, pytest.raises(CourseError, match="already"):
        course.add_feed(active, "games", "https://example.com/feed")


def test_a_topic_is_stored_in_one_spelling() -> None:
    with database.session() as active:
        added = course.add_feed(active, "  Video   Games ", "https://example.com/feed")
    assert added.topic == "video games"


# --- archiving --------------------------------------------------------------------


def test_an_archived_text_leaves_the_library_and_can_come_back(
    drop_in: Callable[..., Path],
) -> None:
    text_id = imported(drop_in)

    with database.session() as active:
        reading.set_archived(active, text_id, True, now=NOW)
    with database.session() as active:
        assert [t.id for t in reading.summaries(active)] == []
        assert [t.id for t in reading.summaries(active, archived=True)] == [text_id]
        assert texts.by_id(active, text_id) is not None, "archived, never deleted"

    with database.session() as active:
        reading.set_archived(active, text_id, False)
    with database.session() as active:
        assert [t.id for t in reading.summaries(active)] == [text_id]


def test_archiving_a_text_that_does_not_exist_says_so() -> None:
    with database.session() as active, pytest.raises(reading.ReadingError):
        reading.set_archived(active, 404, True)
