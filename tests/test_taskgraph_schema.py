"""Phase 10 — TaskGraph + StageSpec schema contracts."""

from __future__ import annotations

import pytest

from app.schemas import (
    StageFailurePolicy,
    StageResult,
    StageSpec,
    StageStatus,
    TaskGraph,
    TaskGraphResult,
)

# ───────────────────────────────────── enum membership pinned


def test_failure_policy_enum_pinned() -> None:
    """Phase 10 ships 3 policies. Phase 12 may add REPAIR — that's a
    deliberate enum change; this test ensures it doesn't drift
    silently."""
    assert {p.value for p in StageFailurePolicy} == {"abort", "continue", "skip"}


def test_stage_status_enum_pinned() -> None:
    assert {s.value for s in StageStatus} == {"passed", "failed", "skipped", "aborted"}


# ───────────────────────────────────── round-trip


def test_stage_spec_defaults() -> None:
    s = StageSpec(stage_id="s1", title="Organize", skill="folder_organizer")
    assert s.planner == "rule"
    assert s.failure_policy == StageFailurePolicy.ABORT
    assert s.max_retries == 1
    assert s.allowed_actions is None  # inherit skill manifest


def test_task_graph_round_trip() -> None:
    g = TaskGraph(
        user_goal="organize then chart",
        workspace_root="/tmp/ws",
        stages=[
            StageSpec(stage_id="s1", title="Organize", skill="folder_organizer"),
            StageSpec(stage_id="s2", title="Chart", skill="workspace_visualizer"),
        ],
    )
    raw = g.model_dump(mode="json")
    again = TaskGraph.model_validate(raw)
    assert len(again.stages) == 2
    assert again.stages[1].skill == "workspace_visualizer"


def test_default_forbidden_actions_includes_delete() -> None:
    """The graph-level defaults bake in the iron rules from the v0.x
    design doc — never accidentally drop delete from the forbidden
    list."""
    g = TaskGraph(
        user_goal="x",
        workspace_root="/x",
        stages=[StageSpec(stage_id="s1", title="t", skill="folder_organizer")],
    )
    assert "delete" in g.forbidden_actions
    assert "overwrite" in g.forbidden_actions
    assert "shell" in g.forbidden_actions


# ───────────────────────────────────── validation


def test_zero_stages_rejected() -> None:
    """A graph with no stages is meaningless — surface it at Pydantic
    validation rather than letting the runner crash later."""
    with pytest.raises(Exception):
        TaskGraph(user_goal="x", workspace_root="/x", stages=[])


def test_duplicate_stage_ids_rejected() -> None:
    with pytest.raises(Exception, match="duplicate stage_id"):
        TaskGraph(
            user_goal="x",
            workspace_root="/x",
            stages=[
                StageSpec(stage_id="s1", title="a", skill="folder_organizer"),
                StageSpec(stage_id="s1", title="b", skill="workspace_visualizer"),
            ],
        )


# ───────────────────────────────────── TaskGraphResult.from_stages


def test_from_stages_all_passed() -> None:
    r = TaskGraphResult.from_stages(
        task_id="t",
        stages=[
            StageResult(stage_id="s1", status=StageStatus.PASSED),
            StageResult(stage_id="s2", status=StageStatus.PASSED),
        ],
        aggregated_manifest_path="/p",
        duration_ms=100,
    )
    assert r.passed is True


def test_from_stages_one_failed_marks_unpassed() -> None:
    r = TaskGraphResult.from_stages(
        task_id="t",
        stages=[
            StageResult(stage_id="s1", status=StageStatus.PASSED),
            StageResult(stage_id="s2", status=StageStatus.FAILED),
        ],
        aggregated_manifest_path="/p",
        duration_ms=100,
    )
    assert r.passed is False


def test_from_stages_skipped_counts_as_pass() -> None:
    """SKIPPED is intentional (failure_policy=SKIP downgrades a
    failure to SKIPPED); the graph as a whole still passes."""
    r = TaskGraphResult.from_stages(
        task_id="t",
        stages=[
            StageResult(stage_id="s1", status=StageStatus.PASSED),
            StageResult(stage_id="s2", status=StageStatus.SKIPPED),
        ],
        aggregated_manifest_path="/p",
        duration_ms=100,
    )
    assert r.passed is True


def test_from_stages_aborted_means_failed() -> None:
    """ABORTED = the stage never ran because an earlier one tripped
    the abort policy; that's a failure of the graph as a whole."""
    r = TaskGraphResult.from_stages(
        task_id="t",
        stages=[
            StageResult(stage_id="s1", status=StageStatus.FAILED),
            StageResult(stage_id="s2", status=StageStatus.ABORTED),
        ],
        aggregated_manifest_path="/p",
        duration_ms=100,
    )
    assert r.passed is False
