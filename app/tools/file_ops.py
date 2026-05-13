from __future__ import annotations

import shutil
from pathlib import Path


def safe_target(target: Path) -> Path:
    """If ``target`` already exists, return a sibling path with a numeric suffix.

    Never overwrites. Caller is responsible for using the returned path.
    """
    target = Path(target)
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def mkdir(path: Path) -> bool:
    """Create a directory (parents too). Returns True if newly created."""
    path = Path(path)
    if path.exists():
        return False
    path.mkdir(parents=True, exist_ok=False)
    return True


def move(source: Path, target: Path) -> Path:
    """Move ``source`` to ``target`` (with parent dirs). Caller chose a non-clobbering target."""
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return target


def copy(source: Path, target: Path) -> Path:
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(target))
    return target


def rename(source: Path, target: Path) -> Path:
    """Rename inside the same directory. Implemented as move for cross-platform safety."""
    return move(source, target)


def write_text(path: Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_bytes(path: Path, content: bytes) -> Path:
    """Phase 3.2: binary write for chart PNGs and similar generated artifacts.

    Creates parent dirs as needed. Used by the executor when an ``index``
    action's target file has a binary extension (.png/.jpg/etc) and its
    metadata carries ``content_b64`` rather than text ``content``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def remove_file(path: Path) -> bool:
    path = Path(path)
    if path.exists() and path.is_file():
        path.unlink()
        return True
    return False


def remove_empty_dir(path: Path) -> bool:
    path = Path(path)
    if path.exists() and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        return True
    return False
