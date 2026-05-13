from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 64 * 1024


def sha256_file(path: Path) -> str:
    """Stream a file through SHA-256. Returns hex digest."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            buf = f.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
