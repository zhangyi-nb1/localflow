"""Phase 27.1 — executor per-action gating via ConfirmationPolicy.

Verify that the executor consults the policy + approver between
actions, and that NEVER policy / no-policy paths preserve v0.24.x
behaviour exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.approval import ApprovalDecision
from app.harness.executor import Executor
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    ConfirmationPolicy,
    ConfirmationPolicyType,
    RiskLevel,
)
from app.storage.run_store import RunStore


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


def _plan(task_id: str, actions: list[Action]) -> ActionPlan:
    return ActionPlan(plan_id=f"plan-{task_id}", task_id=task_id, summary="test", actions=actions)


@pytest.fixture
def executor(tmp_path: Path):
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return Executor(workspace_root=workspace, run_store=run_store), workspace


class TestNoPolicyMeansV0_24_Behaviour:
    def test_no_policy_runs_all_actions(self, executor):
        ex, ws = executor
        plan = _plan(ex.run_store.task_id, [_mkdir("a-1", "first/"), _mkdir("a-2", "second/")])
        outcome = ex.execute(plan, approved=True)
        assert outcome.success
        assert (ws / "first").exists()
        assert (ws / "second").exists()


class TestNeverPolicy:
    def test_never_policy_runs_all_actions(self, executor):
        ex, ws = executor
        plan = _plan(ex.run_store.task_id, [_mkdir("a-1", "first/"), _mkdir("a-2", "second/")])
        outcome = ex.execute(
            plan,
            approved=True,
            confirmation_policy=ConfirmationPolicy(policy_type=ConfirmationPolicyType.NEVER),
        )
        assert outcome.success
        assert (ws / "first").exists()
        assert (ws / "second").exists()


class TestAlwaysPolicyWithApprovedStub:
    def test_always_with_approve_all_runs_everything(self, executor):
        ex, ws = executor
        plan = _plan(ex.run_store.task_id, [_mkdir("a-1", "first/"), _mkdir("a-2", "second/")])
        calls: list[str] = []

        def approver(action):
            calls.append(action.action_id)
            return ApprovalDecision(approved=True, reason="stub yes")

        outcome = ex.execute(
            plan,
            approved=True,
            confirmation_policy=ConfirmationPolicy(
                policy_type=ConfirmationPolicyType.ALWAYS,
                auto_approve_index=False,  # force gating even on safe actions
            ),
            action_approver=approver,
        )
        assert outcome.success
        # Both actions consulted the approver before running.
        assert calls == ["a-1", "a-2"]
        assert (ws / "first").exists()
        assert (ws / "second").exists()

    def test_rejecting_an_action_marks_it_failed_and_continues(self, executor):
        ex, ws = executor
        plan = _plan(ex.run_store.task_id, [_mkdir("a-1", "rejected/"), _mkdir("a-2", "accepted/")])

        def approver(action):
            if action.action_id == "a-1":
                return ApprovalDecision(approved=False, reason="user rejected first")
            return ApprovalDecision(approved=True, reason="user accepted second")

        outcome = ex.execute(
            plan,
            approved=True,
            confirmation_policy=ConfirmationPolicy(
                policy_type=ConfirmationPolicyType.ALWAYS,
                auto_approve_index=False,
            ),
            action_approver=approver,
        )
        # The plan as a whole did NOT succeed (one action blocked) but
        # the second one DID run.
        assert outcome.success is False
        assert not (ws / "rejected").exists()
        assert (ws / "accepted").exists()
        # Action record reflects user rejection.
        rejected = next(r for r in outcome.records if r.action_id == "a-1")
        assert "user_rejected" in (rejected.error or "")


class TestNoApproverWiredFailsClosed:
    """If a policy gates an action but no approver callback is wired,
    the executor must reject — silently passing would defeat the
    whole point of the policy."""

    def test_policy_without_approver_rejects_action(self, executor):
        ex, ws = executor
        plan = _plan(ex.run_store.task_id, [_mkdir("a-1", "blocked/")])
        outcome = ex.execute(
            plan,
            approved=True,
            confirmation_policy=ConfirmationPolicy(
                policy_type=ConfirmationPolicyType.ALWAYS,
                auto_approve_index=False,
            ),
            action_approver=None,
        )
        assert outcome.success is False
        assert not (ws / "blocked").exists()
        rejected = outcome.records[0]
        assert "no approver wired" in (rejected.error or "")


class TestOnHighRiskGate:
    def test_on_high_risk_gates_only_high(self, executor):
        ex, ws = executor
        plan = _plan(
            ex.run_store.task_id,
            [
                _mkdir("a-1", "low/", risk=RiskLevel.LOW),
                _mkdir("a-2", "high/", risk=RiskLevel.HIGH),
            ],
        )
        approver_calls: list[str] = []

        def approver(action):
            approver_calls.append(action.action_id)
            return ApprovalDecision(approved=True, reason="ok")

        outcome = ex.execute(
            plan,
            approved=True,
            confirmation_policy=ConfirmationPolicy(
                policy_type=ConfirmationPolicyType.ON_HIGH_RISK,
                risk_threshold=RiskLevel.HIGH,
            ),
            action_approver=approver,
        )
        # Only the HIGH-risk action consulted the approver.
        assert approver_calls == ["a-2"]
        # Both actions still ran (low auto-approved, high accepted).
        assert outcome.success
        assert (ws / "low").exists()
        assert (ws / "high").exists()
