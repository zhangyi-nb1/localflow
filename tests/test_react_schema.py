"""Phase 26.0 — pin the LoopDecision + ReactConfig + trace event contract.

The runtime that consumes these schemas (react_loop, executor's
react_mode dispatch, Recipe.enable_react_mode wiring) lands in
Phase 26.1+. This PR ships the schema in isolation so the §10.7 4th
deliberate-exception conversation has a stable contract to anchor on
before any executor change happens.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    Action,
    ActionType,
    LoopDecision,
    LoopDecisionType,
    ReactConfig,
    RiskLevel,
)
from app.schemas.trace import TraceEventType


def _action() -> Action:
    return Action(
        action_id="a-001",
        action_type=ActionType.MKDIR,
        target_path="sub/",
        reason="test",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
    )


class TestLoopDecisionType:
    def test_five_legal_values(self):
        # The design doc pins exactly five — guard against accidental
        # additions slipping in without a §10.7 follow-up review.
        assert {v.value for v in LoopDecisionType} == {
            "continue",
            "replace",
            "insert",
            "skip",
            "abort",
        }


class TestLoopDecision:
    def test_continue_decision_needs_no_replacement(self):
        decision = LoopDecision(
            decision_type=LoopDecisionType.CONTINUE,
            reason="observation looks fine",
        )
        assert decision.decision_type == LoopDecisionType.CONTINUE
        assert decision.replacement_action is None

    def test_skip_decision_needs_no_replacement(self):
        decision = LoopDecision(
            decision_type=LoopDecisionType.SKIP,
            reason="target already correct",
        )
        assert decision.replacement_action is None

    def test_abort_decision_needs_no_replacement(self):
        decision = LoopDecision(
            decision_type=LoopDecisionType.ABORT,
            reason="something is very wrong",
        )
        assert decision.replacement_action is None

    def test_replace_requires_action(self):
        with pytest.raises(ValidationError) as exc:
            LoopDecision(
                decision_type=LoopDecisionType.REPLACE,
                reason="swap planned action",
            )
        assert "requires replacement_action" in str(exc.value)

    def test_insert_requires_action(self):
        with pytest.raises(ValidationError) as exc:
            LoopDecision(
                decision_type=LoopDecisionType.INSERT,
                reason="need a prerequisite first",
            )
        assert "requires replacement_action" in str(exc.value)

    def test_continue_with_action_rejected(self):
        with pytest.raises(ValidationError) as exc:
            LoopDecision(
                decision_type=LoopDecisionType.CONTINUE,
                reason="x",
                replacement_action=_action(),
            )
        assert "forbids replacement_action" in str(exc.value)

    def test_skip_with_action_rejected(self):
        with pytest.raises(ValidationError):
            LoopDecision(
                decision_type=LoopDecisionType.SKIP,
                replacement_action=_action(),
            )

    def test_replace_with_action_accepted(self):
        decision = LoopDecision(
            decision_type=LoopDecisionType.REPLACE,
            reason="prior action revealed wrong target",
            replacement_action=_action(),
        )
        assert decision.replacement_action is not None
        assert decision.replacement_action.action_id == "a-001"

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            LoopDecision(
                decision_type=LoopDecisionType.CONTINUE,
                random_extra="surprise",
            )

    def test_reason_capped(self):
        with pytest.raises(ValidationError):
            LoopDecision(
                decision_type=LoopDecisionType.CONTINUE,
                reason="x" * 3000,  # > 2000 cap
            )


class TestReactConfig:
    def test_defaults_are_conservative(self):
        cfg = ReactConfig()
        assert cfg.enabled is False  # opt-in required
        assert cfg.max_drift == 3
        assert cfg.max_loops_per_action == 1
        assert cfg.llm_timeout_sec == 30
        assert cfg.allow_new_action_types is False

    def test_max_drift_lower_bound(self):
        ReactConfig(max_drift=0)  # legal: react can only emit CONTINUE/ABORT
        with pytest.raises(ValidationError):
            ReactConfig(max_drift=-1)

    def test_max_drift_upper_bound(self):
        ReactConfig(max_drift=20)
        with pytest.raises(ValidationError):
            ReactConfig(max_drift=21)

    def test_llm_timeout_bounds(self):
        ReactConfig(llm_timeout_sec=1)
        ReactConfig(llm_timeout_sec=300)
        with pytest.raises(ValidationError):
            ReactConfig(llm_timeout_sec=0)
        with pytest.raises(ValidationError):
            ReactConfig(llm_timeout_sec=301)

    def test_max_loops_per_action_bounds(self):
        ReactConfig(max_loops_per_action=1)
        ReactConfig(max_loops_per_action=5)
        with pytest.raises(ValidationError):
            ReactConfig(max_loops_per_action=0)
        with pytest.raises(ValidationError):
            ReactConfig(max_loops_per_action=6)

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ReactConfig(unknown_field=True)


class TestTraceEvents:
    """3 new TraceEventType members must exist and be referenceable."""

    def test_three_loop_events_present(self):
        assert TraceEventType.LOOP_DECISION_REQUESTED.value == "loop.decision.requested"
        assert TraceEventType.LOOP_DECISION_DECIDED.value == "loop.decision.decided"
        assert TraceEventType.LOOP_DECISION_APPLIED.value == "loop.decision.applied"

    def test_loop_events_in_enum(self):
        all_values = {member.value for member in TraceEventType}
        assert "loop.decision.requested" in all_values
        assert "loop.decision.decided" in all_values
        assert "loop.decision.applied" in all_values
