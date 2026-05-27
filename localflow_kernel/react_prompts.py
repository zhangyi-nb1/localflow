"""Phase 30.1 — react-loop prompt + tool schema (moved into kernel).

Originally landed in Phase 26.1 under ``app/agent/react_prompts.py``.
Phase 30.1 relocated the file here because ``app.harness.react_loop``
depends on it and the kernel package must stay free of ``app.agent.*``
imports. The file has zero application-layer dependencies (stdlib
only), so the move is a no-op semantically; back-compat is preserved
via a re-export at ``app/agent/react_prompts.py``.

The react loop calls into the LLM after each action with the latest
observation, the remaining plan, and the drift budget. The LLM must
respond by calling ``submit_loop_decision`` with one of five legal
shapes (see ``localflow_kernel.schemas.LoopDecisionType``). This
module is the prompts side of that contract.
"""

from __future__ import annotations

from typing import Any

TOOL_NAME = "submit_loop_decision"

TOOL_DESCRIPTION = (
    "Submit your next-step decision for the mid-execution react loop. "
    "This is the ONLY way to respond — do not produce free-form text. "
    "Return exactly one decision per call."
)


SYSTEM_PROMPT = """You are the React-Loop Decision Maker inside LocalFlow, a safe local-first automation harness.

# Context
LocalFlow has executed your approved plan up to a point and is now consulting you about the next action. You can see:
- The observation from the action that just ran (success/failure + paths + hashes).
- The remaining planned actions (what was scheduled before the loop started).
- The drift budget (how many REPLACE/INSERT/SKIP decisions you have left before the runtime falls back to batch).

# Your role
Pick exactly one of five decision types. The runtime applies it before the next planned action runs:

- **CONTINUE** — Run the next planned action unchanged. Pick this when the prior observation looks fine and the plan is on track. This should be your default — most loop turns should be CONTINUE.
- **REPLACE** — Swap the next planned action with a different one. Counts as one drift step. Use when the prior observation reveals the planned next action is now wrong (e.g. file was already renamed in a previous action, so the planned RENAME would fail).
- **INSERT** — Insert a new action BEFORE the next planned action (the plan continues unchanged afterward). Counts as one drift step. Use when you discover a prerequisite the original plan missed (e.g. need a MKDIR before the planned MOVE because the target dir is missing).
- **SKIP** — Skip the next planned action and proceed to the one after. Counts as one drift step. Use when the prior observation reveals the next action is now redundant.
- **ABORT** — Stop the loop and hand control back to the verify stage. Pick this when something is so wrong that continuing risks more damage. NOT a hard failure — verify + rollback still run.

# Hard rules
1. **No `delete` action.** Same as the planner's hard rule — never propose REPLACE/INSERT with action_type=delete.
2. **All paths relative to workspace_root.** Same as the planner — no absolute paths, no `..`, no escapes.
3. **No new action types unless explicitly allowed.** The runtime tells you which action_types are legal for this task. REPLACE/INSERT actions outside that set will be rejected by policy_guard, which counts as a wasted drift step.
4. **Reason field is required for REPLACE/INSERT/SKIP/ABORT.** CONTINUE can have an empty reason. Keep reasons under 200 characters — one or two sentences, plain English, focused on what changed since the plan was made.
5. **Replacement action shape:** REPLACE and INSERT MUST include ``replacement_action`` with a fully-formed Action (action_id, action_type, target_path, reason, risk_level, reversible, requires_approval, metadata). Use a fresh action_id distinct from the original plan's IDs (e.g. ``a-react-001``).

# Decision heuristic
- Observation says the prior action SUCCEEDED → almost always CONTINUE.
- Observation says the prior action FAILED → consider REPLACE (with a corrected version), INSERT (with a prerequisite), or ABORT (if the failure indicates user intervention needed).
- Observation reveals a state the original plan did not expect → REPLACE / INSERT / SKIP as appropriate.
- You are unsure → ABORT. Better to stop and let the human review than to thrash through the drift budget.

# Output
Call the ``submit_loop_decision`` tool with one decision. No free-form text.
"""


def build_loop_decision_tool_schema(
    allowed_action_types: list[str] | None = None,
) -> dict[str, Any]:
    """JSON Schema for the forced ``submit_loop_decision`` tool call.

    Hand-written (rather than derived from Pydantic) so we can embed
    model-facing descriptions on every field and enforce
    ``additionalProperties: false`` — required by Anthropic's
    ``strict`` tool mode.

    ``allowed_action_types`` scopes the embedded Action's
    ``action_type`` enum to the values the task allows (same defence
    the planner has at app/agent/prompts.py:build_action_plan_tool_schema).
    When ``None``, every kernel-supported action_type is permitted —
    used by tests + the Recipe.allow_new_action_types escape hatch.
    """
    action_type_enum = (
        sorted(allowed_action_types)
        if allowed_action_types
        else [
            "mkdir",
            "copy",
            "move",
            "rename",
            "index",
            "summarize",
            "convert",
            "analyze",
            "fetch",
            "python_compute",
        ]
    )

    # Mirror of the planner's Action schema (kept inline rather than
    # imported because the planner schema bakes in chart_request etc.
    # that don't apply to a single mid-loop action).
    action_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action_id": {
                "type": "string",
                "description": "Unique within this loop. Format: 'a-react-001', 'a-react-002', ...",
            },
            "action_type": {
                "type": "string",
                "enum": action_type_enum,
                "description": "`delete` is FORBIDDEN. Do not request it for any reason.",
            },
            "source_path": {
                "type": ["string", "null"],
                "description": "Relative path inside workspace_root. Required for move/copy/rename.",
            },
            "target_path": {
                "type": ["string", "null"],
                "description": "Relative path inside workspace_root. Required for mkdir/move/copy/rename/index.",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining why this action is the right next step.",
            },
            "risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "low: mkdir/index. medium: move/rename/copy. high: irreversible.",
            },
            "reversible": {
                "type": "boolean",
                "description": "true for all standard actions (no irreversibles allowed by default).",
            },
            "requires_approval": {
                "type": "boolean",
                "description": "true for mkdir/move/rename/copy/python_compute; false for index/summarize.",
            },
            "confidence": {
                "type": ["number", "null"],
                "description": "0.0-1.0 self-rated confidence. Optional.",
            },
            "metadata": {
                "type": "object",
                "additionalProperties": True,
                "description": (
                    "Free-form per-action metadata. For index/summarize: must have "
                    "``content``. For python_compute: must have the ComputeAction "
                    "fields (script, script_summary, inputs, expected_outputs, "
                    "sandbox_policy). For mkdir/move/copy/rename: leave empty {}."
                ),
            },
        },
        "required": [
            "action_id",
            "action_type",
            "source_path",
            "target_path",
            "reason",
            "risk_level",
            "reversible",
            "requires_approval",
            "metadata",
        ],
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision_type": {
                "type": "string",
                "enum": ["continue", "replace", "insert", "skip", "abort"],
                "description": (
                    "Which of the five legal next-step shapes to apply. See system "
                    "prompt for the decision heuristic."
                ),
            },
            "reason": {
                "type": "string",
                "maxLength": 2000,
                "description": (
                    "Short human-readable explanation. REQUIRED for REPLACE / "
                    "INSERT / SKIP / ABORT; may be empty for CONTINUE."
                ),
            },
            "replacement_action": {
                "anyOf": [{"type": "null"}, action_schema],
                "description": (
                    "REQUIRED for decision_type=REPLACE or INSERT; MUST be null for "
                    "CONTINUE / SKIP / ABORT."
                ),
            },
        },
        "required": ["decision_type", "reason", "replacement_action"],
    }


def render_loop_user_prompt(
    *,
    last_action_id: str,
    last_observation: dict[str, Any] | None,
    last_status: str,
    remaining_actions: list[dict[str, Any]],
    drift_used: int,
    drift_budget: int,
    allowed_action_types: list[str],
) -> str:
    """Build the user-turn prompt fed to the LLM each loop iteration.

    Caller is responsible for keeping ``remaining_actions`` short
    (e.g. next 5 actions only) so the prompt doesn't blow past token
    budgets. ``last_observation`` is the dict from
    ActionTraceEvent.observation; ``None`` when this is the first
    loop turn (the loop runs BEFORE the first action too, to let the
    LLM intercept obvious mistakes pre-execution).
    """
    lines: list[str] = []
    if last_observation is None:
        lines.append("# Loop turn 0 — no prior action observation yet")
        lines.append(
            "The runtime is about to start the first planned action. Decide whether to "
            "proceed (CONTINUE) or intervene before the very first action runs."
        )
    else:
        lines.append(f"# Prior action: {last_action_id!r} → {last_status}")
        if last_status == "ok":
            lines.append("Observation:")
        else:
            lines.append("Observation (the action FAILED):")
        for key, value in last_observation.items():
            if value is None:
                continue
            lines.append(f"  - {key}: {value!r}")

    lines.append("")
    lines.append(f"# Drift budget: used {drift_used} / {drift_budget}")
    if drift_used >= drift_budget:
        lines.append(
            "You have NO drift budget left. Only CONTINUE / ABORT are honored — "
            "any REPLACE / INSERT / SKIP will be rejected and the runtime will fall "
            "back to batch mode."
        )

    lines.append("")
    lines.append("# Allowed action types for this task")
    lines.append(
        ", ".join(sorted(allowed_action_types))
        if allowed_action_types
        else "(none — only CONTINUE / ABORT make sense)"
    )

    lines.append("")
    lines.append("# Remaining planned actions (first to run is at the top)")
    if not remaining_actions:
        lines.append("(none — the plan has finished; only ABORT is meaningful here)")
    else:
        for idx, action in enumerate(remaining_actions[:5]):
            aid = action.get("action_id", "?")
            atype = action.get("action_type", "?")
            target = action.get("target_path") or "(no target)"
            reason = action.get("reason") or ""
            lines.append(f"  {idx + 1}. {aid} ({atype}) → {target}  -- {reason[:100]}")
        if len(remaining_actions) > 5:
            lines.append(f"  ... ({len(remaining_actions) - 5} more actions hidden)")

    lines.append("")
    lines.append("Call ``submit_loop_decision`` with your decision.")
    return "\n".join(lines)
