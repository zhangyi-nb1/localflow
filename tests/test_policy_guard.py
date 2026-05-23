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
    action = _action(
        "a-1", target_path="secrets/leaked.md", source_path=None, action_type=ActionType.INDEX
    )
    decision = evaluate_action(workspace, action, forbidden_paths=("secrets",))
    assert not decision.allowed
    assert any("forbidden_paths" in r for r in decision.reasons)
    assert any("'secrets'" in r for r in decision.reasons)


def test_forbidden_paths_blocks_source_under_dir(workspace: Path) -> None:
    """Move out of a forbidden directory is also blocked — the user
    said 'don't touch X', which includes moving things out of X."""
    action = _action("a-1", source_path="secrets/old.md", target_path="papers/old.md")
    decision = evaluate_action(workspace, action, forbidden_paths=("secrets",))
    assert not decision.allowed
    assert any("source_path" in r and "forbidden_paths" in r for r in decision.reasons)


def test_forbidden_paths_blocks_exact_file_match(workspace: Path) -> None:
    """Forbidding a specific file (not just a directory) works too."""
    action = _action("a-1", target_path="creds.txt", source_path=None, action_type=ActionType.INDEX)
    decision = evaluate_action(workspace, action, forbidden_paths=("creds.txt",))
    assert not decision.allowed


def test_forbidden_paths_default_empty_is_backwards_compat(workspace: Path) -> None:
    """Pre-Phase-5 callers don't pass forbidden_paths — behavior must
    be identical to before."""
    action = _action("a-1")
    decision = evaluate_action(workspace, action)
    assert decision.allowed


def test_forbidden_paths_unrelated_target_allowed(workspace: Path) -> None:
    action = _action("a-1", target_path="papers/a.pdf")
    decision = evaluate_action(workspace, action, forbidden_paths=("secrets", "creds.txt"))
    assert decision.allowed


def test_forbidden_paths_invalid_entry_silently_ignored(workspace: Path) -> None:
    """A forbidden_paths entry that itself escapes the workspace is
    meaningless — we ignore it rather than throw, so a bogus prefs.json
    can't lock everyone out of every workspace."""
    action = _action("a-1")
    decision = evaluate_action(workspace, action, forbidden_paths=("../etc/passwd", "secrets"))
    # The good entry doesn't match papers/a.pdf, the bad entry is ignored
    assert decision.allowed


def test_assess_plan_propagates_forbidden_paths(workspace: Path) -> None:
    bad = _action("x-1", target_path="secrets/x.md", source_path=None, action_type=ActionType.INDEX)
    plan = ActionPlan(plan_id="p", task_id="t", summary="s", actions=[bad])
    assessment = assess_plan(workspace, plan, forbidden_paths=("secrets",))
    assert not assessment.passed
    assert "x-1" in assessment.blocked_actions


# --------------------------------------------------------------- Phase 23 ---


def _compute_metadata(inputs: list[str] | None = None) -> dict:
    from app.schemas.compute import (
        ArtifactSpec,
        ComputeAction,
        ComputeInputRef,
        SandboxPolicy,
    )

    return ComputeAction(
        script="print('hi')",
        script_summary="test",
        inputs=[ComputeInputRef(rel_path=p, size_bytes=1) for p in (inputs or [])],
        expected_outputs=[ArtifactSpec(relative_path="outputs/out.txt", description="x")],
        sandbox_policy=SandboxPolicy(timeout_sec=5),
    ).model_dump(mode="json")


def test_python_compute_no_source_no_target_is_ok(workspace: Path) -> None:
    """PYTHON_COMPUTE is the only action type that legitimately has
    no source_path AND no target_path — outputs land in scratch, not
    in the workspace. Policy_guard must not flag this as missing."""
    action = Action(
        action_id="c-1",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="run a script",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=_compute_metadata(),
    )
    decision = evaluate_action(workspace, action)
    assert decision.allowed, decision.reasons


def test_python_compute_bad_metadata_rejected(workspace: Path) -> None:
    action = Action(
        action_id="c-2",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="bad",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata={"missing_required_fields": True},
    )
    decision = evaluate_action(workspace, action)
    assert not decision.allowed
    assert any("PYTHON_COMPUTE metadata" in r for r in decision.reasons)


def test_python_compute_input_escape_rejected(workspace: Path) -> None:
    """A ComputeAction declaring an input path that resolves outside
    the workspace must be blocked at policy time — even though the
    typed ComputeInputRef already rejects '..', a hostile planner
    could still try absolute / drive-letter paths."""
    action = Action(
        action_id="c-3",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="escape attempt",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        # ComputeInputRef itself rejects ..; this attempts an absolute
        # path embedded in metadata, which we hand-craft to bypass
        # ComputeInputRef validation. The policy_guard layer is the
        # last line of defence.
        metadata={
            "script": "print('hi')",
            "script_summary": "x",
            "inputs": [{"rel_path": "C:/Windows/System32/cmd.exe", "size_bytes": 1}],
            "expected_outputs": [
                {"relative_path": "outputs/out.txt", "description": "x"}
            ],
            "sandbox_policy": {"timeout_sec": 5},
        },
    )
    decision = evaluate_action(workspace, action)
    assert not decision.allowed
    # Either ComputeAction itself rejects it (rel_path validation), or
    # the policy guard catches the abs path at resolve_inside.
    joined = " ".join(decision.reasons)
    assert "PYTHON_COMPUTE" in joined


def test_python_compute_forbidden_input_blocked(workspace: Path) -> None:
    """An input inside forbidden_paths is blocked."""
    # Put a secret file into the workspace.
    (workspace / "secrets").mkdir(exist_ok=True)
    (workspace / "secrets" / "creds.txt").write_text("nope", encoding="utf-8")
    action = Action(
        action_id="c-4",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="reads forbidden",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=_compute_metadata(inputs=["secrets/creds.txt"]),
    )
    decision = evaluate_action(workspace, action, forbidden_paths=("secrets",))
    assert not decision.allowed
    assert any("forbidden_paths" in r for r in decision.reasons)
