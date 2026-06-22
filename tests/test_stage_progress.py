"""Phase 38 (R6) — stage-level checkpoint / resume / handoff facade tests.

Deterministic, offline (folder_organizer rule planner, no LLM). Covers the
pure helpers (graph hash / progress derivation / handoff render) and the
load-bearing integration: budgeted resume completes a multi-stage task and
produces a workspace equivalent to an uninterrupted run.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.harness.stage_progress import (
    compute_graph_hash,
    derive_progress,
    read_progress,
    render_handoff,
    resume_taskgraph,
)
from app.schemas.progress import StageProgressStatus
from app.schemas.taskgraph import StageResult, StageStatus, TaskGraph, TaskGraphResult
from app.storage.run_store import RunStore


def _graph(ws: Path, n: int = 4) -> TaskGraph:
    return TaskGraph.model_validate(
        {
            "user_goal": "organize",
            "workspace_root": str(ws),
            "stages": [
                {
                    "stage_id": f"s{i}",
                    "title": f"stage {i}",
                    "skill": "folder_organizer",
                    "planner": "rule",
                    "failure_policy": "skip",
                }
                for i in range(n)
            ],
        }
    )


def _seed(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    for name in ["a.txt", "b.csv", "c.png", "d.log"]:
        (ws / name).write_text("x", encoding="utf-8")


def _files(ws: Path) -> list[str]:
    return sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())


# ───────────────────────────── pure helpers


def test_graph_hash_stable_and_sensitive(tmp_path: Path) -> None:
    g = _graph(tmp_path, 3)
    assert compute_graph_hash(g) == compute_graph_hash(g)
    g2 = _graph(tmp_path, 4)  # different stage count
    assert compute_graph_hash(g) != compute_graph_hash(g2)


def test_derive_progress_maps_statuses(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    rs = RunStore.create(home=tmp_path / ".localflow")
    g = _graph(ws, 4)
    # synthetic result: passed+verified / passed-no-evidence / failed / (missing)
    result = TaskGraphResult(
        task_id=rs.task_id,
        passed=False,
        aggregated_manifest_path="x",
        duration_ms=0,
        stages=[
            StageResult(stage_id="s0", status=StageStatus.PASSED, verifier_passed=True),
            StageResult(stage_id="s1", status=StageStatus.PASSED, verifier_passed=None),
            StageResult(stage_id="s2", status=StageStatus.FAILED),
            # s3 absent → PENDING
        ],
    )
    rs.write_json(rs.taskgraph_result_path, result.model_dump(mode="json"))

    state = derive_progress(g, rs, goal="organize")
    by_id = {s.stage_id: s for s in state.stages}
    assert by_id["s0"].status == StageProgressStatus.VERIFIED
    assert by_id["s0"].verified_evidence is not None
    assert by_id["s1"].status == StageProgressStatus.IMPLEMENTED
    assert by_id["s2"].status == StageProgressStatus.BLOCKED
    assert by_id["s3"].status == StageProgressStatus.PENDING
    assert state.done_ids() == {"s0", "s1"}
    assert state.pending_ids() == ["s2", "s3"]
    assert state.next_step == "s2"


def test_render_handoff_has_five_fields(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    rs = RunStore.create(home=tmp_path / ".localflow")
    g = _graph(ws, 2)
    state = derive_progress(g, rs, goal="g")  # no result → all pending
    md = render_handoff(state)
    assert "# Handoff" in md
    assert "## Done" in md
    assert "## Remaining" in md
    assert "## Blocked" in md
    assert "Next start:" in md


# ───────────────────────────── integration: budgeted resume


def test_budgeted_resume_completes_and_equals_uninterrupted(tmp_path: Path) -> None:
    # reference: one uninterrupted run
    ref_ws = tmp_path / "ref"
    _seed(ref_ws)
    ref_rs = RunStore.create(home=tmp_path / ".lf_ref")
    ref_res = resume_taskgraph(_graph(ref_ws, 4), ref_rs, max_stages=None)
    assert ref_res is not None
    assert all(s.status in (StageStatus.PASSED, StageStatus.SKIPPED) for s in ref_res.stages)
    ref_files = _files(ref_ws)

    # budgeted: 4 stages, 2 per session
    ws = tmp_path / "ws"
    _seed(ws)
    rs = RunStore.create(home=tmp_path / ".lf")
    g = _graph(ws, 4)
    completed = False
    for _ in range(3):  # ceil(4/2)=2 sessions suffice
        resume_taskgraph(g, rs, max_stages=2)
        if not read_progress(rs).pending_ids():
            completed = True
            break
    assert completed, "budgeted resume must finish all stages within the session cap"
    # cross-session state was persisted
    assert (rs.run_dir / "progress.json").is_file()
    assert (rs.run_dir / "handoff.md").is_file()
    # equivalence invariant: resumed workspace == uninterrupted workspace
    assert _files(ws) == ref_files


def test_resume_idempotent_when_complete(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _seed(ws)
    rs = RunStore.create(home=tmp_path / ".lf")
    g = _graph(ws, 2)
    resume_taskgraph(g, rs, max_stages=None)  # completes everything
    snap = _files(ws)
    again = resume_taskgraph(g, rs, max_stages=None)  # nothing pending
    assert again is not None
    assert not read_progress(rs).pending_ids()
    assert _files(ws) == snap  # no-op, no further mutation


def test_resume_refuses_changed_graph(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _seed(ws)
    rs = RunStore.create(home=tmp_path / ".lf")
    resume_taskgraph(_graph(ws, 2), rs, max_stages=1)  # writes progress with hash
    with pytest.raises(ValueError, match="graph shape changed"):
        resume_taskgraph(_graph(ws, 4), rs, max_stages=1)  # different shape


# ───────────────────────────── CLI: localflow taskgraph resume


def test_cli_resume_across_sessions(tmp_path, monkeypatch) -> None:
    """`localflow taskgraph resume` re-enters a run, runs a budgeted slice,
    persists the graph (so the 2nd session needs no --graph), and completes."""
    import yaml
    from typer.testing import CliRunner

    from app.cli import app

    monkeypatch.setenv("LOCALFLOW_HOME", str(tmp_path / "lf"))
    ws = tmp_path / "ws"
    _seed(ws)
    graph_yaml = tmp_path / "g.yaml"
    graph_yaml.write_text(
        yaml.safe_dump(
            {
                "user_goal": "resume cli",
                "workspace_root": str(ws),
                "stages": [
                    {
                        "stage_id": f"s{i}",
                        "title": f"s{i}",
                        "skill": "folder_organizer",
                        "planner": "rule",
                        "failure_policy": "skip",
                    }
                    for i in range(4)
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    # session 1: cold start, budget 2 → resumed, stages remain
    r1 = runner.invoke(
        app,
        [
            "taskgraph",
            "resume",
            "--run-id",
            "cli1",
            "--graph",
            str(graph_yaml),
            "--max-stages",
            "2",
        ],
    )
    assert r1.exit_code == 0, r1.output
    assert "RESUMED" in r1.output

    # session 2: NO --graph (loads persisted taskgraph.json) → completes
    r2 = runner.invoke(app, ["taskgraph", "resume", "--run-id", "cli1"])
    assert r2.exit_code == 0, r2.output
    assert "COMPLETE" in r2.output

    # the handoff artifacts exist under the isolated home
    run_dir = tmp_path / "lf" / "runs" / "cli1"
    assert (run_dir / "progress.json").is_file()
    assert (run_dir / "handoff.md").is_file()
