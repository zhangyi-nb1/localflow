"""Phase 27.2 — react loop honours ConfirmationPolicy.

When the executor is wired with a confirmation_policy + approver,
the react loop's _dispatch_one must consult the same gate before
running ANY action — both the originally-planned actions and the
LLM-proposed REPLACE / INSERT substitutes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.agent.client import StructuredResponse
from app.harness.approval import ApprovalDecision
from app.harness.executor import Executor
from app.harness.trace import TraceLogger
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    ConfirmationPolicy,
    ConfirmationPolicyType,
    LoopDecision,
    LoopDecisionType,
    ReactConfig,
    RiskLevel,
)
from app.storage.run_store import RunStore


@dataclass
class _StubLLM:
    decisions: list[LoopDecision]
    _idx: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

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
        self.calls.append({"tool": tool_name})
        if self._idx >= len(self.decisions):
            raise AssertionError(f"stub LLM exhausted at call {self._idx}")
        decision = self.decisions[self._idx]
        self._idx += 1
        return StructuredResponse(
            tool_use_id=f"toolu_{self._idx:03d}",
            payload=decision.model_dump(mode="json"),
            raw_assistant_content=[],
            usage={},
            stop_reason="tool_use",
        )


def _mkdir(action_id: str, target: str, risk: RiskLevel = RiskLevel.LOW) -> Action:
    return Action(
        action_id=action_id,
        action_type=ActionType.MKDIR,
        target_path=target,
        reason="mkdir",
        risk_level=risk,
        reversible=True,
        requires_approval=False,
    )


@pytest.fixture
def react_executor(tmp_path: Path):
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    trace = TraceLogger(run_store.trace_path)
    return (
        Executor(workspace_root=workspace, run_store=run_store, trace=trace),
        workspace,
    )


class TestReactPolicyGating:
    def test_inserted_action_runs_when_approver_accepts(self, react_executor):
        ex, ws = react_executor
        planned = _mkdir("a-1", "planned/")
        inserted = _mkdir("a-react-1", "inserted/", risk=RiskLevel.HIGH)
        plan = ActionPlan(
            plan_id="p",
            task_id=ex.run_store.task_id,
            summary="t",
            actions=[planned],
        )

        approver_calls: list[str] = []

        def approver(action):
            approver_calls.append(action.action_id)
            return ApprovalDecision(approved=True, reason="ok")

        llm = _StubLLM(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.INSERT,
                    reason="need a high-risk prerequisite",
                    replacement_action=inserted,
                ),
                LoopDecision(
                    decision_type=LoopDecisionType.CONTINUE,
                    reason="planned ok",
                ),
            ]
        )

        outcome = ex.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=llm,
            confirmation_policy=ConfirmationPolicy(
                policy_type=ConfirmationPolicyType.ON_HIGH_RISK,
                risk_threshold=RiskLevel.HIGH,
            ),
            action_approver=approver,
        )

        assert outcome.success
        # The HIGH-risk INSERTed action consulted the approver.
        assert "a-react-1" in approver_calls
        # The LOW-risk planned action did NOT (ON_HIGH_RISK skipped it).
        assert "a-1" not in approver_calls
        # Both still ran.
        assert (ws / "inserted").exists()
        assert (ws / "planned").exists()

    def test_inserted_action_rejected_by_user_does_not_run(self, react_executor):
        ex, ws = react_executor
        planned = _mkdir("a-1", "planned/")
        inserted = _mkdir("a-react-1", "rejected/", risk=RiskLevel.HIGH)
        plan = ActionPlan(
            plan_id="p",
            task_id=ex.run_store.task_id,
            summary="t",
            actions=[planned],
        )

        def approver(action):
            if action.action_id == "a-react-1":
                return ApprovalDecision(approved=False, reason="user said no")
            return ApprovalDecision(approved=True, reason="ok")

        llm = _StubLLM(
            decisions=[
                LoopDecision(
                    decision_type=LoopDecisionType.INSERT,
                    reason="prerequisite",
                    replacement_action=inserted,
                ),
                LoopDecision(
                    decision_type=LoopDecisionType.CONTINUE,
                    reason="continue",
                ),
            ]
        )

        outcome = ex.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=llm,
            confirmation_policy=ConfirmationPolicy(
                policy_type=ConfirmationPolicyType.ON_HIGH_RISK,
                risk_threshold=RiskLevel.HIGH,
            ),
            action_approver=approver,
        )

        # The rejected INSERT did NOT run.
        assert not (ws / "rejected").exists()
        # The planned LOW-risk action DID run (was not gated).
        assert (ws / "planned").exists()
        # outcome.success is False because the rejection records a FAILED entry.
        assert outcome.success is False
        rejected = next(r for r in outcome.records if r.action_id == "a-react-1")
        assert "user_rejected" in (rejected.error or "")

    def test_react_loop_without_policy_runs_everything(self, react_executor):
        """Sanity — react loop in v0.24.x mode (no policy) keeps
        running every dispatched action without prompting."""
        ex, ws = react_executor
        plan = ActionPlan(
            plan_id="p",
            task_id=ex.run_store.task_id,
            summary="t",
            actions=[_mkdir("a-1", "first/")],
        )
        llm = _StubLLM(
            decisions=[LoopDecision(decision_type=LoopDecisionType.CONTINUE, reason="ok")]
        )
        outcome = ex.execute(
            plan,
            approved=True,
            react_mode=True,
            react_config=ReactConfig(enabled=True, max_drift=3),
            llm_client=llm,
        )
        assert outcome.success
        assert (ws / "first").exists()
