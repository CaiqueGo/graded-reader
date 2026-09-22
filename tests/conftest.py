"""Shared fixtures.

``tmp_data`` points DEGRAU_DATA_DIR at a throwaway directory and writes a small,
hand-made word list into it. No test reads the real NGSL.

The small list is not a shortcut, it is the point: a test that asserts "sign is
A1" against the real file is really asserting the contents of a CSV somebody else
maintains, and it will break the day they publish a new edition. These tests are
about the band *logic*, so the ranks are fixed here where they can be read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The ranks are chosen to sit exactly on the boundaries declared in bands.toml,
# so an off-by-one in the ceiling comparison shows up as a failing test.
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


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data directory with a miniature NGSL and the real thresholds."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "ngsl.csv").write_text(FAKE_NGSL, encoding="utf-8")
    (data / "bands.toml").write_text(BANDS_TOML, encoding="utf-8")
    monkeypatch.setenv("DEGRAU_DATA_DIR", str(data))
    return data
