"""Phase 10 — TaskGraphRunner end-to-end tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.harness.taskgraph_runner import run_taskgraph
from app.harness.trace import TraceLogger
from app.schemas import (
    StageFailurePolicy,
    StageSpec,
    StageStatus,
    TaskGraph,
)
from app.storage.run_store import RunStore


def _seed(root: Path) -> None:
    (root / "report.pdf").write_text("doc", encoding="utf-8")
    (root / "photo.png").write_text("img", encoding="utf-8")
    (root / "notes.txt").write_text("notes", encoding="utf-8")


# ───────────────────────────────────── happy path: 2 stages


def test_two_stage_graph_runs_cleanly(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="organize then chart",
        workspace_root=str(ws),
        stages=[
            StageSpec(stage_id="s1", title="Organize", skill="folder_organizer"),
            StageSpec(stage_id="s2", title="Chart", skill="workspace_visualizer"),
        ],
    )
    result = run_taskgraph(graph, store, trace=trace, approved=True)

    assert result.passed is True
    assert [s.status for s in result.stages] == [StageStatus.PASSED, StageStatus.PASSED]
    assert result.stages[0].action_count > 0
    assert result.stages[1].action_count > 0


def test_two_stage_graph_writes_per_stage_artifacts(tmp_path: Path) -> None:
    """The runner must namespace each stage's plan.json /
    workspace_snapshot.json / actions.json under
    <run_dir>/stages/<stage_id>/."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[
            StageSpec(stage_id="s1_organize", title="Organize", skill="folder_organizer"),
            StageSpec(stage_id="s2_chart", title="Chart", skill="workspace_visualizer"),
        ],
    )
    run_taskgraph(graph, store, trace=trace, approved=True)

    assert (store.stages_root / "s1_organize" / "plan.json").exists()
    assert (store.stages_root / "s1_organize" / "actions.json").exists()
    assert (store.stages_root / "s2_chart" / "plan.json").exists()
    assert (store.stages_root / "s2_chart" / "actions.json").exists()
    # Graph-level artifacts at the top.
    assert store.taskgraph_path.exists()
    assert store.taskgraph_result_path.exists()
    assert store.rollback_path.exists()


def test_aggregated_rollback_manifest_has_entries_from_all_stages(tmp_path: Path) -> None:
    """The graph-level rollback_manifest.json must include entries
    from EVERY stage. Single `localflow rollback --run-id <id>`
    undoes the whole graph."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[
            StageSpec(stage_id="s1", title="t", skill="folder_organizer"),
            StageSpec(stage_id="s2", title="t", skill="workspace_visualizer"),
        ],
    )
    run_taskgraph(graph, store, trace=trace, approved=True)

    manifest = store.load_rollback()
    s1_entries = [e for e in manifest.entries if e.action_id.startswith("s1.")]
    s2_entries = [e for e in manifest.entries if e.action_id.startswith("s2.")]
    assert s1_entries, "no rollback entries from stage 1"
    assert s2_entries, "no rollback entries from stage 2"


# ───────────────────────────────────── trace context manager populates stage_id


def test_trace_events_tagged_with_stage_id(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[
            StageSpec(stage_id="s1_organize", title="t", skill="folder_organizer"),
            StageSpec(stage_id="s2_chart", title="t", skill="workspace_visualizer"),
        ],
    )
    run_taskgraph(graph, store, trace=trace, approved=True)

    events = TraceLogger(store.trace_path).read_all()
    s1 = [e for e in events if e.stage_id == "s1_organize"]
    s2 = [e for e in events if e.stage_id == "s2_chart"]
    assert s1, "no events tagged stage_id=s1_organize"
    assert s2, "no events tagged stage_id=s2_chart"


# ───────────────────────────────────── failure policy


def test_abort_policy_marks_subsequent_stages_aborted(tmp_path: Path) -> None:
    """Stage 1 fails (synthetic — bogus skill name) → stage 2's
    status must be ABORTED (not run)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[
            StageSpec(
                stage_id="s1",
                title="t",
                skill="this_skill_does_not_exist",
                failure_policy=StageFailurePolicy.ABORT,
            ),
            StageSpec(stage_id="s2", title="t", skill="folder_organizer"),
        ],
    )
    result = run_taskgraph(graph, store, trace=trace, approved=True)

    assert result.stages[0].status == StageStatus.FAILED
    assert result.stages[1].status == StageStatus.ABORTED
    assert result.passed is False


def test_continue_policy_runs_subsequent_stages(tmp_path: Path) -> None:
    """failure_policy=CONTINUE → stage 1 fails but stage 2 still runs."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[
            StageSpec(
                stage_id="s1",
                title="t",
                skill="this_skill_does_not_exist",
                failure_policy=StageFailurePolicy.CONTINUE,
            ),
            StageSpec(stage_id="s2", title="t", skill="folder_organizer"),
        ],
    )
    result = run_taskgraph(graph, store, trace=trace, approved=True)

    assert result.stages[0].status == StageStatus.FAILED
    assert result.stages[1].status == StageStatus.PASSED
    # Graph passes if every non-failed stage passes — here s1 failed
    # so result.passed must be False even though s2 passed.
    assert result.passed is False


def test_skip_policy_downgrades_failure_to_skipped(tmp_path: Path) -> None:
    """failure_policy=SKIP → stage 1's failure recorded as SKIPPED,
    stage 2 still runs, graph passes (SKIPPED is intentional)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[
            StageSpec(
                stage_id="s1",
                title="t",
                skill="this_skill_does_not_exist",
                failure_policy=StageFailurePolicy.SKIP,
            ),
            StageSpec(stage_id="s2", title="t", skill="folder_organizer"),
        ],
    )
    result = run_taskgraph(graph, store, trace=trace, approved=True)

    assert result.stages[0].status == StageStatus.SKIPPED
    assert result.stages[1].status == StageStatus.PASSED
    assert result.passed is True


# ───────────────────────────────────── runner contract


def test_runner_refuses_without_approval(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[StageSpec(stage_id="s1", title="t", skill="folder_organizer")],
    )
    try:
        run_taskgraph(graph, store, approved=False)
        raise AssertionError("runner must refuse approved=False")
    except RuntimeError as exc:
        assert "not approved" in str(exc)


def test_taskgraph_json_persists_graph_spec(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    graph = TaskGraph(
        user_goal="x",
        workspace_root=str(ws),
        stages=[StageSpec(stage_id="s1", title="t", skill="folder_organizer")],
    )
    run_taskgraph(graph, store, approved=True)

    persisted = json.loads(store.taskgraph_path.read_text(encoding="utf-8"))
    assert persisted["user_goal"] == "x"
    assert len(persisted["stages"]) == 1
