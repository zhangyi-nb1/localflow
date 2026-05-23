"""Phase 25.1 — verify executor emits ActionTraceEvent (not plain TraceEvent)
for ACTION_START / ACTION_END events, and that LLM provenance on the
ActionPlan reaches the event.

The schema landed in Phase 25.0; this PR wires the executor's emit
sites so ``trace.jsonl`` rows for ACTION_* are the richer shape from
day one — Phase 25.2 will then collapse ``execution_log.jsonl`` and
``audit.jsonl`` to views over ``trace.jsonl``.

These tests deliberately keep the focus narrow: they reuse the
existing low-risk MKDIR action so we are testing the emission path,
not the dispatch path (already covered by other tests).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.harness.executor import Executor
from app.harness.trace import TraceLogger
from app.schemas import ActionPlan, ActionTraceEvent
from app.schemas.action import Action, ActionType, RiskLevel
from app.schemas.trace import TraceEventType
from app.storage.run_store import RunStore


@pytest.fixture
def executor_with_trace(tmp_path: Path):
    """Executor wired with a TraceLogger so we can read the JSONL."""
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    trace_path = run_store.run_dir / "trace.jsonl"
    trace = TraceLogger(trace_path)
    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        trace=trace,
    )
    return executor, trace_path, workspace


def _mkdir_action(action_id: str = "a-001", target: str = "subdir") -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.MKDIR,
        target_path=target,
        reason="phase 25.1 emission test",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
    )


def _plan_with_provenance(actions: list[Action], task_id: str) -> ActionPlan:
    """An ActionPlan that carries all three LLM provenance fields."""
    return ActionPlan(
        plan_id=f"plan-{task_id}",
        task_id=task_id,
        summary="trace emission test",
        actions=actions,
        llm_thought="I will create one directory then write an index.",
        llm_reasoning=[
            {"type": "thinking", "thinking": "First step is the directory."},
        ],
        llm_tool_call_raw={
            "id": "toolu_test_001",
            "name": "submit_action_plan",
            "input": {"plan_id": "plan-x", "actions": [{"action_id": "a-001"}]},
        },
    )


def _read_trace_rows(path: Path) -> list[dict]:
    """Read trace.jsonl and flatten each row.

    The on-disk shape is ``{ts, event, payload: {<rest>}}`` — the
    JsonlLogger wraps the TraceEvent's serialised body in a payload
    field. For test convenience we merge ``event_type=event`` plus the
    payload contents into one flat dict so assertions read naturally
    against TraceEvent field names.
    """
    out: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        wrapper = json.loads(line)
        flat = dict(wrapper.get("payload") or {})
        flat["event_type"] = wrapper.get("event")
        flat["ts"] = wrapper.get("ts")
        out.append(flat)
    return out


class TestActionEventShape:
    """ACTION_START and ACTION_END rows must use the richer shape."""

    def test_action_end_row_carries_observation(self, executor_with_trace):
        executor, trace_path, _ = executor_with_trace
        plan = _plan_with_provenance([_mkdir_action()], task_id=executor.run_store.task_id)
        outcome = executor.execute(plan, approved=True)
        assert outcome.success

        rows = _read_trace_rows(trace_path)
        end_rows = [r for r in rows if r.get("event_type") == TraceEventType.ACTION_END.value]
        assert end_rows, "no ACTION_END row found in trace.jsonl"

        end = end_rows[0]
        # observation is the centerpiece of the Phase 25.1 contract.
        assert end.get("observation") is not None
        assert end["observation"]["action_type"] == "mkdir"
        # rollback_entry is folded into observation so a single row
        # reconstructs the full lifecycle.
        assert end["observation"].get("rollback_entry") is not None

    def test_action_start_row_carries_thought(self, executor_with_trace):
        executor, trace_path, _ = executor_with_trace
        plan = _plan_with_provenance([_mkdir_action()], task_id=executor.run_store.task_id)
        executor.execute(plan, approved=True)

        rows = _read_trace_rows(trace_path)
        start_rows = [r for r in rows if r.get("event_type") == TraceEventType.ACTION_START.value]
        assert start_rows, "no ACTION_START row found in trace.jsonl"

        start = start_rows[0]
        assert start.get("thought") == "I will create one directory then write an index."

    def test_action_rows_carry_tool_call_raw(self, executor_with_trace):
        executor, trace_path, _ = executor_with_trace
        plan = _plan_with_provenance([_mkdir_action()], task_id=executor.run_store.task_id)
        executor.execute(plan, approved=True)

        rows = _read_trace_rows(trace_path)
        action_rows = [r for r in rows if r.get("event_type", "").startswith("action.")]
        assert action_rows
        for row in action_rows:
            tcr = row.get("tool_call_raw")
            assert tcr is not None
            assert tcr.get("name") == "submit_action_plan"
            assert tcr.get("id") == "toolu_test_001"

    def test_action_rows_can_be_parsed_as_action_trace_event(self, executor_with_trace):
        executor, trace_path, _ = executor_with_trace
        plan = _plan_with_provenance([_mkdir_action()], task_id=executor.run_store.task_id)
        executor.execute(plan, approved=True)

        rows = _read_trace_rows(trace_path)
        action_rows = [r for r in rows if r.get("event_type", "").startswith("action.")]
        for row in action_rows:
            # Strict parse — the row MUST be a valid ActionTraceEvent
            # (extra='forbid' on the model catches accidental rogue fields).
            ActionTraceEvent.model_validate(row)


class TestBackwardCompat:
    """Plans without LLM provenance still emit ActionTraceEvent for
    ACTION_* events, but with the rich fields left out of the JSON."""

    def test_plan_without_provenance_still_emits_action_event(self, executor_with_trace):
        executor, trace_path, _ = executor_with_trace
        plan = ActionPlan(
            plan_id=f"plan-{executor.run_store.task_id}",
            task_id=executor.run_store.task_id,
            summary="no provenance",
            actions=[_mkdir_action()],
        )
        executor.execute(plan, approved=True)

        rows = _read_trace_rows(trace_path)
        action_rows = [r for r in rows if r.get("event_type", "").startswith("action.")]
        assert action_rows

        for row in action_rows:
            # No provenance — the rich fields are written as ``null`` in
            # the JSON (TraceLogger does ``model_dump(mode='json')``
            # without ``exclude_none``; v0.23.x trace consumers depend
            # on null fields being present, so we keep that shape).
            assert row.get("thought") is None
            assert row.get("reasoning") is None
            assert row.get("tool_call_raw") is None
            # ACTION_END still gets observation (from the executor itself).
            if row["event_type"] == TraceEventType.ACTION_END.value:
                assert row.get("observation") is not None


class TestNonActionEventsStayPlain:
    """LLM_CALL_* / POLICY_CHECK / ROLLBACK_ENTRY rows must NOT be
    upgraded to ActionTraceEvent — v0.23.x grader code expects the
    plain shape for those."""

    def test_non_action_events_have_no_rich_fields(self, executor_with_trace):
        executor, trace_path, workspace = executor_with_trace
        # Build a plan that will trigger POLICY_CHECK (forbidden path).
        executor.forbidden_actions = ("mkdir",)
        plan = _plan_with_provenance(
            [_mkdir_action()], task_id=executor.run_store.task_id
        )
        executor.execute(plan, approved=True)

        rows = _read_trace_rows(trace_path)
        policy_rows = [r for r in rows if r.get("event_type") == TraceEventType.POLICY_CHECK.value]
        assert policy_rows
        for row in policy_rows:
            # POLICY_CHECK is emitted as plain TraceEvent (the executor
            # path that fires it doesn't set rich kwargs). The rich
            # fields therefore must not even appear as keys — plain
            # TraceEvent's model_dump never produces them.
            assert "thought" not in row
            assert "observation" not in row
            assert "critic_result" not in row


class TestFailingActionCarriesErrorObservation:
    """When an action fails (e.g. policy blocks at dispatch time), the
    ACTION_END event still carries an observation describing the
    failure — so a single trace.jsonl row can drive the next-iteration
    repair prompt (Phase 25.2 / 26)."""

    def test_failed_action_end_has_error_observation(self, executor_with_trace, monkeypatch):
        executor, trace_path, _ = executor_with_trace

        # Force a dispatch-time failure by monkeypatching _dispatch to raise.
        def _boom(_self, _action, _manifest):
            raise RuntimeError("simulated dispatch failure")

        monkeypatch.setattr(Executor, "_dispatch", _boom)
        plan = _plan_with_provenance([_mkdir_action()], task_id=executor.run_store.task_id)
        outcome = executor.execute(plan, approved=True)
        assert not outcome.success

        rows = _read_trace_rows(trace_path)
        end_row = next(
            (r for r in rows if r.get("event_type") == TraceEventType.ACTION_END.value),
            None,
        )
        assert end_row is not None
        assert end_row.get("status") == "fail"
        obs = end_row.get("observation") or {}
        assert "simulated dispatch failure" in (obs.get("error") or "")
