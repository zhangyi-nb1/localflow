"""Phase 3.3b — LLM prompts + tool schema for AnalysisSpec generation.

The LLM never writes Python. It outputs a typed ``AnalysisSpec`` via
the strict tool call below, and LocalFlow's ``data_analysis`` engine
translates that spec into pandas calls. This is the same shape as the
ActionPlan path in Phase 1: hand-written JSON schema in OpenAI strict
mode (every property in ``required``; nullable types for optionals).

Filenames in spec.source_file are the workspace-relative paths the
planner already showed the model — same convention as everywhere else.
"""
from __future__ import annotations

from typing import Any

from app.schemas import TaskSpec, WorkspaceSnapshot
from app.tools.data_ops import is_supported_tabular
from pathlib import Path


TOOL_NAME = "submit_analysis_spec"
TOOL_DESCRIPTION = (
    "Submit a single typed AnalysisSpec describing the analysis to run. "
    "LocalFlow will execute the spec via pandas (you do NOT write code). "
    "The pipeline is: filters → groupby → sort → limit → chart. "
    "Pick a meaningful default if the user goal is vague."
)


SYSTEM_PROMPT = """You are the analysis planner for LocalFlow's data_analyzer skill.

## Your role
Given a workspace listing of CSV/Excel files and a user goal, you produce ONE typed AnalysisSpec describing the analysis. LocalFlow's engine — not you — runs the pandas operations.

You do NOT write Python code. You do NOT execute anything. You ONLY emit a typed schema via the `submit_analysis_spec` tool call.

## Hard rules (LocalFlow will reject violators)
1. `source_file` MUST be one of the relative paths shown in the workspace summary. No invented paths.
2. Every column referenced (in filters, groupby, sort_by, chart) MUST appear in the source file's schema.
3. `aggregations` must be a non-empty map: `{column_name: AggregationOp}`.
4. For Excel files, set `sheet` to the sheet name shown in the summary. For CSV, leave it null.
5. Filter values: strings stay strings; numeric comparisons need numbers. For `is_null`/`not_null`, leave `value` as null.
6. If the user goal is ambiguous, pick the single most informative analysis (high-cardinality numeric × low-cardinality categorical → groupby+mean+bar chart is usually right).
7. Chart kind options: `bar` (needs y), `histogram` (only x, on a numeric column), `line` (needs y, x must be numeric or ordered).
8. After groupby, the result frame's columns are: the groupby keys + the aggregation result columns (named after the source column, not "amount_mean"). Plan chart axes accordingly.

## How to think
- Read the user goal.
- Pick ONE source file most relevant to the goal.
- Decide which columns matter; build filters → groupby → chart accordingly.
- Sort by the metric the user cares about (often the aggregation result).
- Limit to a reasonable display size if needed.

Output the spec via `submit_analysis_spec`. No prose, just the tool call.
"""


def build_analysis_spec_tool_schema() -> dict[str, Any]:
    """Hand-written JSON Schema for the AnalysisSpec tool call.

    OpenAI strict mode requirements baked in: every property in
    ``required``; optionals use nullable union types (``["string", "null"]``);
    every object has ``additionalProperties: false``.
    """
    filter_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "column": {"type": "string", "description": "Column name in the source file."},
            "op": {
                "type": "string",
                "enum": ["==", "!=", ">", "<", ">=", "<=", "in", "not_in", "is_null", "not_null", "contains"],
                "description": "Comparison operator. is_null/not_null ignore value.",
            },
            "value": {
                # Strict mode supports union types with multiple JSON types,
                # BUT when "array" is in the union we MUST also declare what
                # the array contains (else OpenAI 400s with "array schema
                # missing items"). Primitive items cover IN/NOT_IN lists.
                "type": ["string", "number", "boolean", "null", "array"],
                "items": {"type": ["string", "number", "boolean", "null"]},
                "description": "Value for the comparison. Use null for is_null/not_null. Use an array for in/not_in.",
            },
        },
        "required": ["column", "op", "value"],
    }

    groupby_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Columns to group by (1+ entries).",
            },
            "aggregations": {
                # OpenAI strict mode doesn't allow open-ended dicts.
                # We model aggregations as an array of {column, op} pairs
                # and the engine reconstructs the dict.
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "column": {"type": "string"},
                        "op": {
                            "type": "string",
                            "enum": ["sum", "mean", "median", "min", "max", "count", "nunique", "std", "var", "first", "last"],
                        },
                    },
                    "required": ["column", "op"],
                },
                "description": "Aggregations to compute: each entry is {column, op}.",
            },
        },
        "required": ["by", "aggregations"],
    }

    chart_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "enum": ["bar", "histogram", "line"]},
            "x": {"type": "string", "description": "Column on the X axis."},
            "y": {
                "type": ["string", "null"],
                "description": "Column on Y axis. Required for bar/line; must be null for histogram.",
            },
            "title": {"type": ["string", "null"]},
        },
        "required": ["kind", "x", "y", "title"],
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_file": {
                "type": "string",
                "description": "Workspace-relative path; must match one of the files shown in the workspace summary.",
            },
            "sheet": {
                "type": ["string", "null"],
                "description": "Excel sheet name; use null for CSV/TSV.",
            },
            "filters": {
                "type": "array",
                "items": filter_schema,
                "description": "List of row predicates; empty array = keep all rows.",
            },
            "groupby": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "properties": groupby_schema["properties"],
                "required": groupby_schema["required"],
                "description": "Optional groupby+aggregate step. Set null to skip.",
            },
            "sort_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column names to sort by; empty array = no sort.",
            },
            "sort_descending": {"type": "boolean"},
            "limit": {
                "type": ["integer", "null"],
                "description": "Cap output rows after sort; null = no cap (engine still caps at 5000).",
            },
            "chart": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "properties": chart_schema["properties"],
                "required": chart_schema["required"],
                "description": "Optional chart spec. Set null if the analysis doesn't need a visualization.",
            },
        },
        "required": [
            "source_file",
            "sheet",
            "filters",
            "groupby",
            "sort_by",
            "sort_descending",
            "limit",
            "chart",
        ],
    }


def render_workspace_data_summary(snapshot: WorkspaceSnapshot, *, max_files: int = 50) -> str:
    """Compact description of every tabular file the LLM can reference.

    Includes column names for each — without this the LLM hallucinates
    column names. We don't include sample rows here (would balloon
    context); the user-prompt builder can add a snippet if needed.
    """
    workspace_root = Path(snapshot.root)
    lines: list[str] = []
    lines.append(f"Workspace root: `{workspace_root}`")
    lines.append("")
    lines.append("## Tabular files (CSV/TSV/Excel)")
    lines.append("")

    described = 0
    for f in snapshot.files:
        if f.file_type not in {"tabular", "excel"}:
            continue
        if described >= max_files:
            break
        abs_path = workspace_root / f.path
        if not is_supported_tabular(abs_path):
            continue
        described += 1
        lines.append(f"### `{f.path}`")
        try:
            from app.tools.data_ops import read_tabular

            reads = read_tabular(abs_path, f.path)
        except Exception as exc:
            lines.append(f"_(could not read: {exc})_")
            lines.append("")
            continue
        for tr in reads:
            label = tr.display_path
            if tr.error:
                lines.append(f"- {label}: read error — {tr.error}")
                continue
            if tr.df is None:
                continue
            df = tr.df
            cols = ", ".join(f"`{c}` ({df[c].dtype})" for c in df.columns)
            lines.append(f"- {label}: {len(df)} rows × {df.shape[1]} cols")
            lines.append(f"  columns: {cols}")
            # Phase 3.3b real-data fix: show 2 sample rows per file so
            # the LLM doesn't hallucinate value formats. Without this,
            # an "hour" column with values like "07:00-08:00" gets
            # filtered with value="07:00" → 0 rows → empty chart.
            sample = df.head(2)
            for i, (_, row) in enumerate(sample.iterrows(), start=1):
                pairs: list[str] = []
                for k, v in row.items():
                    val_str = "" if v is None else str(v)
                    if len(val_str) > 40:
                        val_str = val_str[:37] + "..."
                    pairs.append(f"{k}={val_str}")
                lines.append(f"  sample row {i}: {' | '.join(pairs)}")
        lines.append("")

    if described == 0:
        lines.append("_No tabular files found._")

    return "\n".join(lines)


def render_user_prompt(task: TaskSpec, snapshot: WorkspaceSnapshot) -> str:
    summary = render_workspace_data_summary(snapshot)
    return f"""# Task
task_id: `{task.task_id}`

## User goal
{task.user_goal}

## Available data
{summary}

## What to do
Call `submit_analysis_spec` with a single AnalysisSpec that best answers the user goal.
Remember: `source_file` and column names must come from the listing above (no invented paths or columns).
"""


def render_repair_prompt(error_summary: str) -> str:
    """Tool-result content sent back after a failed spec validation."""
    return (
        "The AnalysisSpec you submitted was REJECTED:\n\n"
        f"{error_summary}\n\n"
        "Fix the problems above and call `submit_analysis_spec` again with a corrected spec. "
        "Common fixes: use exact column names from the listing, escape sheet names, "
        "keep aggregation columns numeric, etc."
    )
