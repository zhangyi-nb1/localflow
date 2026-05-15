"""Phase 9 — TraceLogger: append-only ``trace.jsonl`` writer.

Sister to :class:`app.harness.audit.AuditLogger`. AuditLogger records
**mutations to user state** (memory edits, task creation); TraceLogger
records **kernel-internal events** during a single run (LLM calls,
policy checks, action executes, verifier checks, rollback replays).

The split matters: a user reading ``audit.jsonl`` wants "what did I
do" (forbid-path, set-naming-style). A grader reading ``trace.jsonl``
wants "how did the kernel get from goal → outcome and what failed
along the way".

Reuses :class:`app.storage.jsonl_logger.JsonlLogger` for atomic
crash-safe writes. Reads parse each line back into the typed
:class:`TraceEvent` Pydantic model so graders don't get raw dicts.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from app.schemas.trace import FailureType, TraceEvent, TraceEventType
from app.storage.jsonl_logger import JsonlLogger

# Phase 10 — contextual stage_id for multi-stage TaskGraph runs.
# ContextVar is the canonical thread-safe / async-safe primitive for
# this kind of ambient context; Phase 10's runner is single-threaded
# but using ContextVar keeps us future-proof if Phase 10.x adds
# parallel stages.
_STAGE_CTX: ContextVar[str | None] = ContextVar("trace_stage", default=None)


class TraceLogger:
    """Per-run trace stream. Construct once per task; pass to Executor /
    Verifier / Rollback as an optional kwarg. Emission is a no-op when
    the logger is None at the call site.
    """

    def __init__(self, trace_path: Path) -> None:
        self.path = Path(trace_path)
        self._jsonl = JsonlLogger(self.path)

    def emit(self, event: TraceEvent) -> None:
        """Write one TraceEvent to disk. Atomic per record.

        Phase 10: if a ``stage()`` context manager is active and the
        event doesn't already carry a ``stage_id``, inject the
        contextual one. Existing emission sites stay unchanged —
        they emit ``stage_id=None`` and the runner's `with
        trace.stage(...)` block tags every nested event.
        """
        if event.stage_id is None:
            ctx_stage = _STAGE_CTX.get()
            if ctx_stage is not None:
                event = event.model_copy(update={"stage_id": ctx_stage})
        payload = event.model_dump(mode="json", exclude={"ts", "event_type"})
        self._jsonl.write(event.event_type.value, payload)

    @contextmanager
    def stage(self, stage_id: str) -> Iterator[None]:
        """Decorate every event emitted inside the block with this
        ``stage_id`` (unless the event explicitly sets one).

        Nested ``with trace.stage(...)`` blocks restore the outer
        stage_id on exit — useful for Phase 11's potential nested
        TaskGraph patterns. Single-threaded today (Phase 10 sequential
        runner); ContextVar is future-proof for parallel stages.
        """
        token = _STAGE_CTX.set(stage_id)
        try:
            yield
        finally:
            _STAGE_CTX.reset(token)

    def read_all(self) -> list[TraceEvent]:
        """Re-parse every line into a TraceEvent. Skips malformed
        lines (same defensive behaviour as JsonlLogger.read_all)."""
        raw = self._jsonl.read_all()
        out: list[TraceEvent] = []
        for record in raw:
            payload = record.get("payload", {})
            event_type = record.get("event", "")
            ts = record.get("ts")
            data = dict(payload)
            data["event_type"] = event_type
            if ts is not None:
                data["ts"] = ts
            try:
                out.append(TraceEvent.model_validate(data))
            except Exception:
                # A malformed event shouldn't poison the whole trace.
                # Eval graders treat a malformed line as a non-event.
                continue
        return out

    def group_by_failure(self) -> dict[FailureType, list[TraceEvent]]:
        """Bucket failed/blocked events by ``failure_type`` for the
        eval report's failure histogram. Events without a
        ``failure_type`` (the common ``ok`` case) are not included."""
        out: dict[FailureType, list[TraceEvent]] = defaultdict(list)
        for evt in self.read_all():
            if evt.failure_type is None:
                continue
            out[evt.failure_type].append(evt)
        return dict(out)

    def emit_plan_revised(
        self,
        *,
        task_id: str,
        prior_plan_id: str,
        new_plan_id: str,
        version: int,
        user_hint: str,
    ) -> None:
        """Phase 11 helper — emit a single ``plan.revised`` event when the
        user supplied a clarification and the planner produced a new
        ActionPlan. Kept as a typed helper so emission sites don't have
        to assemble the TraceEvent fields by hand."""
        self.emit(
            TraceEvent(
                task_id=task_id,
                event_type=TraceEventType.PLAN_REVISED,
                detail=f"v{version}: {user_hint[:200]}",
                payload={
                    "prior_plan_id": prior_plan_id,
                    "new_plan_id": new_plan_id,
                    "version": version,
                    "user_hint": user_hint,
                },
            )
        )

    def emit_repair_triggered(
        self,
        *,
        task_id: str,
        attempt: int,
        max_attempts: int,
        grader: str,
        suggested_hint: str,
    ) -> None:
        """Phase 13 helper — emit one ``repair.triggered`` event when
        the auto-repair loop fires. ``failure_type`` is pinned to
        SEMANTIC_MISMATCH because Phase 13's repair loop is exclusively
        driven by semantic verdicts (structural failures abort the
        execute path before semantic verify even runs).
        """
        from app.schemas.trace import FailureType

        self.emit(
            TraceEvent(
                task_id=task_id,
                event_type=TraceEventType.REPAIR_TRIGGERED,
                status="fail",
                failure_type=FailureType.SEMANTIC_MISMATCH,
                detail=f"attempt {attempt}/{max_attempts} grader={grader}",
                payload={
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "grader": grader,
                    "suggested_hint": suggested_hint[:300],
                },
            )
        )
