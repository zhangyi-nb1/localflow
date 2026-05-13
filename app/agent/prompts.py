from __future__ import annotations

from typing import Any

from app.schemas import TaskSpec, WorkspaceSnapshot

TOOL_NAME = "submit_action_plan"
TOOL_DESCRIPTION = (
    "Submit a complete, structured action plan for the LocalFlow harness "
    "to validate, dry-run, and execute. This is the ONLY way to respond — "
    "do not produce free-form text."
)


SYSTEM_PROMPT = """You are the LLM Planner for LocalFlow, a safe automation harness for personal local workspaces.

# Your role
Given a workspace snapshot and a user goal, propose a structured ActionPlan. The Harness Kernel — not you — performs the actual filesystem operations. You produce plans; the harness validates, dry-runs, requests approval, executes, and verifies.

You have NO direct filesystem access. The workspace snapshot you receive is your only source of ground truth. For some files the snapshot includes a leading TEXT PREVIEW (first ~2000 chars for PDFs / .md / .txt / .csv / source code / structured config). When a preview is available, USE IT for semantically grounded decisions: classify by topic not extension, propose meaningful rename targets, group related papers/projects together. When a preview is absent (binary files, images, encrypted PDFs, etc.), fall back to filename + extension.

# Hard rules (the harness will reject violators — DO NOT propose them)
1. **No `delete` action.** It is not in the allowed action types. If duplicate files exist (same sha256), emit an `index` action that writes a `duplicates_report.md` listing them — never propose deleting one.
2. **All paths are RELATIVE to workspace_root.** Never use absolute paths, never use `..`, never reference paths outside the workspace.
3. **No overwriting.** Do not point two move/copy actions at the same target. The harness will auto-suffix conflicts at execute time, but plan-time collisions waste user trust.
4. **Every action has a unique `action_id`.** Use `a-001`, `a-002`, ... in order.
5. **Order matters.** Emit `mkdir` actions before the moves that depend on them.
6. **For destructive-looking writes (move/rename/copy):** set `risk_level="medium"`, `reversible=true`, `requires_approval=true`. For directory creation and index writes: `risk_level="low"` is fine, `requires_approval=true` for `mkdir` (the user wants to see them), `false` for `index`.
7. **Every action MUST emit every field**, even when not applicable — use `null` for fields that don't apply:
   - For `mkdir`: `source_path: null`, `confidence: null` (or a number), `metadata: {"content": null}`.
   - For `move`/`copy`/`rename`: `confidence: null` (or a number), `metadata: {"content": null}`.
   - For `index`/`summarize`: `source_path: null`, `confidence: null` (or a number), `metadata: {"content": "..."}` REQUIRED.

# Allowed action types
- `mkdir`: create a directory. `target_path` required, no `source_path`.
- `move`: move a file. `source_path` and `target_path` both required and different.
- `copy`: copy a file. `source_path` and `target_path` both required and different.
- `rename`: rename a file (same parent dir). Identical to `move` mechanically.
- `index`: write a markdown file (catalogue of contents, summary, report). `target_path` required. The file body MUST be supplied in `metadata.content` (a single markdown string). DO NOT request `index` without `metadata.content` — the executor has no other way to know what to write.
- `summarize`: same shape as `index` (Phase 1 treats them identically).

# How to think about the task
1. **Read the previews first.** For every file with a TEXT PREVIEW, scan the first lines: a PDF preview revealing "Attention Is All You Need" is a paper on transformers; a .md preview starting with "# Project Roadmap" is a project plan. Let content override file_type when they conflict.
2. Look at the snapshot's `file_type` labels (pdf, word, image, archive, code, ...) for files WITHOUT previews. The user's goal usually means: group files by category into category directories, generate an index per category, and report duplicates.
3. Choose category directory names that match the user's goal language when reasonable. Default suggestions: papers/, documents/, spreadsheets/, data/, notes/, images/, audio/, video/, archives/, code/, misc/. **If previews reveal a stronger semantic grouping (e.g. all PDFs are about machine learning) you may use more specific names like `ml-papers/`, `transformers/`, etc.**
4. **Semantic rename is allowed and encouraged** when the preview reveals a meaningful title. Example: a PDF named `paper_v3_final.pdf` whose preview's first line is "Agent Memory: A Survey" → propose `move source=paper_v3_final.pdf target=papers/agent-memory-survey.pdf` (kebab-case, lowercased, no dates unless meaningful). Don't rename when the original name is already meaningful.
5. If a file already lives at the top of its category directory AND has a sensible name, do NOT propose moving it.
6. Look for duplicate sha256 hashes across the snapshot. If you find any, emit one `index` action that writes `duplicates_report.md` at the workspace root listing each duplicate set.
7. For every non-empty category directory in the final state, emit one `index` action that writes `<category>/index.md` listing the files that will be there. When previews are available, include a one-line summary per file in the index.

# Output
Call the `submit_action_plan` tool. Make sure every `action_id` is unique, every path is relative, the action_type is one of the six allowed values, and every `index`/`summarize` action's `metadata.content` is a non-empty markdown string.
"""


# --------------------------------------------------------------------- tool schema


def build_action_plan_tool_schema() -> dict[str, Any]:
    """JSON Schema for the forced ``submit_action_plan`` tool call.

    Hand-written (rather than derived from Pydantic) so we can embed
    model-facing descriptions on every field and enforce
    ``additionalProperties: false`` recursively — required by Anthropic's
    ``strict`` tool mode.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "plan_id": {
                "type": "string",
                "description": "Stable identifier for this plan. Use the format 'plan-<8 hex chars>'.",
            },
            "task_id": {
                "type": "string",
                "description": "Echo back the task_id from the user prompt verbatim.",
            },
            "summary": {
                "type": "string",
                "description": "One-paragraph plain-English summary of what this plan accomplishes.",
            },
            "risk_summary": {
                "type": "string",
                "description": "What could go wrong; how the plan mitigates it.",
            },
            "expected_outputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths (relative to workspace_root) of files this plan will CREATE — index files, reports, etc. Do not list moved files here.",
            },
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action_id": {
                            "type": "string",
                            "description": "Unique within this plan. Format: 'a-001', 'a-002', ...",
                        },
                        "action_type": {
                            "type": "string",
                            "enum": ["mkdir", "copy", "move", "rename", "index", "summarize"],
                            "description": "`delete` is FORBIDDEN. Do not request it for any reason.",
                        },
                        "source_path": {
                            "type": ["string", "null"],
                            "description": "Relative path inside workspace_root. Required for move/copy/rename; must be null/omitted for mkdir/index/summarize.",
                        },
                        "target_path": {
                            "type": ["string", "null"],
                            "description": "Relative path inside workspace_root. Required for mkdir/move/copy/rename/index/summarize.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One sentence explaining why this action is in the plan.",
                        },
                        "risk_level": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "low: mkdir, index. medium: move/rename/copy. high: irreversible (rare; flag for review).",
                        },
                        "reversible": {
                            "type": "boolean",
                            "description": "true for all Phase-1 actions (no irreversibles allowed).",
                        },
                        "requires_approval": {
                            "type": "boolean",
                            "description": "true for mkdir/move/rename/copy; false for index/summarize.",
                        },
                        "confidence": {
                            "type": ["number", "null"],
                            "description": "0.0-1.0 self-rated confidence. Optional; omit if uncertain.",
                        },
                        "metadata": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "content": {
                                    "type": ["string", "null"],
                                    "description": "REQUIRED markdown body for index/summarize actions; pass null for mkdir/move/copy/rename.",
                                }
                            },
                            "required": ["content"],
                            "description": "Always provide a metadata object. content is the markdown body for index/summarize, or null otherwise.",
                        },
                    },
                    # OpenAI strict mode demands every property be in required;
                    # Anthropic strict mode accepts this too. Optional fields
                    # use nullable union types (e.g. ["string", "null"]).
                    "required": [
                        "action_id",
                        "action_type",
                        "source_path",
                        "target_path",
                        "reason",
                        "risk_level",
                        "reversible",
                        "requires_approval",
                        "confidence",
                        "metadata",
                    ],
                },
            },
        },
        "required": [
            "plan_id",
            "task_id",
            "summary",
            "actions",
            "expected_outputs",
            "risk_summary",
        ],
    }


# --------------------------------------------------------------------- renderers


def render_workspace_summary(
    snapshot: WorkspaceSnapshot,
    *,
    max_files: int = 200,
    preview_chars_per_file: int = 400,
) -> str:
    """Format the snapshot as a compact, model-friendly listing.

    Group by ``file_type`` so the model sees structure at a glance, then
    list each file with its hash prefix (for duplicate detection) and —
    when available — an inline TEXT PREVIEW (first
    ``preview_chars_per_file`` chars). The preview is what lets the LLM
    planner do semantic categorization and rename — when present, it's
    the most valuable signal in the whole summary.
    """
    lines: list[str] = []
    lines.append(f"Workspace root: `{snapshot.root}`")
    lines.append(
        f"Total files: {snapshot.total_files}  ·  "
        f"Total size: {_fmt_bytes(snapshot.total_size_bytes)}"
    )
    with_preview = sum(1 for f in snapshot.files if f.text_preview)
    if with_preview:
        lines.append(f"Text previews available for {with_preview}/{snapshot.total_files} file(s).")
    lines.append("")

    by_type: dict[str, list] = {}
    for f in snapshot.files:
        by_type.setdefault(f.file_type, []).append(f)

    truncated = False
    seen = 0
    for ftype in sorted(by_type):
        files = sorted(by_type[ftype], key=lambda f: f.path)
        lines.append(f"## {ftype} ({len(files)} file(s))")
        for f in files:
            if seen >= max_files:
                truncated = True
                break
            sha_prefix = f.sha256[:12] if f.sha256 else "—"
            lines.append(f"- `{f.path}`  ·  {_fmt_bytes(f.size_bytes)}  ·  sha256:{sha_prefix}")
            if f.text_preview:
                preview = _clip_preview(f.text_preview, preview_chars_per_file)
                # Indent the preview so it visually attaches to the file
                # entry and is unambiguous in markdown rendering.
                lines.append(f"  preview: {preview}")
            seen += 1
        lines.append("")
        if truncated:
            break
    if truncated:
        lines.append(f"_… {snapshot.total_files - seen} more file(s) truncated_")

    return "\n".join(lines)


def _clip_preview(text: str, max_chars: int) -> str:
    """Compress whitespace + cap length so each preview occupies one
    readable inline blob rather than a sprawling block of newlines."""
    compact = " ".join(text.split())
    if len(compact) > max_chars:
        return compact[: max_chars - 1] + "…"
    return compact


def render_user_prompt(task: TaskSpec, snapshot: WorkspaceSnapshot) -> str:
    summary = render_workspace_summary(snapshot)
    return f"""# Task
task_id: `{task.task_id}`
workspace_root: `{task.workspace_root}`

## User goal
{task.user_goal}

## Workspace snapshot
{summary}

## What to do
Generate a complete ActionPlan and call the `submit_action_plan` tool. Remember:
- Every path RELATIVE to workspace_root.
- No `delete` action.
- Every `index`/`summarize` action MUST include `metadata.content` (the markdown body).
- `task_id` in your plan must be exactly: `{task.task_id}`.
"""


def render_repair_prompt(error_summary: str) -> str:
    """Tool-result message sent back after a failed validation attempt."""
    return (
        "The plan you just submitted was REJECTED by the LocalFlow harness:\n\n"
        f"{error_summary}\n\n"
        "Fix the problems above and call `submit_action_plan` again with a corrected plan. "
        "Keep the actions you got right; only modify or remove the offending ones."
    )


def _fmt_bytes(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    val: float = float(n)
    for unit in units:
        if val < 1024:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} PB"
