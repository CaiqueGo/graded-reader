"""News for the course: reading the reader's feeds and choosing an article.

Two kinds of untrusted input arrive here, and both shaped the module.

**The feed is XML from a site the reader picked.** It is parsed by feedparser,
which is handed bytes this module downloaded under its own timeout and size
limit -- feedparser never opens a connection of its own. Only three things are
taken from each entry: the title, the link and the date. Nothing from a feed is
ever rendered as markup.

**The links inside the feed are chosen by that site, not by the reader.** Until
the course existed, every address the app fetched was one the reader typed, and
the README could call fetching local-network addresses an acceptable limit for
that reason. A feed changes the premise: a hostile or compromised feed could
point the app at ``http://192.168.0.1/`` and have it issue requests inside the
reader's network. So everything fetched on a feed's behalf goes through
``guarded_client``, which refuses any host that does not resolve to a public
address -- checked on every request, redirects included, because a public URL
that answers with a redirect to a private one is the obvious way around a check
made once at the start.

One gap is left and is worth naming: the address is resolved to check it, and
resolved again by the connection. A host that answers the two lookups
differently (DNS rebinding) gets past the check. Closing that means pinning the
connection to the checked address, which is more machinery than a single-user
localhost app has earned yet.
"""

from __future__ import annotations

import ipaddress
import socket
from calendar import timegm
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from pydantic import BaseModel, ConfigDict

from graded_reader.sources import (
    TIMEOUT,
    USER_AGENT,
    Source,
    SourceError,
    check_url,
    from_url,
)

#: A feed is an index, not a document. Past this it is not a feed worth reading.
MAX_FEED_BYTES = 2_000_000

#: Only the newest entries matter; a feed with a thousand items is an archive.
MAX_ITEMS_PER_FEED = 30

#: How many articles to try before giving up on news for the day. Many pages
#: fail to extract -- paywalls, consent walls, pages that need a browser -- and
#: each attempt is a download, so the search is bounded.
MAX_CANDIDATES = 5

#: ``socket.getaddrinfo`` takes no timeout of its own, so the lookup runs in a
#: worker thread and is abandoned after this long.
RESOLVE_TIMEOUT_SECONDS = 5.0

#: Host name to the addresses it resolves to. The seam tests replace, so they
#: never depend on real DNS.
Resolver = Callable[[str], list[str]]


class NewsError(Exception):
    """Expected failure while finding the day's news, with a message for the user."""


class FeedItem(BaseModel):
    """One entry of a feed, reduced to what the course uses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    url: str
    topic: str
    published: datetime | None = None


def resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to, or an error within the timeout.

    Not a ``with`` block: leaving one waits for the worker to finish, which
    would sit through exactly the hung lookup the timeout is there to abandon.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(socket.getaddrinfo, host, None)
    try:
        answers = future.result(timeout=RESOLVE_TIMEOUT_SECONDS)
    except FutureTimeout as error:
        raise NewsError(f"looking up {host} took too long") from error
    except OSError as error:
        raise NewsError(f"could not look up {host}: {error}") from error
    finally:
        pool.shutdown(wait=False)
    return sorted({str(answer[4][0]) for answer in answers})


def is_public(host: str, resolver: Resolver = resolve) -> bool:
    """Whether ``host`` is somewhere on the internet, not inside this network.

    Every address the name resolves to has to be global. One private answer
    among public ones is enough to refuse: which one the connection uses is not
    this function's choice.
    """
    name = host.strip().strip("[]").casefold()
    if not name:
        return False
    try:
        return ipaddress.ip_address(name).is_global
    except ValueError:
        pass  # a name, not a literal address
    if name == "localhost" or name.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        addresses = resolver(name)
    except NewsError:
        return False
    return bool(addresses) and all(_is_global(address) for address in addresses)


def _is_global(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_global
    except ValueError:
        return False


def guarded_client(
    *,
    transport: httpx.BaseTransport | None = None,
    resolver: Resolver = resolve,
) -> httpx.Client:
    """An HTTP client that will only talk to public addresses.

    The check is a request hook, so it runs before every request the client
    sends -- including each hop of a redirect, which is where a check made only
    on the first URL would be walked around.
    """

    def refuse_private(request: httpx.Request) -> None:
        if not is_public(request.url.host, resolver):
            raise NewsError(f"refusing to fetch {request.url.host}: it is not a public address")

    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        transport=transport,
        event_hooks={"request": [refuse_private]},
    )


def fetch_feed(url: str, topic: str, *, client: httpx.Client) -> list[FeedItem]:
    """The newest entries of one feed, with links that can be fetched."""
    try:
        checked = check_url(url)
    except SourceError as error:
        raise NewsError(str(error)) from error
    try:
        response = client.get(checked)
        response.raise_for_status()
    except httpx.TimeoutException as error:
        raise NewsError(f"the feed at {checked} did not answer in time") from error
    except httpx.HTTPStatusError as error:
        raise NewsError(f"the feed at {checked} answered {error.response.status_code}") from error
    except httpx.HTTPError as error:
        raise NewsError(f"could not reach the feed at {checked}: {error}") from error

    parsed = feedparser.parse(response.content[:MAX_FEED_BYTES])
    entries: list[Any] = list(parsed.get("entries", []))
    if not entries:
        raise NewsError(f"{checked} is not a feed, or it has nothing in it")
    return list(_items(entries[:MAX_ITEMS_PER_FEED], topic))


def _items(entries: Iterable[Any], topic: str) -> Iterable[FeedItem]:
    for entry in entries:
        try:
            link = check_url(str(entry.get("link", "")))
        except SourceError:
            continue  # a javascript: or relative link is not an article
        title = " ".join(str(entry.get("title", "")).split())
        yield FeedItem(title=title, url=link, topic=topic, published=_published(entry))


def _published(entry: Any) -> datetime | None:
    """When the entry was published, from whichever date the feed provides."""
    for key in ("published_parsed", "updated_parsed"):
        moment = entry.get(key)
        if moment:
            try:
                # feedparser normalises every date it parses to UTC.
                return datetime.fromtimestamp(timegm(moment), tz=UTC)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def pick_article(
    items: list[FeedItem],
    *,
    used: set[str],
    client: httpx.Client,
    limit: int = MAX_CANDIDATES,
) -> tuple[FeedItem, Source]:
    """The newest unused article that can actually be read.

    Newest first, skipping anything the course already used. Each candidate is
    fetched and extracted; one that fails -- too short, too long, behind a wall,
    refused by the address guard -- is passed over for the next, and the reasons
    are kept so an empty result says why.
    """
    fresh = [item for item in items if item.url not in used]
    if not fresh:
        raise NewsError("every article in your feeds has already been used")

    oldest = datetime.min.replace(tzinfo=UTC)
    fresh.sort(key=lambda item: item.published or oldest, reverse=True)

    reasons: list[str] = []
    for item in fresh[:limit]:
        try:
            return item, from_url(item.url, client=client)
        except (SourceError, NewsError) as error:
            reasons.append(f"{item.url}: {error}")
    raise NewsError("no article in your feeds could be read -- " + "; ".join(reasons))
