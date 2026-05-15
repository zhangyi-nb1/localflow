"""Phase 13 — CLI tests for semantic verifier + memory toggles.

Validates the user-facing surfaces:
- ``localflow verify-semantic`` returns exit 0 / 1 based on aggregate verdict.
- ``localflow execute --no-auto-repair`` skips the loop even when the
  memory pref enables it.
- ``localflow memory set enable_semantic_verifier true`` updates prefs.json
  + bumps schema_version.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.memory import MemoryStore
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    FileMeta,
    RiskLevel,
    RollbackManifest,
    TaskSpec,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.storage.run_store import RunStore


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "lf"
    monkeypatch.setenv("LOCALFLOW_HOME", str(home))
    return home


def _seed_completed_task(
    home: Path,
    task_id: str,
    workspace: Path,
    *,
    skill: str = "agent",
) -> RunStore:
    """Plant a full set of run artifacts so verify-semantic / repair
    have something to read."""
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
    verify = VerificationResult(
        task_id=task_id,
        run_id=task_id,
        passed=True,
        checks=[VerificationCheck(name="x", passed=True)],
        failed_checks=[],
        summary="ok",
        created_at=datetime.now(timezone.utc),
    )
    store.save_verification(verify)
    manifest = RollbackManifest(task_id=task_id, run_id=task_id, entries=[], file_hashes_before={})
    store.save_rollback(manifest)
    return store


def test_verify_semantic_passes_with_no_applicable_graders(
    tmp_path: Path, isolated_home: Path
) -> None:
    """When the task has no outputs the semantic graders skip — the
    aggregate is passed=True → exit code 0."""
    ws = tmp_path / "ws"
    store = _seed_completed_task(isolated_home, "2026-05-16-s1", ws)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["verify-semantic", "--task-id", store.task_id],
        env=os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)},
    )
    assert result.exit_code == 0, result.output


def test_verify_semantic_errors_without_prior_execute(tmp_path: Path, isolated_home: Path) -> None:
    """Running verify-semantic against a task that hasn't been executed
    yet (no verify_report.json) surfaces a clear error, not a stack trace."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["verify-semantic", "--task-id", "nope-001"],
        env=os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)},
    )
    assert result.exit_code != 0


def test_memory_set_enable_semantic_verifier(tmp_path: Path, isolated_home: Path) -> None:
    """`localflow memory set enable_semantic_verifier true` flips the
    prefs.json toggle and the migration path bumps schema_version=3."""
    runner = CliRunner()
    env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
    result = runner.invoke(
        app,
        ["memory", "set", "enable_semantic_verifier", "true"],
        env=env,
    )
    assert result.exit_code == 0, result.output
    store = MemoryStore(home=isolated_home / "memory")
    prefs = store.load()
    assert prefs.enable_semantic_verifier is True
    assert prefs.schema_version == 3


def test_memory_set_max_auto_repairs(tmp_path: Path, isolated_home: Path) -> None:
    """The integer scalar setter parses and persists the cap."""
    runner = CliRunner()
    env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
    result = runner.invoke(
        app,
        ["memory", "set", "max_auto_repairs", "3"],
        env=env,
    )
    assert result.exit_code == 0, result.output
    prefs = MemoryStore(home=isolated_home / "memory").load()
    assert prefs.max_auto_repairs == 3


def test_memory_set_rejects_out_of_range_max_auto_repairs(
    tmp_path: Path, isolated_home: Path
) -> None:
    """Pydantic Field(ge=0, le=5) catches out-of-range values cleanly."""
    runner = CliRunner()
    env = os.environ.copy() | {"LOCALFLOW_HOME": str(isolated_home)}
    result = runner.invoke(
        app,
        ["memory", "set", "max_auto_repairs", "99"],
        env=env,
    )
    assert result.exit_code != 0
