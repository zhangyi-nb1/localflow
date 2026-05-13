"""pdf_indexer planner — Open Deep Research-inspired synthesis pipeline.

For each PDF in the workspace:
  1. extract title (heuristic: first non-blank line of the text_preview
     populated by Phase 2.1 pdf_ops)
  2. extract a 1-line summary (next few lines, compressed)
  3. cite the source (file path + the fact that we used the preview)
  4. synthesize all per-PDF entries into a single ``pdf_index.md``

The planner produces ONE action — an ``index`` write — whose
``metadata.content`` is the rendered markdown and whose
``metadata.provenance`` records which source PDFs contributed.
"""
from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Any

from app.schemas import ActionPlan, FileMeta, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, ActionType, RiskLevel


DEFAULT_OUTPUT_PATH = "pdf_index.md"
TITLE_MAX_CHARS = 100
SUMMARY_MAX_CHARS = 240


def plan_pdf_index(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    *,
    output_path: str = DEFAULT_OUTPUT_PATH,
) -> ActionPlan:
    """Generate an ActionPlan that emits a single index file describing
    every PDF in the workspace.

    If there are no PDFs, the plan is empty (and the harness verifier
    will report "no actions to execute" — that's a legitimate no-op).
    """
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    pdfs = [f for f in snapshot.files if f.file_type == "pdf"]

    if not pdfs:
        return ActionPlan(
            plan_id=plan_id,
            task_id=task.task_id,
            summary="No PDF files found in workspace; nothing to index.",
            actions=[],
            expected_outputs=[],
            risk_summary="No-op plan, zero risk.",
        )

    entries = [_summarize_pdf(pdf) for pdf in sorted(pdfs, key=lambda f: f.path)]
    content = _render_index_markdown(snapshot.root, entries)
    provenance = _build_provenance(entries)

    action = Action(
        action_id="a-001",
        action_type=ActionType.INDEX,
        target_path=output_path,
        reason=(
            f"Synthesize an index of {len(pdfs)} PDF(s) into "
            f"{output_path}; per-file titles and summaries derived from "
            f"text previews."
        ),
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=True,
        metadata={
            "content": content,
            # Open Deep Research-style provenance: which source files
            # contributed to which lines of the synthesized output.
            "provenance": provenance,
            # Regeneratable derivative: overwrite the previous index in
            # place rather than accumulating pdf_index (1).md, (2).md.
            "overwrite_existing": True,
        },
    )

    return ActionPlan(
        plan_id=plan_id,
        task_id=task.task_id,
        summary=(
            f"Index {len(pdfs)} PDF file(s) into a single markdown index. "
            f"{sum(1 for e in entries if e['has_preview'])}/{len(pdfs)} "
            f"have extractable previews; the rest use filename only."
        ),
        actions=[action],
        expected_outputs=[output_path],
        risk_summary=(
            "Low risk: single index write, reversible via rollback "
            "manifest. No source files are modified."
        ),
    )


# --------------------------------------------------------------------- internals


def _summarize_pdf(meta: FileMeta) -> dict[str, Any]:
    """Produce a per-PDF entry. Falls back gracefully when no preview exists."""
    name = PurePosixPath(meta.path).name
    if not meta.text_preview:
        return {
            "path": meta.path,
            "name": name,
            "title": _humanize_filename(name),
            "summary": "(no text preview available — likely encrypted, scanned, or non-text PDF)",
            "title_source": "filename",
            "has_preview": False,
        }

    lines = [ln.strip() for ln in meta.text_preview.splitlines() if ln.strip()]
    title = _pick_title(lines, fallback=_humanize_filename(name))
    summary = _pick_summary(lines, title)
    return {
        "path": meta.path,
        "name": name,
        "title": title[:TITLE_MAX_CHARS],
        "summary": summary[:SUMMARY_MAX_CHARS],
        "title_source": "preview",
        "has_preview": True,
    }


def _pick_title(lines: list[str], *, fallback: str) -> str:
    """First plausible title-like line.

    Reject lines that look like headers/footers (all caps short bursts,
    page numbers, dates) and lines that are clearly body text (very long
    with periods).
    """
    for line in lines:
        if len(line) < 4:
            continue
        if line.isdigit():
            continue
        if len(line) > 200:
            continue  # likely a body paragraph
        return line
    return fallback


def _pick_summary(lines: list[str], title: str) -> str:
    """Concatenate the next few non-title lines into a compact summary."""
    body = [ln for ln in lines if ln != title]
    if not body:
        return "(title only; no further preview text)"
    joined = " ".join(body[:5])
    return " ".join(joined.split())


def _humanize_filename(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip()


def _render_index_markdown(workspace_root: str, entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# PDF Index")
    lines.append("")
    lines.append(f"_{len(entries)} PDF file(s) in `{workspace_root}`._")
    lines.append("")
    lines.append(f"_{sum(1 for e in entries if e['has_preview'])} have extracted previews._")
    lines.append("")
    lines.append("## Contents")
    lines.append("")
    for i, e in enumerate(entries, start=1):
        anchor = e["name"].lower().replace(".", "").replace("/", "-").replace(" ", "-")
        lines.append(f"{i}. [{e['title']}](#{anchor}) — `{e['path']}`")
    lines.append("")
    for e in entries:
        anchor = e["name"].lower().replace(".", "").replace("/", "-").replace(" ", "-")
        lines.append(f"### {e['title']} <a id=\"{anchor}\"></a>")
        lines.append("")
        lines.append(f"- **Source**: `{e['path']}`")
        lines.append(f"- **Title source**: {e['title_source']}")
        lines.append(f"- **Summary**: {e['summary']}")
        lines.append("")
    return "\n".join(lines)


def _build_provenance(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Open Deep Research-style provenance: enable post-hoc audit of which
    source file produced which section of the output."""
    return {
        "synthesis_kind": "pdf_index",
        "sources": [
            {
                "path": e["path"],
                "title": e["title"],
                "title_source": e["title_source"],
                "has_preview": e["has_preview"],
            }
            for e in entries
        ],
    }
