"""Phase 27.0 — pin the ConfirmationPolicy contract + decide-tree."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.harness.approval import (
    ask_action_approval,
    policy_requires_confirmation,
)
from app.schemas import (
    Action,
    ActionType,
    ConfirmationPolicy,
    ConfirmationPolicyType,
    RiskLevel,
)


def _action(
    *,
    action_type: ActionType = ActionType.MKDIR,
    risk_level: RiskLevel = RiskLevel.LOW,
    action_id: str = "a-1",
) -> Action:
    return Action(
        action_id=action_id,
        action_type=action_type,
        target_path="t",
        reason="r",
        risk_level=risk_level,
        reversible=True,
        requires_approval=False,
    )


class TestConfirmationPolicySchema:
    def test_default_is_never_with_threshold_high(self):
        p = ConfirmationPolicy()
        assert p.policy_type == ConfirmationPolicyType.NEVER
        assert p.risk_threshold == RiskLevel.HIGH
        assert p.auto_approve_index is True
        assert p.allow_approve_rest is True

    def test_four_legal_policy_types(self):
        assert {v.value for v in ConfirmationPolicyType} == {
            "never",
            "always",
            "on_high_risk",
            "on_write",
        }

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ConfirmationPolicy(unknown_field=True)

    def test_risk_threshold_must_be_risklevel(self):
        with pytest.raises(ValidationError):
            ConfirmationPolicy(risk_threshold="not a risklevel")


class TestPolicyNever:
    def test_never_returns_false_for_any_action(self):
        p = ConfirmationPolicy(policy_type=ConfirmationPolicyType.NEVER)
        for atype in [ActionType.MKDIR, ActionType.MOVE, ActionType.PYTHON_COMPUTE]:
            for rl in [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]:
                action = _action(action_type=atype, risk_level=rl)
                assert policy_requires_confirmation(action, p) is False


class TestPolicyAlways:
    def test_always_returns_true_for_writes(self):
        p = ConfirmationPolicy(policy_type=ConfirmationPolicyType.ALWAYS)
        a = _action(action_type=ActionType.MOVE, risk_level=RiskLevel.LOW)
        assert policy_requires_confirmation(a, p) is True

    def test_always_short_circuits_on_index(self):
        """auto_approve_index=True wins over ALWAYS for INDEX actions —
        the artefact writes are not worth gating."""
        p = ConfirmationPolicy(
            policy_type=ConfirmationPolicyType.ALWAYS,
            auto_approve_index=True,
        )
        a = _action(action_type=ActionType.INDEX, risk_level=RiskLevel.LOW)
        assert policy_requires_confirmation(a, p) is False

    def test_always_does_gate_index_when_opt_out(self):
        p = ConfirmationPolicy(
            policy_type=ConfirmationPolicyType.ALWAYS,
            auto_approve_index=False,
        )
        a = _action(action_type=ActionType.INDEX, risk_level=RiskLevel.LOW)
        assert policy_requires_confirmation(a, p) is True


class TestPolicyOnWrite:
    def test_on_write_gates_writes_only(self):
        p = ConfirmationPolicy(policy_type=ConfirmationPolicyType.ON_WRITE)
        # WRITE_ACTIONS per app/schemas/action.py:
        #   MKDIR / COPY / MOVE / RENAME / INDEX / CONVERT / FETCH
        # PYTHON_COMPUTE is NOT in WRITE_ACTIONS — it lives in scratch
        # only, the workspace mutation happens via a follow-up stage.
        for atype in [
            ActionType.MKDIR,
            ActionType.MOVE,
            ActionType.COPY,
            ActionType.RENAME,
        ]:
            a = _action(action_type=atype)
            assert policy_requires_confirmation(a, p) is True, f"ON_WRITE should gate {atype.value}"

    def test_on_write_lets_index_pass_when_auto_approve_index(self):
        p = ConfirmationPolicy(policy_type=ConfirmationPolicyType.ON_WRITE)
        # INDEX is artefact-only — auto_approve_index=True (default) skips it.
        a = _action(action_type=ActionType.INDEX)
        assert policy_requires_confirmation(a, p) is False


class TestPolicyOnHighRisk:
    def test_on_high_risk_default_threshold(self):
        p = ConfirmationPolicy(policy_type=ConfirmationPolicyType.ON_HIGH_RISK)
        # threshold defaults to HIGH; only HIGH gets gated.
        for rl, expected in [
            (RiskLevel.LOW, False),
            (RiskLevel.MEDIUM, False),
            (RiskLevel.HIGH, True),
        ]:
            a = _action(action_type=ActionType.MOVE, risk_level=rl)
            assert policy_requires_confirmation(a, p) is expected, (
                f"ON_HIGH_RISK + risk={rl.value}: expected {expected}"
            )

    def test_on_high_risk_with_medium_threshold(self):
        p = ConfirmationPolicy(
            policy_type=ConfirmationPolicyType.ON_HIGH_RISK,
            risk_threshold=RiskLevel.MEDIUM,
        )
        for rl, expected in [
            (RiskLevel.LOW, False),
            (RiskLevel.MEDIUM, True),
            (RiskLevel.HIGH, True),
        ]:
            a = _action(action_type=ActionType.MOVE, risk_level=rl)
            assert policy_requires_confirmation(a, p) is expected

    def test_on_high_risk_with_index_auto_approved_short_circuits(self):
        """``auto_approve_index=True`` (the default) makes INDEX skip
        the whole policy decision tree — even under ON_HIGH_RISK with
        a HIGH-risk INDEX action, no prompt fires."""
        p = ConfirmationPolicy(
            policy_type=ConfirmationPolicyType.ON_HIGH_RISK,
            auto_approve_index=True,  # default
        )
        a = _action(action_type=ActionType.INDEX, risk_level=RiskLevel.HIGH)
        assert policy_requires_confirmation(a, p) is False

    def test_on_high_risk_gates_high_risk_index_when_opt_out(self):
        """When auto_approve_index=False, INDEX is treated like any
        other write action — under ON_HIGH_RISK + risk_level=HIGH,
        the prompt fires."""
        p = ConfirmationPolicy(
            policy_type=ConfirmationPolicyType.ON_HIGH_RISK,
            auto_approve_index=False,
        )
        a = _action(action_type=ActionType.INDEX, risk_level=RiskLevel.HIGH)
        assert policy_requires_confirmation(a, p) is True


class TestAskActionApprovalAutoApprove:
    """When the policy says auto-approve, no prompt happens — the
    function must NOT block on stdin."""

    def test_auto_approves_low_risk_under_never(self):
        p = ConfirmationPolicy(policy_type=ConfirmationPolicyType.NEVER)
        decision = ask_action_approval(_action(), policy=p)
        assert decision.approved is True
        assert "auto-approved" in decision.reason

    def test_auto_approves_index_under_always_when_opt_in(self):
        p = ConfirmationPolicy(
            policy_type=ConfirmationPolicyType.ALWAYS,
            auto_approve_index=True,
        )
        decision = ask_action_approval(_action(action_type=ActionType.INDEX), policy=p)
        assert decision.approved is True
