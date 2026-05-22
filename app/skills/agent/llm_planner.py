"""LLM prompt + chart_request post-processor for the agent meta-skill.

The system prompt extends folder_organizer's instructions with the
chart-rendering convention used by data_analyzer / data_reporter:

  - Chart actions are normal ``INDEX`` actions targeting a ``.png``
    path with ``metadata.chart_request = {...}`` instead of
    ``metadata.content``.
  - After the LLM submits its plan, :func:`render_chart_actions`
    walks every action and substitutes ``binary_content_b64`` for
    ``chart_request`` so the harness executor (which already
    base64-decodes binary content) can write the file unchanged.

The LLM never sees the actual PNG bytes — it only describes WHAT to
chart. Python renders. This keeps plan.json human-readable and small.
"""

from __future__ import annotations

import base64
from typing import Any

from app.schemas import ActionPlan, TaskSpec, WorkspaceSnapshot
from app.schemas.action import ActionType
from app.tools import chart_ops

# NOTE: long lines inside AGENT_SYSTEM_PROMPT below are literal prose
# delivered to the LLM as paragraphs (same as app/agent/prompts.py).
# Wrapping mid-paragraph via implicit concatenation would insert no
# newline — same content — but make the source noticeably more verbose
# without changing what the LLM sees. Existing tests
# (`test_system_prompt_documents_chart_request_convention`) pin
# substrings that any rewrap must preserve. Ruff format intentionally
# leaves triple-quoted strings alone.
AGENT_SYSTEM_PROMPT = """You are the LocalFlow Agent — an autonomous file-system planner that produces a SINGLE ActionPlan covering a compound user goal end-to-end (no follow-up tasks required, no manual hand-off).

# Your role
Given a workspace snapshot and a user goal, propose ONE structured ActionPlan that completes the WHOLE goal. The Harness Kernel — not you — performs the actual filesystem operations. You produce plans; the harness validates, dry-runs, requests approval, executes, and verifies.

You have NO direct filesystem access. The workspace snapshot you receive is your only ground truth. For some files the snapshot includes a leading TEXT PREVIEW (first ~2000 chars for PDFs / .md / .txt / .csv / source code / structured config). When a preview is available, USE IT for semantically grounded decisions: classify by topic not extension, propose meaningful rename targets, group related papers/projects together. When a preview is absent (binary files, images, encrypted PDFs, etc.), fall back to filename + extension.

# Hard rules (the harness will reject violators — DO NOT propose them)
1. **No `delete` action.** It is not in the allowed action types. If duplicate files exist (same sha256), emit an `index` action that writes a `duplicates_report.md` listing them — never propose deleting one.
2. **All paths are RELATIVE to workspace_root.** Never use absolute paths, never use `..`, never reference paths outside the workspace.
3. **No overwriting between two writes.** Do not point two move/copy actions at the same target. If you intentionally overwrite an existing on-disk file with an `index` (e.g. regenerating a report), set `metadata.overwrite_existing=true` on that action.
4. **Every action has a unique `action_id`.** Use `a-001`, `a-002`, ... in order.
5. **Order matters.** Emit `mkdir` actions before the moves that depend on them. Emit chart actions LAST, after all moves — your chart's counts must reflect the post-move state.
6. **For destructive-looking writes (move/rename/copy):** set `risk_level="medium"`, `reversible=true`, `requires_approval=true`. For directory creation: `risk_level="low"`, `requires_approval=true`. For `index` actions: `risk_level="low"`, `requires_approval=false`.
7. **Every action MUST emit every field**, even when not applicable — use `null` for fields that don't apply:
   - For `mkdir`: `source_path: null`, `confidence: null` (or a number), `metadata: {"content": null}`.
   - For `move`/`copy`/`rename`: `confidence: null` (or a number), `metadata: {"content": null}`.
   - For `index`/`summarize` writing TEXT: `source_path: null`, `metadata: {"content": "..."}` REQUIRED.
   - For `index` writing a PNG CHART: `source_path: null`, `metadata: {"content": null, "chart_request": {...}}` (see below).

# Allowed action types
- `mkdir`: create a directory. `target_path` required, no `source_path`.
- `move`: move a file. `source_path` and `target_path` both required and different.
- `copy`: copy a file. `source_path` and `target_path` both required and different.
- `rename`: rename a file (same parent dir). Identical to `move` mechanically.
- `index`: write either a markdown report OR a PNG bar chart. `target_path` required.
  * For MARKDOWN: `target_path` ends with `.md`; `metadata.content` MUST contain the markdown body as a single string.
  * For PNG CHART: `target_path` ends with `.png`; `metadata.chart_request` MUST contain the chart spec (see below). `metadata.content` MUST be null.
- `summarize`: identical to `index` for markdown output.

# Chart action convention (REAL PNG output, not mermaid)
When the user asks you to draw / plot / chart / visualize / 绘制 / 可视化 / 柱状图 / 图表 / 图象, emit ONE `index` action with these EXACT fields:

  - `target_path`: a path ending in `.png` (e.g. `images/file_counts.png` or `charts/categories.png`).
  - `metadata.content`: must be `null`.
  - `metadata.chart_request`: a dict with FOUR keys, ALL REQUIRED:
      * `"kind"`: literally the string `"bar"` (only bar charts in v0.9.0).
      * `"title"`: a chart title (string).
      * `"xlabel"`: an x-axis label (string).
      * `"counts"`: a LIST of `{"label": "<category>", "value": <integer>}` objects — these counts MUST reflect the POST-move workspace state (i.e. how many files end up in each category dir after your move actions execute). The list form is required by the tool schema (dict-with-dynamic-keys is not allowed).
  - `metadata.overwrite_existing`: `true` (charts are regenerable artifacts).

⚠ CRITICAL: a PNG `target_path` without a `chart_request` block is treated as a planner error and downgraded to an error-markdown placeholder. If you emit a chart, you MUST include `chart_request`. The harness post-processor then renders the PNG via `chart_ops.bar_png(counts, title, xlabel)` and substitutes the bytes. You do NOT emit `binary_content_b64` yourself.

**Example chart action** (for a goal that asked for a bar chart of files per category):
```
{
  "action_id": "a-099",
  "action_type": "index",
  "source_path": null,
  "target_path": "images/file_counts.png",
  "reason": "Bar chart of file counts per category, post-organization.",
  "risk_level": "low",
  "reversible": true,
  "requires_approval": false,
  "metadata": {
    "content": null,
    "chart_request": {
      "kind": "bar",
      "title": "Files per category",
      "xlabel": "category",
      "counts": [
        {"label": "papers", "value": 3},
        {"label": "images", "value": 4},
        {"label": "notes", "value": 2},
        {"label": "spreadsheets", "value": 1}
      ]
    },
    "overwrite_existing": true
  }
}
```

# How to think about a compound task
1. **Decompose the goal into atomic steps.** A goal like "整理 + 总结 + 绘制柱状图" implies:
   - (a) Organize: mkdir + move actions to categorize files.
   - (b) Summarize: one `index` action per category writing `<category>/index.md` describing the files now living there.
   - (c) Visualize: one `index` action emitting `images/file_counts.png` with `chart_request` whose counts reflect the post-move category sizes.
   Emit ALL of (a), (b), (c) in ONE plan — do not assume the user will run a follow-up task.
   The harness performs a compound-goal coverage check: if the goal explicitly asks to organize, summarize/index, or chart/visualize and your plan omits that step, your plan is rejected and you must repair it.
2. **Read previews first.** For every file with a TEXT PREVIEW, scan the first lines: a PDF preview revealing "Attention Is All You Need" is a paper on transformers; a .md preview starting with "# Project Roadmap" is a project plan. Let content override file_type when they conflict.
3. **Category directories**: default to papers/, documents/, spreadsheets/, data/, notes/, images/, audio/, video/, archives/, code/, misc/. Use more specific names when previews reveal a strong semantic theme.
4. **Semantic rename is encouraged** when the preview reveals a meaningful title. Example: a PDF named `paper_v3_final.pdf` whose preview's first line is "Agent Memory: A Survey" → propose `move source=paper_v3_final.pdf target=papers/agent-memory-survey.pdf` (kebab-case, lowercased).
5. If a file already lives at the top of its target category AND has a sensible name, do NOT propose moving it.
6. **Duplicate report**: if any sha256 collisions exist, emit one `index` writing `duplicates_report.md`.
7. **Compound goals**: if the user listed multiple steps (含「然后/再/最后/then/finally/and」), every step must show up as one or more actions in your plan. Missing steps are planner errors, not acceptable simplifications.
8. If the goal asks for a summary/index, include at least one text `index` or `summarize` action with non-empty `metadata.content`. If it asks for a chart/visualization, include a PNG `index` action with `metadata.chart_request`. Put chart actions after all organization actions so counts reflect the post-move state.

# Content-driven rename (v0.16.1 — explicit rules)
When the user's goal mentions "rename", "重命名", "改名", "title-based",
"内容命名", or asks for "中文文件名 based on content" / "Chinese
filenames from PDF content":

8. **You MUST emit one `rename` action per file with a usable preview**,
   even if the source filename looks reasonable. Use the PDF/text preview
   to extract a short descriptive title (max 40 chars). If the user asked
   for **Chinese names** ("中文重命名"), translate the title to Chinese
   yourself — the LLM you ARE is the translator; do not output English
   names just because the source is English.
9. **Preserve the source path's directory**: a rename from
   `papers/foo.pdf` targets `papers/<new-name>.pdf` (same parent, new
   basename). Use rename, not move — the harness treats them
   equivalently but rename keeps audit-log intent clearer.
10. **Files with no preview (encrypted PDFs, binary stubs, etc.)**:
    DO NOT rename. Add their original path to the plan summary's
    risk_summary line explicitly: "skipped renaming N file(s) without
    extractable text — keep original names". This is honest signalling;
    do NOT invent names from filenames alone.
11. **Filename safety**: kebab-case OR Chinese chars; never use `/`,
    `\\`, `:`, `?`, `*`, `"`, `<`, `>`, `|` in any rename target.
    Truncate to 40 chars + extension. If two files would collide,
    suffix `-2` / `-3` to disambiguate.

# Vague data-analysis goals (v0.16.1 — when the user is unclear)
When the goal mentions "analyze", "解读", "分析数据", "画图", "chart",
"统计" AND the workspace contains .csv / .xlsx, the agent meta-skill is
the WRONG planner — defer to the `data_analyzer` skill via the harness's
routing logic. Specifically: if you receive such a goal and your
TaskSpec.skill is "agent", you should still attempt the plan, but
explicitly note in the risk_summary that "this goal would be better
served by `data_analyzer` — the user should re-run with --skill
data_analyzer for deeper aggregation + chart synthesis". The user's UI
auto-routes goals containing 分析/解读/统计 + workspace .xlsx to
data_analyzer automatically.

# Output
Call the `submit_action_plan` tool exactly once. Make sure every `action_id` is unique, every path is workspace-relative, the action_type is one of the six allowed values, every `index`/`summarize` action writing text has non-empty `metadata.content`, and every chart action has a complete `metadata.chart_request` with integer counts.
"""


class _ChartPostProcessError(RuntimeError):
    """Raised when an action's chart_request can't be rendered. The
    skill's plan_with_llm wrapper catches this and falls back to a
    markdown placeholder so one bad chart doesn't sink the whole plan.
    """


_ORGANIZE_HINTS = (
    "organize",
    "organise",
    "sort",
    "group",
    "categorize",
    "categorise",
    "classify",
    "arrange",
    "整理",
    "分类",
    "归类",
    "按类型",
    "移动",
    "收纳",
)
_SUMMARY_HINTS = (
    "summarize",
    "summarise",
    "summary",
    "index",
    "catalog",
    "catalogue",
    "write a report",
    "write report",
    "generate a report",
    "generate report",
    "总结",
    "汇总",
    "摘要",
    "索引",
    "生成报告",
    "写报告",
    "概览",
)
_CHART_HINTS = (
    "chart",
    "plot",
    "visualize",
    "visualise",
    "graph",
    "bar chart",
    "draw",
    "画图",
    "绘制",
    "可视化",
    "图表",
    "柱状图",
)
_COMPOUND_MARKERS = (
    " then ",
    " and then ",
    " finally ",
    " after that ",
    " next ",
    "然后",
    "最后",
    "接着",
    "之后",
    "再",
)
_ORGANIZE_ACTIONS = {
    ActionType.MKDIR,
    ActionType.MOVE,
    ActionType.RENAME,
    ActionType.COPY,
}
_TEXT_OUTPUT_ACTIONS = {ActionType.INDEX, ActionType.SUMMARIZE}


def validate_compound_goal_coverage(
    task: TaskSpec,
    snapshot: WorkspaceSnapshot,
    plan: ActionPlan,
) -> list[str]:
    """Reject agent plans that are safe but incomplete for explicit
    compound goals.

    This runs before chart post-processing so chart requests are still
    visible as ``metadata.chart_request``. It deliberately checks only
    the presence and ordering of user-requested steps; content quality is
    left to the LLM and later semantic verifiers.
    """
    goal = task.user_goal or ""
    wants_organize = _has_any(goal, _ORGANIZE_HINTS)
    wants_summary = _has_any(goal, _SUMMARY_HINTS)
    wants_chart = _has_any(goal, _CHART_HINTS)
    has_compound_marker = _has_any(goal, _COMPOUND_MARKERS)
    explicit_step_count = sum(1 for flag in (wants_organize, wants_summary, wants_chart) if flag)

    # Single-step goals still get their explicit requirement checked, but
    # completely unrelated requests should not be forced into the
    # organize/summarize/chart template.
    if explicit_step_count == 0 and not has_compound_marker:
        return []

    errors: list[str] = []
    if wants_organize and _has_top_level_files(snapshot) and not _has_organize_action(plan):
        errors.append(
            "compound goal requests file organization, but the plan has no mkdir/move/"
            "rename/copy action for the top-level workspace files."
        )
    if wants_summary and not _has_text_summary_output(plan):
        errors.append(
            "compound goal requests a summary/index, but the plan has no text "
            "index/summarize action with non-empty metadata.content."
        )
    if wants_chart and not _has_chart_output(plan):
        errors.append(
            "compound goal requests a chart/visualization, but the plan has no PNG "
            "index action with metadata.chart_request."
        )
    if wants_organize and wants_chart:
        order_error = _validate_chart_after_organization(plan)
        if order_error:
            errors.append(order_error)
    return errors


def _has_any(goal: str, hints: tuple[str, ...]) -> bool:
    goal_lower = goal.lower()
    return any(h.lower() in goal_lower for h in hints)


def _has_top_level_files(snapshot: WorkspaceSnapshot) -> bool:
    return any("/" not in f.path.replace("\\", "/") for f in snapshot.files)


def _has_organize_action(plan: ActionPlan) -> bool:
    return any(action.action_type in _ORGANIZE_ACTIONS for action in plan.actions)


def _has_text_summary_output(plan: ActionPlan) -> bool:
    for action in plan.actions:
        if action.action_type not in _TEXT_OUTPUT_ACTIONS:
            continue
        if (action.target_path or "").lower().endswith(".png"):
            continue
        content = action.metadata.get("content") if action.metadata else None
        if isinstance(content, str) and content.strip():
            return True
    return False


def _has_chart_output(plan: ActionPlan) -> bool:
    for action in plan.actions:
        target = (action.target_path or "").lower()
        if action.action_type == ActionType.INDEX and target.endswith(".png"):
            if isinstance(action.metadata.get("chart_request"), dict):
                return True
    return False


def _validate_chart_after_organization(plan: ActionPlan) -> str | None:
    org_positions = [
        idx for idx, action in enumerate(plan.actions) if action.action_type in _ORGANIZE_ACTIONS
    ]
    chart_positions = [
        idx
        for idx, action in enumerate(plan.actions)
        if action.action_type == ActionType.INDEX
        and (action.target_path or "").lower().endswith(".png")
        and isinstance(action.metadata.get("chart_request"), dict)
    ]
    if not org_positions or not chart_positions:
        return None
    if min(chart_positions) <= max(org_positions):
        return (
            "compound goal chart action must come after organization actions so "
            "chart counts reflect the post-move workspace state."
        )
    return None


def render_chart_actions(plan: ActionPlan) -> ActionPlan:
    """Walk ``plan.actions`` in place: for each action with a
    ``chart_request`` metadata block, render a PNG via ``chart_ops`` and
    substitute it as ``binary_content_b64``. Returns the same plan
    object (mutated). Bad chart specs are downgraded to a markdown
    error placeholder rather than crashing the whole plan — the user
    sees an explicit "chart could not be rendered" file instead of a
    silent miss.
    """
    for action in plan.actions:
        if action.action_type != ActionType.INDEX:
            continue
        target = (action.target_path or "").lower()
        spec = action.metadata.get("chart_request")

        # Case: PNG target without chart_request AND without already-rendered
        # binary content. The LLM dropped the chart_request block — downgrade
        # to a markdown error placeholder so the plan still validates.
        if (
            target.endswith(".png")
            and not isinstance(spec, dict)
            and not action.metadata.get("binary_content_b64")
        ):
            action.target_path = _png_to_md_target(action.target_path or "chart_error.png")
            action.metadata["content"] = (
                "# Chart could not be rendered\n\n"
                "The agent emitted a PNG action without a `chart_request` "
                "block, so the harness has no chart spec to render. This is "
                "an LLM-side mistake — the action was downgraded to this "
                "markdown placeholder so the rest of the plan can still run.\n"
            )
            action.metadata.pop("chart_request", None)
            continue

        if not isinstance(spec, dict):
            continue
        try:
            png_bytes = _render_one_chart(spec)
        except _ChartPostProcessError as exc:
            # Replace the PNG action with a Markdown error so the plan
            # still passes validation. The user sees what went wrong.
            action.target_path = _png_to_md_target(action.target_path or "chart_error.png")
            action.metadata["content"] = (
                "# Chart could not be rendered\n\n"
                f"The agent emitted a chart_request that the harness rejected: {exc}\n\n"
                f"```json\n{spec}\n```\n"
            )
            action.metadata.pop("chart_request", None)
            action.metadata.pop("binary_content_b64", None)
            continue
        action.metadata["binary_content_b64"] = base64.b64encode(png_bytes).decode("ascii")
        action.metadata["overwrite_existing"] = True
        # Keep chart_request as audit trail — the validator no longer
        # treats it as a missing-content signal because binary_content_b64
        # is present.
    return plan


# --------------------------------------------------------------------- internals


def _render_one_chart(spec: dict[str, Any]) -> bytes:
    kind = spec.get("kind", "bar")
    if kind != "bar":
        raise _ChartPostProcessError(
            f"unsupported chart kind {kind!r} — only 'bar' is implemented in v0.9.0"
        )
    clean_counts = _normalize_counts(spec.get("counts"))
    if not clean_counts:
        raise _ChartPostProcessError(
            "chart_request.counts must be a non-empty mapping or [{label, value}, ...] list"
        )
    title = str(spec.get("title", "File counts"))
    xlabel = str(spec.get("xlabel", "category"))
    return chart_ops.bar_png(clean_counts, title=title, xlabel=xlabel)


def _normalize_counts(counts: Any) -> dict[str, int]:
    """Accept both shapes the LLM might emit:

      * Dict form: ``{"papers": 3, "images": 4}``
      * Array form: ``[{"label": "papers", "value": 3}, ...]`` — required by
        OpenAI strict mode, which doesn't allow dicts with dynamic keys.

    LLMs sometimes send strings instead of ints ("3" instead of 3); coerce.
    Raise :class:`_ChartPostProcessError` on any other shape so the
    defensive downgrade path kicks in.
    """
    if isinstance(counts, dict):
        items = counts.items()
    elif isinstance(counts, list):
        items = []
        for entry in counts:
            if not isinstance(entry, dict):
                raise _ChartPostProcessError(
                    f"chart_request.counts list entry must be {{label, value}}, got {entry!r}"
                )
            if "label" not in entry or "value" not in entry:
                raise _ChartPostProcessError(
                    f"chart_request.counts entry missing label/value: {entry!r}"
                )
            items.append((entry["label"], entry["value"]))
    else:
        raise _ChartPostProcessError(
            f"chart_request.counts must be dict or list, got {type(counts).__name__}"
        )
    try:
        return {str(k): int(v) for k, v in items}
    except (TypeError, ValueError) as exc:
        raise _ChartPostProcessError(f"chart_request.counts has non-integer value: {exc}") from exc


def _png_to_md_target(target: str) -> str:
    if target.lower().endswith(".png"):
        return target[:-4] + "_error.md"
    return target + ".md"
