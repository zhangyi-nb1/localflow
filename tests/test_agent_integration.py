"""v0.9.1 — agent meta-skill end-to-end integration tests.

These tests construct **synthetic** ActionPlans matching what the LLM
would emit and drive them through the real harness pipeline
(policy_guard → validator → executor → verifier → rollback). The LLM
itself is not invoked — the goal is to pin the *harness behaviour*
around the agent's expanded action surface so a future LLM mistake
(or a deliberate adversarial plan) gets caught by deterministic rules.

The review feedback explicitly listed these cases as the agent's
"防黑箱" minimum bar:

  * compound goal → multi-action plan
  * organize + index + chart in one plan
  * illegal action (path traversal, forbidden action) blocked
  * bad chart_request rejected
  * verify catches missing chart
  * rollback restores generated chart/index
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.harness import control_loop
from app.harness.executor import Executor
from app.harness.rollback import Rollback
from app.schemas import ActionPlan, TaskSpec
from app.schemas.action import Action, ActionType, RiskLevel
from app.skills.agent.llm_planner import render_chart_actions
from app.skills.agent.validator import AgentValidationError, validate_agent_plan
from app.storage.run_store import RunStore
from app.tools.file_scan import scan_workspace

# ───────────────────────────────────── helpers


def _seed_workspace(root: Path) -> None:
    (root / "a.pdf").write_bytes(b"%PDF-1.4\nfake")
    (root / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (root / "c.txt").write_text("hello", encoding="utf-8")


def _compound_plan(task_id: str, workspace: Path) -> ActionPlan:
    """Synthesize the kind of plan the agent's LLM would emit for the
    user's compound goal: organize 3 files into 3 categories, write one
    summary md per category, and a single PNG chart of file counts."""
    actions: list[Action] = []
    counter = 0

    def nxt() -> str:
        nonlocal counter
        counter += 1
        return f"a-{counter:03d}"

    for cat in ("papers", "images", "notes"):
        actions.append(
            Action(
                action_id=nxt(),
                action_type=ActionType.MKDIR,
                target_path=cat,
                reason=f"category dir {cat}",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            )
        )
    actions.append(
        Action(
            action_id=nxt(),
            action_type=ActionType.MOVE,
            source_path="a.pdf",
            target_path="papers/a.pdf",
            reason="move pdf",
            risk_level=RiskLevel.MEDIUM,
            reversible=True,
            requires_approval=True,
        )
    )
    actions.append(
        Action(
            action_id=nxt(),
            action_type=ActionType.MOVE,
            source_path="b.png",
            target_path="images/b.png",
            reason="move image",
            risk_level=RiskLevel.MEDIUM,
            reversible=True,
            requires_approval=True,
        )
    )
    actions.append(
        Action(
            action_id=nxt(),
            action_type=ActionType.MOVE,
            source_path="c.txt",
            target_path="notes/c.txt",
            reason="move text",
            risk_level=RiskLevel.MEDIUM,
            reversible=True,
            requires_approval=True,
        )
    )
    for cat in ("papers", "images", "notes"):
        actions.append(
            Action(
                action_id=nxt(),
                action_type=ActionType.INDEX,
                target_path=f"{cat}/index.md",
                reason=f"summary {cat}",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": f"# {cat}\n\n1 file in {cat}/.\n"},
            )
        )
    # The single chart action — chart_request in metadata, post-processor
    # will substitute binary_content_b64.
    actions.append(
        Action(
            action_id=nxt(),
            action_type=ActionType.INDEX,
            target_path="images/file_counts.png",
            reason="bar chart of file counts",
            risk_level=RiskLevel.LOW,
            reversible=True,
            requires_approval=False,
            metadata={
                "content": None,
                "chart_request": {
                    "kind": "bar",
                    "title": "Files per category",
                    "xlabel": "category",
                    "counts": [
                        {"label": "papers", "value": 1},
                        {"label": "images", "value": 1},
                        {"label": "notes", "value": 1},
                    ],
                },
                "overwrite_existing": True,
            },
        )
    )
    return ActionPlan(
        plan_id="plan-integration",
        task_id=task_id,
        summary="compound: 3 mkdir + 3 move + 3 index.md + 1 chart png",
        actions=actions,
        expected_outputs=[
            "papers/index.md",
            "images/index.md",
            "notes/index.md",
            "images/file_counts.png",
        ],
    )


# ───────────────────────────────────── compound plan end-to-end


def test_compound_plan_executes_and_rollbacks_cleanly(tmp_path: Path) -> None:
    """Full lifecycle: render chart → validate → policy_guard → execute
    → verify → rollback. Asserts each stage's contract holds for a plan
    spanning every action type the agent is allowed to emit (mkdir,
    move, text index, binary chart index)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace(workspace)

    snap = scan_workspace(workspace, task_id="t", compute_hash=False, compute_preview=False)
    store = RunStore.create()
    task = TaskSpec(
        task_id=store.task_id,
        user_goal="compound integration",
        workspace_root=str(workspace),
        skill="agent",
        allowed_actions=["mkdir", "move", "rename", "copy", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
    )
    store.save_task(task)
    store.save_workspace(snap)

    plan = _compound_plan(store.task_id, workspace)
    plan = render_chart_actions(plan)  # substitute chart_request → binary
    validate_agent_plan(plan)  # skill validator
    store.save_plan(plan)

    # policy_guard layer
    assessment = control_loop.run_risk_check(task, plan)
    assert assessment.passed, assessment.warnings

    # execute
    executor = Executor(workspace_root=workspace, run_store=store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success, outcome.failed
    assert (workspace / "papers" / "a.pdf").exists()
    assert (workspace / "images" / "b.png").exists()
    chart_png = workspace / "images" / "file_counts.png"
    assert chart_png.exists()
    assert chart_png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # verifier — independent
    verification = control_loop.run_verify(task, plan, store, outcome, snap)
    assert verification.passed, verification.failed_checks

    # rollback — restore originals, remove generated chart + indexes
    rollback = Rollback(workspace_root=workspace, run_store=store)
    result = rollback.run(outcome.manifest)
    assert result.success, result.failed
    assert (workspace / "a.pdf").exists()
    assert not chart_png.exists()
    assert not (workspace / "papers" / "index.md").exists()


# ───────────────────────────────────── illegal action blocked


def test_path_traversal_in_chart_action_blocked_by_policy_guard(tmp_path: Path) -> None:
    """If the LLM hallucinates a `target_path` that escapes the workspace
    (../etc/passwd, /tmp/foo, etc.), the kernel's policy_guard MUST
    reject the plan during risk check — before the chart post-processor
    or executor sees it. This is the harness's last line of defense."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace(workspace)
    task = TaskSpec(
        task_id="t",
        user_goal="malicious",
        workspace_root=str(workspace),
        skill="agent",
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
    )
    bad_plan = ActionPlan(
        plan_id="p-bad",
        task_id="t",
        summary="path traversal attempt",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="../escape.png",
                reason="naive escape",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={
                    "content": None,
                    "chart_request": {
                        "kind": "bar",
                        "title": "t",
                        "xlabel": "x",
                        "counts": [{"label": "a", "value": 1}],
                    },
                },
            ),
        ],
    )
    assessment = control_loop.run_risk_check(task, bad_plan)
    assert not assessment.passed, "policy_guard must reject ../ escape"
    assert assessment.blocked_actions or assessment.warnings


def test_forbidden_action_type_rejected_at_plan_validation() -> None:
    """`delete` is in forbidden_actions; the plan must be rejected
    structurally regardless of skill. The Pydantic ActionType enum
    doesn't include 'delete', so this is enforced at schema parse time
    — pin it so a future refactor that loosens the enum surfaces here."""
    from app.harness.action_validator import (
        PlanValidationError,
        validate_plan_structure,
    )

    # `delete` isn't in the Pydantic enum, so trying to build an Action
    # with it raises at construction time. That's the failure mode we
    # want — agents can't ever emit delete.
    with pytest.raises(Exception):
        Action(
            action_id="a-001",
            action_type="delete",  # type: ignore[arg-type]
            source_path="a.pdf",
            target_path=None,
            reason="forbidden",
            risk_level=RiskLevel.HIGH,
            reversible=False,
            requires_approval=True,
        )

    # And a plan with duplicate action_ids should still be caught by
    # validate_plan_structure — same defensive layer the agent leans on.
    dup_plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="duplicate ids",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MKDIR,
                target_path="x",
                reason="r",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            ),
            Action(
                action_id="a-001",
                action_type=ActionType.MKDIR,
                target_path="y",
                reason="r",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=True,
            ),
        ],
    )
    with pytest.raises(PlanValidationError):
        validate_plan_structure(dup_plan)


def test_forbidden_path_blocked_when_set_in_task_spec(tmp_path: Path) -> None:
    """Memory-side `forbidden_paths` is the user's explicit "never touch
    X" rule. policy_guard must reject any action whose target intersects
    a forbidden path even when the LLM thinks it's fine."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "secrets").mkdir()
    (workspace / "secrets" / "note.txt").write_text("private", encoding="utf-8")

    task = TaskSpec(
        task_id="t",
        user_goal="x",
        workspace_root=str(workspace),
        skill="agent",
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete"],
        forbidden_paths=["secrets"],
    )
    bad_plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="touches forbidden",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MOVE,
                source_path="secrets/note.txt",
                target_path="notes/note.txt",
                reason="should be blocked",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            ),
        ],
    )
    assessment = control_loop.run_risk_check(task, bad_plan)
    assert not assessment.passed
    assert assessment.blocked_actions or assessment.warnings


# ───────────────────────────────────── chart spec defensiveness


def test_chart_request_with_zero_value_still_renders() -> None:
    """v0.9.1 regression: a category with 0 files should still render
    (chart shows the bar at height 0) rather than getting rejected as
    empty. Real users may organize a workspace where one category
    ends up empty."""
    from app.skills.agent.llm_planner import render_chart_actions as _r

    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="zero-count chart",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="images/file_counts.png",
                reason="chart",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={
                    "content": None,
                    "chart_request": {
                        "kind": "bar",
                        "title": "Files per category",
                        "xlabel": "category",
                        "counts": [
                            {"label": "papers", "value": 3},
                            {"label": "audio", "value": 0},
                        ],
                    },
                },
            ),
        ],
    )
    out = _r(plan)
    action = out.actions[0]
    assert "binary_content_b64" in action.metadata
    decoded = base64.b64decode(action.metadata["binary_content_b64"])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_validator_catches_chart_without_post_processing(tmp_path: Path) -> None:
    """If a caller skips render_chart_actions() and feeds a chart-bearing
    plan straight to validate_agent_plan, the validator catches it.
    This is the regression case the v0.8.2 prompt-fix exposed."""
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="forgot to post-process",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="chart.png",
                reason="r",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={
                    "content": None,
                    "chart_request": {
                        "kind": "bar",
                        "title": "t",
                        "xlabel": "x",
                        "counts": [{"label": "a", "value": 1}],
                    },
                },
            ),
        ],
    )
    with pytest.raises(AgentValidationError, match="binary_content_b64"):
        validate_agent_plan(plan)


# ───────────────────────────────────── rollback restores chart + index outputs


def test_rollback_removes_generated_chart_and_index(tmp_path: Path) -> None:
    """v0.9.1 regression: after a compound plan writes a PNG + index.md
    files, rollback must delete them along with the moves. The harness's
    rollback layer must treat the chart's binary INDEX action the same
    as a text INDEX action — both create files, both get rolled back."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace(workspace)
    snap = scan_workspace(workspace, task_id="t", compute_hash=False, compute_preview=False)
    store = RunStore.create()
    task = TaskSpec(
        task_id=store.task_id,
        user_goal="x",
        workspace_root=str(workspace),
        skill="agent",
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete"],
    )
    store.save_task(task)
    store.save_workspace(snap)
    plan = render_chart_actions(_compound_plan(store.task_id, workspace))
    store.save_plan(plan)

    executor = Executor(workspace_root=workspace, run_store=store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    # Both kinds of generated artifacts present:
    assert (workspace / "images" / "file_counts.png").exists()
    assert (workspace / "papers" / "index.md").exists()

    rollback = Rollback(workspace_root=workspace, run_store=store)
    result = rollback.run(outcome.manifest)
    assert result.success, result.failed
    # Both kinds removed:
    assert not (workspace / "images" / "file_counts.png").exists()
    assert not (workspace / "papers" / "index.md").exists()
    # And the moves were reversed:
    assert (workspace / "a.pdf").exists()
    assert not (workspace / "papers" / "a.pdf").exists()
