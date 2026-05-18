"""v0.15.0 — Phase 15 capability tests.

Covers:
- chart_accurate vision grader (graceful skip without LLM)
- MCP taskgraph_run / verify_semantic / repair_run tool registration
- per-stage rollback filter
- cross-stage replay rejection on unknown stage
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.eval.graders.vision import chart_accurate
from app.eval.schema import EvalTask, GraderContext
from app.harness.rollback import filter_manifest_to_stage
from app.harness.taskgraph_runner import replay_from_stage
from app.mcp.tools import TOOLS, get_tool
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    RiskLevel,
    RollbackEntry,
    RollbackManifest,
    TaskGraph,
    TaskSpec,
    VerificationCheck,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.schemas.rollback import RollbackOpType
from app.storage.run_store import RunStore

# ─────────────────────────────────── chart_accurate vision grader


def _ctx(workspace: Path, chart_actions: list[Action]) -> GraderContext:
    task_spec = TaskSpec(
        task_id="t-1",
        user_goal="chart",
        workspace_root=str(workspace),
        skill="data_analyzer",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )
    plan = ActionPlan(
        plan_id="p",
        task_id="t-1",
        summary="seed",
        actions=chart_actions,
        expected_outputs=[a.target_path for a in chart_actions if a.target_path],
        risk_summary="low",
    )
    snap = WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t-1",
        root=str(workspace),
        files=[],
        total_files=0,
        total_size_bytes=0,
    )
    eval_task = EvalTask.model_construct(
        task_id="t-1",
        title="t",
        goal="t",
        skill="data_analyzer",
        planner="rule",
        expected_outputs=[],
        workspace_seed=[],
        graders=[],
        must_pass=[],
        stages=None,
    )
    return GraderContext(
        task=eval_task,
        task_spec=task_spec,
        plan=plan,
        snapshot_before=snap,
        snapshot_after=None,
        execution_records=[],
        manifest=RollbackManifest(task_id="t-1", run_id="t-1", entries=[], file_hashes_before={}),
        verification=VerificationResult(
            task_id="t-1",
            run_id="t-1",
            passed=True,
            checks=[VerificationCheck(name="x", passed=True)],
            failed_checks=[],
            summary="ok",
            created_at=datetime.now(timezone.utc),
        ),
        trace_events=[],
        workspace_path=workspace,
        seed_hashes={},
    )


def test_chart_accurate_skips_when_no_chart_actions(tmp_path: Path) -> None:
    """Plans without any PNG chart actions → grader trivially passes."""
    ctx = _ctx(tmp_path, chart_actions=[])
    v = chart_accurate(ctx)
    assert v.passed is True
    assert "no chart" in v.detail.lower()


def test_chart_accurate_skips_without_llm_client(tmp_path: Path) -> None:
    """When no LLM client is configured, the grader gracefully passes
    with a 'skipped' detail — never fails the run on infra issues."""
    chart_action = Action(
        action_id="a-001",
        action_type=ActionType.INDEX,
        target_path="chart.png",
        reason="seed",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
        metadata={"chart_request": {"kind": "bar", "title": "x", "xlabel": "y", "counts": []}},
    )
    (tmp_path / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    ctx = _ctx(tmp_path, chart_actions=[chart_action])
    with patch("app.eval.graders.vision.get_default_client_or_none", return_value=None):
        v = chart_accurate(ctx)
    assert v.passed is True
    assert "skipped" in v.detail.lower()


def test_chart_accurate_handles_missing_chart_file(tmp_path: Path) -> None:
    """Plan declares a chart action but the PNG isn't on disk → grader
    skips with a clear detail rather than crashing."""
    chart_action = Action(
        action_id="a-001",
        action_type=ActionType.INDEX,
        target_path="ghost.png",
        reason="seed",
        risk_level=RiskLevel.LOW,
        reversible=True,
        requires_approval=False,
        metadata={"chart_request": {"kind": "bar", "title": "x", "xlabel": "y", "counts": []}},
    )
    ctx = _ctx(tmp_path, chart_actions=[chart_action])
    with patch("app.eval.graders.vision.get_default_client_or_none", return_value=object()):
        v = chart_accurate(ctx)
    assert v.passed is True
    assert "missing" in v.detail.lower()


# ─────────────────────────────────── MCP Phase 15 tool registration


def test_phase15_mcp_tools_registered() -> None:
    """The three new tools are visible in the default registry view."""
    names = {t.name for t in TOOLS}
    assert {"taskgraph_run", "verify_semantic", "repair_run"} <= names


def test_phase15_mcp_tools_have_input_schema() -> None:
    """Every new tool advertises a JSON schema with required fields."""
    for name in ("taskgraph_run", "verify_semantic", "repair_run"):
        tool = get_tool(name)
        assert tool is not None
        assert tool.input_schema.get("type") == "object"
        assert "properties" in tool.input_schema


# ─────────────────────────────────── per-stage rollback filter


def test_filter_manifest_to_stage() -> None:
    """Aggregated manifest → just the entries from one stage's prefix."""
    manifest = RollbackManifest(
        task_id="t",
        run_id="t",
        entries=[
            RollbackEntry(action_id="s1.a-001", op=RollbackOpType.MOVE_BACK, target_path="x"),
            RollbackEntry(action_id="s1.a-002", op=RollbackOpType.MOVE_BACK, target_path="y"),
            RollbackEntry(
                action_id="s2.a-001", op=RollbackOpType.DELETE_CREATED_FILE, target_path="z"
            ),
        ],
        file_hashes_before={},
    )
    s1_only = filter_manifest_to_stage(manifest, "s1")
    s2_only = filter_manifest_to_stage(manifest, "s2")
    missing = filter_manifest_to_stage(manifest, "s99_not_there")

    assert [e.action_id for e in s1_only.entries] == ["s1.a-001", "s1.a-002"]
    assert [e.action_id for e in s2_only.entries] == ["s2.a-001"]
    assert missing.entries == []


# ─────────────────────────────────── cross-stage replay validation


def test_replay_from_stage_rejects_unknown_stage(tmp_path: Path) -> None:
    """An unknown stage_id raises a clear ValueError rather than
    silently no-oping."""
    store = RunStore(task_id="t-1", home=tmp_path)
    # Seed a minimal manifest so the loader has something to find.
    store.save_rollback(
        RollbackManifest(task_id="t-1", run_id="t-1", entries=[], file_hashes_before={})
    )
    graph = TaskGraph.model_validate(
        {
            "user_goal": "g",
            "workspace_root": str(tmp_path),
            "stages": [
                {"stage_id": "s1", "title": "x", "skill": "folder_organizer"},
                {"stage_id": "s2", "title": "y", "skill": "workspace_visualizer"},
            ],
        }
    )
    with pytest.raises(ValueError, match="not in graph stages"):
        replay_from_stage(graph=graph, run_store=store, from_stage="ghost_stage")


# ─────────────────────────────────── StageSpec.cross_stage_repair_target field


def test_stage_spec_accepts_cross_stage_repair_target() -> None:
    """The new optional field round-trips via Pydantic."""
    graph = TaskGraph.model_validate(
        {
            "user_goal": "g",
            "workspace_root": "/tmp/ws",
            "stages": [
                {"stage_id": "s1", "title": "x", "skill": "folder_organizer"},
                {
                    "stage_id": "s2",
                    "title": "y",
                    "skill": "workspace_visualizer",
                    "cross_stage_repair_target": "s1",
                },
            ],
        }
    )
    assert graph.stages[0].cross_stage_repair_target is None
    assert graph.stages[1].cross_stage_repair_target == "s1"
