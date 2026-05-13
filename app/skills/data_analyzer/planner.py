"""data_analyzer planner — Phase 3.3 rule path.

Walks every tabular file in the workspace and picks ONE meaningful
AnalysisSpec per file/sheet. The heuristic mirrors data_reporter's
chart picker but goes further: we don't just pick a column to plot, we
*compute* a groupby aggregation against it.

Phase 3.3b will add an ``LLMAnalysisPlanner`` that emits AnalysisSpec(s)
from natural language. The rule planner stays as the fallback / fast
path / test fixture / dev mode.
"""
from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path
from typing import Any

from app.schemas import ActionPlan, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, ActionType, RiskLevel
from app.schemas.analysis import (
    AggregationOp,
    AnalysisResult,
    AnalysisSpec,
    ChartRequest,
    GroupBy,
)
from app.tools import data_analysis, data_ops


DEFAULT_REPORT_PATH = "analysis_report.md"
CHARTS_DIR = "analysis_charts"
DESCRIBABLE_FILE_TYPES: frozenset[str] = frozenset({"tabular", "excel"})


def plan_data_analysis(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    *,
    output_path: str = DEFAULT_REPORT_PATH,
) -> ActionPlan:
    """Build an ActionPlan: one analysis_report.md + one chart per
    successfully-analyzed table.

    Pipeline per file:
      1. ``data_ops.read_tabular`` → DataFrame (or error)
      2. ``_choose_default_spec(df)`` → AnalysisSpec heuristic
      3. ``data_analysis.execute_analysis(df, spec)`` → AnalysisResult
      4. Markdown section + chart action emit

    The chart goes to ``analysis_charts/`` to avoid colliding with
    ``data_reporter``'s ``charts/``. Both skills can coexist in the
    same workspace.
    """
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    workspace_root = Path(snapshot.root)
    data_files = [f for f in snapshot.files if f.file_type in DESCRIBABLE_FILE_TYPES]

    if not data_files:
        return ActionPlan(
            plan_id=plan_id,
            task_id=task.task_id,
            summary="No CSV/TSV/Excel files found; nothing to analyze.",
            actions=[],
            expected_outputs=[],
            risk_summary="No-op plan, zero risk.",
        )

    results: list[AnalysisResult] = []
    chart_b64_by_result_idx: dict[int, str] = {}

    for meta in sorted(data_files, key=lambda f: f.path):
        abs_path = workspace_root / meta.path
        if not data_ops.is_supported_tabular(abs_path):
            continue
        for tr in data_ops.read_tabular(abs_path, meta.path):
            if tr.error is not None or tr.df is None:
                results.append(_error_result(tr.display_path, tr.error or "unreadable"))
                continue

            spec = _choose_default_spec(tr.display_path, tr.df, _sheet_name_or_none(tr.display_path))
            if spec is None:
                results.append(_skip_result(tr.display_path, "no analyzable columns"))
                continue

            result = data_analysis.execute_analysis(tr.df, spec)
            results.append(result)

    return build_plan_from_results(
        plan_id=plan_id,
        task=task,
        workspace_root=workspace_root,
        results=results,
        input_file_count=len(data_files),
        output_path=output_path,
    )


def build_plan_from_results(
    *,
    plan_id: str,
    task: TaskSpec,
    workspace_root: Path,
    results: list[AnalysisResult],
    input_file_count: int,
    output_path: str = DEFAULT_REPORT_PATH,
) -> ActionPlan:
    """Phase 3.3b: package a list of ``AnalysisResult`` into an ``ActionPlan``.

    Pure shaping logic — no I/O, no LLM. Used by both the rule planner
    above and the LLM planner in ``app/agent/analysis_planner.py``.
    Keeping it here means a single source of truth for how analyses
    are turned into actions: one markdown report action plus one chart
    action per result that produced a chart.
    """
    chart_b64_by_result_idx: dict[int, str] = {
        idx: r.chart_png_b64
        for idx, r in enumerate(results)
        if r.chart_png_b64 is not None
    }

    # Build the chart write actions BEFORE the report action so the
    # report can reference relative chart paths in its markdown body.
    chart_actions: list[Action] = []
    chart_paths_by_result_idx: dict[int, str] = {}
    counter = 1
    for idx, b64 in chart_b64_by_result_idx.items():
        counter += 1
        action_id = f"a-{counter:03d}"
        result = results[idx]
        chart_x = result.spec.chart.x if result.spec.chart else "x"
        chart_kind = result.spec.chart.kind if result.spec.chart else "bar"
        chart_rel = f"{CHARTS_DIR}/{_slug(result.spec.source_file)}__{_slug(chart_x)}__{chart_kind}.png"
        chart_actions.append(_chart_action(action_id, chart_rel, b64, result))
        chart_paths_by_result_idx[idx] = chart_rel

    report_md = _render_report_md(workspace_root, results, chart_paths_by_result_idx)
    report_action = Action(
        action_id="a-001",
        action_type=ActionType.INDEX,
        target_path=output_path,
        reason=(
            f"Synthesize a typed-analysis report across {input_file_count} input file(s) "
            f"({len(results)} analyses run, {sum(1 for r in results if r.outcome.value == 'ok')} succeeded)."
        ),
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=True,
        metadata={
            "content": report_md,
            "overwrite_existing": True,
            "provenance": _build_provenance(results, chart_paths_by_result_idx),
        },
    )

    all_actions = [report_action, *chart_actions]
    expected = [output_path, *chart_paths_by_result_idx.values()]

    ok_count = sum(1 for r in results if r.outcome.value == "ok")
    return ActionPlan(
        plan_id=plan_id,
        task_id=task.task_id,
        summary=(
            f"Run {len(results)} typed AnalysisSpec(s) across {input_file_count} file(s); "
            f"{ok_count} succeeded, {len(results) - ok_count} skipped/errored. "
            f"Output: {output_path} + {len(chart_actions)} chart(s)."
        ),
        actions=all_actions,
        expected_outputs=expected,
        risk_summary=(
            "Low risk: markdown write + PNG chart writes, all reversible. "
            "Source data is never modified — pandas operations are pure."
        ),
    )


# --------------------------------------------------------------------- spec heuristic


def _choose_default_spec(
    source_file: str, df: Any, sheet: str | None,
) -> AnalysisSpec | None:
    """Pick a sensible default analysis for a DataFrame.

    Heuristic:
      1. Find the categorical column with the lowest cardinality
         between 2..15 (good groupby key).
      2. Find the numeric column with the highest std (most interesting
         to aggregate).
      3. Produce: groupby(cat).agg({num: mean}) + bar chart.
      4. Fall back: if no categorical found, just histogram the numeric.
      5. Fall back: if no numeric found, skip.
    """
    import pandas as pd

    numeric_cols: list[tuple[str, float]] = []
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_numeric_dtype(s):
            try:
                std = float(s.dropna().std())
            except Exception:
                std = 0.0
            if not pd.isna(std) and std > 0:
                numeric_cols.append((str(col), std))

    cat_cols: list[tuple[str, int]] = []
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_numeric_dtype(s):
            continue
        try:
            uniq = int(s.dropna().nunique())
        except Exception:
            uniq = -1
        if 2 <= uniq <= 15:
            cat_cols.append((str(col), uniq))

    numeric_cols.sort(key=lambda t: -t[1])  # highest std first
    cat_cols.sort(key=lambda t: t[1])  # lowest cardinality first (most readable bar)

    if numeric_cols and cat_cols:
        num_name, _ = numeric_cols[0]
        cat_name, _ = cat_cols[0]
        return AnalysisSpec(
            source_file=source_file.split("  (sheet:")[0].strip(),
            sheet=sheet,
            groupby=GroupBy(by=[cat_name], aggregations={num_name: AggregationOp.MEAN}),
            sort_by=[num_name],
            sort_descending=True,
            chart=ChartRequest(
                kind="bar",
                x=cat_name,
                y=num_name,
                title=f"Mean {num_name} by {cat_name}",
            ),
        )

    if numeric_cols:
        num_name, _ = numeric_cols[0]
        return AnalysisSpec(
            source_file=source_file.split("  (sheet:")[0].strip(),
            sheet=sheet,
            chart=ChartRequest(
                kind="histogram",
                x=num_name,
                title=f"Distribution of {num_name}",
            ),
        )

    return None


def _sheet_name_or_none(display_path: str) -> str | None:
    """Extract the sheet name out of a display_path of the form
    ``foo.xlsx  (sheet: Sheet1)``. Returns None for plain CSV paths."""
    m = re.search(r"\(sheet:\s*(.+?)\)\s*$", display_path)
    if m:
        return m.group(1).strip()
    return None


# --------------------------------------------------------------------- rendering


def _chart_action(action_id: str, target_rel: str, png_b64: str, result: AnalysisResult) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.INDEX,
        target_path=target_rel,
        reason=(
            f"Render {result.spec.chart.kind} chart for analysis of "
            f"{result.spec.source_file}."
        ),
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
        metadata={
            "binary_content_b64": png_b64,
            "overwrite_existing": True,
            "chart_spec": result.spec.chart.model_dump(),
        },
    )


def _render_report_md(
    workspace_root: Path,
    results: list[AnalysisResult],
    chart_paths_by_idx: dict[int, str],
) -> str:
    lines: list[str] = []
    lines.append("# Data Analysis Report")
    lines.append("")
    lines.append(f"_Workspace: `{workspace_root}`_")
    lines.append(f"_{len(results)} analyses run._")
    succeeded = sum(1 for r in results if r.outcome.value == "ok")
    lines.append(f"_Succeeded: {succeeded}/{len(results)}._")
    lines.append("")

    lines.append("## Contents")
    lines.append("")
    for i, r in enumerate(results, start=1):
        anchor = _anchor_for(r.spec.source_file, i)
        status = r.outcome.value
        lines.append(f"{i}. [`{r.spec.source_file}`](#{anchor}) — {status}")
    lines.append("")

    for idx, r in enumerate(results):
        anchor = _anchor_for(r.spec.source_file, idx + 1)
        lines.append(f"### `{r.spec.source_file}` <a id=\"{anchor}\"></a>")
        lines.append("")
        lines.append(f"**Outcome**: `{r.outcome.value}`")
        if r.error and r.outcome.value not in ("ok",):
            lines.append("")
            lines.append(f"**Error**: {r.error}")
        if r.summary:
            lines.append("")
            lines.append(f"**Summary**: {r.summary}")
        if idx in chart_paths_by_idx:
            lines.append("")
            lines.append(f"![chart for {r.spec.source_file}]({chart_paths_by_idx[idx]})")
        lines.append("")

        if r.rows:
            cols = r.columns or list(r.rows[0].keys())
            lines.append("#### Result")
            lines.append("")
            lines.append("| " + " | ".join(f"`{c}`" for c in cols) + " |")
            lines.append("|" + "|".join(["---"] * len(cols)) + "|")
            for row in r.rows[:50]:
                cells = [_md_cell(row.get(c)) for c in cols]
                lines.append("| " + " | ".join(cells) + " |")
            if r.row_count > len(r.rows):
                lines.append("")
                lines.append(f"_({r.row_count - len(r.rows)} more row(s) not shown)_")
            lines.append("")
        elif r.outcome.value == "ok":
            lines.append("")
            lines.append("_(empty result)_")
            lines.append("")

    return "\n".join(lines)


def _anchor_for(path: str, idx: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return f"{base}-{idx}"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def _md_cell(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if len(s) > 60:
        return s[:57] + "..."
    return s.replace("|", "\\|")


def _build_provenance(
    results: list[AnalysisResult],
    chart_paths_by_idx: dict[int, str],
) -> dict[str, Any]:
    return {
        "synthesis_kind": "data_analysis",
        "analyses": [
            {
                "source_file": r.spec.source_file,
                "sheet": r.spec.sheet,
                "outcome": r.outcome.value,
                "row_count": r.row_count,
                "spec": r.spec.model_dump(),
                "chart": chart_paths_by_idx.get(idx),
            }
            for idx, r in enumerate(results)
        ],
    }


# --------------------------------------------------------------------- error helpers


def _error_result(display_path: str, msg: str) -> AnalysisResult:
    from app.schemas.analysis import AnalysisOutcome, AnalysisSpec

    return AnalysisResult(
        spec=AnalysisSpec(source_file=display_path),
        outcome=AnalysisOutcome.READ_ERROR,
        error=msg,
        summary=f"{display_path}: read_error — {msg}",
    )


def _skip_result(display_path: str, reason: str) -> AnalysisResult:
    from app.schemas.analysis import AnalysisOutcome, AnalysisSpec

    return AnalysisResult(
        spec=AnalysisSpec(source_file=display_path),
        outcome=AnalysisOutcome.INVALID_SPEC,
        error=reason,
        summary=f"{display_path}: skipped — {reason}",
    )
