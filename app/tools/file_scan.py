from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import FileMeta, WorkspaceSnapshot
from app.tools import pdf_ops, text_ops
from app.tools.hash_ops import sha256_file

_EXTENSION_CATEGORY: dict[str, str] = {
    # documents
    ".pdf": "pdf",
    ".doc": "word",
    ".docx": "word",
    ".rtf": "word",
    ".odt": "word",
    # spreadsheets / data
    ".xls": "excel",
    ".xlsx": "excel",
    ".ods": "excel",
    ".csv": "tabular",
    ".tsv": "tabular",
    ".parquet": "tabular",
    # text / notes
    ".md": "text",
    ".markdown": "text",
    ".txt": "text",
    ".rst": "text",
    # images
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
    ".svg": "image",
    ".tiff": "image",
    ".heic": "image",
    # audio
    ".mp3": "audio",
    ".wav": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".ogg": "audio",
    # video
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".avi": "video",
    ".webm": "video",
    # archives
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".bz2": "archive",
    ".7z": "archive",
    ".rar": "archive",
    # code
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".tsx": "code",
    ".jsx": "code",
    ".go": "code",
    ".rs": "code",
    ".java": "code",
    ".kt": "code",
    ".c": "code",
    ".h": "code",
    ".cpp": "code",
    ".hpp": "code",
    ".cs": "code",
    ".rb": "code",
    ".php": "code",
    ".sh": "code",
    ".ps1": "code",
    # structured data
    ".json": "structured",
    ".yaml": "structured",
    ".yml": "structured",
    ".xml": "structured",
    ".toml": "structured",
    ".ini": "structured",
}


def classify(path: Path) -> str:
    """Map a file path to a coarse category by extension."""
    ext = path.suffix.lower()
    return _EXTENSION_CATEGORY.get(ext, "other")


def _is_under_runs_dir(path: Path, root: Path) -> bool:
    """Skip our own state directory if a user accidentally scans the repo root."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return ".localflow" in rel.parts


def _extract_preview(path: Path, file_type: str) -> str | None:
    """Dispatch to the right content extractor based on file_type.

    PDFs go through pypdf; text-like types (text/structured/tabular/code)
    are read directly. Phase 11: Excel + plain tabular (csv/tsv) now go
    through ``data_ops.extract_tabular_preview`` so the LLM can see real
    cell values instead of guessing from a filename. Everything else
    (image/audio/video/archive/other) has no readable text and returns
    None.
    """
    if file_type == "pdf":
        return pdf_ops.extract_text_preview(path)
    if file_type in ("excel", "tabular"):
        # Lazy import — only paid when the scanner actually hits a data
        # file. Installs without the [data] extra fall back to None.
        try:
            from app.tools import data_ops
        except ImportError:
            return None
        return data_ops.extract_tabular_preview(path)
    if text_ops.can_preview_as_text(file_type):
        return text_ops.extract_text_preview(path)
    return None


def scan_workspace(
    root: Path,
    task_id: str,
    *,
    compute_hash: bool = True,
    compute_preview: bool = True,
    follow_symlinks: bool = False,
) -> WorkspaceSnapshot:
    """Walk ``root`` and return a structured snapshot.

    By design this is read-only. Symlinks are not followed by default to
    prevent escape from the workspace boundary.

    Phase 2: when ``compute_preview=True`` (the default), each file's
    leading text content is extracted into ``FileMeta.text_preview`` so
    downstream planners — especially the LLM planner — can make
    content-aware decisions instead of relying on filenames alone.
    Pass ``compute_preview=False`` to skip extraction (faster scans,
    useful for large workspaces or pure-rule planning).
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"workspace root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"workspace root is not a directory: {root}")

    files: list[FileMeta] = []
    total_size = 0

    for entry in root.rglob("*"):
        if entry.is_symlink() and not follow_symlinks:
            continue
        if not entry.is_file():
            continue
        if _is_under_runs_dir(entry, root):
            continue
        stat = entry.stat()
        rel = entry.relative_to(root).as_posix()
        file_type = classify(entry)
        sha = sha256_file(entry) if compute_hash else None
        preview = _extract_preview(entry, file_type) if compute_preview else None
        files.append(
            FileMeta(
                path=rel,
                file_type=file_type,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                sha256=sha,
                text_preview=preview,
            )
        )
        total_size += stat.st_size

    files.sort(key=lambda f: f.path)
    return WorkspaceSnapshot(
        snapshot_id=f"snap-{uuid.uuid4().hex[:8]}",
        task_id=task_id,
        root=str(root.resolve()),
        files=files,
        total_files=len(files),
        total_size_bytes=total_size,
    )
