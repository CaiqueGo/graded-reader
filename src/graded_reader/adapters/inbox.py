"""Reading an adapted text off the filesystem.

The v1 adapter. Claude Code has already done the expensive part and left a JSON
file in ``inbox/``; this reads it and checks that it is what it claims to be.

The validation here is deliberately lenient about everything except the three
fields that cannot be reconstructed. A file that is missing its glossary still
carries a usable text, and refusing it would throw away a model call to enforce
a preference.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from graded_reader.adapters.base import SCHEMA_VERSION, AdaptedText
from graded_reader.lexicon.models import parse_level
from graded_reader.profile import Profile


class InboxError(Exception):
    """A file in the inbox is not a usable adapted text, with the reason why."""


def parse(raw: str) -> AdaptedText:
    """Turn the contents of an inbox file into an adapted text, or say why not.

    Every failure path produces a sentence a person can act on. These messages
    are written into the ``.error.txt`` that sits next to the rejected file, and
    that note is the only thing standing between a bad file and a silent gap in
    the library.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InboxError(f"not valid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise InboxError(f"expected a JSON object at the top level, found {type(payload).__name__}")

    version = payload.get("schema")
    if version is None:
        raise InboxError("missing required field 'schema'")
    if version != SCHEMA_VERSION:
        raise InboxError(
            f"schema {version!r} is not supported; this build understands schema {SCHEMA_VERSION}"
        )

    try:
        adapted = AdaptedText.model_validate(payload)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise InboxError(f"does not match the contract: {problems}") from error

    try:
        parse_level(adapted.level)
    except ValueError as error:
        raise InboxError(str(error)) from error

    return adapted


def read(path: Path) -> AdaptedText:
    """Parse one inbox file by path."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InboxError(f"file not found: {path}") from None
    except UnicodeDecodeError as error:
        raise InboxError(f"not UTF-8: {error}") from error
    return parse(raw)


def pending(inbox: Path) -> list[Path]:
    """The JSON files waiting to be imported, oldest first.

    Only the top level. ``processed/`` and ``rejected/`` live inside the inbox
    and must not be picked up again on the next run.
    """
    if not inbox.is_dir():
        return []
    return sorted((item for item in inbox.glob("*.json") if item.is_file()), key=lambda p: p.name)


class InboxAdapter:
    """The ``Adapter`` implementation for v1.

    It satisfies the protocol without doing the adapting: by the time this runs,
    a model has already written the file. ``source_text`` and ``profile`` are
    accepted and ignored, which is honest about what v1 is -- a human-driven loop
    with the app on the receiving end -- and keeps the call site identical to
    what the v2 ``ApiAdapter`` will need.
    """

    def __init__(self, inbox: Path) -> None:
        self.inbox = inbox

    def adapt(self, source_text: str, profile: Profile) -> AdaptedText:
        waiting = pending(self.inbox)
        if not waiting:
            raise InboxError(f"nothing waiting in {self.inbox}. Run /adapt in Claude Code first.")
        return read(waiting[0])
