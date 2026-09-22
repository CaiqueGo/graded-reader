"""Getting an adapted text into the library, and building the next prompt.

This is the layer that knows about all the others. The lexicon does not know a
database exists, the store does not know what a CEFR band means, and the profile
does not know a text was ever imported. Here they meet.

Order matters in ``import_file`` and it is the reason the function is not
shorter: the database is written and committed *before* the file is moved out of
the inbox. If the move then fails, the next run finds the file again, recognises
the hash and moves it without importing twice. The other order loses the file on
a failed commit, and the file is the only copy.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from degrau import config
from degrau.adapters.base import AdaptedText
from degrau.adapters.inbox import InboxError, pending, read
from degrau.lexicon import CoverageReport, LexiconError, coverage
from degrau.lexicon.bands import band_for, load_bands, load_ngsl
from degrau.lexicon.models import LEVELS, Band, level_index, parse_level
from degrau.profile import LevelRules, Profile, build, load_levels, next_band
from degrau.store import database, texts, words
from degrau.store import settings as settings_store
from degrau.store.models import Text

#: How far ahead a fading word counts as worth reinforcing in the next text.
REINFORCEMENT_HORIZON_DAYS = 3


class ImportAction(StrEnum):
    """What happened to one file."""

    IMPORTED = "imported"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class ImportResult(BaseModel):
    """The outcome of one file, in a shape the CLI and a future API can print."""

    model_config = ConfigDict(extra="forbid")

    action: ImportAction
    source: str
    title: str = ""
    level: str = ""
    text_id: int | None = None
    coverage_pct: float | None = None
    threshold: float | None = None
    meets_threshold: bool | None = None
    out_of_level_count: int = 0
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.action is not ImportAction.REJECTED


def content_hash(adapted_text: str) -> str:
    """Identity of a text, for idempotency.

    Line endings are normalised and the ends trimmed before hashing, so the same
    English coming back with a trailing newline is the same text. Everything
    else counts: a single reworded sentence is a different adaptation and
    deserves its own row.
    """
    normalised = adapted_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _unique_destination(directory: Path, name: str) -> Path:
    """A free path in ``directory``, suffixing rather than overwriting."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for attempt in range(1, 1000):
        candidate = directory / f"{stem}-{attempt}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"cannot find a free name for {name} in {directory}")


def _reject(path: Path, reason: str) -> ImportResult:
    """Move a bad file aside and write down why, keeping the file itself."""
    destination = _unique_destination(config.rejected_dir(), path.name)
    shutil.move(str(path), str(destination))
    note = destination.with_suffix(destination.suffix + ".error.txt")
    note.write_text(
        f"rejected at {datetime.now().isoformat(timespec='seconds')}\n"
        f"original file: {path.name}\n\n"
        f"{reason}\n",
        encoding="utf-8",
    )
    return ImportResult(action=ImportAction.REJECTED, source=path.name, reason=reason)


def _to_row(adapted: AdaptedText, report: CoverageReport, digest: str) -> Text:
    return Text(
        title=adapted.title,
        level=report.level.value,
        source_kind=adapted.source.kind,
        source_value=adapted.source.value,
        original_text=adapted.source.original_text,
        adapted_text=adapted.adapted_text,
        glossary_json=json.dumps(
            [entry.model_dump() for entry in adapted.glossary], ensure_ascii=False
        ),
        questions_json=json.dumps(
            [question.model_dump() for question in adapted.questions], ensure_ascii=False
        ),
        prompt_used=adapted.prompt_used,
        coverage_pct=report.coverage_pct,
        out_of_level=json.dumps(
            [
                {"lemma": word.lemma, "band": word.band.value, "count": word.count}
                for word in report.out_of_level
            ],
            ensure_ascii=False,
        ),
        content_hash=digest,
    )


def import_file(path: Path, *, db: Path | None = None) -> ImportResult:
    """Validate one inbox file, measure it, store it and file it away."""
    try:
        adapted = read(path)
    except InboxError as error:
        return _reject(path, str(error))

    digest = content_hash(adapted.adapted_text)

    try:
        report = coverage(adapted.adapted_text, adapted.level)
    except (LexiconError, ValueError) as error:
        return _reject(path, f"could not measure coverage: {error}")

    with database.session(db) as active:
        existing = texts.by_hash(active, digest)
        if existing is not None:
            result = ImportResult(
                action=ImportAction.DUPLICATE,
                source=path.name,
                title=existing.title,
                level=existing.level,
                text_id=existing.id,
                coverage_pct=existing.coverage_pct,
                reason=f"already imported as text {existing.id}",
            )
        else:
            stored = texts.save(active, _to_row(adapted, report, digest))
            result = ImportResult(
                action=ImportAction.IMPORTED,
                source=path.name,
                title=stored.title,
                level=stored.level,
                text_id=stored.id,
                coverage_pct=report.coverage_pct,
                threshold=report.threshold,
                meets_threshold=report.meets_threshold,
                out_of_level_count=len(report.out_of_level),
            )

    # Committed. Only now does the file move -- see the module docstring.
    shutil.move(str(path), str(_unique_destination(config.processed_dir(), path.name)))
    return result


def import_inbox(*, db: Path | None = None) -> list[ImportResult]:
    """Import everything waiting in the inbox, oldest name first."""
    return [import_file(path, db=db) for path in pending(config.inbox_dir())]


def bands_above(level: Band) -> list[str]:
    """Every band strictly above ``level``, as stored in the database."""
    return [band.value for band in LEVELS[level_index(level) + 1 :]]


def frequent_in_band(band: Band, *, exclude: set[str]) -> list[str]:
    """The band's words, most frequent first, minus what is already in the deck.

    Only NGSL words are offered. Beyond it the ranking is Zipf over general
    English, and "the most frequent word you do not know" stops being a sentence
    about a teachable list and starts being a sentence about a corpus.
    """
    table = load_bands()
    ranks = load_ngsl()
    return [
        lemma
        for lemma, _rank in sorted(ranks.items(), key=lambda item: item[1])
        if lemma not in exclude and band_for(lemma, table=table, ranks=ranks)[0] is band
    ]


def build_profile(
    level: Band | str | None = None,
    *,
    new_words: int,
    db: Path | None = None,
) -> Profile:
    """Assemble the profile for the next adaptation, from the deck as it is now.

    The level falls back to the one stored in settings, so ``degrau profile``
    with no arguments answers for wherever you actually are.
    """
    rules_by_level = load_levels(config.levels_path())

    with database.session(db) as active:
        stored_level = settings_store.get(active, settings_store.KEY_LEVEL)
        target = parse_level(level) if level is not None else parse_level(stored_level)
        known_above = words.mastered_in_bands(active, bands_above(target))
        in_deck = words.deck_lemmas(active)
        due_soon = words.due_within(active, days=REINFORCEMENT_HORIZON_DAYS)

    above = next_band(target)
    candidates = frequent_in_band(above, exclude=in_deck) if above is not None else []

    return build(
        target,
        rules_by_level.get(target, LevelRules()),
        mastered_above=known_above,
        candidates_above=candidates,
        due_soon=due_soon,
        new_words=new_words,
    )
