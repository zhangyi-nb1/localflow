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

from pydantic import BaseModel, Field


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
