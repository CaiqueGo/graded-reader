"""Reading a text and measuring it against a level.

This is where the product's first claim gets enforced. A model told to "write in
A1" will drift and never say so. Here the text is tokenised, every word is
reduced to its base form, every base form gets a band, and the result is a
fraction you can argue with.

Lemmatising matters more than it looks: without it ``run``, ``runs`` and
``running`` are three unknown words and three flashcards, and the coverage number
is wrong in the pessimistic direction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from degrau.lexicon.bands import BandTable, LexiconError, band_for, load_bands, load_ngsl
from degrau.lexicon.models import Band, CoverageReport, WordCount, is_within, parse_level

SPACY_MODEL = "en_core_web_sm"

#: The parser costs time and decides nothing here. The tagger and the attribute
#: ruler stay, because the lemmatiser needs the part of speech to tell the noun
#: ``saw`` from the past of ``see``.
_DISABLED_PIPES = ("parser",)

#: Parts of speech that carry no vocabulary load for a learner.
_NA_POS = frozenset({"PROPN", "NUM", "X", "SYM"})

#: Entity labels that mean "this is a name, not a word to learn".
#:
#: The POS tag alone is not enough, and this is the reason NER stays switched on.
#: ``en_core_web_sm`` tags a capitalised word at the start of a sentence as a
#: common NOUN: "Iceland turns carbon into stone" yields NOUN for Iceland, which
#: would put a country on the flashcard pile and count it against the text's
#: coverage. The entity recogniser gets it right, and -- the part that matters --
#: it stays right in the other direction too: "Water is wet" leaves Water alone.
#:
#: DATE and TIME are deliberately absent. They are built from words a beginner
#: does need: Monday, morning, year.
_NA_ENTS = frozenset(
    {
        "PERSON",
        "NORP",
        "FAC",
        "ORG",
        "GPE",
        "LOC",
        "PRODUCT",
        "EVENT",
        "WORK_OF_ART",
        "LAW",
        "LANGUAGE",
    }
)


@lru_cache(maxsize=1)
def nlp() -> Any:
    """The spaCy pipeline. Loaded once, on first use."""
    try:
        import spacy
    except ImportError:
        raise LexiconError(
            "spaCy is not installed. Run: uv pip install -e '.[lexicon,dev]'"
        ) from None

    try:
        return spacy.load(SPACY_MODEL, disable=list(_DISABLED_PIPES))
    except OSError:
        raise LexiconError(
            f"spaCy model {SPACY_MODEL} is not installed. "
            f"Run: python -m spacy download {SPACY_MODEL}"
        ) from None


def _is_acronym(text: str) -> bool:
    """``NASA`` and ``CO2`` are acronyms; ``I`` and ``Iceland`` are not.

    spaCy tags acronyms inconsistently -- sometimes PROPN, sometimes NOUN -- so
    the shape of the string gets a vote too.
    """
    return len(text) > 1 and text.isupper() and any(char.isalpha() for char in text)


def lemmatize(text: str) -> list[tuple[str, str, bool]]:
    """Split a text into ``(lemma, display, is_na)``, in order of appearance.

    Punctuation and whitespace are dropped: they are not words and counting them
    would quietly inflate every denominator in the report.
    """
    tokens: list[tuple[str, str, bool]] = []
    for token in nlp()(text):
        if token.is_space or token.is_punct:
            continue

        surface: str = token.text
        if (
            token.pos_ in _NA_POS
            or token.ent_type_ in _NA_ENTS
            or token.like_num
            or _is_acronym(surface)
        ):
            tokens.append((surface.casefold(), surface, True))
            continue

        lemma: str = token.lemma_.strip().casefold()
        if not lemma or not any(char.isalpha() for char in lemma):
            continue
        tokens.append((lemma, surface, False))
    return tokens


def coverage(
    text: str,
    level: Band | str,
    known: Iterable[str] = (),
    *,
    table: BandTable | None = None,
) -> CoverageReport:
    """Measure ``text`` against ``level``, given what the reader already knows.

    ``known`` does not move the coverage number. Coverage answers "is this text
    at this level", which is a property of the text; the personal vocabulary only
    decides which of the hard words are worth offering as flashcards.
    """
    target = parse_level(level)
    table = table or load_bands()
    ranks = load_ngsl()
    known_lemmas = {lemma.strip().casefold() for lemma in known}

    counts: Counter[str] = Counter()
    displays: dict[str, str] = {}
    na_tokens = 0

    for lemma, display, is_na in lemmatize(text):
        if is_na:
            na_tokens += 1
            continue
        counts[lemma] += 1
        displays.setdefault(lemma, display)

    counted_tokens = sum(counts.values())
    within_tokens = 0
    out_of_level: list[WordCount] = []

    for lemma, count in counts.items():
        band, zipf = band_for(lemma, table=table, ranks=ranks)
        if is_within(band, target):
            within_tokens += count
            continue
        out_of_level.append(
            WordCount(lemma=lemma, display=displays[lemma], band=band, count=count, zipf=zipf)
        )

    # Most frequent first, then alphabetical: a stable order, and the words that
    # actually get in the way are the ones at the top.
    out_of_level.sort(key=lambda word: (-word.count, word.lemma))

    return CoverageReport(
        level=target,
        total_tokens=counted_tokens + na_tokens,
        counted_tokens=counted_tokens,
        within_tokens=within_tokens,
        na_tokens=na_tokens,
        coverage_pct=(within_tokens / counted_tokens) if counted_tokens else 0.0,
        threshold=table.threshold_for(target),
        out_of_level=out_of_level,
        candidates=[word for word in out_of_level if word.lemma not in known_lemmas],
    )
