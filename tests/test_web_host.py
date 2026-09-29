"""Tests for refusing requests addressed to a name the app is not served on.

Without this check, DNS rebinding reads the whole app: a hostile page whose name
now resolves to 127.0.0.1 is same-origin to the browser, so it passes the origin
check, and it can read what comes back -- the deck, the texts, the settings.

The opposite failure breaks the app for everyone: refuse 127.0.0.1 or localhost
and no page loads at all, and forget the address ``serve --host`` binds to and
the one reader who asked for it gets nothing. So both directions are tested.

The background preparation is replaced with a recorder, as in the course tests.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import anyio
import httpx
import pytest
from typer.testing import CliRunner

from graded_reader import cli, config, course, preparation
from graded_reader.store import database
from graded_reader.web.app import create_app
from graded_reader.web.host import (
    LOOPBACK_NAMES,
    RefuseForeignHost,
    allowed_hosts,
    host_name,
    is_foreign_host,
)

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

#: What a page on a rebound name sends: its own name, with the app's port.
REBOUND = "http://evil.example:8000"


@pytest.fixture(autouse=True)
def not_served(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start as the tests' app does, with no ``serve --host`` in the environment."""
    monkeypatch.delenv(config.VAR_SERVE_HOST, raising=False)


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
    base_url: str,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> httpx.Response:
    """The app in-process over ASGI, as a browser at ``base_url`` would reach it."""

    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as http:
            return await http.request(method, url, headers=headers, data=data)

    return anyio.run(send)


def plan() -> tuple[str, int]:
    with database.session() as active:
        return course.current_level(active).value, course.minutes_per_day(active)


# --- through the application ---------------------------------------------------------


def test_a_rebound_page_cannot_read_the_app() -> None:
    response = call("GET", "/settings", base_url=REBOUND)

    assert response.status_code == 400
    assert "not served under that name" in response.text
    assert "<form" not in response.text, "the settings page must not reach it"


def test_a_rebound_page_opening_the_front_door_starts_no_model_run(
    started: list[date],
) -> None:
    response = call("GET", "/", base_url=REBOUND)

    assert response.status_code == 400
    assert started == []


def test_a_rebound_post_changes_nothing_although_the_browser_calls_it_same_origin() -> None:
    before = plan()
    level, minutes = before
    wanted = {"level": "C1" if level != "C1" else "B1", "minutes": str(minutes + 7)}

    response = call(
        "POST",
        "/settings",
        base_url=REBOUND,
        headers={"Sec-Fetch-Site": "same-origin", "Origin": REBOUND},
        data=wanted,
    )

    assert response.status_code == 400
    assert plan() == before


@pytest.mark.parametrize(
    "base_url",
    ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"],
)
def test_the_loopback_names_work(base_url: str) -> None:
    response = call("GET", "/settings", base_url=base_url)

    assert response.status_code == 200


def test_the_address_serve_binds_to_works_and_others_are_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.VAR_SERVE_HOST, "192.168.0.10")

    assert call("GET", "/settings", base_url="http://192.168.0.10:8000").status_code == 200
    assert call("GET", "/settings", base_url="http://localhost:8000").status_code == 200
    assert call("GET", "/settings", base_url=REBOUND).status_code == 400


def test_a_wildcard_bind_answers_to_localhost_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.VAR_SERVE_HOST, "0.0.0.0")

    assert call("GET", "/settings", base_url="http://127.0.0.1:8000").status_code == 200
    assert call("GET", "/settings", base_url="http://192.168.0.10:8000").status_code == 400
    assert call("GET", "/settings", base_url="http://0.0.0.0:8000").status_code == 400


# --- the middleware, below what httpx can send --------------------------------------


class Recorder:
    """An inner app that notes it was reached, and what the middleware sent."""

    def __init__(self) -> None:
        self.reached = False
        self.sent: list[dict[str, Any]] = []

    async def app(self, scope: Any, receive: Any, send: Any) -> None:
        self.reached = True

    async def send(self, message: Any) -> None:
        self.sent.append(message)


def run_scope(scope_type: str, headers: list[tuple[bytes, bytes]]) -> Recorder:
    recorder = Recorder()
    middleware = RefuseForeignHost(recorder.app)
    scope = {
        "type": scope_type,
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "headers": headers,
    }

    async def receive() -> dict[str, Any]:
        return {"type": f"{scope_type}.disconnect"}

    anyio.run(middleware, scope, receive, recorder.send)
    return recorder


def test_a_request_without_a_host_is_refused() -> None:
    """No browser leaves it out; HTTP/1.0 can."""
    recorder = run_scope("http", [])

    assert not recorder.reached
    assert recorder.sent[0]["status"] == 400


def test_a_websocket_from_a_rebound_page_is_closed_before_it_opens() -> None:
    recorder = run_scope("websocket", [(b"host", b"evil.example:8000")])

    assert not recorder.reached
    assert recorder.sent == [{"type": "websocket.close", "code": 1008}]


def test_a_websocket_to_localhost_goes_through() -> None:
    recorder = run_scope("websocket", [(b"host", b"localhost:8000")])

    assert recorder.reached


# --- the rule ------------------------------------------------------------------------


def test_the_name_is_read_without_its_port_or_case() -> None:
    assert host_name("localhost:8000") == "localhost"
    assert host_name("127.0.0.1") == "127.0.0.1"
    assert host_name("LocalHost:8000") == "localhost"
    assert host_name("[::1]:8000") == "[::1]", "not '[', as a split on ':' would give"
    assert host_name("[::1]") == "[::1]"


def test_a_malformed_host_has_no_name() -> None:
    for header in (None, "", "[::1", "user@localhost", "localhost/x", "local host", "a:b:c"):
        assert host_name(header) is None, header


def test_names_that_merely_contain_a_loopback_one_are_foreign() -> None:
    for header in (
        "evil.example:8000",
        "localhost.evil.example",
        "127.0.0.1.nip.io",
        "evil.localhost.example",
        "localhost.",
    ):
        assert is_foreign_host(header, LOOPBACK_NAMES), header


def test_without_serve_or_with_a_wildcard_only_loopback_is_allowed() -> None:
    for bound in (None, "0.0.0.0", "::", "[::]", "127.0.0.1", "::1"):
        assert allowed_hosts(bound) == LOOPBACK_NAMES, bound


def test_a_specific_bind_address_is_added_as_a_browser_writes_it() -> None:
    assert allowed_hosts("192.168.0.10") == LOOPBACK_NAMES | {"192.168.0.10"}
    assert allowed_hosts("MyBox.lan") == LOOPBACK_NAMES | {"mybox.lan"}
    assert allowed_hosts("fe80::1") == LOOPBACK_NAMES | {"[fe80::1]"}


# --- reader serve --------------------------------------------------------------------


@pytest.fixture()
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Run ``serve`` without a server; returns what uvicorn.run was called with.

    ``serve`` writes the address into the environment itself. Setting it here
    first is what makes monkeypatch put the environment back afterwards.
    """
    import uvicorn

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setenv(config.VAR_SERVE_HOST, "left-over")
    return calls


def said(result: Any) -> str:
    """What serve printed, with rich's line wrapping undone."""
    return " ".join(result.output.split())


def test_serve_tells_the_app_the_address_it_binds_to(served: list[dict[str, Any]]) -> None:
    result = CliRunner().invoke(cli.app, ["serve", "--host", "192.168.0.10"])

    assert result.exit_code == 0, result.output
    assert served[0]["host"] == "192.168.0.10"
    assert config.serve_host() == "192.168.0.10"
    assert "no authentication" in said(result)


def test_serve_on_a_wildcard_says_the_app_answers_to_localhost_only(
    served: list[dict[str, Any]],
) -> None:
    result = CliRunner().invoke(cli.app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code == 0, result.output
    assert "answers only to 127.0.0.1 and localhost" in said(result)
    assert "http://127.0.0.1:8000" in said(result), "0.0.0.0 would be refused"


def test_serve_on_localhost_says_nothing_more(served: list[dict[str, Any]]) -> None:
    result = CliRunner().invoke(cli.app, ["serve"])

    assert result.exit_code == 0, result.output
    assert "warning" not in said(result)
    assert config.serve_host() == "127.0.0.1"
