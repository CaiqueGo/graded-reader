"""Preparing a course day's text, ahead of the reader.

A text costs a model call -- tens of seconds, sometimes minutes -- so it has to
exist before the reader asks for it. Preparation is started from three places:
the server starting, the Today screen opening on a day with nothing prepared,
and the end of a session, for tomorrow. Any two of them can overlap.

The order of operations is the whole design:

1. **Claim the day**, in one short transaction. ``store.days.claim`` is a single
   statement against the unique index on the day, so of two overlapping calls
   exactly one gets to continue. The other returns having done nothing.
2. **Do the slow work with no transaction open.** Fetching feeds, fetching the
   article and running the CLI all happen here. A database transaction held
   across minutes of network would lock SQLite for everyone else.
3. **Record the result**, ready or failed, in another short transaction.

A day that fails can be claimed again, so a retry is just calling this again.
A day stuck in ``preparing`` because the server stopped halfway is taken over
once it is older than ``STALE_AFTER``.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from graded_reader import course, library, news
from graded_reader.adapters.claude_cli import AdapterError, ClaudeCliAdapter
from graded_reader.lexicon import LexiconError
from graded_reader.library import ImportResult
from graded_reader.news import NewsError
from graded_reader.sources import SourceError
from graded_reader.store import database, days, texts
from graded_reader.store import feeds as feed_store
from graded_reader.store.models import CourseDayKind, SourceKind, utcnow

#: Longer than a CLI run can take (claude_cli.TIMEOUT_SECONDS, twice over for a
#: news day that falls back to a written one). A claim older than this belongs
#: to a process that is not coming back.
STALE_AFTER = timedelta(minutes=15)

#: The failures preparation expects and records. Anything else is a bug: it is
#: recorded too, so the day does not sit in ``preparing``, and then raised.
EXPECTED = (AdapterError, SourceError, NewsError, LexiconError, course.CourseError)


class PreparationError(Exception):
    """Expected failure while preparing a day, with a message for the user."""


def prepare(
    day: date,
    *,
    adapter: ClaudeCliAdapter | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    db: Path | None = None,
) -> bool:
    """Prepare ``day``'s text. True if this call did the work, False if another had it.

    ``adapter`` and ``client`` are the seams: tests pass a fake CLI and an HTTP
    client over a mock transport, so nothing here spends plan usage or touches
    the network.
    """
    moment = now or utcnow()
    with database.session(db) as active:
        # A plan that cannot be made -- no situations file, say -- still claims
        # the day, so the failure is recorded where the Today screen shows it,
        # with a retry button. Raised before the claim, it would leave the page
        # waiting on a day nobody is preparing.
        plan: course.Plan | None = None
        problem = ""
        try:
            plan = course.plan_for(active, day)
        except course.CourseError as error:
            problem = str(error)
        topic = plan.topic if plan is not None else ""
        feed_urls = [feed.url for feed in feed_store.all_feeds(active) if feed.topic == topic]
        used = texts.source_values(active, SourceKind.URL.value)
        claimed = days.claim(
            active,
            day,
            kind=plan.kind.value if plan is not None else CourseDayKind.WORK.value,
            situation=plan.situation if plan is not None else "",
            topic=topic,
            now=moment,
            stale_before=moment - STALE_AFTER,
        )
        if claimed and plan is None:
            days.mark_failed(active, day, problem)
    if not claimed or plan is None:
        return claimed

    try:
        text_id, kind, note = _prepare(plan, feed_urls, used, adapter=adapter, client=client, db=db)
    except (*EXPECTED, PreparationError) as error:
        with database.session(db) as active:
            days.mark_failed(active, day, str(error) or error.__class__.__name__)
        return True
    except Exception as error:
        with database.session(db) as active:
            days.mark_failed(active, day, f"unexpected error: {error}")
        raise

    with database.session(db) as active:
        days.mark_ready(active, day, text_id=text_id, kind=kind.value, note=note, now=utcnow())
    return True


def _prepare(
    plan: course.Plan,
    feed_urls: list[str],
    used: set[str],
    *,
    adapter: ClaudeCliAdapter | None,
    client: httpx.Client | None,
    db: Path | None,
) -> tuple[int, CourseDayKind, str]:
    """The slow part. No database session is open while this runs."""
    note = ""
    if plan.kind is CourseDayKind.NEWS:
        try:
            return _news(plan, feed_urls, used, adapter=adapter, client=client, db=db)
        except (*EXPECTED, PreparationError) as error:
            # News is the motivation half of the course, not the syllabus. A day
            # without it still gets a text; it just says why it is not the news.
            note = f"No news today ({error}), so a work situation instead."

    result = library.write_and_import(plan.situation, plan.level, adapter=adapter, db=db)
    return _text_id(result), CourseDayKind.WORK, note


def _news(
    plan: course.Plan,
    feed_urls: list[str],
    used: set[str],
    *,
    adapter: ClaudeCliAdapter | None,
    client: httpx.Client | None,
    db: Path | None,
) -> tuple[int, CourseDayKind, str]:
    if not feed_urls:
        raise PreparationError(f"there are no feeds for {plan.topic!r}")

    owned = client is None
    active = client or news.guarded_client()
    try:
        items: list[news.FeedItem] = []
        problems: list[str] = []
        for url in feed_urls:
            try:
                items.extend(news.fetch_feed(url, plan.topic, client=active))
            except NewsError as error:
                problems.append(str(error))
        if not items:
            raise NewsError("; ".join(problems) or f"the feeds for {plan.topic!r} are empty")
        item, source = news.pick_article(items, used=used, client=active)
    finally:
        if owned:
            active.close()

    result = library.adapt_and_import(
        source.text,
        plan.level,
        adapter=adapter,
        kind=SourceKind.URL.value,
        value=source.value,
        title_hint=item.title or source.title,
        db=db,
    )
    return _text_id(result), CourseDayKind.NEWS, ""


def _text_id(result: ImportResult) -> int:
    if result.text_id is None:
        raise PreparationError(result.reason or "the text was rejected on import")
    return result.text_id


# --- in the background --------------------------------------------------------------

#: Days this process is already preparing. Only a saving -- it spares starting a
#: thread whose claim is bound to fail. The database claim is what actually
#: guarantees a day is prepared once, across threads and processes alike.
_in_flight: set[date] = set()
_in_flight_lock = threading.Lock()


def ensure(day: date, *, db: Path | None = None) -> bool:
    """Start preparing ``day`` in the background. False if it was already under way here."""
    with _in_flight_lock:
        if day in _in_flight:
            return False
        _in_flight.add(day)

    def work() -> None:
        try:
            prepare(day, db=db)
        finally:
            with _in_flight_lock:
                _in_flight.discard(day)

    threading.Thread(target=work, name=f"prepare-{day.isoformat()}", daemon=True).start()
    return True


def ensure_today(*, now: datetime | None = None, db: Path | None = None) -> bool:
    return ensure(course.local_day(now), db=db)


def ensure_tomorrow(*, now: datetime | None = None, db: Path | None = None) -> bool:
    return ensure(course.local_day(now) + timedelta(days=1), db=db)
