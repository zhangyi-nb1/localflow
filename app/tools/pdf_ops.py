from __future__ import annotations

import logging
from pathlib import Path

# pypdf logs WARNING for every malformed file it sees (e.g. our test
# fixtures and seed.py write plain text with .pdf extension). The
# extractor handles those cases by returning None, so the noise is
# already accounted for — silence it.
logging.getLogger("pypdf").setLevel(logging.ERROR)


DEFAULT_MAX_PAGES = 3
DEFAULT_MAX_CHARS = 2000


def extract_text_preview(
    path: Path,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str | None:
    """Extract a leading text preview from a PDF.

    Returns ``None`` (not an exception) for:
      * file is not a valid PDF (e.g. our test fixtures that write plain
        text with a ``.pdf`` suffix)
      * file is encrypted / scanned-without-OCR / corrupted
      * pypdf is not installed

    Phase 2 deliberately handles failure as "no preview available" rather
    than crashing — the LLM planner can still operate using filenames
    alone for files it can't read.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    path = Path(path)
    if not path.exists() or not path.is_file():
        return None

    try:
        reader = PdfReader(str(path))
    except Exception:
        return None

    if reader.is_encrypted:
        # Don't attempt decryption — that's outside our security scope.
        return None

    parts: list[str] = []
    char_budget = max_chars
    try:
        for page in reader.pages[:max_pages]:
            if char_budget <= 0:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            text = text.strip()
            if not text:
                continue
            if len(text) > char_budget:
                parts.append(text[:char_budget])
                break
            parts.append(text)
            char_budget -= len(text)
    except Exception:
        # pypdf can raise on malformed page trees mid-iteration.
        if not parts:
            return None

    preview = "\n".join(parts).strip()
    return preview or None
