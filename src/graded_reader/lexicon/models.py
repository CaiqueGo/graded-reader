"""The types of the lexicon.

The whole product rests on one claim: that a level can be *verified* instead of
asked for. These types are the shape of that verification -- what a word's band
is, and what a text scored against a target level.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Band(StrEnum):
    """Where a word sits on the CEFR scale.

    ``NA`` is not a level. It marks tokens that carry no vocabulary load for a
    learner -- proper nouns, numbers, acronyms. A text about Reykjavik is not
    harder because of the word Reykjavik, so those tokens are counted separately
    and kept out of the coverage fraction entirely.
    """

    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"
    NA = "NA"


#: The bands that form a scale, in order. ``Band.NA`` is deliberately absent.
LEVELS: tuple[Band, ...] = (Band.A1, Band.A2, Band.B1, Band.B2, Band.C1, Band.C2)


def level_index(band: Band) -> int:
    """Position of a band on the scale, for comparison.

    Raises ``ValueError`` for ``Band.NA``, which has no position: asking whether
    a proper noun is above or below A2 is a question with no answer, and silently
    returning a number would hide the bug that asked it.
    """
    if band is Band.NA:
        raise ValueError("Band.NA has no position on the CEFR scale")
    return LEVELS.index(band)


def is_within(band: Band, target: Band) -> bool:
    """True when a word at ``band`` is at or below the ``target`` level."""
    if band is Band.NA:
        return True
    return level_index(band) <= level_index(target)


def parse_level(value: Band | str) -> Band:
    """Accept 'a1', 'A1' or ``Band.A1`` and return a real level.

    Rejects ``NA``: it is a marker, never a target you can adapt a text to.
    """
    if isinstance(value, Band):
        band = value
    else:
        try:
            band = Band(value.strip().upper())
        except ValueError:
            known = ", ".join(b.value for b in LEVELS)
            raise ValueError(f"unknown level {value!r}; expected one of {known}") from None
    if band is Band.NA:
        raise ValueError("NA is not a target level")
    return band


class WordCount(BaseModel):
    """One lemma and how often it showed up in a text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lemma: str = Field(description="Base form, lowercase. The identity of a flashcard.")
    display: str = Field(description="How the word first appeared in the text.")
    band: Band = Field(description="CEFR band assigned by the lexicon.")
    count: int = Field(gt=0, description="Occurrences in this text.")
    zipf: float | None = Field(
        default=None,
        description="Zipf frequency in general English, when it came from wordfreq.",
    )


class CoverageReport(BaseModel):
    """What a text scored against a target level.

    ``coverage_pct`` divides by ``counted_tokens``, not ``total_tokens``: the NA
    tokens are outside the question being asked.
    """

    model_config = ConfigDict(extra="forbid")

    level: Band = Field(description="The level the text was measured against.")
    total_tokens: int = Field(ge=0, description="Word tokens found, including NA.")
    counted_tokens: int = Field(ge=0, description="Tokens that count: total minus NA.")
    within_tokens: int = Field(ge=0, description="Counted tokens at or below the target band.")
    na_tokens: int = Field(ge=0, description="Proper nouns, numbers and acronyms.")
    coverage_pct: float = Field(ge=0.0, le=1.0, description="within_tokens / counted_tokens.")
    threshold: float = Field(gt=0.0, le=1.0, description="Minimum coverage for this level.")
    out_of_level: list[WordCount] = Field(
        default_factory=list,
        description="Lemmas above the target band, most frequent first.",
    )
    candidates: list[WordCount] = Field(
        default_factory=list,
        description="Out-of-level lemmas the user does not know yet: the cards to offer.",
    )

    @property
    def meets_threshold(self) -> bool:
        """Whether the text is at the level it claims to be."""
        return self.coverage_pct >= self.threshold

    @property
    def out_of_level_tokens(self) -> int:
        return self.counted_tokens - self.within_tokens
