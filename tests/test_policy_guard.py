from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.policy_guard import (
    PolicyViolation,
    assess_plan,
    evaluate_action,
    resolve_inside,
)
from app.schemas import ActionPlan
from app.schemas.action import Action, ActionType, RiskLevel


def test_resolve_inside_accepts_relative(workspace: Path) -> None:
    resolved = resolve_inside(workspace, "subdir/a_copy.pdf")
    assert resolved.exists()
    assert workspace in resolved.parents or resolved.parent == workspace


def test_resolve_inside_rejects_parent_traversal(workspace: Path) -> None:
    with pytest.raises(PolicyViolation):
        resolve_inside(workspace, "../escape.txt")


def test_resolve_inside_rejects_absolute(workspace: Path) -> None:
    abs_str = str((workspace / "a.pdf").resolve())
    with pytest.raises(PolicyViolation):
        resolve_inside(workspace, abs_str)


def test_resolve_inside_rejects_empty(workspace: Path) -> None:
    with pytest.raises(PolicyViolation):
        resolve_inside(workspace, "")


def test_evaluate_action_blocks_forbidden(workspace: Path) -> None:
    action = Action(
        action_id="a-1",
        action_type=ActionType.MOVE,
        source_path="a.pdf",
        target_path="papers/a.pdf",
        reason="r",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
    )
    decision = evaluate_action(workspace, action, forbidden_actions=("move",))
    assert not decision.allowed
    assert any("forbidden" in r for r in decision.reasons)


def test_evaluate_action_requires_source_for_move(workspace: Path) -> None:
    action = Action(
        action_id="a-1",
        action_type=ActionType.MOVE,
        target_path="papers/a.pdf",
        reason="r",
        risk_level=RiskLevel.MEDIUM,
    )
    decision = evaluate_action(workspace, action)
    assert not decision.allowed
    assert any("source_path" in r for r in decision.reasons)


def test_assess_plan_blocks_duplicate_action_ids(workspace: Path) -> None:
    action = Action(
        action_id="dup",
        action_type=ActionType.MKDIR,
        target_path="papers",
        reason="r",
    )
    plan = ActionPlan(
        plan_id="p-1",
        task_id="t-1",
        summary="s",
        actions=[action, action.model_copy()],
    )
    assessment = assess_plan(workspace, plan)
    assert not assessment.passed
    assert "dup" in assessment.blocked_actions


def test_assess_plan_blocks_path_escape(workspace: Path) -> None:
    bad = Action(
        action_id="x-1",
        action_type=ActionType.MOVE,
        source_path="a.pdf",
        target_path="../escape/a.pdf",
        reason="r",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
    )
    plan = ActionPlan(plan_id="p", task_id="t", summary="s", actions=[bad])
    assessment = assess_plan(workspace, plan)
    assert not assessment.passed
    assert "x-1" in assessment.blocked_actions


# --------------------------------------------------------------- Phase 5 ---


def _action(action_id: str, **overrides) -> Action:
    defaults = dict(
        action_id=action_id,
        action_type=ActionType.MOVE,
        source_path="a.pdf",
        target_path="papers/a.pdf",
        reason="r",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
    )
    defaults.update(overrides)
    return Action(**defaults)


def test_forbidden_paths_blocks_target_under_dir(workspace: Path) -> None:
    """Phase 5: an action whose target is under a forbidden directory
    is rejected even when allowed_actions / target_path itself are valid."""
    action = _action("a-1", target_path="secrets/leaked.md", source_path=None,
                     action_type=ActionType.INDEX)
    decision = evaluate_action(
        workspace, action, forbidden_paths=("secrets",)
    )
    assert not decision.allowed
    assert any("forbidden_paths" in r for r in decision.reasons)
    assert any("'secrets'" in r for r in decision.reasons)


def test_forbidden_paths_blocks_source_under_dir(workspace: Path) -> None:
    """Move out of a forbidden directory is also blocked — the user
    said 'don't touch X', which includes moving things out of X."""
    action = _action("a-1", source_path="secrets/old.md", target_path="papers/old.md")
    decision = evaluate_action(
        workspace, action, forbidden_paths=("secrets",)
    )
    assert not decision.allowed
    assert any("source_path" in r and "forbidden_paths" in r for r in decision.reasons)


def test_forbidden_paths_blocks_exact_file_match(workspace: Path) -> None:
    """Forbidding a specific file (not just a directory) works too."""
    action = _action("a-1", target_path="creds.txt", source_path=None,
                     action_type=ActionType.INDEX)
    decision = evaluate_action(
        workspace, action, forbidden_paths=("creds.txt",)
    )
    assert not decision.allowed


def test_forbidden_paths_default_empty_is_backwards_compat(workspace: Path) -> None:
    """Pre-Phase-5 callers don't pass forbidden_paths — behavior must
    be identical to before."""
    action = _action("a-1")
    decision = evaluate_action(workspace, action)
    assert decision.allowed


def test_forbidden_paths_unrelated_target_allowed(workspace: Path) -> None:
    action = _action("a-1", target_path="papers/a.pdf")
    decision = evaluate_action(
        workspace, action, forbidden_paths=("secrets", "creds.txt")
    )
    assert decision.allowed


def test_forbidden_paths_invalid_entry_silently_ignored(workspace: Path) -> None:
    """A forbidden_paths entry that itself escapes the workspace is
    meaningless — we ignore it rather than throw, so a bogus prefs.json
    can't lock everyone out of every workspace."""
    action = _action("a-1")
    decision = evaluate_action(
        workspace, action, forbidden_paths=("../etc/passwd", "secrets")
    )
    # The good entry doesn't match papers/a.pdf, the bad entry is ignored
    assert decision.allowed


def test_assess_plan_propagates_forbidden_paths(workspace: Path) -> None:
    bad = _action("x-1", target_path="secrets/x.md", source_path=None,
                  action_type=ActionType.INDEX)
    plan = ActionPlan(plan_id="p", task_id="t", summary="s", actions=[bad])
    assessment = assess_plan(workspace, plan, forbidden_paths=("secrets",))
    assert not assessment.passed
    assert "x-1" in assessment.blocked_actions
