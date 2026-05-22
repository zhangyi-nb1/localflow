"""Phase 18 — Primitive catalog.

A small registry of "what capabilities does LocalFlow expose at the
primitive (not skill) level?". Productisation guide §4.3 lists 9
primitives; Phase 18 ships typed implementations of 2 and CATALOG-ONLY
entries for the rest (with a pointer to the tool / skill that already
provides the behaviour).

The catalog is what the LLM Goal Interpreter will eventually use to
decide "what primitives must this user goal compose?" without having
to know skill names. Phase 18 surfaces the catalog as data; Phase 19's
verifiers will introspect the same entries to know which output schema
to validate against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PrimitiveEntry:
    """One row in the capability catalog."""

    name: str
    """Stable identifier — used by GoalInterpreter to talk about the
    primitive without naming a skill."""

    summary: str
    """One-line product description ('extract textual content from a
    PDF / Markdown / CSV …')."""

    implemented: bool
    """True when ``callable_`` is a real function ready to use. False
    means the entry is documented but the implementation lives in a
    skill or tool today."""

    backed_by: str
    """Where the behaviour lives today — e.g. ``app.tools.pdf_ops`` for
    the implemented entries, ``app.skills.topic_clusterer`` for an
    entry that's still skill-only."""

    callable_: Callable | None = None
    """The Python callable when ``implemented`` is True. Always None
    for catalog-only entries (Phase 18 deliberately doesn't force a
    pseudo-API; Phase 19/20 will close the gap as it becomes needed)."""


def _build_catalog() -> dict[str, PrimitiveEntry]:
    """Lazy import so importing ``app.primitives.catalog`` doesn't
    transitively drag in pdf / data extras when those extras are not
    installed."""
    from app.primitives.classify_content import classify_content
    from app.primitives.extract_content import extract_content

    entries: list[PrimitiveEntry] = [
        PrimitiveEntry(
            name="extract_content",
            summary=(
                "Best-effort textual extraction from a file. Returns a "
                "typed Content (preview + metadata + error)."
            ),
            implemented=True,
            backed_by="app.tools.pdf_ops + text_ops + data_ops",
            callable_=extract_content,
        ),
        PrimitiveEntry(
            name="classify_content",
            summary=(
                "Deterministic extension-based classification. Returns "
                "a Classification with label + confidence."
            ),
            implemented=True,
            backed_by="app.primitives.classify_content (curated table)",
            callable_=classify_content,
        ),
        PrimitiveEntry(
            name="cluster_topics",
            summary=(
                "Cluster text-bearing files into N topic dirs via "
                "LLM-driven semantic grouping. Phase 18 leaves this "
                "skill-only."
            ),
            implemented=False,
            backed_by="app.skills.topic_clusterer",
        ),
        PrimitiveEntry(
            name="generate_index",
            summary=(
                "Walk a directory and emit a per-category index.md "
                "linking every file. Phase 18 leaves this skill-only "
                "(folder_organizer does it as part of MOVE actions)."
            ),
            implemented=False,
            backed_by="app.skills.folder_organizer",
        ),
        PrimitiveEntry(
            name="build_source_ledger",
            summary=(
                "Walk a workspace + a rollback manifest and emit a "
                "typed SourceLedger. Phase 14.1 already provides this "
                "with a stable schema."
            ),
            implemented=False,
            backed_by="app.tools.source_ledger_ops",
        ),
        PrimitiveEntry(
            name="analyze_table",
            summary=(
                "Run a typed AnalysisSpec against a tabular file and "
                "produce an AnalysisResult. Phase 18 wraps the "
                "existing engine via a Recipe; no separate primitive."
            ),
            implemented=False,
            backed_by="app.tools.data_analysis + app.skills.data_analyzer",
        ),
        PrimitiveEntry(
            name="render_chart",
            summary=(
                "Turn a DataFrame + spec into a PNG. Phase 18 leaves "
                "this as a tool; chart_ops is already typed."
            ),
            implemented=False,
            backed_by="app.tools.chart_ops",
        ),
        PrimitiveEntry(
            name="synthesize_report",
            summary=(
                "LLM-driven prose synthesis (README / SOURCES / "
                "executive summary). Phase 18 leaves this in the "
                "agent meta-skill — calling it directly bypasses the "
                "Harness's approval / rollback path."
            ),
            implemented=False,
            backed_by="app.skills.agent",
        ),
        PrimitiveEntry(
            name="fetch_sources",
            summary=(
                "HTTPS GET of an allowlisted URL into the workspace. "
                "Phase 16 ships this as a skill + ActionType.FETCH "
                "kernel hook; no separate primitive needed."
            ),
            implemented=False,
            backed_by="app.skills.webcollect",
        ),
        PrimitiveEntry(
            name="validate_deliverable",
            summary=(
                "Run a recipe's verifier list against a finished run "
                "and produce a typed verdict. Phase 18 leaves this as "
                "a stub — Phase 19's Deliverable Verifier expansion "
                "will deliver the typed wrapper around the existing "
                "Phase 9 grader registry."
            ),
            implemented=False,
            backed_by="app.eval.graders (Phase 19 will tighten)",
        ),
    ]
    return {entry.name: entry for entry in entries}


_CATALOG: dict[str, PrimitiveEntry] | None = None


def get_catalog() -> dict[str, PrimitiveEntry]:
    """Return the lazily-built catalog. Process-wide cached."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _build_catalog()
    return _CATALOG


def list_names() -> list[str]:
    """Stable, sorted list of primitive names."""
    return sorted(get_catalog())


def get(name: str) -> PrimitiveEntry:
    """Look up by name. Raises ``KeyError`` on miss."""
    return get_catalog()[name]
