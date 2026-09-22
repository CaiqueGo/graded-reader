"""Turning a word into a CEFR band.

Two sources, in order. The NGSL is a ranked list of the 2809 words that cover
most of general English; a rank maps straight onto a band. Everything else falls
back to wordfreq's Zipf scale.

Neither source is a CEFR mapping, and this module does not pretend otherwise.
The thresholds come from ``bands.toml`` so that recalibrating is editing a file,
not editing code and re-reading this docstring to see what you broke.
"""

from __future__ import annotations

import csv
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from wordfreq import zipf_frequency

from graded_reader import config
from graded_reader.lexicon.models import LEVELS, Band


class LexiconError(Exception):
    """Expected failure while reading the word lists, with a message for the user."""


@dataclass(frozen=True)
class BandTable:
    """The thresholds, loaded from ``bands.toml``.

    ``ngsl_ceilings`` and ``zipf_floors`` are kept in scale order so the lookup
    can stop at the first match instead of trusting the file's key order.
    """

    ngsl_ceilings: tuple[tuple[Band, int], ...]
    zipf_floors: tuple[tuple[Band, float], ...]
    coverage_thresholds: dict[Band, float]

    def threshold_for(self, level: Band) -> float:
        """Minimum coverage for a text to count as being at ``level``."""
        try:
            return self.coverage_thresholds[level]
        except KeyError:
            raise LexiconError(f"bands.toml has no [coverage] entry for {level.value}") from None


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise LexiconError(f"thresholds file not found: {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise LexiconError(f"{path} is not valid TOML: {error}") from error


def _section(data: dict[str, object], name: str, path: Path) -> dict[str, object]:
    section = data.get(name)
    if not isinstance(section, dict):
        raise LexiconError(f"{path} is missing the [{name}] section")
    return section


def load_bands(path: Path | None = None) -> BandTable:
    """Read the thresholds. Cached per resolved path.

    The path is resolved *before* the cache is consulted. Caching on the
    ``None`` default instead would pin the first data directory ever used and
    silently ignore a later GRADED_READER_DATA_DIR -- which is exactly what a test does.
    """
    return _load_bands(path or config.bands_path())


@cache
def _load_bands(path: Path) -> BandTable:
    data = _read_toml(path)

    ngsl = _section(data, "ngsl", path)
    zipf = _section(data, "zipf", path)
    coverage_section = _section(data, "coverage", path)

    ceilings: list[tuple[Band, int]] = []
    floors: list[tuple[Band, float]] = []
    thresholds: dict[Band, float] = {}
    for band in LEVELS:
        if band.value in ngsl:
            ceilings.append((band, int(str(ngsl[band.value]))))
        if band.value in zipf:
            floors.append((band, float(str(zipf[band.value]))))
        if band.value in coverage_section:
            thresholds[band] = float(str(coverage_section[band.value]))

    if not ceilings:
        raise LexiconError(f"{path} defines no NGSL rank ceilings under [ngsl]")
    if not floors:
        raise LexiconError(f"{path} defines no Zipf floors under [zipf]")

    return BandTable(
        ngsl_ceilings=tuple(ceilings),
        # Highest floor first: the lookup takes the first band the word clears.
        zipf_floors=tuple(sorted(floors, key=lambda item: item[1], reverse=True)),
        coverage_thresholds=thresholds,
    )


def load_ngsl(path: Path | None = None) -> dict[str, int]:
    """Read the NGSL into ``lemma -> rank``. Cached per resolved path.

    The published file is ``Lemma,SFI Rank,SFI,Adjusted Frequency per Million``.
    Only the first two columns matter here.
    """
    return _load_ngsl(path or config.ngsl_path())


@cache
def _load_ngsl(path: Path) -> dict[str, int]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise LexiconError(
            f"NGSL not found at {path}. Download NGSL_12_stats.csv from "
            "newgeneralservicelist.com and save it there as ngsl.csv."
        ) from None

    ranks: dict[str, int] = {}
    for row in csv.DictReader(text.splitlines()):
        lemma = (row.get("Lemma") or "").strip().casefold()
        raw_rank = (row.get("SFI Rank") or "").strip()
        if not lemma or not raw_rank:
            continue
        try:
            rank = int(raw_rank)
        except ValueError:
            continue
        # First rank wins: the list is already sorted, and a duplicate lemma
        # should not silently get demoted to the worse of its two ranks.
        ranks.setdefault(lemma, rank)

    if not ranks:
        raise LexiconError(f"{path} has no usable rows; expected 'Lemma' and 'SFI Rank' columns")
    return ranks


def band_for(
    lemma: str,
    *,
    table: BandTable | None = None,
    ranks: dict[str, int] | None = None,
) -> tuple[Band, float | None]:
    """Band of a lemma, plus its Zipf when the band came from wordfreq.

    Returns the Zipf as ``None`` for NGSL hits, because there the rank is the
    evidence and the Zipf was never consulted. Carrying a number the decision did
    not use is how a report starts lying.
    """
    table = table or load_bands()
    ranks = ranks if ranks is not None else load_ngsl()

    rank = ranks.get(lemma)
    if rank is not None:
        for band, ceiling in table.ngsl_ceilings:
            if rank <= ceiling:
                return band, None
        # Ranked beyond the last ceiling: the file grew past what bands.toml
        # describes. Treat it as the hardest listed band rather than guessing.
        return table.ngsl_ceilings[-1][0], None

    zipf = zipf_frequency(lemma, "en")
    for band, floor in table.zipf_floors:
        if zipf >= floor:
            return band, zipf
    return Band.C2, zipf
