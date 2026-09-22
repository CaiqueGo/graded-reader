"""Tests for adapting a source with the model.

Nothing here calls the model or the network. A real adaptation takes a minute
and spends the reader's plan, so the runner and the HTTP client are both seams
that the tests fill in. What is tested is everything around the call: that a
fenced reply is unwrapped, that a bad one is refused with a reason, that the
result goes through the same validation as a hand-written file, and that a page
is reduced to text before any of it reaches a prompt.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from degrau import config, library, sources
from degrau.adapters.claude_cli import (
    AdapterError,
    ClaudeCliAdapter,
    Run,
    build_prompt,
    unfence,
)
from degrau.lexicon.models import Band
from degrau.library import ImportAction
from degrau.profile import LevelRules, Profile
from degrau.store import database, texts

pytestmark = pytest.mark.usefixtures("tmp_data", "tmp_db", "tmp_inbox")

REPLY: dict[str, Any] = {
    "title": "Water Under The Rock",
    "adapted_text": "They put the water deep under a rock.",
    "glossary": [
        {
            "en": "rock",
            "pt": "rocha",
            "example_en": "They put the water under a rock.",
            "example_pt": "Eles puseram a agua sob uma rocha.",
        }
    ],
    "questions": [{"q": "Where does the water go?", "a": "Under a rock."}],
}


def runner_returning(text: str) -> Callable[[str, Path | None], Run]:
    def _run(prompt: str, cwd: Path | None = None) -> Run:
        _run.prompt = prompt  # type: ignore[attr-defined]
        return Run(text=text, cost_usd=0.05, seconds=20.0)

    return _run


def profile() -> Profile:
    return Profile(
        level=Band.A1,
        rules=LevelRules(vocabulary="The most frequent 500 words.", sentences="6 to 10 words."),
        targets=["under", "favorite"],
    )


# --- unwrapping the reply ------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '  \n```json\n{"a": 1}\n```  \n',
    ],
)
def test_the_markdown_fence_comes_off(reply: str) -> None:
    """The model fences its JSON however firmly it is told not to."""
    assert json.loads(unfence(reply)) == {"a": 1}


def test_a_fence_inside_the_text_is_not_mistaken_for_the_wrapper() -> None:
    body = '{"adapted_text": "Use ```code``` sparingly."}'
    assert unfence(body) == body


# --- the prompt ----------------------------------------------------------------


def test_the_prompt_carries_the_profile_and_the_source() -> None:
    prompt = build_prompt("The water is deep.", profile())
    assert "## Target level: A1" in prompt
    assert "The most frequent 500 words." in prompt
    assert "under, favorite" in prompt
    assert "The water is deep." in prompt


def test_the_prompt_asks_for_json_and_forbids_inventing() -> None:
    prompt = build_prompt("x", profile())
    assert "A single JSON object and nothing else" in prompt
    assert "Do not invent facts." in prompt


# --- adapting ------------------------------------------------------------------


def test_a_good_reply_becomes_an_adapted_text() -> None:
    adapter = ClaudeCliAdapter(runner=runner_returning(json.dumps(REPLY)))
    adapted = adapter.adapt_source("The original.", profile(), kind="url", value="http://x")

    assert adapted.level == "A1"
    assert adapted.title == "Water Under The Rock"
    assert adapted.source.kind == "url"
    assert adapted.source.original_text == "The original."
    assert adapted.glossary[0].en == "rock"
    assert adapter.last_run is not None and adapter.last_run.cost_usd == 0.05


def test_the_original_is_kept_whole() -> None:
    """Rereading the source after the adaptation is half the method."""
    original = "A long original.\n\nWith two paragraphs."
    adapter = ClaudeCliAdapter(runner=runner_returning(json.dumps(REPLY)))
    assert adapter.adapt_source(original, profile()).source.original_text == original


def test_the_level_comes_from_the_profile_not_from_the_model() -> None:
    """A model that answers with the wrong level must not relabel the text."""
    reply = {**REPLY, "level": "C2"}
    adapter = ClaudeCliAdapter(runner=runner_returning(json.dumps(reply)))
    assert adapter.adapt_source("x", profile()).level == "A1"


def test_a_reply_that_is_not_json_says_what_came_back_instead() -> None:
    adapter = ClaudeCliAdapter(runner=runner_returning("I cannot help with that."))
    with pytest.raises(AdapterError, match="did not return JSON"):
        adapter.adapt_source("x", profile())


def test_a_json_reply_of_the_wrong_shape_is_refused() -> None:
    adapter = ClaudeCliAdapter(runner=runner_returning('["a list"]'))
    with pytest.raises(AdapterError, match="expected a JSON object"):
        adapter.adapt_source("x", profile())


def test_a_reply_missing_the_text_is_refused_by_the_contract() -> None:
    adapter = ClaudeCliAdapter(runner=runner_returning('{"title": "Only a title"}'))
    with pytest.raises(AdapterError, match="does not fit the contract"):
        adapter.adapt_source("x", profile())


def test_adapting_nothing_is_refused_before_the_model_is_called() -> None:
    def explode(prompt: str, cwd: Path | None = None) -> Run:
        raise AssertionError("the model must not be called for an empty source")

    with pytest.raises(AdapterError, match="no source text"):
        ClaudeCliAdapter(runner=explode).adapt_source("   ", profile())


# --- all the way into the library ----------------------------------------------


def test_an_adapted_text_lands_in_the_library_like_any_other() -> None:
    adapter = ClaudeCliAdapter(runner=runner_returning(json.dumps(REPLY)))
    result = library.adapt_and_import("The original.", "A1", adapter=adapter)

    assert result.action is ImportAction.IMPORTED
    assert result.coverage_pct == pytest.approx(0.75), "it is measured, not trusted"

    with database.session() as active:
        stored = texts.by_id(active, result.text_id or 0)
        assert stored is not None
        assert stored.title == "Water Under The Rock"
        assert stored.prompt_used, "the prompt is kept for reproducibility"


def test_the_adapted_document_is_written_to_the_inbox() -> None:
    """Same path as a file: it survives, and it can be looked at afterwards."""
    adapter = ClaudeCliAdapter(runner=runner_returning(json.dumps(REPLY)))
    library.adapt_and_import("The original.", "A1", adapter=adapter)

    kept = list(config.processed_dir().glob("adapted-*.json"))
    assert len(kept) == 1
    assert json.loads(kept[0].read_text(encoding="utf-8"))["schema"] == 1


def test_adapting_the_same_thing_twice_does_not_duplicate_the_text() -> None:
    adapter = ClaudeCliAdapter(runner=runner_returning(json.dumps(REPLY)))
    first = library.adapt_and_import("The original.", "A1", adapter=adapter)
    second = library.adapt_and_import("The original.", "A1", adapter=adapter)

    assert first.action is ImportAction.IMPORTED
    assert second.action is ImportAction.DUPLICATE


# --- fetching the source --------------------------------------------------------

PAGE = """<html><head><title>Bees In Cities</title></head><body>
<nav>menu everywhere</nav>
<article><h1>Bees In Cities</h1>
<p>Urban beekeeping has expanded rapidly over the past decade, and researchers
now find that metropolitan areas sustain healthier pollinator populations than
the farmland around them, which is a counterintuitive but well documented
result that has been replicated in several European capitals.</p>
<p>Pesticide exposure compounds the disparity, because commercial fields are
treated repeatedly while municipal parks are sprayed only sparingly.</p></article>
<footer>copyright</footer></body></html>"""


def fake_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_page_is_reduced_to_its_article() -> None:
    with fake_client(lambda request: httpx.Response(200, html=PAGE)) as client:
        source = sources.from_url("https://example.com/bees", client=client)

    assert source.kind == "url"
    assert "Urban beekeeping" in source.text
    assert "menu everywhere" not in source.text, "navigation is not the article"
    assert "copyright" not in source.text


@pytest.mark.parametrize(
    "url",
    ["file:///c:/windows/system32/drivers/etc/hosts", "ftp://example.com/x", "/etc/passwd"],
)
def test_only_http_addresses_are_accepted(url: str) -> None:
    """A text box in a browser must not become a way to read this disk."""
    with pytest.raises(sources.SourceError):
        sources.check_url(url)


def test_an_address_with_no_host_is_refused() -> None:
    with pytest.raises(sources.SourceError, match="no host"):
        sources.check_url("https://")


def test_a_page_that_answers_with_an_error_says_which() -> None:
    with (
        fake_client(lambda request: httpx.Response(404)) as client,
        pytest.raises(sources.SourceError, match="404"),
    ):
        sources.from_url("https://example.com/gone", client=client)


def test_a_page_with_no_article_in_it_suggests_pasting() -> None:
    """Sites that render with JavaScript come back empty, and should say so."""
    empty = "<html><body></body></html>"
    with (
        fake_client(lambda request: httpx.Response(200, html=empty)) as client,
        pytest.raises(sources.SourceError, match="paste"),
    ):
        sources.from_url("https://example.com/spa", client=client)


def test_a_timeout_is_reported_as_one() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    with (
        fake_client(slow) as client,
        pytest.raises(sources.SourceError, match="did not answer in time"),
    ):
        sources.from_url("https://example.com/slow", client=client)


def test_pasted_text_needs_no_network() -> None:
    source = sources.from_text("  An article.  ")
    assert source.kind == "paste"
    assert source.text == "An article."
    assert source.words == 2


def test_pasting_nothing_is_refused() -> None:
    with pytest.raises(sources.SourceError, match="nothing to adapt"):
        sources.from_text("   \n ")


def test_an_article_too_long_to_read_is_refused_before_it_is_paid_for() -> None:
    """A whole encyclopedia entry is a reference work, not a graded reading."""
    long_page = "<html><body><article><p>" + ("word " * 3000) + "</p></article></body></html>"
    with (
        fake_client(lambda request: httpx.Response(200, html=long_page)) as client,
        pytest.raises(sources.SourceError, match="the limit is"),
    ):
        sources.from_url("https://example.com/huge", client=client)


def test_pasting_something_too_long_is_refused_the_same_way() -> None:
    with pytest.raises(sources.SourceError, match="the limit is"):
        sources.from_text("word " * (sources.MAX_WORDS + 1))
