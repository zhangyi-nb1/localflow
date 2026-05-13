"""data_reporter planner — Phase 3.1 / outline §14 DataOps.

Reads every CSV/TSV in the workspace, produces a per-file schema +
basic-statistics summary, and synthesizes a single ``data_report.md``.

Architectural note (per outline §7.1 / §3.7):
    The LLM does NOT write code here. Every pandas call lives in
    ``app/tools/data_ops.py`` and is invoked deterministically from
    this planner. The skill outputs a typed ``index`` Action; the
    Harness Kernel writes the markdown via the same path used by
    ``pdf_indexer``.
"""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any

from app.schemas import ActionPlan, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, ActionType, RiskLevel
from app.tools import chart_ops
from app.tools.data_ops import (
    DataFrameSummary,
    TabularRead,
    is_supported_tabular,
    read_tabular,
    summarize_dataframe,
)

DEFAULT_OUTPUT_PATH = "data_report.md"
CHARTS_DIR = "charts"

# file_type labels (from app/tools/file_scan.classify) we treat as
# describable. ``tabular`` covers .csv/.tsv; ``excel`` covers .xlsx/.xls.
DESCRIBABLE_FILE_TYPES: frozenset[str] = frozenset({"tabular", "excel"})


def plan_data_report(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    *,
    output_path: str = DEFAULT_OUTPUT_PATH,
) -> ActionPlan:
    """Generate an ActionPlan that emits a single data report.

    If there are no describable data files, the plan is empty (a
    legitimate no-op the harness reports as "0 actions"). Reading
    failures don't abort the skill — each file's error is surfaced in
    its section of the report so the user can see what went wrong.

    Phase 3.1b: handles CSV/TSV (single table per file) AND Excel
    (one DataFrameSummary per sheet — a 5-sheet workbook becomes 5
    sections in the report).
    """
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    workspace_root = Path(snapshot.root)
    data_files = [f for f in snapshot.files if f.file_type in DESCRIBABLE_FILE_TYPES]

    if not data_files:
        return ActionPlan(
            plan_id=plan_id,
            task_id=task.task_id,
            summary="No CSV/TSV/Excel files found; nothing to report.",
            actions=[],
            expected_outputs=[],
            risk_summary="No-op plan, zero risk.",
        )

    table_reads: list[TabularRead] = []
    for meta in sorted(data_files, key=lambda f: f.path):
        abs_path = workspace_root / meta.path
        if not is_supported_tabular(abs_path):
            table_reads.append(
                TabularRead(
                    display_path=meta.path,
                    df=None,
                    rows_truncated=False,
                    error=f"unsupported format: {abs_path.suffix}",
                )
            )
            continue
        table_reads.extend(read_tabular(abs_path, meta.path))

    # Build the JSON-serializable summaries from the in-memory DataFrames
    # once. We need both: summaries for the markdown report, DataFrames
    # for chart_ops. Keeping them paired avoids re-reading from disk.
    summaries: list[DataFrameSummary] = [
        summarize_dataframe(
            tr.display_path,
            tr.df,
            truncated=tr.rows_truncated,
            error=tr.error,
        )
        for tr in table_reads
    ]

    # Phase 3.2: pick one chart per successfully-read table.
    chart_actions: list[Action] = []
    chart_links: dict[str, str] = {}  # summary.path → chart relative path
    action_counter = 1
    for tr, summary in zip(table_reads, summaries):
        if tr.error is not None or tr.df is None or summary.error is not None:
            continue
        chart = _pick_chart_for_table(tr.df, summary)
        if chart is None:
            continue
        action_counter += 1
        action_id = f"a-{action_counter:03d}"
        chart_rel = f"{CHARTS_DIR}/{_chart_filename(summary.path, chart['column'], chart['kind'])}"
        chart_actions.append(
            _chart_action(action_id, chart_rel, chart["png_bytes"], chart, summary)
        )
        chart_links[summary.path] = chart_rel

    content = _render_data_report_md(workspace_root, summaries, chart_links)
    provenance = _build_provenance(summaries, chart_links)

    succeeded = sum(1 for s in summaries if s.error is None)
    report_action = Action(
        action_id="a-001",
        action_type=ActionType.INDEX,
        target_path=output_path,
        reason=(
            f"Synthesize a data report covering {len(data_files)} input file(s) "
            f"({len(summaries)} tables incl. Excel sheets); {succeeded} parsed."
        ),
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=True,
        metadata={
            "content": content,
            "provenance": provenance,
            "overwrite_existing": True,
        },
    )

    all_actions = [report_action, *chart_actions]
    expected = [output_path, *sorted(chart_links.values())]

    return ActionPlan(
        plan_id=plan_id,
        task_id=task.task_id,
        summary=(
            f"Summarize {len(data_files)} input file(s) into {output_path} "
            f"({len(summaries)} tables incl. Excel sheets): "
            f"{succeeded} parsed, {len(summaries) - succeeded} had read errors. "
            f"Pandas-based schema + basic stats + {len(chart_actions)} chart(s)."
        ),
        actions=all_actions,
        expected_outputs=expected,
        risk_summary=(
            "Low risk: text markdown write + PNG chart writes, all reversible "
            "via rollback manifest. No source data is modified or copied."
        ),
    )


# --------------------------------------------------------------------- chart picking


def _pick_chart_for_table(df: Any, summary: DataFrameSummary) -> dict[str, Any] | None:
    """Return a chart spec dict ``{kind, column, png_bytes}`` or None if
    no informative column is available. Heuristic priorities:
      1. The numeric column with the highest std (most variance → most
         interesting histogram).
      2. Otherwise the categorical column with 2-20 distinct values
         (low-cardinality → readable bar chart).
    """
    if df is None or len(df) == 0:
        return None

    numeric_cols = [c for c in summary.columns if c.numeric_stats]
    if numeric_cols:
        # Skip near-constant columns (std == 0) — flat histograms are
        # never informative.
        useful = [c for c in numeric_cols if c.numeric_stats.get("std", 0) > 0]
        useful.sort(key=lambda c: -c.numeric_stats["std"])
        if useful:
            best = useful[0]
            try:
                png = chart_ops.histogram_png(
                    df[best.name],
                    title=f"Distribution of {best.name}",
                    xlabel=best.name,
                )
                return {"kind": "histogram", "column": best.name, "png_bytes": png}
            except Exception:
                pass

    # Fall back: low-cardinality string column → bar of counts
    cat_cols = [c for c in summary.columns if not c.numeric_stats and 1 < c.unique <= 20]
    if cat_cols:
        cat_cols.sort(key=lambda c: -c.unique)
        best = cat_cols[0]
        try:
            counts = df[best.name].value_counts(dropna=True).to_dict()
            counts = {str(k): int(v) for k, v in counts.items()}
            png = chart_ops.bar_png(
                counts,
                title=f"Counts of {best.name}",
                xlabel=best.name,
            )
            return {"kind": "bar", "column": best.name, "png_bytes": png}
        except Exception:
            pass

    return None


def _chart_filename(table_path: str, column: str, kind: str) -> str:
    """Render a filesystem-safe chart filename — readable but predictable."""
    base = _slug(table_path)
    col = _slug(column)
    return f"{base}__{col}__{kind}.png"


def _slug(s: str) -> str:
    """ASCII-safe slug: lowercase, replace non-alnum with hyphens, collapse."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "table"


def _chart_action(
    action_id: str,
    target_rel: str,
    png_bytes: bytes,
    chart: dict[str, Any],
    summary: DataFrameSummary,
) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.INDEX,
        target_path=target_rel,
        reason=(
            f"Generate {chart['kind']} of column {chart['column']!r} from "
            f"{summary.path} ({summary.rows_read} rows)."
        ),
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
        metadata={
            # PNG bytes round-tripped through base64 so plan.json stays
            # JSON-safe. Executor's _do_index decodes back to bytes.
            "binary_content_b64": base64.b64encode(png_bytes).decode("ascii"),
            "overwrite_existing": True,
            "chart_spec": {
                "kind": chart["kind"],
                "column": chart["column"],
                "source_table": summary.path,
            },
        },
    )


# --------------------------------------------------------------------- rendering


def _render_data_report_md(
    workspace_root: Path,
    summaries: list[DataFrameSummary],
    chart_links: dict[str, str] | None = None,
) -> str:
    chart_links = chart_links or {}
    lines: list[str] = []
    lines.append("# Data Report")
    lines.append("")
    lines.append(f"_{len(summaries)} tabular file(s) in `{workspace_root}`._")
    succeeded = sum(1 for s in summaries if s.error is None)
    lines.append(f"_Successfully parsed: {succeeded}/{len(summaries)}._")
    if chart_links:
        lines.append(f"_Charts generated: {len(chart_links)}._")
    lines.append("")
    lines.append("## Contents")
    lines.append("")
    for i, s in enumerate(summaries, start=1):
        anchor = _anchor_for(s.path)
        status = "ok" if s.error is None else "error"
        chart_note = " · chart" if s.path in chart_links else ""
        lines.append(f"{i}. [`{s.path}`](#{anchor}) — {status}{chart_note}")
    lines.append("")

    for s in summaries:
        anchor = _anchor_for(s.path)
        lines.append(f'### `{s.path}` <a id="{anchor}"></a>')
        lines.append("")
        if s.error:
            lines.append(f"**Read error**: {s.error}")
            lines.append("")
            continue
        if s.path in chart_links:
            lines.append(f"![chart for {s.path}]({chart_links[s.path]})")
            lines.append("")
        truncated_note = f" _(truncated to first {s.rows_read} rows)_" if s.rows_truncated else ""
        lines.append(f"- **Shape**: {s.rows_read} rows × {s.cols} cols{truncated_note}")
        lines.append("")
        lines.append("#### Schema & basic stats")
        lines.append("")
        lines.append("| Column | dtype | non-null | nulls | unique | numeric stats | samples |")
        lines.append("|---|---|---:|---:|---:|---|---|")
        for col in s.columns:
            stats = _fmt_numeric_stats(col.numeric_stats)
            samples = ", ".join(f"`{v}`" for v in col.sample_values) if col.sample_values else "—"
            lines.append(
                f"| `{col.name}` | {col.dtype} | {col.non_null} | {col.nulls} | "
                f"{col.unique if col.unique >= 0 else 'n/a'} | {stats} | {samples} |"
            )
        lines.append("")
        if s.sample_rows:
            lines.append("#### Sample rows")
            lines.append("")
            cols = [c.name for c in s.columns]
            lines.append("| " + " | ".join(f"`{c}`" for c in cols) + " |")
            lines.append("|" + "|".join(["---"] * len(cols)) + "|")
            for row in s.sample_rows:
                lines.append("| " + " | ".join(row.get(c, "") for c in cols) + " |")
            lines.append("")

    return "\n".join(lines)


def _fmt_numeric_stats(stats: dict[str, float] | None) -> str:
    if not stats:
        return "—"
    return (
        f"min={stats['min']:.3g} · max={stats['max']:.3g} · "
        f"mean={stats['mean']:.3g} · std={stats['std']:.3g}"
    )


def _anchor_for(path: str) -> str:
    return path.lower().replace("/", "-").replace(".", "").replace(" ", "-").replace("_", "-")


def _build_provenance(
    summaries: list[DataFrameSummary],
    chart_links: dict[str, str] | None = None,
) -> dict[str, Any]:
    """TaskWeaver-style audit trail: which source files contributed."""
    chart_links = chart_links or {}
    return {
        "synthesis_kind": "data_report",
        "sources": [
            {
                "path": s.path,
                "rows_read": s.rows_read,
                "cols": s.cols,
                "truncated": s.rows_truncated,
                "error": s.error,
                "chart": chart_links.get(s.path),
            }
            for s in summaries
        ],
    }
