"""Tests for the course's screens, through the application.

The rest of the suite tests the rules; these test the wiring, which no rule test
can: that the front door renders in each state of the day, that opening it on
an empty day starts the preparation, that an answer comes back with the header
the summary listens for. A template that raises on a missing attribute passes
every unit test and breaks the one screen the reader opens every day.

The background preparation is replaced with a recorder. The real one would
start a thread that calls the Claude CLI.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from graded_reader import course, library, preparation
from graded_reader.store import database, days
from graded_reader.web.app import create_app

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

#: The app answers only to a loopback name (see web.host), so the tests use one.
BASE_URL = "http://127.0.0.1:8000"

GLOSSARY: list[dict[str, Any]] = [
    {"en": "rock", "pt": "rocha", "example_en": "They put the water under a rock."},
]


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    """Every day the screens asked to have prepared."""
    calls: list[date] = []

    def record(day: date, **_: object) -> bool:
        calls.append(day)
        return True

    monkeypatch.setattr(preparation, "ensure", record)
    return calls


class Client:
    """The app called in-process over ASGI -- no port, no server, no lifespan.

    Not Starlette's TestClient, which this version deprecates. And no lifespan
    on purpose: the real one starts preparing today's text in a thread.
    """

    def __init__(self) -> None:
        self.app = create_app()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http:
                return await http.request(method, url, **kwargs)

        return anyio.run(send)

    def get(self, url: str) -> httpx.Response:
        return self.request("GET", url)

    def post(self, url: str, data: dict[str, str] | None = None) -> httpx.Response:
        return self.request("POST", url, data=data)


@pytest.fixture()
def client() -> Client:
    return Client()


def today() -> date:
    return course.local_day()


def ready_today(drop_in: Callable[..., Path], *, status: str = "ready") -> int:
    result = library.import_file(drop_in(glossary=GLOSSARY))
    assert result.text_id is not None
    now = datetime.now().astimezone()
    with database.session() as active:
        days.claim(
            active,
            today(),
            kind="work",
            situation="Explaining a small bug",
            now=now,
            stale_before=now - timedelta(minutes=15),
        )
        if status == "ready":
            days.mark_ready(active, today(), text_id=result.text_id, kind="work")
        else:
            days.mark_failed(active, today(), "you have reached your usage limit")
    return result.text_id


# --- the front door -----------------------------------------------------------------


def test_opening_the_page_starts_nothing_by_itself(client: Client, started: list[date]) -> None:
    """Another site can make the browser GET this page; that must cost nothing."""
    response = client.get("/")

    assert response.status_code == 200
    assert started == []


def test_an_empty_day_asks_for_its_text_with_a_post_as_it_loads(client: Client) -> None:
    page = client.get("/").text

    assert 'hx-post="/today/start"' in page
    assert 'hx-trigger="load"' in page


def test_starting_an_empty_day_writes_its_text(client: Client, started: list[date]) -> None:
    response = client.post("/today/start")

    assert "Writing today" in response.text
    assert started == [today()]


def test_starting_does_not_retry_a_day_that_failed(
    client: Client, started: list[date], drop_in: Callable[..., Path]
) -> None:
    """Retrying spends the plan again; that is the retry button's decision."""
    ready_today(drop_in, status="failed")

    response = client.post("/today/start")

    assert "could not be prepared" in response.text
    assert started == []


def test_another_site_cannot_start_the_day(client: Client, started: list[date]) -> None:
    response = client.request("POST", "/today/start", headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403
    assert started == []


def test_a_ready_day_shows_its_text_audio_and_exercises(
    client: Client, started: list[date], drop_in: Callable[..., Path]
) -> None:
    ready_today(drop_in)

    response = client.get("/")

    assert response.status_code == 200
    assert "Water Under The Rock" in response.text
    assert "data-audio" in response.text
    assert "Practise" in response.text
    assert started == [], "a ready day is not prepared again"


def test_a_failed_day_says_why_and_is_retried_only_on_request(
    client: Client, started: list[date], drop_in: Callable[..., Path]
) -> None:
    ready_today(drop_in, status="failed")

    page = client.get("/")
    assert "usage limit" in page.text
    assert started == [], "reloading must not spend the plan again"

    retried = client.post("/today/retry")
    assert "Writing today" in retried.text
    assert started == [today()]


def test_an_answer_comes_back_checked_and_tells_the_summary(
    client: Client, started: list[date], drop_in: Callable[..., Path]
) -> None:
    ready_today(drop_in)

    response = client.post(
        "/today/exercise", data={"kind": "cloze", "target": "rock", "answer": "rock"}
    )

    assert response.status_code == 200
    assert "Right." in response.text
    assert response.headers["HX-Trigger"] == "exercise-answered"
    assert "1 of" in client.get("/today/summary").text


def test_finishing_the_day_starts_tomorrows_text(
    client: Client, started: list[date], drop_in: Callable[..., Path]
) -> None:
    ready_today(drop_in)

    response = client.post("/today/finish")

    assert "Done for today" in response.text
    assert started == [today() + timedelta(days=1)]


def test_finishing_a_day_whose_text_is_not_ready_starts_nothing(
    client: Client, started: list[date], drop_in: Callable[..., Path]
) -> None:
    """The button is hidden until then; the server has to hold the rule too."""
    ready_today(drop_in, status="failed")

    response = client.post("/today/finish")

    assert "Done for today" not in response.text
    assert started == []


# --- settings and the library -------------------------------------------------------


def test_the_settings_screen_renders_and_a_bad_feed_is_explained(client: Client) -> None:
    assert client.get("/settings").status_code == 200

    response = client.post("/settings/feeds", data={"topic": "games", "url": "ftp://x/feed"})

    assert response.status_code == 200, "the message has to reach the page"
    assert "http" in response.text


def test_a_text_archived_from_its_page_moves_to_the_archive(
    client: Client, drop_in: Callable[..., Path]
) -> None:
    text_id = library.import_file(drop_in()).text_id
    assert "Listen" in client.get(f"/texts/{text_id}").text

    assert "Archived." in client.post(f"/texts/{text_id}/archive").text

    assert "Water Under The Rock" not in client.get("/library").text
    assert "Water Under The Rock" in client.get("/library?archived=true").text
