"""Getting the text that is going to be adapted.

The app fetches the page itself, rather than letting the model do it. That is
the security decision in this module and the reason it exists.

An article you ask Degrau to adapt is untrusted text. If a model with Write and
Bash access went and read it, anything written on that page would be arriving as
input to an agent that can change this repository. Here the page is reduced to
plain text first, and the model that sees it has no tools at all -- so the worst
a hostile page can do is produce a bad adaptation, which you will read and throw
away.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import trafilatura
from pydantic import BaseModel, ConfigDict

#: Every external call gets one. Ten seconds to connect, thirty to finish: a
#: news page that cannot answer in that time is not worth blocking a request on.
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

#: Stop reading after this much. A prompt is charged by the token, and a page
#: that is megabytes long is a download, not an article.
MAX_BYTES = 2_000_000

#: Below this, whatever came back was not an article -- a consent wall, an error
#: page, or a site that needs JavaScript to render anything at all.
MIN_CHARS = 200

#: Above this, refuse rather than adapt. Two reasons, and the second is the one
#: that matters: a three-thousand-word encyclopedia entry is not a graded
#: reading, it is a reference work, and adapting it produces something nobody
#: reads to the end. The first is that the whole thing goes into a prompt, and
#: the reader pays for it out of a plan with limits.
MAX_WORDS = 2_000

USER_AGENT = "Degrau/0.1 (personal graded reader; +https://github.com/)"


class SourceError(Exception):
    """Expected failure while fetching a source, with a message for the user."""


class Source(BaseModel):
    """A text to adapt, and where it came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    value: str
    title: str = ""
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


def from_text(text: str) -> Source:
    """A source the reader pasted in directly."""
    cleaned = text.strip()
    if not cleaned:
        raise SourceError("nothing to adapt")
    source = Source(kind="paste", value="", text=cleaned)
    if source.words > MAX_WORDS:
        raise SourceError(
            f"that is {source.words:,} words, and the limit is {MAX_WORDS:,}. "
            "Paste the section you actually want to read."
        )
    return source


def check_url(raw: str) -> str:
    """Validate a URL before anything goes out to the network.

    Only http and https, and only with a hostname. Without this, a scheme like
    ``file://`` turns a text box in a browser into a way to read this machine's
    disk, and that is a hole even in an app with one user.
    """
    url = raw.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SourceError(f"only http and https addresses are supported, not {parsed.scheme!r}")
    if not parsed.hostname:
        raise SourceError(f"{url!r} has no host in it")
    return url


def extract(html: str, *, url: str = "") -> tuple[str, str]:
    """Pull the article out of a page, returning ``(title, text)``."""
    text = trafilatura.extract(html, url=url or None, include_comments=False) or ""
    metadata = trafilatura.extract_metadata(html)
    title = (getattr(metadata, "title", "") or "") if metadata else ""
    return title.strip(), text.strip()


def from_url(raw: str, *, client: httpx.Client | None = None) -> Source:
    """Fetch a page and reduce it to the article inside it."""
    url = check_url(raw)
    owned = client is None
    active = client or httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        response = active.get(url)
        response.raise_for_status()
        body = response.content[:MAX_BYTES].decode(response.encoding or "utf-8", errors="replace")
    except httpx.TimeoutException as error:
        raise SourceError(f"{url} did not answer in time") from error
    except httpx.HTTPStatusError as error:
        raise SourceError(f"{url} answered {error.response.status_code}") from error
    except httpx.HTTPError as error:
        raise SourceError(f"could not reach {url}: {error}") from error
    finally:
        if owned:
            active.close()

    title, text = extract(body, url=url)
    return _checked(Source(kind="url", value=url, title=title, text=text), where=url)


def _checked(source: Source, *, where: str) -> Source:
    """Refuse a source that is too short to be an article or too long to adapt."""
    if len(source.text) < MIN_CHARS:
        raise SourceError(
            f"found only {len(source.text)} characters of article text at {where}. "
            "Some sites need a browser to render; copy the text and paste it instead."
        )
    if source.words > MAX_WORDS:
        raise SourceError(
            f"that is {source.words:,} words, and the limit is {MAX_WORDS:,}. "
            "A text this long makes a poor graded reading and a large model run. "
            "Paste the section you actually want to read."
        )
    return source
