"""Rule-based planner for the workspace_visualizer skill.

Two layouts the planner handles cleanly:

  1. **Files already organized** into subfolders (e.g. user ran
     folder_organizer first → `papers/`, `images/`, `notes/`, …).
     The planner counts by the top-level subdirectory name.
  2. **Flat workspace** — files sitting at the root, classified by
     ``file_type`` from the snapshot. The planner counts by category.

The output is a small fixed set of actions:

  * Optional ``mkdir images`` if `images/` doesn't exist yet (so the
    PNG has somewhere to live).
  * One ``index`` action writing the PNG via ``binary_content_b64`` —
    same mechanism ``data_analyzer`` / ``data_reporter`` use for charts.
  * One ``index`` action writing a Markdown summary that **references**
    the PNG (image link, not mermaid).

If the workspace is empty the planner still emits a single summary
action describing the empty state — never zero actions.
"""

from __future__ import annotations

import base64
import uuid
from collections import Counter
from pathlib import PurePosixPath

from app.schemas import ActionPlan, TaskSpec, WorkspaceSnapshot
from app.schemas.action import Action, ActionType, RiskLevel
from app.tools import chart_ops

CHART_DIR = "images"
CHART_FILENAME = "file_counts.png"
SUMMARY_FILENAME = "file_counts_summary.md"

# When ≥ this fraction of files live inside a top-level subfolder, we
# infer "user has already organized this workspace" and group by folder
# instead of file_type.
SUBDIR_INFER_THRESHOLD = 0.6


def plan_workspace_visualization(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
) -> ActionPlan:
    plan_id = f"plan-{uuid.uuid4().hex[:8]}"
    actions: list[Action] = []
    expected_outputs: list[str] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"a-{counter:03d}"

    grouping_mode, counts = _decide_grouping(snapshot)
    chart_target = f"{CHART_DIR}/{CHART_FILENAME}"
    summary_target = SUMMARY_FILENAME

    if not _images_dir_exists(snapshot):
        actions.append(
            Action(
                action_id=next_id(),
                action_type=ActionType.MKDIR,
                target_path=CHART_DIR,
                reason=f"Create `{CHART_DIR}/` directory for the chart PNG.",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            )
        )

    chart_title, chart_xlabel = _labels_for(grouping_mode)
    png_bytes = _render_bar_chart(counts, title=chart_title, xlabel=chart_xlabel)
    actions.append(
        Action(
            action_id=next_id(),
            action_type=ActionType.INDEX,
            target_path=chart_target,
            reason=(
                f"Bar chart of file counts ({grouping_mode}) across "
                f"{snapshot.total_files} file(s) in {len(counts)} group(s)."
            ),
            risk_level=RiskLevel.LOW,
            reversible=True,
            requires_approval=False,
            metadata={
                "binary_content_b64": base64.b64encode(png_bytes).decode("ascii"),
                "overwrite_existing": True,
                "chart_spec": {
                    "kind": "bar",
                    "grouping": grouping_mode,
                    "groups": dict(counts),
                },
            },
        )
    )
    expected_outputs.append(chart_target)

    summary_md = _render_summary_md(
        task=task,
        snapshot=snapshot,
        grouping_mode=grouping_mode,
        counts=counts,
        chart_rel_path=chart_target,
    )
    actions.append(
        Action(
            action_id=next_id(),
            action_type=ActionType.INDEX,
            target_path=summary_target,
            reason="Markdown summary referencing the chart PNG.",
            risk_level=RiskLevel.LOW,
            reversible=True,
            requires_approval=False,
            metadata={
                "content": summary_md,
                "overwrite_existing": True,
            },
        )
    )
    expected_outputs.append(summary_target)

    summary_line = (
        f"Counted {snapshot.total_files} file(s) by {grouping_mode}; "
        f"rendered bar chart with {len(counts)} group(s)."
    )
    return ActionPlan(
        plan_id=plan_id,
        task_id=task.task_id,
        summary=summary_line,
        actions=actions,
        expected_outputs=expected_outputs,
    )


# --------------------------------------------------------------------- internals


def _decide_grouping(snapshot: WorkspaceSnapshot) -> tuple[str, Counter[str]]:
    """Choose between 'folder' and 'file_type' grouping.

    Heuristic: if at least SUBDIR_INFER_THRESHOLD of files live inside a
    top-level subdirectory (excluding the chart's own ``images/`` dir
    when it would dominate trivially), group by parent folder. This is
    the post-organize case. Otherwise group by ``file_type`` from the
    snapshot — the pre-organize / flat case.
    """
    if snapshot.total_files == 0:
        return "file_type", Counter()

    subdir_count = 0
    folder_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()

    for f in snapshot.files:
        parts = PurePosixPath(f.path).parts
        if len(parts) >= 2:
            subdir_count += 1
            folder_counts[parts[0]] += 1
        type_counts[f.file_type] += 1

    ratio = subdir_count / max(snapshot.total_files, 1)
    if ratio >= SUBDIR_INFER_THRESHOLD and len(folder_counts) >= 2:
        return "folder", folder_counts
    return "file_type", type_counts


def _images_dir_exists(snapshot: WorkspaceSnapshot) -> bool:
    """True if any file in the snapshot lives under ``CHART_DIR/``."""
    prefix = f"{CHART_DIR}/"
    return any(f.path.startswith(prefix) for f in snapshot.files)


def _labels_for(grouping_mode: str) -> tuple[str, str]:
    if grouping_mode == "folder":
        return ("File counts by folder", "folder")
    return ("File counts by category", "category")


def _render_bar_chart(counts: Counter[str], *, title: str, xlabel: str) -> bytes:
    """Render PNG via chart_ops.bar_png. The chart_ops module handles
    empty input by drawing a 'no data' placeholder, so this never
    raises on an empty workspace."""
    return chart_ops.bar_png(dict(counts), title=title, xlabel=xlabel)


def _render_summary_md(
    *,
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    grouping_mode: str,
    counts: Counter[str],
    chart_rel_path: str,
) -> str:
    lines: list[str] = []
    lines.append("# Workspace file-count summary")
    lines.append("")
    lines.append(f"_Workspace: `{snapshot.root}`_")
    lines.append(f"_Total files: **{snapshot.total_files}** · grouped by **{grouping_mode}**._")
    lines.append("")
    lines.append("## Bar chart")
    lines.append("")
    lines.append(f"![File counts]({chart_rel_path})")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    if not counts:
        lines.append("_(workspace is empty — chart shows a placeholder)_")
    else:
        lines.append("| Group | Count |")
        lines.append("|---|---:|")
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{name}` | {n} |")
    lines.append("")
    lines.append("---")
    lines.append(f"_Generated by `workspace_visualizer` for task `{task.task_id}`._")
    return "\n".join(lines)
