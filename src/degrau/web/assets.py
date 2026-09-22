"""Static file URLs that change when the file does.

``/static/app.css`` is a URL the browser is entitled to cache, and it does --
aggressively. The first version of this app shipped the plain path, and the
consequence was a dashboard that rendered with none of its own styles: the
browser had a copy from before those styles existed and no reason to ask again.
It looked like broken CSS and it was a stale cache, which are very different
bugs and look identical from the outside.

So the URL carries a digest of the file's contents. It stays the same while the
file does -- caching still works, which is the point -- and changes the moment
it is edited, which makes the reload automatic instead of a thing to remember.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

#: How much of the digest goes in the URL. Eight hex characters is plenty to
#: tell two versions of one file apart; this is a cache key, not a signature.
DIGEST_LENGTH = 8


@lru_cache(maxsize=64)
def _digest(path: Path, mtime: float, size: int) -> str:
    """Hash a file's bytes.

    ``mtime`` and ``size`` are part of the cache key rather than the hash: they
    are what makes this recompute after an edit, while a request that changes
    nothing reads the cached answer instead of the disk.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:DIGEST_LENGTH]


def static_url(name: str, *, root: Path) -> str:
    """The URL for a static file, carrying a version that tracks its contents.

    A missing file gets the bare path rather than an exception. A stylesheet
    that is not there is already going to be obvious, and failing to render the
    page over it would hide the actual problem behind a traceback.
    """
    path = root / name
    try:
        stat = path.stat()
    except OSError:
        return f"/static/{name}"
    return f"/static/{name}?v={_digest(path, stat.st_mtime, stat.st_size)}"
