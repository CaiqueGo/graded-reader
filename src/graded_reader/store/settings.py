"""User-owned configuration, kept in the database.

Distinct from the files in ``data/``: those are calibration, changed when the
measurement is wrong. These change by using the app.
"""

from __future__ import annotations

from sqlmodel import Session

from graded_reader.store.models import Setting

KEY_LEVEL = "level"
KEY_DAILY_NEW = "daily_new_cards"

DEFAULTS = {
    KEY_LEVEL: "A1",
    KEY_DAILY_NEW: "10",
}


def get(session: Session, key: str, default: str | None = None) -> str:
    """The stored value, or the built-in default, or ``default``."""
    row = session.get(Setting, key)
    if row is not None:
        return row.value
    fallback = DEFAULTS.get(key, default)
    if fallback is None:
        raise KeyError(f"no setting {key!r} and no default for it")
    return fallback


def set_value(session: Session, key: str, value: str) -> None:
    """Write a setting, replacing whatever was there."""
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
        session.add(row)
