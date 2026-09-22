"""Where the project's data lives.

A single place decides the path, and it is overridable by environment variable.
That is what lets a test run against a throwaway directory without ever touching
the real word lists or the real database.
"""

from __future__ import annotations

import os
from pathlib import Path

VAR_DATA_DIR = "DEGRAU_DATA_DIR"
DEFAULT_DATA_DIR = "data"

NGSL_FILENAME = "ngsl.csv"
BANDS_FILENAME = "bands.toml"


def data_dir() -> Path:
    """Root of the data directory. Respects DEGRAU_DATA_DIR when set."""
    return Path(os.environ.get(VAR_DATA_DIR, DEFAULT_DATA_DIR))


def ngsl_path() -> Path:
    """The New General Service List, 2809 lemmas ranked by frequency."""
    return data_dir() / NGSL_FILENAME


def bands_path() -> Path:
    """The tunable thresholds that turn a frequency rank into a CEFR band."""
    return data_dir() / BANDS_FILENAME
