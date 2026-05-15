"""Phase 9 / v0.10.0 — TraceEvent schema + TraceLogger tests.

Pins the structural contract of the trace stream so future emission
changes have a guard rail. The eval graders consume these objects;
any breaking schema change must update graders too — these tests
catch the schema half of that.
"""

from __future__ import annotations

from pathlib import Path

from app.harness.trace import TraceLogger
from app.schemas.trace import FailureType, TraceEvent, TraceEventType

# ───────────────────────────────────── enum membership


def test_every_event_type_used_in_kernel_pinned() -> None:
    """If an emission site adds a new event_type, this list should
    grow. Catches silent removals."""
    expected = {
        "llm.call.start",
        "llm.call.end",
        "llm.repair",
        "policy.check",
        "dry_run.rendered",
        "token.minted",
        "token.consumed",
        "token.rejected",
        "action.start",
        "action.end",
        "verifier.check",
        "rollback.entry",
        "repair.triggered",
    }
    actual = {e.value for e in TraceEventType}
    assert actual == expected, f"unexpected drift: {actual ^ expected}"


def test_failure_taxonomy_pinned() -> None:
    """The eval failure histogram groups by FailureType.value. Pin
    every value so a typo in the enum doesn't silently drop a bucket
    from the report."""
    expected = {
        "schema_invalid",
        "policy_blocked",
        "path_forbidden",
        "missing_output",
        "unsupported_file",
        "data_analysis_failed",
        "chart_render_failed",
        "semantic_mismatch",
        "low_confidence_classification",
        "summary_not_grounded",
        "stale_plan",
        "rollback_drift",
        "user_ambiguity",
        "unknown",
    }
    actual = {f.value for f in FailureType}
    assert actual == expected


# ───────────────────────────────────── Pydantic round-trip


def test_trace_event_round_trip() -> None:
    evt = TraceEvent(
        task_id="t-1",
        event_type=TraceEventType.ACTION_END,
        status="ok",
        action_id="a-001",
        duration_ms=42,
        detail="mkdir ok",
        payload={"hash_before": None, "hash_after": "abc"},
    )
    raw = evt.model_dump(mode="json")
    again = TraceEvent.model_validate(raw)
    assert again.event_type == TraceEventType.ACTION_END
    assert again.action_id == "a-001"
    assert again.duration_ms == 42


def test_is_failure_helper() -> None:
    """`status in {fail, blocked}` → True; ok/skipped → False."""
    for status in ("fail", "blocked"):
        evt = TraceEvent(task_id="t", event_type=TraceEventType.ACTION_END, status=status)  # type: ignore[arg-type]
        assert evt.is_failure()
    for status in ("ok", "skipped"):
        evt = TraceEvent(task_id="t", event_type=TraceEventType.ACTION_END, status=status)  # type: ignore[arg-type]
        assert not evt.is_failure()


# ───────────────────────────────────── TraceLogger atomicity


def test_logger_writes_one_line_per_event(tmp_path: Path) -> None:
    logger = TraceLogger(tmp_path / "trace.jsonl")
    logger.emit(TraceEvent(task_id="t", event_type=TraceEventType.DRY_RUN_RENDERED, detail="a"))
    logger.emit(
        TraceEvent(
            task_id="t",
            event_type=TraceEventType.POLICY_CHECK,
            status="blocked",
            failure_type=FailureType.PATH_FORBIDDEN,
        )
    )
    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_logger_read_all_returns_typed_events(tmp_path: Path) -> None:
    logger = TraceLogger(tmp_path / "trace.jsonl")
    logger.emit(
        TraceEvent(
            task_id="t",
            event_type=TraceEventType.VERIFIER_CHECK,
            status="fail",
            failure_type=FailureType.MISSING_OUTPUT,
            detail="generated_files_present",
        )
    )
    events = logger.read_all()
    assert len(events) == 1
    evt = events[0]
    assert evt.event_type == TraceEventType.VERIFIER_CHECK
    assert evt.failure_type == FailureType.MISSING_OUTPUT
    assert evt.is_failure()


def test_logger_group_by_failure_counts_buckets(tmp_path: Path) -> None:
    logger = TraceLogger(tmp_path / "trace.jsonl")
    for ftype in (
        FailureType.PATH_FORBIDDEN,
        FailureType.PATH_FORBIDDEN,
        FailureType.MISSING_OUTPUT,
    ):
        logger.emit(
            TraceEvent(
                task_id="t",
                event_type=TraceEventType.VERIFIER_CHECK,
                status="fail",
                failure_type=ftype,
            )
        )
    # Add one ok event to confirm it's excluded.
    logger.emit(TraceEvent(task_id="t", event_type=TraceEventType.DRY_RUN_RENDERED))

    groups = logger.group_by_failure()
    assert len(groups[FailureType.PATH_FORBIDDEN]) == 2
    assert len(groups[FailureType.MISSING_OUTPUT]) == 1
    # ok event not bucketed
    assert FailureType.UNKNOWN not in groups


def test_logger_skips_malformed_lines(tmp_path: Path) -> None:
    """A user (or a partially-flushed crash) leaves a bad line; the
    logger reads what it can without crashing."""
    path = tmp_path / "trace.jsonl"
    path.write_text(
        '{"ts": "2026-05-15T00:00:00Z", "event": "policy.check", "payload": {"task_id": "t", "status": "ok"}}\n'
        "not-json-at-all\n"
        '{"ts": "2026-05-15T00:00:01Z", "event": "action.end", "payload": {"task_id": "t", "status": "ok"}}\n',
        encoding="utf-8",
    )
    logger = TraceLogger(path)
    events = logger.read_all()
    # Two parseable, one dropped, no exception.
    assert len(events) == 2
