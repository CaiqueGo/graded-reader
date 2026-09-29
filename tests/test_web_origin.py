"""Tests for refusing changes that another site asked the browser to make.

Without this check, any page the reader visits can post a hidden form to the app
on localhost, and the app obeys: it starts a model run on the reader's plan, or
rewrites the deck and the settings. Nothing on screen would show it happened.

The opposite failure is as real and more visible: a check that refuses the
app's own pages breaks every button. So both directions are tested through the
application, and the header rule on its own, where each case is one line.

The background preparation is replaced with a recorder, as in the course tests.
"""

from __future__ import annotations

from datetime import date

import anyio
import httpx
import pytest

from graded_reader import course, preparation
from graded_reader.store import database
from graded_reader.web.app import create_app
from graded_reader.web.origin import is_cross_site

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

#: The app answers only to a loopback name (see web.host), so the tests use one.
OWN_ORIGIN = "http://localhost:8000"
FROM_ANOTHER_SITE = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[date]:
    """Every day a request asked to have prepared -- each one a model run."""
    calls: list[date] = []

    def record(day: date, **_: object) -> bool:
        calls.append(day)
        return True

    monkeypatch.setattr(preparation, "ensure", record)
    return calls


def call(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> httpx.Response:
    """The app in-process over ASGI, without its lifespan -- see test_web_today."""

    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url=OWN_ORIGIN) as http:
            return await http.request(method, url, headers=headers, data=data)

    return anyio.run(send)


def plan() -> tuple[str, int]:
    with database.session() as active:
        return course.current_level(active).value, course.minutes_per_day(active)


def other_plan() -> dict[str, str]:
    """A plan different from whatever the current one is."""
    level, minutes = plan()
    return {"level": "C1" if level != "C1" else "B1", "minutes": str(minutes + 7)}


# --- through the application ---------------------------------------------------------


def test_a_cross_site_post_is_refused_and_starts_no_model_run(started: list[date]) -> None:
    response = call("POST", "/today/retry", headers=FROM_ANOTHER_SITE)

    assert response.status_code == 403
    assert "another site" in response.text
    assert started == [], "a forged form must not spend the reader's plan"


def test_a_cross_site_post_changes_nothing() -> None:
    before = plan()

    response = call("POST", "/settings", headers=FROM_ANOTHER_SITE, data=other_plan())

    assert response.status_code == 403
    assert plan() == before


def test_a_post_from_the_apps_own_pages_works() -> None:
    wanted = other_plan()

    response = call("POST", "/settings", headers={"Sec-Fetch-Site": "same-origin"}, data=wanted)

    assert response.status_code == 200
    assert plan() == (wanted["level"], int(wanted["minutes"]))


def test_an_older_browser_is_judged_by_its_origin(started: list[date]) -> None:
    refused = call("POST", "/today/retry", headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403
    assert started == []

    allowed = call("POST", "/today/retry", headers={"Origin": OWN_ORIGIN})
    assert allowed.status_code == 200
    assert len(started) == 1


def test_a_post_with_neither_header_works(started: list[date]) -> None:
    """curl, the tests, anything that is not a page in a browser."""
    response = call("POST", "/today/retry")

    assert response.status_code == 200
    assert len(started) == 1


def test_reading_is_never_refused() -> None:
    response = call("GET", "/settings", headers=FROM_ANOTHER_SITE)

    assert response.status_code == 200


# --- the rule ------------------------------------------------------------------------


def post(**headers: str) -> bool:
    return is_cross_site("POST", headers, OWN_ORIGIN)


def test_the_apps_own_pages_and_the_address_bar_are_allowed() -> None:
    assert not post(**{"sec-fetch-site": "same-origin"})
    assert not post(**{"sec-fetch-site": "none"})


def test_another_site_is_refused_and_so_is_another_port_on_localhost() -> None:
    assert post(**{"sec-fetch-site": "cross-site"})
    assert post(**{"sec-fetch-site": "same-site"}), "e.g. a dev server on localhost:3000"


def test_the_fetch_header_decides_over_the_origin() -> None:
    assert post(**{"sec-fetch-site": "cross-site", "origin": OWN_ORIGIN})


def test_an_unknown_fetch_site_value_is_refused() -> None:
    assert post(**{"sec-fetch-site": "somewhere-new"})


def test_without_the_fetch_header_the_origin_must_be_this_app() -> None:
    assert not post(origin=OWN_ORIGIN)
    assert not post(origin="HTTP://LOCALHOST:8000"), "origins compare without case"
    assert post(origin="http://localhost:3000")
    assert post(origin="https://localhost:8000"), "another scheme is another origin"
    assert post(origin="null"), "what a sandboxed page or a file:// page sends"


def test_every_method_that_changes_something_is_checked() -> None:
    headers = {"sec-fetch-site": "cross-site"}
    for method in ("POST", "PUT", "PATCH", "DELETE", "post"):
        assert is_cross_site(method, headers, OWN_ORIGIN), method
    for method in ("GET", "HEAD", "OPTIONS"):
        assert not is_cross_site(method, headers, OWN_ORIGIN), method
