"""Tests for preparing a course day's text ahead of the reader.

The CLI is a fake runner and the network is a mock transport, so nothing here
spends plan usage. What is tested is the orchestration, because that is where
the costly mistakes live: preparing a day twice, leaving a day stuck when the
model fails, losing the news day entirely because one feed was down, or
treating a written text as trusted without measuring it.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from graded_reader import course, news, preparation
from graded_reader.adapters.claude_cli import ClaudeCliAdapter, Run
from graded_reader.store import database, days, texts
from graded_reader.store.models import CourseDayStatus, Feed

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DAY = course.local_day(NOW)

WRITTEN: dict[str, Any] = {
    "title": "First Day",
    "adapted_text": "I am new. I put my water under the desk.",
    "glossary": [{"en": "water", "pt": "agua", "example_en": "The water is cold."}],
    "questions": [{"q": "Who is new?", "a": "I am."}],
}
ADAPTED: dict[str, Any] = {
    "title": "A New Game",
    "adapted_text": "They put the game on the rock. The child is under a sign.",
    "glossary": [],
    "questions": [],
}

ARTICLE = (
    "<html><body><article><p>"
    + ("The team shipped the new game today. " * 30)
    + ("</p></article></body></html>")
)
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Games</title>
<item><title>New game</title><link>https://news.example/game</link>
<pubDate>Sun, 27 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>"""


class FakeCli:
    """Answers like the CLI would, and remembers every prompt it was given."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.prompts: list[str] = []
        self.fail_with = fail_with

    def __call__(self, prompt: str, cwd: Path | None = None) -> Run:
        self.prompts.append(prompt)
        if self.fail_with is not None:
            raise self.fail_with
        reply = WRITTEN if "## The situation" in prompt else ADAPTED
        return Run(text=json.dumps(reply), cost_usd=0.05, seconds=30.0)


def adapter(cli: FakeCli) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(runner=cli)


def network(pages: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return pages.get(str(request.url), httpx.Response(404))

    public = {"feed.example": ["93.184.216.35"], "news.example": ["93.184.216.34"]}
    return news.guarded_client(
        transport=httpx.MockTransport(handler), resolver=lambda host: public[host]
    )


def day_row() -> Any:
    with database.session() as active:
        row = days.by_day(active, DAY)
        assert row is not None
        active.expunge(row)
        return row


def news_is_due() -> None:
    """A feed, and yesterday a work text -- so today alternates to the news."""
    yesterday = DAY - timedelta(days=1)
    with database.session() as active:
        active.add(Feed(topic="games", url="https://feed.example/rss"))
        assert days.claim(active, yesterday, kind="work", now=NOW, stale_before=NOW)
        days.mark_ready(active, yesterday, text_id=0, kind="work")


# --- a work day ---------------------------------------------------------------------


def test_a_work_day_is_written_measured_and_recorded() -> None:
    cli = FakeCli()

    assert preparation.prepare(DAY, adapter=adapter(cli), now=NOW)

    row = day_row()
    assert row.status == CourseDayStatus.READY.value
    assert row.kind == "work"
    assert row.situation == "Introducing yourself to the team"
    assert "Introducing yourself to the team" in cli.prompts[0]

    with database.session() as active:
        text = texts.by_id(active, row.text_id)
        assert text is not None
        assert text.source_kind == "generated"
        assert text.coverage_pct is not None, "written for the level is not taken on trust"


def test_a_day_already_prepared_is_not_prepared_again() -> None:
    cli = FakeCli()
    preparation.prepare(DAY, adapter=adapter(cli), now=NOW)

    assert not preparation.prepare(DAY, adapter=adapter(cli), now=NOW)
    assert len(cli.prompts) == 1, "the plan is spent once"


# --- a news day ---------------------------------------------------------------------


def test_a_news_day_adapts_the_newest_article_from_the_feeds() -> None:
    news_is_due()
    pages = {
        "https://feed.example/rss": httpx.Response(200, text=RSS),
        "https://news.example/game": httpx.Response(200, text=ARTICLE),
    }

    with network(pages) as client:
        preparation.prepare(DAY, adapter=adapter(FakeCli()), client=client, now=NOW)

    row = day_row()
    assert row.status == CourseDayStatus.READY.value
    assert row.kind == "news"
    assert row.topic == "games"
    with database.session() as active:
        text = texts.by_id(active, row.text_id)
        assert text is not None
        assert text.source_value == "https://news.example/game"


def test_when_the_news_cannot_be_read_the_day_falls_back_to_work() -> None:
    news_is_due()
    pages = {"https://feed.example/rss": httpx.Response(503)}

    with network(pages) as client:
        preparation.prepare(DAY, adapter=adapter(FakeCli()), client=client, now=NOW)

    row = day_row()
    assert row.status == CourseDayStatus.READY.value, "a day without news still gets a text"
    assert row.kind == "work"
    assert row.note.startswith("No news today")
    assert "503" in row.note


def test_an_article_already_read_is_not_chosen_again() -> None:
    news_is_due()
    pages = {
        "https://feed.example/rss": httpx.Response(200, text=RSS),
        "https://news.example/game": httpx.Response(200, text=ARTICLE),
    }
    with network(pages) as client:
        preparation.prepare(DAY, adapter=adapter(FakeCli()), client=client, now=NOW)

    # The day after tomorrow is news again, and the feed has nothing new.
    later = DAY + timedelta(days=2)
    with database.session() as active:
        days.claim(active, later - timedelta(days=1), kind="work", now=NOW, stale_before=NOW)
        days.mark_ready(active, later - timedelta(days=1), text_id=0, kind="work")
    with network(pages) as client:
        preparation.prepare(later, adapter=adapter(FakeCli()), client=client, now=NOW)

    with database.session() as active:
        row = days.by_day(active, later)
        assert row is not None
        assert row.kind == "work"
        assert "already been used" in row.note


# --- failures -----------------------------------------------------------------------


def test_a_failure_is_recorded_with_its_reason_and_can_be_retried() -> None:
    failing = FakeCli(fail_with=preparation.AdapterError("you have reached your usage limit"))

    assert preparation.prepare(DAY, adapter=adapter(failing), now=NOW)
    row = day_row()
    assert row.status == CourseDayStatus.FAILED.value
    assert "usage limit" in row.note

    assert preparation.prepare(DAY, adapter=adapter(FakeCli()), now=NOW)
    assert day_row().status == CourseDayStatus.READY.value


def test_a_reply_that_breaks_the_contract_fails_the_day_instead_of_storing_it() -> None:
    class Garbled(FakeCli):
        def __call__(self, prompt: str, cwd: Path | None = None) -> Run:
            return Run(text="I'm sorry, I can't write that.")

    preparation.prepare(DAY, adapter=adapter(Garbled()), now=NOW)

    row = day_row()
    assert row.status == CourseDayStatus.FAILED.value
    assert row.text_id is None


def test_a_day_that_cannot_even_be_planned_is_recorded_as_failed(tmp_data: Path) -> None:
    """Not left looking like it is being written, with nobody writing it."""
    (tmp_data / "situations.toml").unlink()
    cli = FakeCli()

    assert preparation.prepare(DAY, adapter=adapter(cli), now=NOW)

    row = day_row()
    assert row.status == CourseDayStatus.FAILED.value
    assert "situations" in row.note
    assert cli.prompts == [], "nothing was asked of the model"


def test_an_unexpected_error_still_releases_the_day_and_is_raised() -> None:
    """A bug must not leave the day stuck in 'preparing' for fifteen minutes."""
    broken = FakeCli(fail_with=RuntimeError("a bug"))

    with pytest.raises(RuntimeError, match="a bug"):
        preparation.prepare(DAY, adapter=adapter(broken), now=NOW)

    row = day_row()
    assert row.status == CourseDayStatus.FAILED.value
    assert "unexpected" in row.note


# --- in the background ----------------------------------------------------------------


def test_a_day_already_being_prepared_here_does_not_start_a_second_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    calls: list[object] = []

    def slow_prepare(day: object, **_: object) -> bool:
        calls.append(day)
        release.wait(timeout=5)
        return True

    monkeypatch.setattr(preparation, "prepare", slow_prepare)

    assert preparation.ensure(DAY)
    assert not preparation.ensure(DAY), "already under way in this process"
    release.set()

    for _ in range(200):
        if DAY not in preparation._in_flight:
            break
        threading.Event().wait(0.01)
    assert calls == [DAY]
    assert preparation.ensure(DAY), "once finished, it can be started again"
    release.set()
