"""Tests for reading the reader's news feeds.

Nothing here touches the network or real DNS: the HTTP client runs over a mock
transport and the resolver is a dictionary. What is tested is the part that
would fail silently -- a feed link that points inside the reader's network being
followed, a redirect walking around the check, a broken entry sinking the whole
feed, an article used twice.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from graded_reader import news
from graded_reader.news import FeedItem, NewsError

#: Enough words to pass the article-length check in sources.
ARTICLE = (
    "<html><body><article><p>"
    + ("The team shipped the new game today. " * 30)
    + ("</p></article></body></html>")
)

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Games</title>
<item><title>Old story</title><link>https://news.example/old</link>
  <pubDate>Mon, 21 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>New story</title><link>https://news.example/new</link>
  <pubDate>Sun, 27 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Sneaky</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Tech</title>
<entry><title>An atom entry</title><link href="https://news.example/atom"/>
  <updated>2026-09-26T08:00:00Z</updated></entry>
</feed>"""

PUBLIC = {"news.example": ["93.184.216.34"], "feed.example": ["93.184.216.35"]}


def resolver(table: dict[str, list[str]]) -> news.Resolver:
    def _resolve(host: str) -> list[str]:
        if host not in table:
            raise NewsError(f"could not look up {host}")
        return table[host]

    return _resolve


def client(
    handler: Callable[[httpx.Request], httpx.Response],
    table: dict[str, list[str]] | None = None,
) -> httpx.Client:
    return news.guarded_client(
        transport=httpx.MockTransport(handler), resolver=resolver(table or PUBLIC)
    )


def serving(pages: dict[str, httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return pages.get(str(request.url), httpx.Response(404))

    return handler


# --- the address guard --------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.0.0.5", "192.168.0.1", "169.254.169.254", "::1", "localhost"]
)
def test_an_address_inside_the_network_is_not_public(host: str) -> None:
    assert not news.is_public(host, resolver({}))


def test_a_public_address_is_public() -> None:
    assert news.is_public("93.184.216.34", resolver({}))
    assert news.is_public("news.example", resolver(PUBLIC))


def test_a_name_that_resolves_inside_the_network_is_refused() -> None:
    assert not news.is_public("sneaky.example", resolver({"sneaky.example": ["10.0.0.7"]}))


def test_one_private_answer_among_public_ones_is_enough_to_refuse() -> None:
    mixed = {"mixed.example": ["93.184.216.34", "192.168.1.10"]}
    assert not news.is_public("mixed.example", resolver(mixed))


def test_a_name_that_cannot_be_resolved_is_refused() -> None:
    assert not news.is_public("nowhere.example", resolver({}))


def test_the_guarded_client_will_not_fetch_a_private_address() -> None:
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        return httpx.Response(200, text="secret")

    with client(handler) as guarded, pytest.raises(NewsError, match="not a public address"):
        guarded.get("http://10.0.0.5/admin")
    assert reached == [], "the request never left"


def test_a_redirect_to_a_private_address_is_refused_too() -> None:
    """The check runs on every hop, not only on the first URL."""
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        if request.url.host == "news.example":
            return httpx.Response(302, headers={"Location": "http://192.168.0.1/router"})
        return httpx.Response(200, text="router admin page")

    with client(handler) as guarded, pytest.raises(NewsError, match="192.168.0.1"):
        guarded.get("https://news.example/story")
    assert reached == ["https://news.example/story"]


# --- reading a feed -----------------------------------------------------------------


def test_an_rss_feed_gives_its_entries_newest_date_included() -> None:
    handler = serving({"https://feed.example/rss": httpx.Response(200, text=RSS)})
    with client(handler) as guarded:
        items = news.fetch_feed("https://feed.example/rss", "games", client=guarded)

    assert [item.title for item in items] == ["Old story", "New story"]
    assert items[1].published == datetime(2026, 9, 27, 10, 0, tzinfo=UTC)
    assert all(item.topic == "games" for item in items)


def test_a_link_that_is_not_a_web_address_is_dropped_not_fatal() -> None:
    handler = serving({"https://feed.example/rss": httpx.Response(200, text=RSS)})
    with client(handler) as guarded:
        items = news.fetch_feed("https://feed.example/rss", "games", client=guarded)
    assert "javascript:alert(1)" not in {item.url for item in items}


def test_an_atom_feed_reads_as_well() -> None:
    handler = serving({"https://feed.example/atom": httpx.Response(200, text=ATOM)})
    with client(handler) as guarded:
        items = news.fetch_feed("https://feed.example/atom", "tech", client=guarded)
    assert [item.url for item in items] == ["https://news.example/atom"]


def test_a_page_that_is_not_a_feed_is_said_plainly() -> None:
    handler = serving({"https://feed.example/page": httpx.Response(200, text=ARTICLE)})
    with client(handler) as guarded, pytest.raises(NewsError, match="not a feed"):
        news.fetch_feed("https://feed.example/page", "games", client=guarded)


def test_a_feed_that_answers_with_an_error_says_which() -> None:
    handler = serving({"https://feed.example/rss": httpx.Response(503)})
    with client(handler) as guarded, pytest.raises(NewsError, match="503"):
        news.fetch_feed("https://feed.example/rss", "games", client=guarded)


def test_a_feed_that_does_not_answer_in_time_is_said_plainly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with client(handler) as guarded, pytest.raises(NewsError, match="in time"):
        news.fetch_feed("https://feed.example/rss", "games", client=guarded)


# --- choosing the article -------------------------------------------------------------


def item(url: str, day: int) -> FeedItem:
    return FeedItem(title=url, url=url, topic="games", published=datetime(2026, 9, day, tzinfo=UTC))


def test_the_newest_unused_article_is_chosen() -> None:
    handler = serving({"https://news.example/b": httpx.Response(200, text=ARTICLE)})
    items = [item("https://news.example/a", 20), item("https://news.example/b", 25)]

    with client(handler) as guarded:
        chosen, source = news.pick_article(items, used=set(), client=guarded)

    assert chosen.url == "https://news.example/b"
    assert "shipped the new game" in source.text


def test_an_article_already_read_is_skipped() -> None:
    handler = serving({"https://news.example/a": httpx.Response(200, text=ARTICLE)})
    items = [item("https://news.example/a", 20), item("https://news.example/b", 25)]

    with client(handler) as guarded:
        chosen, _ = news.pick_article(items, used={"https://news.example/b"}, client=guarded)

    assert chosen.url == "https://news.example/a"


def test_an_article_that_cannot_be_read_is_passed_over_for_the_next() -> None:
    handler = serving(
        {
            "https://news.example/walled": httpx.Response(200, text="<p>Subscribe.</p>"),
            "https://news.example/open": httpx.Response(200, text=ARTICLE),
        }
    )
    items = [item("https://news.example/walled", 26), item("https://news.example/open", 25)]

    with client(handler) as guarded:
        chosen, _ = news.pick_article(items, used=set(), client=guarded)

    assert chosen.url == "https://news.example/open"


def test_when_nothing_can_be_read_the_reasons_are_kept() -> None:
    handler = serving({})
    items = [item("https://news.example/gone", 26)]

    with client(handler) as guarded, pytest.raises(NewsError, match="404"):
        news.pick_article(items, used=set(), client=guarded)


def test_when_every_article_was_used_it_says_so() -> None:
    items = [item("https://news.example/a", 20)]
    with client(serving({})) as guarded, pytest.raises(NewsError, match="already been used"):
        news.pick_article(items, used={"https://news.example/a"}, client=guarded)
