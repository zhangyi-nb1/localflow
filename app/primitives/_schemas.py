"""Phase 18 — Typed I/O models for capability primitives (v0.18.0).

Productisation guide §4.3 calls out a primitive layer above tools and
below skills: "extract_content / classify_content / analyze_table /
render_chart / build_source_ledger / synthesize_report / …". The
primary motivation is composition — Recipe authors and the LLM
Goal Interpreter need to talk about capabilities at a stable level,
not at the level of "which skill happens to wrap this".

This module hosts the input/output Pydantic models every primitive
shares. Each primitive function takes one of these as input and
returns another. The corresponding Phase 19 deliverable verifiers
will inspect the *output* schemas, not the underlying tool calls —
so when a future skill produces e.g. a `Content` via a different
backend (vision-language model, an MCP server, etc.), the verifier
is unchanged.

§10.7 invariant: this is application-layer schema only. No kernel
references.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ContentKind(str, Enum):
    """Coarse content classification used across primitives.

    Wider than ``FileMeta.file_type`` (which is extension-derived).
    Lets the LLM-driven Goal Interpreter reason about "this is a
    paper" vs "this is a row-oriented table" vs "this is a binary
    artefact we can't introspect" without inspecting per-file
    extensions.
    """

    DOCUMENT = "document"
    """Long-form text we can summarise (PDF / DOCX / long Markdown)."""

    NOTE = "note"
    """Short markdown / txt notes — typically <2 KB of prose."""

    TABLE = "table"
    """Tabular data — CSV / TSV / Parquet / XLSX sheets."""

    IMAGE = "image"
    """Picture (PNG / JPG / etc.). No textual content extractable."""

    CODE = "code"
    """Source-code file. Treated as text for content extraction."""

    STRUCTURED = "structured"
    """Machine-readable but non-tabular (JSON / YAML / XML / TOML)."""

    BINARY = "binary"
    """Opaque blob — archive / audio / video / unknown. No preview."""


class ContentRef(BaseModel):
    """Pointer to a source file inside the workspace. Stable across
    primitives so e.g. ``extract_content`` and ``build_source_ledger``
    can reference the same row by ``rel_path`` without re-passing the
    raw bytes."""

    rel_path: str = Field(..., description="Workspace-relative path, forward-slashed.")
    kind: ContentKind
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(
        default=None,
        description="Hex digest if computed by the scanner. Optional — primitives may add it.",
    )


class Content(BaseModel):
    """The output of ``extract_content``.

    ``preview`` is plain text (or markdown) suitable for LLM context —
    the same shape we already use as ``FileMeta.text_preview``. Truly
    binary files (images / archives) yield ``preview=None`` and
    ``error="binary"`` rather than throwing — primitives never raise
    on legal-but-empty extractions so callers can batch over a
    workspace.
    """

    ref: ContentRef
    preview: str | None = Field(
        default=None, description="Best-effort textual representation."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Backend-specific extras (e.g. PDF page count, sheet "
            "count, encoding). Empty for binary content."
        ),
    )
    error: str | None = Field(
        default=None,
        description=(
            "Set to a short error code when extraction failed "
            "('binary' / 'unreadable' / 'too_large'). Preview is None "
            "when error is non-None."
        ),
    )


class Classification(BaseModel):
    """Output of ``classify_content``. Light — just kind + confidence.

    Phase 18 ships a deterministic extension-based classifier; Phase
    19's LLM verifier may attach a richer label via the same schema
    (e.g. ``label='research_paper'`` for a PDF whose first page
    matches a paper layout).
    """

    ref: ContentRef
    label: str = Field(..., description="Free-form label ('paper' / 'data' / 'note' / …).")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0 = pure guess, 1.0 = unambiguous (extension match).",
    )
    rationale: str | None = Field(
        default=None,
        description="One short sentence explaining the call. Surfaced in trace events.",
    )


def to_abs(workspace_root: Path | str, ref: ContentRef) -> Path:
    """Convenience — convert a ``ContentRef`` back to an absolute path
    rooted at ``workspace_root``. Centralised here so every primitive
    handles slash normalisation the same way."""
    rel = ref.rel_path.replace("\\", "/").lstrip("/")
    return Path(workspace_root) / rel
