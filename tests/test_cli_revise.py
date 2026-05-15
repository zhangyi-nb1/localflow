"""Phase 11 — `localflow revise` CLI command tests.

The CLI is the developer / automation surface for the same refinement
loop the UI exposes via a button. These tests drive the command via
Typer's CliRunner against an isolated LOCALFLOW_HOME so they don't
pollute the user's real ~/.localflow/.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.harness.control_loop import MAX_REVISIONS
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    FileMeta,
    RiskLevel,
    TaskSpec,
    WorkspaceSnapshot,
)
from app.storage.run_store import RunStore


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "lf"
    monkeypatch.setenv("LOCALFLOW_HOME", str(home))
    return home


def _seed_task(
    home: Path,
    task_id: str,
    workspace: Path,
    *,
    skill: str = "agent",
) -> RunStore:
    """Plant a task.json + plan.json + workspace_snapshot.json so
    `localflow revise` has something to revise."""
    store = RunStore(task_id=task_id, home=home)
    workspace.mkdir(parents=True, exist_ok=True)
    task = TaskSpec(
        task_id=task_id,
        user_goal="seed goal",
        workspace_root=str(workspace),
        skill=skill,
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )
    store.save_task(task)
    snap = WorkspaceSnapshot(
        snapshot_id=f"snap-{task_id}",
        task_id=task_id,
        root=str(workspace),
        files=[
            FileMeta(
                path="a.txt",
                file_type="text",
                size_bytes=3,
                modified_at=datetime.now(timezone.utc),
            )
        ],
        total_files=1,
        total_size_bytes=3,
    )
    store.save_workspace(snap)
    plan = ActionPlan(
        plan_id="plan-seed",
        task_id=task_id,
        summary="seed plan",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.INDEX,
                target_path="out.md",
                reason="seed",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": "seed"},
            )
        ],
        expected_outputs=["out.md"],
        risk_summary="low",
    )
    store.save_plan(plan)
    return store


def test_revise_creates_plan_v2(tmp_path: Path, isolated_home: Path) -> None:
    """Happy path: `localflow revise --task-id X --hint "..."` produces
    plans/plan_v2.json AND updates plan.json (the canonical view).
    The agent skill's plan_with_llm is mocked so the test doesn't hit
    a real LLM."""
    ws = tmp_path / "ws"
    store = _seed_task(isolated_home, "2026-05-16-t1", ws, skill="agent")

    def fake_plan_with_llm(self, task, snapshot, **kwargs):
        assert "user_hint" in kwargs
        assert "prior_plan_actions" in kwargs
        return ActionPlan(
            plan_id="plan-revised",
            task_id=task.task_id,
            summary="revised plan",
            actions=[
                Action(
                    action_id="a-001",
                    action_type=ActionType.INDEX,
                    target_path="new_out.md",
                    reason="revised",
                    risk_level=RiskLevel.LOW,
                    reversible=True,
                    requires_approval=False,
                    metadata={"content": "revised"},
                )
            ],
            expected_outputs=["new_out.md"],
            risk_summary="low",
        )

    runner = CliRunner()
    env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
    with patch("app.skills.agent.skill.AgentSkill.plan_with_llm", fake_plan_with_llm):
        result = runner.invoke(
            app,
            ["revise", "--task-id", store.task_id, "--hint", "use a pie chart"],
            env=env,
        )

    assert result.exit_code == 0, result.output
    assert store.plan_version_path(2).exists()
    # plan.json mirrors the latest revision
    new_plan = store.load_plan()
    assert new_plan.plan_id == "plan-revised"


def test_revise_without_existing_plan_errors_cleanly(tmp_path: Path, isolated_home: Path) -> None:
    """Defensive: revise on a task_id that doesn't exist should bubble
    a clear error, not a stack trace."""
    runner = CliRunner()
    env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
    result = runner.invoke(
        app,
        ["revise", "--task-id", "nope", "--hint", "anything"],
        env=env,
    )
    assert result.exit_code != 0
    # Typer surfaces the BadParameter as part of its standard error path.
    assert "no plan" in result.output.lower() or "no plan" in (result.stderr or "").lower()


def test_revise_caps_at_max_revisions(tmp_path: Path, isolated_home: Path) -> None:
    """After MAX_REVISIONS versions, the CLI should refuse instead of
    burning more LLM budget."""
    ws = tmp_path / "ws"
    store = _seed_task(isolated_home, "2026-05-16-t2", ws, skill="agent")
    for v in range(1, MAX_REVISIONS + 1):
        store.save_plan_version(store.load_plan(), v)

    runner = CliRunner()
    env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
    result = runner.invoke(
        app,
        ["revise", "--task-id", store.task_id, "--hint", "one more"],
        env=env,
    )
    assert result.exit_code != 0
    assert "already revised" in result.output.lower()
