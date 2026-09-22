"""Adapting a text by calling the Claude Code CLI.

This is the second implementation of ``Adapter``, and the one section 14 of the
MVP was anticipating. ``InboxAdapter`` picks up what a person already asked
Claude Code to write; this one asks for it.

Three decisions are load-bearing, and all three were found by trying the
obvious thing first and watching it fail.

**The child process gets no tools.** ``--tools ""``. It cannot read a file,
write one or run a command -- it reads a prompt and returns text. That removes
the permission problem entirely, and it removes something worse: the article
being adapted is untrusted text, and an agent with Write access reading a
hostile page is a different risk from a model with no hands. It is also about
three times lighter on the plan's usage, because there is no tool loop.

It also sidesteps a real defect. Running the ``/adapt`` slash command through
``claude -p`` *with* tools fails reproducibly on CLI 2.0.76 with
``API Error 400: tool_use ids must be unique``, on the first assistant message,
on both Opus and Sonnet, with and without MCP servers. Without tools there are
no tool_use blocks and the problem cannot arise.

**The prompt goes in on stdin.** Passed as an argument it is silently truncated
past the Windows command-line limit -- the process exits 0 and prints nothing,
which is the worst possible failure. stdin has no such limit on any platform.

**The reply is unwrapped before parsing.** The model returns its JSON inside a
markdown fence however firmly it is told not to. ``--json-schema`` would be the
right answer and currently returns a 400, so the fence is simply removed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from degrau.adapters.base import SCHEMA_VERSION, AdaptedText
from degrau.profile import Profile, render

#: How long to wait for one adaptation. Runs take twenty to sixty seconds; past
#: this something is wrong and the reader should be told rather than left
#: watching a spinner.
TIMEOUT_SECONDS = 300

#: Where the CLI writes its own answer, and what it wraps it in.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class AdapterError(Exception):
    """Expected failure while adapting, with a message for the user."""


@dataclass(frozen=True)
class Run:
    """What one call to the CLI produced."""

    text: str
    cost_usd: float = 0.0
    seconds: float = 0.0


#: The seam the tests replace. A real adaptation costs plan usage and takes a
#: minute, so nothing in the suite is allowed to make one.
Runner = Callable[[str, Path | None], Run]


def find_cli() -> str:
    """Locate the claude executable, or say what to do about it.

    ``shutil.which`` is what makes this portable: it finds ``claude.CMD`` on
    Windows and ``claude`` elsewhere, and hands back a path that ``subprocess``
    can run without a shell.
    """
    found = shutil.which("claude")
    if not found:
        raise AdapterError(
            "the claude command is not on PATH. Install Claude Code and sign in; "
            "this adapter drives the CLI you already use, with the same account."
        )
    return found


def run_cli(prompt: str, cwd: Path | None = None) -> Run:
    """Send a prompt to the CLI with no tools, and return what came back."""
    command = [find_cli(), "-p", "--tools", "", "--output-format", "json"]
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_SECONDS,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as error:
        raise AdapterError(
            f"the adaptation did not finish within {TIMEOUT_SECONDS} seconds"
        ) from error
    except OSError as error:
        raise AdapterError(f"could not start the claude command: {error}") from error

    if not completed.stdout.strip():
        detail = completed.stderr.strip()[:300] or f"exit code {completed.returncode}"
        raise AdapterError(f"the claude command returned nothing: {detail}")

    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AdapterError(f"could not read the CLI's reply: {error}") from error

    if envelope.get("is_error"):
        raise AdapterError(f"the model run failed: {str(envelope.get('result', ''))[:300]}")

    return Run(
        text=str(envelope.get("result", "")),
        cost_usd=float(envelope.get("total_cost_usd") or 0.0),
        seconds=float(envelope.get("duration_ms") or 0) / 1000,
    )


def unfence(reply: str) -> str:
    """Strip the markdown fence the model puts around its JSON."""
    match = _FENCE.match(reply.strip())
    return match.group(1) if match else reply.strip()


def build_prompt(source_text: str, profile: Profile, *, title_hint: str = "") -> str:
    """The whole instruction, assembled here rather than in a command file.

    The profile block is the same one ``degrau profile`` prints, so the manual
    loop and this one are asking for the same thing in the same words. If they
    ever drift, the difference would show up as adaptations that are subtly
    worse through one door than the other, and nothing would report it.
    """
    hint = f'\nA title has been suggested by the source: "{title_hint}".\n' if title_hint else ""
    return f"""You are adapting an English text for a graded reader. Your reply is
read by a program, not a person.

{render(profile)}

## The source text
{hint}
{source_text}

## What to return

A single JSON object and nothing else, with exactly these keys:

- `title`: a short English title.
- `adapted_text`: the adapted English. Separate paragraphs with a blank line.
- `glossary`: a list of `{{en, pt, example_en, example_pt}}`. Every word above
  the target level belongs here, with a Portuguese translation and an English
  example sentence that contains the word.
- `questions`: 3 to 5 objects of `{{q, a}}`, answerable from the adapted text
  alone.

Keep the meaning and the order of the source. Do not invent facts. Do not add
opinion. If a paragraph is too hard, rewrite it -- do not delete it.
"""


class ClaudeCliAdapter:
    """``Adapter`` backed by the Claude Code CLI on this machine.

    It uses the same login as the terminal does, so it spends the same plan
    rather than an API key. The ``runner`` argument is the seam for tests.
    """

    def __init__(self, *, runner: Runner | None = None, cwd: Path | None = None) -> None:
        self.runner: Runner = runner or run_cli
        self.cwd = cwd
        self.last_run: Run | None = None

    def adapt(self, source_text: str, profile: Profile) -> AdaptedText:
        return self.adapt_source(source_text, profile)

    def adapt_source(
        self,
        source_text: str,
        profile: Profile,
        *,
        kind: str = "paste",
        value: str = "",
        title_hint: str = "",
    ) -> AdaptedText:
        """Adapt a text and return it in the shape the importer already accepts."""
        if not source_text.strip():
            raise AdapterError("there is no source text to adapt")

        prompt = build_prompt(source_text, profile, title_hint=title_hint)
        run = self.runner(prompt, self.cwd)
        self.last_run = run

        try:
            payload: Any = json.loads(unfence(run.text))
        except json.JSONDecodeError as error:
            raise AdapterError(
                f"the model did not return JSON: {error}. It said: {run.text[:200]}"
            ) from error

        if not isinstance(payload, dict):
            raise AdapterError(
                f"expected a JSON object from the model, got {type(payload).__name__}"
            )

        document = {
            "schema": SCHEMA_VERSION,
            "level": profile.level.value,
            "title": payload.get("title", "") or title_hint,
            "source": {"kind": kind, "value": value, "original_text": source_text},
            "adapted_text": payload.get("adapted_text", ""),
            "glossary": payload.get("glossary", []),
            "questions": payload.get("questions", []),
            "prompt_used": prompt,
        }
        try:
            return AdaptedText.model_validate(document)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors()
            )
            raise AdapterError(
                f"the model's reply does not fit the contract: {problems}"
            ) from error
