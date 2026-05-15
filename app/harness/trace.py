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
from pathlib import Path

from app.schemas.trace import FailureType, TraceEvent
from app.storage.jsonl_logger import JsonlLogger


class TraceLogger:
    """Per-run trace stream. Construct once per task; pass to Executor /
    Verifier / Rollback as an optional kwarg. Emission is a no-op when
    the logger is None at the call site.
    """

    def __init__(self, trace_path: Path) -> None:
        self.path = Path(trace_path)
        self._jsonl = JsonlLogger(self.path)

    def emit(self, event: TraceEvent) -> None:
        """Write one TraceEvent to disk. Atomic per record."""
        payload = event.model_dump(mode="json", exclude={"ts", "event_type"})
        self._jsonl.write(event.event_type.value, payload)

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
