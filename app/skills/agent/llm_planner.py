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

from app.schemas import ActionPlan
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
2. **Read previews first.** For every file with a TEXT PREVIEW, scan the first lines: a PDF preview revealing "Attention Is All You Need" is a paper on transformers; a .md preview starting with "# Project Roadmap" is a project plan. Let content override file_type when they conflict.
3. **Category directories**: default to papers/, documents/, spreadsheets/, data/, notes/, images/, audio/, video/, archives/, code/, misc/. Use more specific names when previews reveal a strong semantic theme.
4. **Semantic rename is encouraged** when the preview reveals a meaningful title. Example: a PDF named `paper_v3_final.pdf` whose preview's first line is "Agent Memory: A Survey" → propose `move source=paper_v3_final.pdf target=papers/agent-memory-survey.pdf` (kebab-case, lowercased).
5. If a file already lives at the top of its target category AND has a sensible name, do NOT propose moving it.
6. **Duplicate report**: if any sha256 collisions exist, emit one `index` writing `duplicates_report.md`.
7. **Compound goals**: if the user listed multiple steps (含「然后/再/最后/then/finally/and」), every step must show up as one or more actions in your plan. Missing steps will leave the user unhappy.

# Output
Call the `submit_action_plan` tool exactly once. Make sure every `action_id` is unique, every path is workspace-relative, the action_type is one of the six allowed values, every `index`/`summarize` action writing text has non-empty `metadata.content`, and every chart action has a complete `metadata.chart_request` with integer counts.
"""


class _ChartPostProcessError(RuntimeError):
    """Raised when an action's chart_request can't be rendered. The
    skill's plan_with_llm wrapper catches this and falls back to a
    markdown placeholder so one bad chart doesn't sink the whole plan.
    """


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
