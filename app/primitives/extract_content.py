"""Phase 18 — ``extract_content`` primitive.

Productisation guide §4.3 #1. Takes a ``ContentRef`` (or path) and
returns typed :class:`Content`. Dispatches over ``ContentKind`` and
delegates to the appropriate existing tool (pdf_ops / text_ops /
data_ops). This is intentionally thin — the value is the **stable
output schema**, not new extraction logic.

Backends:
  * DOCUMENT (.pdf) → ``app.tools.pdf_ops.extract_text_preview``
  * NOTE / CODE / STRUCTURED → ``app.tools.text_ops.extract_text_preview``
  * TABLE → ``app.tools.data_ops.extract_tabular_preview``
  * IMAGE / BINARY → empty preview + ``error="binary"``
"""

from __future__ import annotations

from pathlib import Path

from app.primitives._schemas import Content, ContentKind, ContentRef
from app.tools import pdf_ops, text_ops

_TEXTUAL_KINDS = {
    ContentKind.NOTE,
    ContentKind.CODE,
    ContentKind.STRUCTURED,
}


def extract_content(workspace_root: Path | str, ref: ContentRef) -> Content:
    """Best-effort textual extraction.

    Never raises on legal-but-empty input — primitives are meant to be
    batched, so a single bad file shouldn't kill the loop. Returns a
    Content with ``error`` set when extraction can't proceed.
    """
    abs_path = Path(workspace_root) / ref.rel_path.replace("\\", "/").lstrip("/")
    if not abs_path.exists():
        return Content(ref=ref, preview=None, error="missing")

    if ref.kind is ContentKind.DOCUMENT:
        preview = pdf_ops.extract_text_preview(abs_path)
        return Content(ref=ref, preview=preview, error=None if preview else "unreadable")

    if ref.kind is ContentKind.TABLE:
        try:
            from app.tools import data_ops
        except ImportError:
            return Content(ref=ref, preview=None, error="missing_extra:data")
        preview = data_ops.extract_tabular_preview(abs_path)
        return Content(ref=ref, preview=preview, error=None if preview else "unreadable")

    if ref.kind in _TEXTUAL_KINDS:
        preview = text_ops.extract_text_preview(abs_path)
        return Content(ref=ref, preview=preview, error=None if preview else "unreadable")

    return Content(ref=ref, preview=None, error="binary")
