"""Where the project's data lives.

A single place decides the path, and it is overridable by environment variable.
That is what lets a test run against a throwaway directory without ever touching
the real word lists or the real database.
"""

from __future__ import annotations

import os
from pathlib import Path

VAR_DATA_DIR = "GRADED_READER_DATA_DIR"
DEFAULT_DATA_DIR = "data"

VAR_INBOX_DIR = "GRADED_READER_INBOX_DIR"
DEFAULT_INBOX_DIR = "inbox"

VAR_DB_PATH = "GRADED_READER_DB"
DEFAULT_DB_NAME = "graded-reader.db"

NGSL_FILENAME = "ngsl.csv"
BANDS_FILENAME = "bands.toml"
LEVELS_FILENAME = "levels.toml"

PROCESSED_DIRNAME = "processed"
REJECTED_DIRNAME = "rejected"


def data_dir() -> Path:
    """Root of the reference data. Respects GRADED_READER_DATA_DIR when set."""
    return Path(os.environ.get(VAR_DATA_DIR, DEFAULT_DATA_DIR))


def ngsl_path() -> Path:
    """The New General Service List, 2809 lemmas ranked by frequency."""
    return data_dir() / NGSL_FILENAME


def bands_path() -> Path:
    """The tunable thresholds that turn a frequency rank into a CEFR band."""
    return data_dir() / BANDS_FILENAME


def levels_path() -> Path:
    """The grammar budget of each level, quoted into the adaptation prompt."""
    return data_dir() / LEVELS_FILENAME


def db_path() -> Path:
    """The SQLite file. One process, one file, and a backup is a copy."""
    return Path(os.environ.get(VAR_DB_PATH, DEFAULT_DB_NAME))


def inbox_dir() -> Path:
    """Where adapted texts land before they are imported."""
    return Path(os.environ.get(VAR_INBOX_DIR, DEFAULT_INBOX_DIR))


def processed_dir() -> Path:
    """Where a successfully imported file is moved to."""
    return inbox_dir() / PROCESSED_DIRNAME


def rejected_dir() -> Path:
    """Where an invalid file is moved to, next to a .error.txt saying why.

    The input is never deleted. A rejected file is usually one prompt away from
    being valid, and the text inside it cost a model call to produce.
    """
    return inbox_dir() / REJECTED_DIRNAME
