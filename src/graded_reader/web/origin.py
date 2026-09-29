"""Refuse a change that another site asked the browser to make.

The app has no login, so nothing in the browser tells a request from this app's
own pages apart from one a page on any other site submits. A plain HTML form on
a site you happen to visit can post to http://127.0.0.1:8000/adapt, and the
browser sends it without asking: a form post is a "simple request", with no
preflight. The page cannot read the answer, but it does not need to -- the
request is the attack. It spends a model run on your plan, or changes the deck.

So every method that changes something is checked for where it came from, using
what the browser itself says and a page cannot forge:

- ``Sec-Fetch-Site``, when present, decides. ``same-origin`` is this app's own
  pages, HTMX included; ``none`` is you, typing or bookmarking. ``same-site``
  is refused too: on localhost that is any other port, another dev server.
- Without it (an older browser), ``Origin`` must be this app's own origin. The
  literal ``null`` a sandboxed page sends does not match, and is refused.
- With neither, the request did not come from a browser page -- curl, a test,
  the CLI -- and passes. The threat this answers is a browser.

No token scheme: a single user on localhost, whose every change is made from the
app's own pages, is exactly the case these headers settle on their own.
"""

from __future__ import annotations

from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
REFUSAL = "Refused: this change was requested by another site.\n"


def is_cross_site(method: str, headers: Mapping[str, str], own_origin: str) -> bool:
    """Whether a request that changes something came from somewhere else.

    Reading is never refused: a GET changes nothing, and a cross-site page
    cannot read the answer. Any method not known to be safe is checked, so a
    verb nobody thought of is refused rather than let through. An unknown
    ``Sec-Fetch-Site`` value is refused for the same reason.
    """
    if method.upper() in SAFE_METHODS:
        return False
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site.strip().lower() not in ALLOWED_FETCH_SITES
    origin = headers.get("origin")
    if origin is not None:
        return origin.strip().lower() != own_origin.lower()
    return False


class RefuseCrossSite:
    """ASGI middleware answering 403 to :func:`is_cross_site` requests.

    Plain ASGI rather than ``BaseHTTPMiddleware``, which wraps every response in
    a stream and has had its share of surprises. This one reads the headers and
    either answers or gets out of the way.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            own_origin = f"{scope['scheme']}://{headers.get('host', '')}"
            if is_cross_site(scope["method"], headers, own_origin):
                response = PlainTextResponse(REFUSAL, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
