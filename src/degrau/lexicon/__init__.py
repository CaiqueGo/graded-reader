"""The vocabulary layer: bands, lemmatisation and coverage.

This is the core the rest of the project is built on, and it knows nothing about
HTTP, the database or the CLI.
"""

from __future__ import annotations

from degrau.lexicon.analysis import Token, coverage, lemmatize, nlp, tokenize
from degrau.lexicon.bands import BandTable, LexiconError, band_for, load_bands, load_ngsl
from degrau.lexicon.models import (
    LEVELS,
    Band,
    CoverageReport,
    WordCount,
    is_within,
    level_index,
    parse_level,
)

__all__ = [
    "LEVELS",
    "Band",
    "BandTable",
    "CoverageReport",
    "LexiconError",
    "Token",
    "WordCount",
    "band_for",
    "coverage",
    "is_within",
    "lemmatize",
    "level_index",
    "load_bands",
    "load_ngsl",
    "nlp",
    "parse_level",
    "tokenize",
]
