"""What the reading screen needs, assembled without knowing HTTP exists.

The templates are handed these types and never an ORM row. That is not
ceremony: a template that reaches into a database row decides, by accident,
which columns are part of the interface, and the first schema change then breaks
a page rather than a function. It also means this whole module is testable
without a client, which is why the route tests the MVP tells us not to write are
not missed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from graded_reader.lexicon import LexiconError, Token, band_for, load_bands, tokenize
from graded_reader.lexicon.models import parse_level
from graded_reader.store import texts, words

#: A blank line separates paragraphs. Section 7 of the MVP writes an adapted
#: text as two paragraphs divided by an empty line, so a lone newline inside
#: a paragraph is the source file's hard wrapping and not the author's.
#: Rendering it as a break would reflow the reader's text to whatever width
#: the file happened to be written at.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n\s*")
_WHITESPACE_RUN = re.compile(r"\s+")


def split_paragraphs(text: str) -> list[str]:
    """The text as paragraphs, each with its internal whitespace collapsed."""
    return [
        cleaned
        for chunk in _PARAGRAPH_BREAK.split(text.strip())
        if (cleaned := _WHITESPACE_RUN.sub(" ", chunk).strip())
    ]


class ReadingError(Exception):
    """Expected failure while opening a text, with a message for the user."""


class RenderedToken(BaseModel):
    """One token as the reader sees it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    whitespace: str
    lemma: str = ""
    in_deck: bool = False
    above_level: bool = False

    @property
    def clickable(self) -> bool:
        """Names, numbers and punctuation are not offers to learn anything."""
        return bool(self.lemma)


class GlossaryItem(BaseModel):
    """A glossary entry, plus whether it is already saved."""

    model_config = ConfigDict(extra="forbid")

    en: str
    pt: str = ""
    example_en: str = ""
    example_pt: str = ""
    lemma: str = ""
    in_deck: bool = False


class QuestionItem(BaseModel):
    """A comprehension question with its answer hidden until asked for."""

    model_config = ConfigDict(extra="forbid")

    index: int
    q: str
    a: str = ""


class TextSummary(BaseModel):
    """One row of the library list."""

    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    level: str
    coverage_pct: float | None = None
    threshold: float | None = None
    out_of_level_count: int = 0
    created_at: str = ""

    @property
    def meets_threshold(self) -> bool:
        """Whether the text is at the level it claims to be.

        Unmeasured counts as fine. Flagging a text whose coverage was never
        computed would be an accusation the app cannot support.
        """
        if self.coverage_pct is None or self.threshold is None:
            return True
        return self.coverage_pct >= self.threshold


class ReadingView(BaseModel):
    """Everything the reading page shows."""

    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    level: str
    coverage_pct: float | None = None
    source_kind: str = ""
    source_value: str = ""
    has_original: bool = False
    paragraphs: list[list[RenderedToken]] = Field(default_factory=list)
    glossary: list[GlossaryItem] = Field(default_factory=list)
    questions: list[QuestionItem] = Field(default_factory=list)
    above_level: list[str] = Field(default_factory=list)
    saved_count: int = 0

    @property
    def tokens(self) -> list[RenderedToken]:
        """Every token, paragraphs flattened. The reader never sees this order."""
        return [token for paragraph in self.paragraphs for token in paragraph]

    @property
    def coverage_label(self) -> str:
        if self.coverage_pct is None:
            return "not measured"
        return f"{self.coverage_pct * 100:.0f}% within {self.level}"


class Excerpt(BaseModel):
    """A stretch of text the reader selected, ready to become a card.

    The words are listed so one of them can be picked as the target. Which word
    a sentence is teaching is the reader's judgement -- the same sentence can be
    mined for its verb, its preposition or its idiom, and only they know which
    one sent them to select it.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    text_id: int | None = None
    words: list[WordChoice] = Field(default_factory=list)
    already_saved: bool = False

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class WordChoice(BaseModel):
    """One candidate target inside a selected excerpt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lemma: str
    display: str
    band: str = ""
    above_level: bool = False
    in_deck: bool = False


class WordCard(BaseModel):
    """The side panel for one clicked word."""

    model_config = ConfigDict(extra="forbid")

    lemma: str
    display: str
    band: str = ""
    pt: str = ""
    example_en: str = ""
    example_pt: str = ""
    in_deck: bool = False
    text_id: int | None = None


def _loads(raw: str) -> list[dict[str, Any]]:
    """Parse a stored JSON column, treating damage as emptiness.

    These columns were written by this app from validated models. If one is
    unreadable the honest thing on a reading screen is to show the text without
    its glossary, not to refuse to open a text the reader can still read.
    """
    try:
        parsed = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _lemma_of(term: str) -> str:
    """The lemma a glossary headword maps to.

    Multi-word entries keep their own surface form as the lemma: 'give up' is one
    card, not a card for 'give'. Section 13 of the MVP calls this out as the
    known weak spot, and this is the half of it that can be handled here.
    """
    cleaned = term.strip().casefold()
    if not cleaned:
        return ""
    if " " in cleaned:
        return cleaned
    found = [token.lemma for token in tokenize(cleaned) if token.is_word]
    return found[0] if found else cleaned


def _threshold_for(level: str) -> float | None:
    """The coverage a text at this level has to clear, or None if unknown."""
    try:
        return load_bands().threshold_for(parse_level(level))
    except (LexiconError, ValueError):
        return None


def summaries(session: Session, limit: int = 50) -> list[TextSummary]:
    """The library list, most recent first."""
    return [
        TextSummary(
            id=row.id or 0,
            title=row.title or "(untitled)",
            level=row.level,
            coverage_pct=row.coverage_pct,
            threshold=_threshold_for(row.level),
            out_of_level_count=len(_loads(row.out_of_level)),
            created_at=row.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
        )
        for row in texts.recent(session, limit=limit)
    ]


def build_view(session: Session, text_id: int) -> ReadingView:
    """Assemble the reading page for one text."""
    row = texts.by_id(session, text_id)
    if row is None:
        raise ReadingError(f"no text with id {text_id}")

    parsed = [tokenize(chunk) for chunk in split_paragraphs(row.adapted_text)]
    above = {entry.get("lemma", "") for entry in _loads(row.out_of_level)}
    glossary_raw = _loads(row.glossary_json)

    wanted = {token.lemma for chunk in parsed for token in chunk if token.is_word}
    wanted |= {_lemma_of(str(entry.get("en", ""))) for entry in glossary_raw}
    in_deck = words.lemmas_in(session, {lemma for lemma in wanted if lemma})

    return ReadingView(
        id=row.id or 0,
        title=row.title or "(untitled)",
        level=row.level,
        coverage_pct=row.coverage_pct,
        source_kind=row.source_kind,
        source_value=row.source_value,
        has_original=bool(row.original_text.strip()),
        paragraphs=[[_render(token, in_deck, above) for token in chunk] for chunk in parsed],
        glossary=[_glossary_item(entry, in_deck) for entry in glossary_raw],
        questions=[
            QuestionItem(index=n, q=str(entry.get("q", "")), a=str(entry.get("a", "")))
            for n, entry in enumerate(_loads(row.questions_json))
            if entry.get("q")
        ],
        above_level=sorted(lemma for lemma in above if lemma),
        saved_count=len(in_deck),
    )


def _render(token: Token, in_deck: set[str], above: set[str]) -> RenderedToken:
    return RenderedToken(
        text=token.text,
        whitespace=token.whitespace,
        lemma="" if token.is_na else token.lemma,
        in_deck=bool(token.lemma) and token.lemma in in_deck,
        above_level=bool(token.lemma) and token.lemma in above,
    )


def _glossary_item(entry: dict[str, Any], in_deck: set[str]) -> GlossaryItem:
    en = str(entry.get("en", ""))
    lemma = _lemma_of(en)
    return GlossaryItem(
        en=en,
        pt=str(entry.get("pt", "")),
        example_en=str(entry.get("example_en", "")),
        example_pt=str(entry.get("example_pt", "")),
        lemma=lemma,
        in_deck=lemma in in_deck,
    )


def build_excerpt(session: Session, selection: str, *, text_id: int | None = None) -> Excerpt:
    """Turn a selection into something the reader can turn into a card.

    The excerpt is offered exactly as selected, whitespace flattened. Trimming
    it to a sentence boundary would be second-guessing: a clause, an idiom or
    half a line can each be the thing worth rehearsing.
    """
    from graded_reader.deck import normalise_sentence

    cleaned = normalise_sentence(selection)
    if not cleaned:
        raise ReadingError("nothing selected")

    row = texts.by_id(session, text_id) if text_id is not None else None
    above = {entry.get("lemma", "") for entry in _loads(row.out_of_level)} if row else set()

    seen: dict[str, WordChoice] = {}
    for token in tokenize(cleaned):
        if not token.is_word or token.is_na or token.lemma in seen:
            continue
        seen[token.lemma] = WordChoice(
            lemma=token.lemma,
            display=token.text,
            band=band_for(token.lemma)[0].value,
            above_level=token.lemma in above,
        )

    in_deck = words.lemmas_in(session, set(seen))
    choices = [
        choice.model_copy(update={"in_deck": choice.lemma in in_deck}) for choice in seen.values()
    ]
    # The words the text itself flagged as above level come first: those are the
    # ones the reader is most likely to have selected the sentence for.
    choices.sort(key=lambda choice: (not choice.above_level, choice.lemma))

    return Excerpt(
        text=cleaned,
        text_id=text_id,
        words=choices,
        already_saved=words.by_sentence(session, cleaned) is not None,
    )


def build_card(session: Session, lemma: str, *, text_id: int | None = None) -> WordCard:
    """The side panel for a word, drawing on the glossary of the text it is in."""
    normalised = lemma.strip().casefold()
    if not normalised:
        raise ReadingError("no word given")

    existing = words.by_lemma(session, normalised)
    entry: dict[str, Any] = {}
    if text_id is not None:
        row = texts.by_id(session, text_id)
        if row is not None:
            entry = next(
                (
                    item
                    for item in _loads(row.glossary_json)
                    if _lemma_of(str(item.get("en", ""))) == normalised
                ),
                {},
            )

    band = existing.band if existing is not None else band_for(normalised)[0].value
    return WordCard(
        lemma=normalised,
        display=existing.display if existing is not None else lemma,
        band=band or "",
        pt=(existing.pt if existing is not None else None) or str(entry.get("pt", "")),
        example_en=(existing.example_en if existing is not None else None)
        or str(entry.get("example_en", "")),
        example_pt=(existing.example_pt if existing is not None else None)
        or str(entry.get("example_pt", "")),
        in_deck=existing is not None,
        text_id=text_id,
    )
