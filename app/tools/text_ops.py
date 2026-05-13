from __future__ import annotations

from pathlib import Path

DEFAULT_MAX_CHARS = 2000

# File types whose contents are inherently text. Anything else
# (image, audio, archive, etc.) we skip — there's no readable text.
TEXT_FILE_TYPES: frozenset[str] = frozenset({"text", "structured", "tabular", "code"})


def can_preview_as_text(file_type: str) -> bool:
    return file_type in TEXT_FILE_TYPES


def extract_text_preview(
    path: Path,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str | None:
    """Read the first ``max_chars`` characters of a text file.

    Returns None on read errors, binary files, or empty content.
    Tolerates non-UTF-8 with ``errors='replace'`` so a stray Latin-1
    file doesn't crash the scan.
    """
    path = Path(path)
    if not path.exists() or not path.is_file():
        return None

    try:
        # Read a bounded number of BYTES (a multiple of max_chars to be
        # safe under multi-byte UTF-8) then decode, then slice.
        with path.open("rb") as f:
            raw = f.read(max_chars * 4)
    except OSError:
        return None

    # Reject obvious binary content: presence of NUL byte is a strong
    # signal we mis-categorized the file type (pypdf failed earlier, etc.)
    if b"\x00" in raw[:1024]:
        return None

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return None

    text = text[:max_chars].strip()
    return text or None
