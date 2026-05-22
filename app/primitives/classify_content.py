"""Phase 18 — ``classify_content`` primitive.

Productisation guide §4.3 #2. Maps a file's path / kind to a typed
:class:`Classification`. Phase 18 ships the deterministic extension-
based path; future phases may add an LLM-driven backend behind the
same schema (e.g. inspecting a PDF's first page to decide "research
paper" vs "tax document").

The output ``label`` is intentionally free-form so callers can attach
richer taxonomies later without bumping the schema.
"""

from __future__ import annotations

from app.primitives._schemas import Classification, ContentKind, ContentRef

# Extension → label table reused from folder_organizer's defaults so
# the primitive produces the same buckets a Phase 17 pack sees. Kept
# here (rather than re-imported from the skill) to keep the layering
# clean: primitives do NOT depend on skills.
_EXT_TO_LABEL: dict[str, str] = {
    # papers
    ".pdf": "paper",
    ".docx": "paper",
    ".doc": "paper",
    # tables
    ".csv": "data",
    ".tsv": "data",
    ".xlsx": "data",
    ".xls": "data",
    ".parquet": "data",
    # notes / prose
    ".md": "note",
    ".markdown": "note",
    ".txt": "note",
    ".rst": "note",
    # code
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".tsx": "code",
    ".go": "code",
    ".rs": "code",
    ".java": "code",
    ".rb": "code",
    ".sh": "code",
    ".ps1": "code",
    # structured
    ".json": "structured",
    ".yaml": "structured",
    ".yml": "structured",
    ".xml": "structured",
    ".toml": "structured",
    ".ini": "structured",
    # images
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".bmp": "image",
    ".svg": "image",
}

_KIND_FALLBACK: dict[ContentKind, str] = {
    ContentKind.DOCUMENT: "paper",
    ContentKind.NOTE: "note",
    ContentKind.TABLE: "data",
    ContentKind.IMAGE: "image",
    ContentKind.CODE: "code",
    ContentKind.STRUCTURED: "structured",
    ContentKind.BINARY: "binary",
}


def classify_content(ref: ContentRef) -> Classification:
    """Deterministic, no-LLM classification.

    Confidence is 1.0 when the extension is in the curated table (a
    near-certain signal), 0.5 when we only have the coarse
    ContentKind (still useful but less specific), 0.2 for binary.
    """
    rel = ref.rel_path.lower()
    # str.rpartition keeps us off pathlib.suffix to honour ContentRef's
    # already-forward-slashed convention without spinning a Path.
    _, _, ext = rel.rpartition(".")
    ext = f".{ext}" if ext else ""

    label = _EXT_TO_LABEL.get(ext)
    if label is not None:
        return Classification(
            ref=ref,
            label=label,
            confidence=1.0,
            rationale=f"extension {ext!r} is a known {label} signal",
        )

    fallback = _KIND_FALLBACK.get(ref.kind, "other")
    confidence = 0.2 if ref.kind is ContentKind.BINARY else 0.5
    return Classification(
        ref=ref,
        label=fallback,
        confidence=confidence,
        rationale=f"no curated label for extension {ext!r}; defaulting from kind={ref.kind.value}",
    )
