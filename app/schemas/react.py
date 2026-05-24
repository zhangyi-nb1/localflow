"""Phase 26.0 — typed contracts for the execute-stage react loop.

Background: see ``docs/PHASE_26_DESIGN.md``. The react loop is the
core of Route B — keep the plan/dry-run/approval/verify/rollback
spine, but make the execute stage step-by-step instead of batch:
after each action runs, the LLM reads the observation and decides
the next step (CONTINUE / REPLACE / INSERT / SKIP / ABORT) within a
bounded drift budget.

This module ships **schema only** — Phase 26.0 introduces no behaviour
change. The actual loop implementation lands in Phase 26.1
(``app/harness/react_loop.py``), the wiring lands in Phase 26.2, and
the Recipe integration lands in Phase 26.3. Putting the contract in
its own commit lets the §10.7 4th deliberate-exception conversation
happen against a stable, reviewable shape before any executor code
touches it.

§10.7 invariant: this is application-layer schema only. No kernel
references. ``LoopDecision`` carries an embedded ``Action`` (for
REPLACE / INSERT decisions) — the same typed shape the planner
emits, so policy_guard's existing ``evaluate_action`` path remains
the only gate between LLM intent and disk mutation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.action import Action


class LoopDecisionType(str, Enum):
    """Five legal next-step shapes the LLM can request mid-loop.

    The values are deliberately short lower-case strings so they fit
    cleanly into LLM tool-call JSON without escape weirdness.
    """

    CONTINUE = "continue"
    """Run the next action from the original plan unchanged. The
    common case — most loop turns should pick this. The LLM is
    saying "I saw the observation, nothing in the plan needs to
    change, proceed as scheduled."."""

    REPLACE = "replace"
    """Swap the next action with ``replacement_action``. Counts as
    one drift step. Used when the prior observation reveals the
    planned next action is wrong (e.g. plan said MOVE but the file
    actually needs RENAME with a different target)."""

    INSERT = "insert"
    """Insert ``replacement_action`` BEFORE the next planned action.
    The plan continues unchanged afterward. Counts as one drift
    step. Used to handle a discovered prerequisite (e.g. need MKDIR
    before the planned MOVE because the target dir vanished)."""

    SKIP = "skip"
    """Skip the next planned action. Counts as one drift step. Used
    when the observation reveals the next action is now redundant
    (e.g. target file already exists with correct content)."""

    ABORT = "abort"
    """Stop the react loop and surface control to the verify stage.
    The user gets to see whatever the executor has done so far. NOT
    the same as a hard failure — verify + rollback still run."""


class LoopDecision(BaseModel):
    """One mid-loop decision from the LLM. The forced-tool-call
    response from ``react_prompts.submit_loop_decision`` deserialises
    into exactly this shape.
    """

    model_config = ConfigDict(extra="forbid")

    decision_type: LoopDecisionType = Field(
        ...,
        description=(
            "Which of the five legal next-step shapes the LLM is "
            "requesting. The runtime applies the decision against the "
            "remaining plan; REPLACE/INSERT/SKIP also count toward the "
            "drift budget."
        ),
    )

    reason: str = Field(
        default="",
        max_length=2000,
        description=(
            "Short human-readable explanation of why the LLM picked "
            "this decision. Logged to trace.jsonl for auditability — "
            "users reading 'why did the agent do X mid-execution?' "
            "should find the answer here."
        ),
    )

    replacement_action: Action | None = Field(
        default=None,
        description=(
            "REQUIRED when decision_type is REPLACE or INSERT; MUST "
            "be None for CONTINUE / SKIP / ABORT. Carries the same "
            "typed Action shape the planner emits, so policy_guard's "
            "evaluate_action() is the single gate before dispatch."
        ),
    )

    @model_validator(mode="after")
    def _action_required_iff_replace_or_insert(self) -> "LoopDecision":
        """Pin the invariant in the schema rather than the runtime so
        an LLM that returns a malformed shape is rejected at parse
        time (not at dispatch time when state has already moved)."""
        needs_action = self.decision_type in (
            LoopDecisionType.REPLACE,
            LoopDecisionType.INSERT,
        )
        has_action = self.replacement_action is not None
        if needs_action and not has_action:
            raise ValueError(
                f"decision_type={self.decision_type.value!r} requires "
                "replacement_action, but it was None"
            )
        if has_action and not needs_action:
            raise ValueError(
                f"decision_type={self.decision_type.value!r} forbids "
                "replacement_action, but one was provided"
            )
        return self


class ReactConfig(BaseModel):
    """Per-task react loop policy. Defaults are conservative so an
    accidentally-enabled react_mode cannot run away."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch. When False, the executor stays in v0.23.x "
            "batch behaviour regardless of any other ReactConfig field. "
            "Defaults to False so opting in is always explicit — either "
            "via Recipe.enable_react_mode, ``localflow execute --react``, "
            "or the UI checkbox."
        ),
    )

    max_drift: int = Field(
        default=3,
        ge=0,
        le=20,
        description=(
            "How many REPLACE / INSERT / SKIP decisions the loop will "
            "apply before forcing a fallback to batch mode for the "
            "remaining plan. Bounds 0..20: 0 = react can only emit "
            "CONTINUE / ABORT (mostly useless), 20 = practical upper "
            "limit before LLM costs blow up. Default 3 matches the "
            "design doc's small-edit-budget assumption."
        ),
    )

    max_loops_per_action: int = Field(
        default=1,
        ge=1,
        le=5,
        description=(
            "How many LLM consultations are allowed between two real "
            "actions. 1 = ask once per gap (the default; matches the "
            "step-by-step shape OpenHands uses). >1 enables the LLM "
            "to chain INSERT decisions multiple times before the next "
            "planned action runs — useful for recipes that expect "
            "multi-step fix-ups between phases."
        ),
    )

    llm_timeout_sec: int = Field(
        default=30,
        ge=1,
        le=300,
        description=(
            "Per-decision LLM call timeout. On timeout the loop falls "
            "back to batch mode (consumes the next planned action and "
            "logs a LOOP_DECISION_DECIDED event with status=fail). "
            "Defaults to 30s — long enough for a model call with "
            "extended thinking, short enough that a stuck loop "
            "doesn't block the user indefinitely."
        ),
    )

    allow_new_action_types: bool = Field(
        default=False,
        description=(
            "When False (default), REPLACE / INSERT actions must be "
            "of an action_type already present in the task's "
            "``allowed_actions`` — same defence-in-depth as the static "
            "policy_guard check. When True, the LLM can propose any "
            "action_type the kernel supports (e.g. PYTHON_COMPUTE) "
            "subject to per-action policy_guard approval. This is the "
            "Recipe-level switch that closes the Phase 23 ComputeAction "
            "reachability gap (see docs/PHASE_26_DESIGN.md §6)."
        ),
    )
