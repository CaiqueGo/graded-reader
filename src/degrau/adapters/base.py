"""The boundary between whatever adapts a text and the app that stores it.

This is the most important contract in the project. On one side, today, sits
Claude Code writing a JSON file into ``inbox/``; tomorrow it could be an API
call. On this side sits everything that does not care which of those it was.

One abstraction, and deliberately only one. There is no repository interface, no
renderer interface and no transcription interface here, because there is exactly
one implementation of each of those and inventing the second one on paper is how
a small project acquires the shape of a large one without the reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from degrau.profile import Profile

#: The version of the JSON contract this code understands.
SCHEMA_VERSION = 1


class GlossaryEntry(BaseModel):
    """One word the adaptation chose to teach, with its example sentence.

    ``example_en`` is what ends up on the front of the flashcard with the word
    blanked out, so an entry without one produces a poorer card -- but not a
    broken import, which is why it is optional.
    """

    model_config = ConfigDict(extra="ignore")

    en: str
    pt: str = ""
    example_en: str = ""
    example_pt: str = ""


class Question(BaseModel):
    """A comprehension question and its answer."""

    model_config = ConfigDict(extra="ignore")

    q: str
    a: str = ""


class Source(BaseModel):
    """Where the original came from, and the original itself.

    The original text is kept whole. Re-reading it after understanding the
    adapted version is half the value of the method, so losing it would quietly
    remove a feature.
    """

    model_config = ConfigDict(extra="ignore")

    kind: str = "paste"
    value: str = ""
    original_text: str = ""


class AdaptedText(BaseModel):
    """A text rewritten for a level, as it arrives from the adapter.

    ``extra="ignore"`` on purpose: a producer that adds a field must not break
    an importer that predates it. The three fields that genuinely cannot be
    guessed -- schema, level and the text itself -- are required, and everything
    else has a defensible default.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_version: int = Field(alias="schema")
    level: str
    adapted_text: str = Field(min_length=1)
    title: str = ""
    source: Source = Field(default_factory=Source)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    prompt_used: str = ""
    generated_at: datetime | None = None


class Adapter(Protocol):
    """Turns a source text into an adapted one, for a profile.

    ``InboxAdapter`` implements this today by reading a file a model already
    wrote. ``ApiAdapter`` will implement it in v2 by making the call itself. The
    rest of the app is written against this and should not need to change.
    """

    def adapt(self, source_text: str, profile: Profile) -> AdaptedText: ...
