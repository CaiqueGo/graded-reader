"""Refuse a request addressed to a name the app is not served on.

The origin check in web.origin trusts the browser's word that a page is the
app's own, and the browser decides that by name. DNS rebinding turns the name
against it: a hostile site, say evil.example, serves a page, then points its own
DNS at 127.0.0.1. The page's requests now reach this app, and to the browser
they are same-origin -- so they pass web.origin, and unlike any other site's
page this one can read the answers: the deck, the texts, the settings.

What the browser cannot hide is the name it used. Every request carries it in
``Host``, and a rebound page sends ``evil.example:8000``. So the app answers
only when ``Host`` names an address it is served on:

- ``127.0.0.1``, ``localhost`` and ``[::1]``, always. No outside party can make
  a browser send those for a page it serves.
- The address ``reader serve --host`` binds to, when it is a specific one. A
  wildcard such as ``0.0.0.0`` names no address, so it adds none: the app then
  answers to localhost only, and ``serve`` says so.

The port is not checked. It is not what rebinding changes, and a proxy or a
second port in front of the app is the user's own business.

This stops browsers, not programs. Anything that reaches the port directly can
send whatever ``Host`` it likes; that is what binding to localhost is for.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
#: Addresses that bind every interface, and so name none of them.
WILDCARDS = frozenset({"0.0.0.0", "::", "[::]"})
REFUSAL = "Refused: this app is not served under that name. Open it at 127.0.0.1.\n"
#: WebSocket "policy violation". Sent before the handshake is accepted, the
#: server answers the upgrade with a 403 instead.
POLICY_VIOLATION = 1008

#: A host, bracketed when it is IPv6, and an optional port. Anything else --
#: userinfo, a path, an unclosed bracket -- does not match, and is refused.
_HOST_HEADER = re.compile(r"(?P<name>\[[0-9a-f:.]+\]|[^:\[\]@/\s]+)(?::\d*)?")


def as_host_name(address: str) -> str:
    """How an address to bind to is written in a ``Host`` header.

    Lowercase, and an IPv6 address in brackets: ``::1`` is sent as ``[::1]``.
    """
    name = address.strip().lower()
    if ":" in name and not name.startswith("["):
        return f"[{name}]"
    return name


def is_wildcard(address: str) -> bool:
    """Whether binding to this address listens on every interface."""
    return as_host_name(address) in WILDCARDS


def allowed_hosts(bound: str | None) -> frozenset[str]:
    """The names the app answers to when bound to ``bound``."""
    if bound is None or is_wildcard(bound):
        return LOOPBACK_NAMES
    return LOOPBACK_NAMES | {as_host_name(bound)}


def host_name(header: str | None) -> str | None:
    """The name in a ``Host`` header, without its port; None when it has none.

    Not ``header.split(":")[0]``, which is what Starlette's TrustedHostMiddleware
    does, and which turns ``[::1]:8000`` into ``[``.
    """
    if header is None:
        return None
    match = _HOST_HEADER.fullmatch(header.strip().lower())
    return match["name"] if match else None


def is_foreign_host(header: str | None, allowed: Collection[str]) -> bool:
    """Whether a request's ``Host`` names something the app is not served on.

    A missing or malformed header is foreign too: every browser sends one, so
    only something that is not a browser page leaves it out.
    """
    return host_name(header) not in allowed


class RefuseForeignHost:
    """ASGI middleware refusing requests whose ``Host`` is :func:`is_foreign_host`.

    Plain ASGI, like web.origin's. It checks WebSocket upgrades too, although
    the app has none today: a rebound page can open one as easily as it can
    fetch, and a route added later should not have to remember this.
    """

    def __init__(self, app: ASGIApp, allowed: Collection[str] = LOOPBACK_NAMES) -> None:
        self.app = app
        self.allowed = frozenset(allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = Headers(scope=scope)
            if is_foreign_host(headers.get("host"), self.allowed):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": POLICY_VIOLATION})
                else:
                    response = PlainTextResponse(REFUSAL, status_code=400)
                    await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
