"""The context block that goes into an adaptation prompt.

What a model is bad at is not English frequency -- it has a good sense of which
words are common. What it cannot know is *you*: which words above your level you
have already earned, which ones are worth introducing next, and which ones are
about to fade unless they show up again this week.

So this block carries the profile and not the word list. Sending the whole 1000
words of A2 would spend the context window restating something the model already
knows, and would push the part it does not know to the bottom, where it gets
skimmed.

Nothing here reads the database or knows a text exists. It takes lists and
returns a string, which is what makes it testable without a fixture.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from graded_reader.lexicon.models import LEVELS, Band, level_index

#: Upper bound on the words we claim you already know above your level. Past
#: roughly this many, the list stops being an exception list and starts being a
#: second vocabulary the model has to hold in mind while writing.
MAX_EXCEPTIONS = 40

#: Upper bound on the words due for reinforcement. They are a nice-to-have in
#: the text, not a requirement, and a long list reads as noise.
MAX_REINFORCEMENT = 10

#: The i+1 window. Below five, a text teaches too little to be worth the run;
#: above eight, the reader spends the text looking things up.
MIN_NEW_WORDS = 5
MAX_NEW_WORDS = 8
DEFAULT_NEW_WORDS = 6


class LevelRules(BaseModel):
    """What a level is allowed to use, as written in ``data/levels.toml``."""

    model_config = ConfigDict(extra="ignore")

    vocabulary: str = ""
    sentences: str = ""
    grammar: str = ""
    guidance: str = ""


class Profile(BaseModel):
    """Everything an adapter needs to know about the reader and the target."""

    model_config = ConfigDict(extra="forbid")

    level: Band
    rules: LevelRules = Field(default_factory=LevelRules)
    exceptions: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    reinforcement: list[str] = Field(default_factory=list)


class ProfileError(Exception):
    """Expected failure while building a profile, with a message for the user."""


def load_levels(path: Path) -> dict[Band, LevelRules]:
    """Read the per-level grammar budget."""
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raise ProfileError(f"levels file not found: {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"{path} is not valid TOML: {error}") from error

    rules: dict[Band, LevelRules] = {}
    for band in LEVELS:
        section = raw.get(band.value)
        if isinstance(section, dict):
            rules[band] = LevelRules.model_validate(section)
    if not rules:
        raise ProfileError(f"{path} defines no levels; expected a [A1] section and friends")
    return rules


def next_band(level: Band) -> Band | None:
    """The band immediately above ``level``, or None at the top of the scale."""
    index = level_index(level)
    if index + 1 >= len(LEVELS):
        return None
    return LEVELS[index + 1]


def clamp_new_words(count: int) -> int:
    """Hold the i+1 window inside the range the method actually works in."""
    return max(MIN_NEW_WORDS, min(MAX_NEW_WORDS, count))


def build(
    level: Band,
    rules: LevelRules,
    *,
    mastered_above: Sequence[str] = (),
    candidates_above: Sequence[str] = (),
    due_soon: Sequence[str] = (),
    new_words: int = DEFAULT_NEW_WORDS,
) -> Profile:
    """Assemble a profile from lists somebody else went and fetched.

    ``candidates_above`` is expected to arrive already ordered by frequency, most
    frequent first: the next word worth learning is the most common one you do
    not have yet, and that ordering is a property of the word list, not of this
    function.
    """
    return Profile(
        level=level,
        rules=rules,
        exceptions=list(mastered_above[:MAX_EXCEPTIONS]),
        targets=list(candidates_above[: clamp_new_words(new_words)]),
        reinforcement=list(due_soon[:MAX_REINFORCEMENT]),
    )


def _bullet_list(words: Sequence[str]) -> str:
    return ", ".join(words) if words else "(none yet)"


def render(profile: Profile) -> str:
    """The block to paste into an adaptation prompt.

    Markdown, because that is what the consumer is. Headings rather than prose so
    that the four sections stay visually separable when this lands in the middle
    of a longer prompt.
    """
    target_band = next_band(profile.level)
    target_label = target_band.value if target_band else profile.level.value
    rules = profile.rules

    return "\n".join(
        [
            f"## Target level: {profile.level.value}",
            "",
            f"**Vocabulary.** {rules.vocabulary}".rstrip(),
            f"**Sentences.** {rules.sentences}".rstrip(),
            "",
            "**Grammar.**",
            rules.grammar.strip(),
            "",
            "**How to write it.**",
            rules.guidance.strip(),
            "",
            "## This reader",
            "",
            f"**Already known, above {profile.level.value}.** Use these freely, "
            "they cost the reader nothing:",
            _bullet_list(profile.exceptions),
            "",
            f"**Teach these ({len(profile.targets)} words, band {target_label}).** "
            "Each must appear in the text and in the glossary:",
            _bullet_list(profile.targets),
            "",
            "**Fading, reuse if it fits naturally.** Do not force them:",
            _bullet_list(profile.reinforcement),
            "",
            "## Not negotiable",
            "",
            "- Keep the meaning and the order of the original. Do not invent facts.",
            "- Every word outside the target level must be in the glossary.",
            "- Do not simplify by deleting: if a paragraph is hard, rewrite it.",
        ]
    )
