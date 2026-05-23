"""Phase 25.0 — pin the ActionTraceEvent schema contract.

The schema lands first (this PR). Executor emission lands in Phase
25.1. These tests fix the typed surface so the upcoming executor /
trace-logger rewrite has a stable anchor and so trace.jsonl readers
written for v0.23.x keep working after the migration.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas import ActionTraceEvent, TraceEvent
from app.schemas.trace import FailureType, TraceEventType


def _minimal(**overrides) -> dict:
    base = {
        "task_id": "task-1",
        "event_type": TraceEventType.ACTION_END,
    }
    base.update(overrides)
    return base


class TestSubclassContract:
    """ActionTraceEvent must be a strict superset of TraceEvent so old
    trace.jsonl readers and graders keep working unchanged."""

    def test_inherits_from_trace_event(self) -> None:
        assert issubclass(ActionTraceEvent, TraceEvent)

    def test_minimal_construction_just_like_trace_event(self) -> None:
        evt = ActionTraceEvent(**_minimal())
        assert evt.task_id == "task-1"
        assert evt.event_type == TraceEventType.ACTION_END
        assert evt.status == "ok"
        assert evt.event_id.startswith("evt-")
        assert isinstance(evt.ts, datetime)

    def test_base_fields_round_trip_through_json(self) -> None:
        evt = ActionTraceEvent(
            task_id="task-1",
            event_type=TraceEventType.ACTION_START,
            action_id="a-001",
            duration_ms=123,
            detail="MOVE foo.txt -> archive/foo.txt",
        )
        dumped = evt.model_dump_json(exclude_none=True)
        assert "a-001" in dumped
        # New optional fields must NOT appear in the dump when unset —
        # otherwise we'd bloat every trace line.
        for new_field in ("thought", "reasoning", "tool_call_raw", "observation", "critic_result"):
            assert f'"{new_field}"' not in dumped, (
                f"unset new field {new_field!r} leaked into the JSON dump"
            )

    def test_can_be_read_as_plain_trace_event(self) -> None:
        # A consumer that only knows about TraceEvent (e.g. an
        # in-the-wild eval grader) must be able to deserialize an
        # ActionTraceEvent-shaped row and ignore the extra fields.
        rich = ActionTraceEvent(
            **_minimal(action_id="a-002"),
            thought="picking the canonical destination for foo.txt",
            observation={"sha256_before": "deadbeef", "sha256_after": "deadbeef"},
        )
        as_dict = rich.model_dump(exclude_none=True)
        plain = TraceEvent.model_validate(
            {k: v for k, v in as_dict.items() if k in TraceEvent.model_fields}
        )
        assert plain.action_id == "a-002"
        assert plain.task_id == "task-1"


class TestNewFields:
    """Each new field must be optional and accept the documented shape."""

    def test_thought_optional_string(self) -> None:
        evt = ActionTraceEvent(**_minimal(), thought="grouping pdfs by topic")
        assert evt.thought == "grouping pdfs by topic"

    def test_reasoning_optional_list_of_dicts(self) -> None:
        blocks = [
            {"type": "thinking", "text": "I should classify by file_type first…"},
            {"type": "thinking", "text": "papers go under papers/"},
        ]
        evt = ActionTraceEvent(**_minimal(), reasoning=blocks)
        assert evt.reasoning == blocks

    def test_tool_call_raw_optional_dict(self) -> None:
        raw = {
            "name": "submit_action_plan",
            "input": {"plan_id": "plan-x", "actions": [{"action_id": "a-001"}]},
        }
        evt = ActionTraceEvent(**_minimal(), tool_call_raw=raw)
        assert evt.tool_call_raw == raw

    def test_observation_optional_dict(self) -> None:
        obs = {"sha256_before": "abc", "sha256_after": "abc", "bytes_written": 4096}
        evt = ActionTraceEvent(**_minimal(), observation=obs)
        assert evt.observation == obs

    def test_critic_result_optional_dict(self) -> None:
        crit = {"passed": True, "confidence": 0.92, "reason": "target name reads natural"}
        evt = ActionTraceEvent(**_minimal(), critic_result=crit)
        assert evt.critic_result == crit

    def test_all_new_fields_default_none(self) -> None:
        evt = ActionTraceEvent(**_minimal())
        assert evt.thought is None
        assert evt.reasoning is None
        assert evt.tool_call_raw is None
        assert evt.observation is None
        assert evt.critic_result is None


class TestSchemaVersion:
    """The schema_version sentinel is required for Phase 26+ migrations."""

    def test_default_schema_version_is_1(self) -> None:
        evt = ActionTraceEvent(**_minimal())
        assert evt.schema_version == 1

    def test_schema_version_int_only(self) -> None:
        with pytest.raises(ValidationError):
            ActionTraceEvent(**_minimal(), schema_version="v1")


class TestForwardCompat:
    """A trace.jsonl row written today must remain readable after Phase
    26 adds more optional fields (the schema_version sentinel will tell
    readers whether to expect new shapes)."""

    def test_unknown_fields_rejected_by_default(self) -> None:
        # Pydantic v2 default behaviour rejects unknown fields. This is
        # deliberate — Phase 26 fields will land via explicit additions,
        # not via free-form dicts.
        with pytest.raises(ValidationError):
            ActionTraceEvent(
                **_minimal(),
                some_field_phase_26_will_add="surprise",
            )

    def test_jsonl_round_trip(self) -> None:
        evt = ActionTraceEvent(
            task_id="task-rt",
            event_type=TraceEventType.ACTION_END,
            action_id="a-077",
            thought="step 3 of 5",
            observation={"status": "ok"},
        )
        line = evt.model_dump_json(exclude_none=True)
        # Round-trip via JSON: must reconstruct losslessly.
        parsed = json.loads(line)
        rebuilt = ActionTraceEvent.model_validate(parsed)
        assert rebuilt.action_id == "a-077"
        assert rebuilt.thought == "step 3 of 5"
        assert rebuilt.observation == {"status": "ok"}
        assert rebuilt.schema_version == 1


class TestEventTypeBinding:
    """ActionTraceEvent is intended for ACTION_* event types, but is
    NOT exclusively bound (a planner-side event may use it too in
    Phase 26). Pin the current expected use without over-constraining."""

    def test_accepts_action_start_and_end(self) -> None:
        for kind in (TraceEventType.ACTION_START, TraceEventType.ACTION_END):
            evt = ActionTraceEvent(task_id="t", event_type=kind)
            assert evt.event_type == kind

    def test_accepts_other_event_types_without_complaint(self) -> None:
        # Phase 26's LLM-loop may emit ActionTraceEvent rows even for
        # mid-step events. We do not constrain the enum here — the
        # caller picks the right event_type for the lifecycle moment.
        evt = ActionTraceEvent(
            task_id="t",
            event_type=TraceEventType.LLM_CALL_END,
            thought="ok",
        )
        assert evt.thought == "ok"


class TestFailureSemantics:
    """is_failure() must keep the same semantics as the parent class."""

    def test_inherits_is_failure(self) -> None:
        ok_evt = ActionTraceEvent(**_minimal(), status="ok")
        fail_evt = ActionTraceEvent(
            **_minimal(), status="fail", failure_type=FailureType.MISSING_OUTPUT
        )
        assert not ok_evt.is_failure()
        assert fail_evt.is_failure()
