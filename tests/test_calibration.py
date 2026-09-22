"""Tests for the shipped calibration, against the real word lists.

Every other test here runs on a miniature NGSL, because they are about the band
*logic* and should not break when somebody publishes a new edition of the list.
These are the opposite: they check the numbers in ``data/bands.toml`` against the
real data, because on this product the calibration is not configuration, it is
the claim. A coverage percentage that is quietly wrong is worse than no
percentage, since the reader has no way to tell.

They exist because of a real miss. The NGSL lists ``they``, ``this``, ``he`` and
``a`` as headwords; spaCy leaves ``their``, ``these``, ``his`` and ``an`` as
themselves. Thirteen of the most common words in English matched nothing, fell
through to the Zipf scale -- which had no floor above B2 -- and were reported as
B2. Every text containing "my" or "your" lost coverage for it.
"""

from __future__ import annotations

import pytest

from degrau.lexicon import Band, band_for, coverage, load_bands, load_ngsl
from degrau.lexicon.models import LEVELS, is_within, level_index

#: Forms the lemmatiser leaves alone and the NGSL files under another headword.
FUNCTION_WORDS = [
    "their",
    "these",
    "those",
    "its",
    "his",
    "her",
    "them",
    "us",
    "am",
    "an",
    "my",
    "your",
    "our",
]


@pytest.mark.parametrize("word", FUNCTION_WORDS)
def test_a_basic_function_word_is_never_above_a1(word: str) -> None:
    band, _zipf = band_for(word)
    assert is_within(band, Band.A1), (
        f"{word!r} came out as {band.value}. It is one of the most common words "
        "in English; if this fails, the Zipf floors in data/bands.toml no longer "
        "cover the forms the lemmatiser does not reduce."
    )


def test_a_sentence_of_nothing_but_common_words_is_fully_covered() -> None:
    """The whole point of the metric, on a text that cannot be anything but A1."""
    # Every content word here is inside the first 500 of the NGSL, so anything
    # this reports as above level came from the function words.
    report = coverage("My friend and I saw their house. These are his books.", "A1")
    assert report.out_of_level == []
    assert report.coverage_pct == 1.0


@pytest.mark.parametrize("word", ["bee", "poison", "honey", "carbon", "pesticide"])
def test_an_uncommon_content_word_stays_above_a1(word: str) -> None:
    """The other direction: the floors must not wave everything through."""
    band, _zipf = band_for(word)
    assert not is_within(band, Band.A1), f"{word!r} came out as {band.value}"


def test_the_zipf_floors_descend_with_the_scale() -> None:
    """A1 must demand a higher frequency than A2, and so on down."""
    floors = dict(load_bands().zipf_floors)
    ordered = [floors[band] for band in LEVELS if band in floors]
    assert ordered == sorted(ordered, reverse=True), floors


def test_the_ngsl_rank_ceilings_cover_the_whole_list() -> None:
    """A rank past the last ceiling would silently fall off the scale."""
    table = load_bands()
    ranks = load_ngsl()
    assert max(ranks.values()) <= table.ngsl_ceilings[-1][1]
    assert len(ranks) >= 2_800, "the NGSL should be about 2809 lemmas"


def test_the_list_itself_lands_where_its_ceilings_say() -> None:
    """Guards the seam between data/ngsl.csv and data/bands.toml."""
    table = load_bands()
    ranks = load_ngsl()
    by_rank = {rank: lemma for lemma, rank in ranks.items()}

    previous = 0
    for band, ceiling in table.ngsl_ceilings:
        for rank in (previous + 1, ceiling):
            lemma = by_rank.get(rank)
            if lemma is None:
                continue
            assert band_for(lemma)[0] is band, f"rank {rank} ({lemma}) is not {band.value}"
        previous = ceiling


def test_an_unknown_string_is_the_hardest_band_rather_than_a_crash() -> None:
    band, zipf = band_for("qqzzxwv")
    assert band is Band.C2
    assert zipf == 0.0
    assert level_index(band) == len(LEVELS) - 1
