"""Shared fixtures.

Every environment variable the app reads is redirected at a throwaway directory:
the word lists, the inbox and the database. No test touches the real ones.

The miniature NGSL is not a shortcut, it is the point. A test that asserts "sign
is A1" against the real file is really asserting the contents of a CSV somebody
else maintains, and it breaks the day they publish a new edition. These tests are
about the band *logic*, so the ranks are fixed here where they can be read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

# The ranks sit exactly on the boundaries declared in bands.toml, so an
# off-by-one in the ceiling comparison shows up as a failing test.
FAKE_NGSL = """Lemma,SFI Rank,SFI,Adjusted Frequency per Million (U)
the,1,87.85,60910
be,2,86.86,48575
a,3,80.00,40000
they,4,79.00,39000
put,5,78.00,38000
go,6,77.00,37000
child,7,76.00,36000
water,8,75.00,35000
deep,499,63.00,200
sign,500,62.93,197
under,501,62.00,190
favorite,1000,59.62,92
rock,1001,59.00,90
thick,2000,55.53,36
thirst,2809,44.79,3
"""

BANDS_TOML = """
[ngsl]
A1 = 500
A2 = 1000
B1 = 2000
B2 = 2809

[zipf]
B2 = 4.0
C1 = 3.0

[coverage]
A1 = 0.95
A2 = 0.95
B1 = 0.92
B2 = 0.92
C1 = 0.90
C2 = 0.90
"""

LEVELS_TOML = """
[A1]
vocabulary = "The most frequent 500 words."
sentences = "6 to 10 words."
grammar = "Present simple."
guidance = "One thing per sentence."

[A2]
vocabulary = "The most frequent 1000 words."
sentences = "8 to 14 words."
grammar = "Past continuous."
guidance = "One idea and its consequence."

[B1]
vocabulary = "The most frequent 2000 words."
sentences = "12 to 20 words."
grammar = "Passive voice."
guidance = "An argument across a paragraph."
"""

#: A minimal file that satisfies the import contract.
VALID_PAYLOAD: dict[str, Any] = {
    "schema": 1,
    "level": "A1",
    "title": "Water Under The Rock",
    "source": {
        "kind": "url",
        "value": "https://example.com/story",
        "original_text": "The original, as it was pasted.",
    },
    "adapted_text": "They put the water deep under a rock.",
    "glossary": [
        {
            "en": "rock",
            "pt": "rocha",
            "example_en": "They put the water under a rock.",
            "example_pt": "Eles puseram a agua sob uma rocha.",
        }
    ],
    "questions": [{"q": "Where does the water go?", "a": "Under a rock."}],
    "prompt_used": "the full prompt",
    "generated_at": "2026-09-21T22:40:00-03:00",
}


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data directory with a miniature NGSL and the real threshold files."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "ngsl.csv").write_text(FAKE_NGSL, encoding="utf-8")
    (data / "bands.toml").write_text(BANDS_TOML, encoding="utf-8")
    (data / "levels.toml").write_text(LEVELS_TOML, encoding="utf-8")
    monkeypatch.setenv("GRADED_READER_DATA_DIR", str(data))
    return data


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty database of its own, created on first use."""
    path = tmp_path / "graded-reader.db"
    monkeypatch.setenv("GRADED_READER_DB", str(path))
    return path


@pytest.fixture()
def tmp_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An inbox directory. processed/ and rejected/ are created on demand."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("GRADED_READER_INBOX_DIR", str(inbox))
    return inbox


@pytest.fixture()
def drop_in(tmp_inbox: Path) -> Callable[..., Path]:
    """Write a file into the inbox.

    Takes a payload dict, or ``raw`` for the cases where the point is that the
    bytes are not a valid document at all.
    """

    def _drop(name: str = "text.json", *, raw: str | None = None, **overrides: Any) -> Path:
        path = tmp_inbox / name
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        else:
            body = {**VALID_PAYLOAD, **overrides}
            path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return path

    return _drop


@pytest.fixture()
def valid_payload() -> dict[str, Any]:
    """A copy of the reference document, safe to mutate."""
    return json.loads(json.dumps(VALID_PAYLOAD))
