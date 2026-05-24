"""Phase 26.1 — react loop core integration tests.

A stub ``LLMClient`` returns predetermined decisions so the loop can
be driven end-to-end without an API call. Real-LLM integration sits
behind ``Executor.execute(react_mode=True)`` exactly the same way —
the stub here is interchangeable with a production AnthropicClient
because both honour the ``LLMClient`` protocol.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.agent.client import LLMClientError, StructuredResponse
from app.harness.executor import Executor
from app.harness.trace import TraceLogger
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    LoopDecision,
    LoopDecisionType,
    ReactConfig,
    RiskLevel,
)
from app.storage.run_store import RunStore

# ─────────────────────────────────────── stub LLMClient


@dataclass
class _StubLLMClient:
    """LLMClient that returns predetermined LoopDecisions in order.

    Each entry in ``decisions`` is either:
      - a LoopDecision (returned as the model's tool call input)
      - an LLMClientError instance (raised — used to test failure paths)

    After the list is exhausted, the next call raises StopIteration
    (we want tests to fail loudly when the loop runs more turns than
    we set up).
    """

    decisions: list[LoopDecision | LLMClientError | dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)
    _idx: int = 0

    def generate_structured(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> StructuredResponse:
        self.calls.append(
            {
                "user_content": messages[0]["content"] if messages else "",
                "tool_name": tool_name,
            }
        )
        if self._idx >= len(self.decisions):
            raise AssertionError(f"stub LLM exhausted at call {self._idx} — set up more decisions")
        item = self.decisions[self._idx]
        self._idx += 1
        if isinstance(item, LLMClientError):
            raise item
        if isinstance(item, LoopDecision):
            payload = item.model_dump(mode="json")
        else:
            payload = item  # raw dict — used to exercise malformed shapes
        return StructuredResponse(
            tool_use_id=f"toolu_stub_{self._idx:03d}",
            payload=payload,
            raw_assistant_content=[
                {
                    "type": "tool_use",
                    "id": f"toolu_stub_{self._idx:03d}",
                    "name": tool_name,
                    "input": payload,
                }
            ],
            usage={"input_tokens": 0, "output_tokens": 0},
            stop_reason="tool_use",
        )


# ─────────────────────────────────────── fixtures


def _mkdir(action_id: str, target: str) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.MKDIR,
        target_path=target,
        reason=f"mkdir {target}",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
    )


def _plan(task_id: str, actions: list[Action]) -> ActionPlan:
    return ActionPlan(
        plan_id=f"plan-{task_id}",
        task_id=task_id,
        summary="react loop test plan",
        actions=actions,
    )


@pytest.fixture
def executor_with_trace(tmp_path: Path):
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    trace = TraceLogger(run_store.trace_path)
    executor = Executor(workspace_root=workspace, run_store=run_store, trace=trace)
    return executor, workspace


def _trace_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ─────────────────────────────────────── core happy paths


class TestReactModeOff:
    def test_react_mode_false_uses_batch_path(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(executor.run_store.task_id, [_mkdir("a-1", "sub/")])
        # No LLM client provided; if the react path is taken with no
        # llm_client it would still fall back — but this asserts the
        # explicit react_mode=False codepath.
        outcome = executor.execute(plan, approved=True, react_mode=False)
        assert outcome.success
        assert (ws / "sub").exists()

    def test_react_mode_true_with_no_llm_client_falls_back_to_batch(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(executor.run_store.task_id, [_mkdir("a-1", "sub/")])
        # Explicit react_mode=True but llm_client=None — must not crash.
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True),
            llm_client=None,
        )
        assert outcome.success
        assert (ws / "sub").exists()


class TestContinueOnlyLoop:
    def test_two_continues_runs_both_actions(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(
            executor.run_store.task_id,
            [_mkdir("a-1", "first/"), _mkdir("a-2", "second/")],
        )
        client = _StubLLMClient(
            decisions=[
                LoopDecision(decision_type=LoopDecisionType.CONTINUE, reason="ok"),
                LoopDecision(decision_type=LoopDecisionType.CONTINUE, reason="ok"),
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        assert outcome.success
        assert (ws / "first").exists()
        assert (ws / "second").exists()
        assert len(outcome.records) == 2
        # Exactly two consultations.
        assert len(client.calls) == 2


class TestSkipDecision:
    def test_skip_does_not_run_action(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(
            executor.run_store.task_id,
            [_mkdir("a-1", "skipme/"), _mkdir("a-2", "keepme/")],
        )
        client = _StubLLMClient(
            decisions=[
                LoopDecision(decision_type=LoopDecisionType.SKIP, reason="not needed"),
                LoopDecision(decision_type=LoopDecisionType.CONTINUE, reason="ok"),
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        assert outcome.success
        # First action was SKIPPED — directory should not exist.
        assert not (ws / "skipme").exists()
        assert (ws / "keepme").exists()
        # Only one ExecutionRecord (the one that actually ran).
        assert len(outcome.records) == 1


class TestReplaceDecision:
    def test_replace_runs_substitute_action(self, executor_with_trace):
        executor, ws = executor_with_trace
        original = _mkdir("a-1", "wrong/")
        substitute = _mkdir("a-react-1", "right/")
        plan = _plan(executor.run_store.task_id, [original])
        client = _StubLLMClient(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.REPLACE,
                    reason="target name was wrong",
                    replacement_action=substitute,
                ),
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        assert outcome.success
        assert not (ws / "wrong").exists()
        assert (ws / "right").exists()
        # The record's action_id reflects the SUBSTITUTE.
        assert outcome.records[0].action_id == "a-react-1"


class TestInsertDecision:
    def test_insert_runs_extra_action_before_planned(self, executor_with_trace):
        executor, ws = executor_with_trace
        planned = _mkdir("a-1", "planned/")
        inserted = _mkdir("a-react-1", "inserted/")
        plan = _plan(executor.run_store.task_id, [planned])
        client = _StubLLMClient(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.INSERT,
                    reason="missed a prerequisite",
                    replacement_action=inserted,
                ),
                LoopDecision(decision_type=LoopDecisionType.CONTINUE, reason="ok"),
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        assert outcome.success
        assert (ws / "inserted").exists()
        assert (ws / "planned").exists()
        # Order matters: inserted ran first.
        action_ids = [r.action_id for r in outcome.records]
        assert action_ids == ["a-react-1", "a-1"]


class TestAbortDecision:
    def test_abort_stops_loop_immediately(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(
            executor.run_store.task_id,
            [_mkdir("a-1", "before/"), _mkdir("a-2", "after/")],
        )
        client = _StubLLMClient(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.CONTINUE,
                    reason="first one ok",
                ),
                LoopDecision(
                    decision_type=LoopDecisionType.ABORT,
                    reason="something looks wrong",
                ),
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        # First one ran; second was aborted before it ran.
        assert (ws / "before").exists()
        assert not (ws / "after").exists()
        assert len(outcome.records) == 1


class TestDriftBudget:
    def test_drift_exceeded_forces_continue(self, executor_with_trace):
        """With max_drift=1, a REPLACE consumes the only drift slot.
        The next SKIP gets converted to CONTINUE under the hood."""
        executor, ws = executor_with_trace
        substitute = _mkdir("a-react-1", "alt/")
        plan = _plan(
            executor.run_store.task_id,
            [_mkdir("a-1", "first/"), _mkdir("a-2", "second/")],
        )
        client = _StubLLMClient(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.REPLACE,
                    reason="swap first",
                    replacement_action=substitute,
                ),
                LoopDecision(
                    decision_type=LoopDecisionType.SKIP,
                    reason="this should be blocked",
                ),
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=1),
            llm_client=client,
        )
        # First REPLACE used the only drift; SECOND turn's SKIP was
        # downgraded to CONTINUE, so a-2 should have run.
        assert (ws / "alt").exists()  # from REPLACE
        assert (ws / "second").exists()  # CONTINUE forced
        assert not (ws / "first").exists()  # original first never ran
        action_ids = [r.action_id for r in outcome.records]
        assert action_ids == ["a-react-1", "a-2"]


class TestLLMFailureFallback:
    def test_llm_error_falls_back_to_batch(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(
            executor.run_store.task_id,
            [_mkdir("a-1", "first/"), _mkdir("a-2", "second/")],
        )
        client = _StubLLMClient(
            decisions=[
                LLMClientError("simulated API outage"),
                # No more decisions — if the loop kept consulting,
                # the stub would AssertionError. The fallback should
                # prevent any further calls.
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        # Both actions still ran via the batch fallback.
        assert (ws / "first").exists()
        assert (ws / "second").exists()
        assert len(outcome.records) == 2
        # Only one LLM call attempted before fallback engaged.
        assert len(client.calls) == 1

    def test_malformed_llm_response_falls_back_to_batch(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(executor.run_store.task_id, [_mkdir("a-1", "first/")])
        client = _StubLLMClient(
            decisions=[
                # Schema-invalid payload: decision_type=REPLACE without replacement_action.
                {"decision_type": "replace", "reason": "broken", "replacement_action": None},
            ]
        )
        outcome = executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        assert (ws / "first").exists()
        assert len(outcome.records) == 1


class TestTraceEvents:
    def test_loop_decision_events_emitted(self, executor_with_trace):
        executor, ws = executor_with_trace
        plan = _plan(executor.run_store.task_id, [_mkdir("a-1", "sub/")])
        client = _StubLLMClient(
            decisions=[
                LoopDecision(decision_type=LoopDecisionType.CONTINUE, reason="ok"),
            ]
        )
        executor.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=client,
        )
        rows = _trace_rows(executor.run_store.trace_path)
        events = [r["event"] for r in rows]
        # The three new event types from Phase 26.0 should all appear.
        assert "loop.decision.requested" in events
        assert "loop.decision.decided" in events
        assert "loop.decision.applied" in events
