"""Phase 9 — Trace event schema.

A ``TraceEvent`` is a structured record of one interesting moment in a
task's life. The harness emits these alongside the existing
``execution_log.jsonl`` / ``audit.jsonl`` streams so eval reports can:

  * reconstruct what the kernel did at each lifecycle stage
  * group failures by ``FailureType`` (e.g. "we caught 4
    `policy_blocked` events across the batch")
  * measure timing + token usage without bolting on per-skill
    instrumentation

The trace layer is **observation-only**. Emission must not change
kernel behaviour — every call site accepts an optional TraceLogger
and is a no-op when it's absent. This preserves the additive-only
contract from Phase 1 (the `§10.7` ledger).

Phase 9 ships the schema + emission at 7 sites in the kernel and
agent layers. Phase 10 (TaskGraph) will populate ``stage_id``;
Phase 12 (Semantic Verifier + Repair Loop) will populate the
semantic ``FailureType`` values that are currently placeholders.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TraceEventType(str, Enum):
    """Closed enum of every event kind the kernel emits.

    Adding new kinds is fine — but only when a new emission site is
    actually added. Spurious enum members rot tests.
    """

    LLM_CALL_START = "llm.call.start"
    LLM_CALL_END = "llm.call.end"
    LLM_REPAIR = "llm.repair"
    POLICY_CHECK = "policy.check"
    DRY_RUN_RENDERED = "dry_run.rendered"
    TOKEN_MINTED = "token.minted"
    TOKEN_CONSUMED = "token.consumed"
    TOKEN_REJECTED = "token.rejected"
    ACTION_START = "action.start"
    ACTION_END = "action.end"
    VERIFIER_CHECK = "verifier.check"
    ROLLBACK_ENTRY = "rollback.entry"
    REPAIR_TRIGGERED = "repair.triggered"
    # Phase 11 — user-initiated plan refinement loop. Emitted by
    # control_loop.run_revise once per accepted revision turn.
    PLAN_REVISED = "plan.revised"
    # Phase 23 — Sandboxed ComputeAction lifecycle. Part of the §10.7
    # 3rd deliberate exception alongside ActionType.PYTHON_COMPUTE.
    # Status semantics:
    #   COMPUTE_ACTION_START — status=ok; payload carries script_summary
    #   COMPUTE_ACTION_END   — status=ok|fail; payload carries outcome.status
    #   SANDBOX_TIMEOUT      — status=fail; payload carries timeout_sec
    #   COMPUTE_OUTPUT_VERIFIED — status=ok|fail; payload carries artifact list
    COMPUTE_ACTION_START = "compute.action.start"
    COMPUTE_ACTION_END = "compute.action.end"
    SANDBOX_TIMEOUT = "compute.sandbox.timeout"
    COMPUTE_OUTPUT_VERIFIED = "compute.output.verified"


class FailureType(str, Enum):
    """The closed taxonomy of failures the harness can attribute.

    Eval graders sum these into histograms ("of the 20 tasks, 4 ended
    in ``missing_output`` and 1 in ``rollback_drift``") so iterations
    can target the dominant failure mode instead of relying on prompt
    guesswork.

    The Phase 12 entries (SEMANTIC_*, SUMMARY_NOT_GROUNDED, etc.) are
    pinned now so external graders + the failure histogram code don't
    need a schema bump when Phase 12 starts emitting them.
    """

    SCHEMA_INVALID = "schema_invalid"
    POLICY_BLOCKED = "policy_blocked"
    PATH_FORBIDDEN = "path_forbidden"
    MISSING_OUTPUT = "missing_output"
    UNSUPPORTED_FILE = "unsupported_file"
    DATA_ANALYSIS_FAILED = "data_analysis_failed"
    CHART_RENDER_FAILED = "chart_render_failed"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    LOW_CONFIDENCE = "low_confidence_classification"
    SUMMARY_NOT_GROUNDED = "summary_not_grounded"
    STALE_PLAN = "stale_plan"
    ROLLBACK_DRIFT = "rollback_drift"
    USER_AMBIGUITY = "user_ambiguity"
    UNKNOWN = "unknown"


TraceStatus = Literal["ok", "fail", "blocked", "skipped"]


class TraceEvent(BaseModel):
    """One record in ``trace.jsonl``.

    The fields are deliberately wide so a single schema covers every
    event type. A ``LLM_CALL_END`` event uses ``duration_ms`` +
    ``token_usage``; an ``ACTION_END`` uses ``action_id`` +
    ``status``; a ``POLICY_CHECK`` failure uses ``failure_type``. Per
    type, only a subset is meaningful — that's the cost of one stream
    over typed-per-event streams, and it pays for itself in eval
    grader simplicity (one filter chain instead of N).
    """

    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}")
    task_id: str
    run_id: str | None = None
    stage_id: str | None = None
    event_type: TraceEventType
    status: TraceStatus = "ok"
    failure_type: FailureType | None = None
    action_id: str | None = None
    tool_name: str | None = None
    model_name: str | None = None
    duration_ms: int | None = None
    token_usage: dict[str, int] | None = None
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def is_failure(self) -> bool:
        """True when this event represents a caught failure that an
        eval grader should count toward the failure histogram. ``ok``
        and ``skipped`` are non-failures; ``fail`` and ``blocked`` are."""
        return self.status in ("fail", "blocked")


# ---------------------------------------------------------------------
# Phase 25.0 — ActionTraceEvent (richer event shape for ACTION_* events)
# ---------------------------------------------------------------------
#
# Background: see ``docs/PHASE_25_PLAN.md`` + ``docs/research/OPENHANDS_HARNESS_STUDY.md``.
# LocalFlow currently splits the lifecycle of one action across three
# JSONL streams — ``trace.jsonl`` (kernel events), ``execution_log.jsonl``
# (executor progress), ``audit.jsonl`` (user-initiated actions). One
# action ends up as N rows scattered across N files; reconstructing
# "what the LLM was thinking when it proposed this move" requires
# stitching ``llm.call.end``-with-payload + ``action.start`` + auxiliary
# metadata.
#
# OpenHands' (``agent-sdk@main``) ``ActionEvent`` is a single
# self-contained object holding the LLM thought + tool_call + the
# typed action + the security risk + reasoning_content + critic result.
# One event = one complete record of one step; trace.jsonl alone
# becomes enough to drive UI / graders / LLM-history reconstruction.
#
# This module ships the SCHEMA only — Phase 25.0 explicitly does not
# change executor emission (see PHASE_25_PLAN.md §4 "Phase 25.0 step 1
# — schema-only PR"). Phase 25.1 will rewrite ``executor._run_one`` to
# emit ``ActionTraceEvent`` and downgrade ``execution_log.jsonl`` +
# ``audit.jsonl`` to filter views over ``trace.jsonl``.
#
# Backward compatibility: ``ActionTraceEvent`` is a subclass of
# ``TraceEvent``. Code that reads ``trace.jsonl`` as a list of
# ``TraceEvent`` already accepts the new shape — all new fields are
# Optional with default ``None`` and never appear in
# ``model_dump_json(exclude_none=True)`` unless populated. v0.23.x
# traces remain readable by Phase 25.x readers unchanged.


class ActionTraceEvent(TraceEvent):
    # Strict: Phase 26+ schema additions must land as explicit field
    # declarations + a ``schema_version`` bump, NOT as free-form
    # extras smuggled past the validator. The parent ``TraceEvent``
    # is intentionally left in Pydantic-default ``ignore`` mode so
    # v0.23.x trace consumers that read TraceEvent rows containing
    # this subclass' extra fields don't break.
    model_config = ConfigDict(extra="forbid")

    """A richer ``TraceEvent`` shape for ACTION_* events.

    Carries everything needed to reconstruct, from a single line of
    ``trace.jsonl``:

      * what the LLM was thinking (``thought``, ``reasoning``)
      * what tool call it actually emitted (``tool_call_raw``)
      * what the action observed when it ran (``observation``)
      * any critic / verifier-side evaluation of the action's quality
        (``critic_result``)

    Use sites (Phase 25.1+):

      * ``executor._run_one`` emits ONE ``ActionTraceEvent`` per
        action (replacing the current ``ACTION_START`` +
        ``ACTION_END`` pair).
      * ``llm_planner`` passes its ``thought`` / ``reasoning_content``
        into the executor, which forwards them into the event.
      * Semantic verifier writes its verdict into ``critic_result``.

    The base ``TraceEvent`` fields (``event_id`` / ``task_id`` /
    ``event_type`` / ``status`` / etc.) keep their existing semantics
    — readers that don't care about the new fields are unaffected.
    """

    # The LLM's free-form chain-of-thought that produced this action.
    # When the planner is rule-based (not LLM), this stays None.
    thought: str | None = Field(
        default=None,
        description=(
            "The LLM's reasoning narrative that produced this action. "
            "Captured from the LLM API's ``thought`` / ``reasoning_content`` "
            "field. None when the planner is rule-based."
        ),
    )

    # The full reasoning_content blocks from the LLM (Anthropic
    # extended thinking, OpenAI o1, etc.). Distinct from ``thought``
    # in that this is the structured raw blocks, not a flattened
    # narrative.
    reasoning: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Structured ``thinking_blocks`` / ``reasoning_content`` blocks "
            "from the model. Distinct from ``thought``: this is the raw "
            "structured trace from the API; ``thought`` is the human-readable "
            "narrative. None when the model doesn't produce extended "
            "thinking."
        ),
    )

    # The raw tool_call dict as the LLM emitted it, BEFORE the harness
    # validates / coerces it into a typed Action.
    tool_call_raw: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw ``tool_use`` block as the LLM emitted it (name + input). "
            "Captured BEFORE the harness validates / coerces into a typed "
            "Action — preserves the un-normalised intent for debugging."
        ),
    )

    # What the action actually OBSERVED when it ran. For MOVE/COPY:
    # before/after file metadata. For PYTHON_COMPUTE: the
    # ``ComputeOutcome``. For INDEX: byte count written.
    observation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured outcome of the action — kept loose-typed (dict) "
            "so each ActionType can include its own shape "
            "(``ComputeOutcome`` for PYTHON_COMPUTE; file metadata for "
            "MOVE/COPY/RENAME; etc.). Phase 25.1 fills this in."
        ),
    )

    # The semantic verifier / critic's verdict on the action quality.
    critic_result: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Critic / semantic-verifier verdict on this action's quality. "
            "Shape mirrors ``SemanticVerdict`` (passed / reasoning / "
            "confidence). None when no critic ran."
        ),
    )

    # Schema version sentinel for future migrations. Plain int rather
    # than enum because Phase 26+ may add lifecycle steps inside one
    # action (LLM-loop sub-steps) and we'll bump this.
    schema_version: int = Field(
        default=1,
        description=(
            "Schema version. ``1`` = Phase 25.0 initial shape. Bumps when "
            "the in-event LLM-loop steps land in Phase 26+."
        ),
    )
