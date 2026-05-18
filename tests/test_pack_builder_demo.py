"""Phase 14 — Workspace Pack Builder demo smoke tests.

Validate that the bundled ``examples/research_pack/seed.py`` +
``workspace_pack.yaml`` produce a runnable end-to-end pipeline. No
LLM is exercised in these tests — stages 1-4 are rule-planned, and
stage 5 (LLM) is marked ``failure_policy: skip`` so the graph still
produces stages 1-4 outputs even without an API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.schemas import TaskGraph
from app.storage.run_store import RunStore

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "research_pack"


def _import_seed_module():
    """seed.py is not a package — load it via importlib so the test
    can call ``seed(dest)`` directly without spawning a subprocess."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("research_pack_seed", EXAMPLE_DIR / "seed.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_pack_seed"] = module
    spec.loader.exec_module(module)
    return module


def test_seed_creates_expected_files(tmp_path: Path) -> None:
    """``seed(dest)`` plants the documented 10 files (or 11 with
    model_scores.xlsx) in a clean directory."""
    seed = _import_seed_module().seed
    target = tmp_path / "ws"
    seed(target)
    names = sorted(p.name for p in target.iterdir())
    # PDF + CSV + 2 PNGs + 2 notes + xlsx + unknown stub
    assert "attention_is_all_you_need.pdf" in names
    assert "memory_agents_survey.pdf" in names
    assert "rag_eval_2026.pdf" in names
    assert "experiment_results.csv" in names
    assert "model_scores.xlsx" in names
    assert "architecture.png" in names
    assert "loss_curve.png" in names
    assert "lecture_notes.txt" in names
    assert "TODO.md" in names
    assert "untitled.dat" in names


def test_workspace_pack_yaml_parses_as_taskgraph() -> None:
    """The bundled workspace_pack.yaml must validate against the
    TaskGraph Pydantic model — guards against schema drift breaking
    the canonical demo."""
    import yaml

    raw = (EXAMPLE_DIR / "workspace_pack.yaml").read_text(encoding="utf-8")
    payload = yaml.safe_load(raw)
    graph = TaskGraph.model_validate(payload)
    stage_ids = [s.stage_id for s in graph.stages]
    assert stage_ids == [
        "s1_organize",
        "s2_pdf_index",
        "s3_data_analyze",
        "s4_workspace_chart",
        "s5_synthesize",
    ]
    # Stage 5 must be skip-on-failure so CI without an LLM still completes.
    from app.schemas import StageFailurePolicy

    assert graph.stages[-1].failure_policy == StageFailurePolicy.SKIP


def test_pipeline_runs_stages_1_to_4_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the YAML's stages 1-4 against a freshly-seeded workspace
    and assert each declared output ends up on disk. Stage 5 is left
    on its default skip path — even if no LLM client is configured
    here, the test still passes because we only assert against the
    rule-planned stages.
    """
    from app.harness.taskgraph_runner import run_taskgraph

    monkeypatch.setenv("LOCALFLOW_HOME", str(tmp_path / "lf"))

    # Seed the workspace under the tmp_path so the test stays hermetic.
    workspace = tmp_path / "ws"
    _import_seed_module().seed(workspace)

    # Build a TaskGraph that points at the seeded workspace + first 4 stages.
    import yaml

    payload = yaml.safe_load((EXAMPLE_DIR / "workspace_pack.yaml").read_text(encoding="utf-8"))
    payload["workspace_root"] = str(workspace)
    payload["stages"] = payload["stages"][:4]  # drop s5 (LLM)
    graph = TaskGraph.model_validate(payload)

    store = RunStore.create()
    result = run_taskgraph(graph, run_store=store, approved=True)

    assert result.passed, [(s.stage_id, s.status, s.error) for s in result.stages]

    # Per-stage outputs landed in the workspace (not in stages/ subdir —
    # the runner writes per-stage artifacts to stages/<id>/ but skill
    # outputs go to the workspace root).
    assert (workspace / "papers" / "index.md").exists()
    assert (workspace / "pdf_index.md").exists()
    assert (workspace / "analysis_report.md").exists()
    assert (workspace / "images" / "file_counts.png").exists()


def test_rollback_restores_seeded_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After running the pipeline, ``localflow rollback`` restores the
    seeded workspace bit-for-bit. Validates the aggregated rollback
    manifest spans every stage."""
    from app.harness.rollback import Rollback
    from app.harness.taskgraph_runner import run_taskgraph
    from app.tools.hash_ops import sha256_file

    monkeypatch.setenv("LOCALFLOW_HOME", str(tmp_path / "lf"))
    workspace = tmp_path / "ws"
    _import_seed_module().seed(workspace)

    # Snapshot pre-run hashes so we can verify byte-exact restoration.
    seed_hashes = {f.name: sha256_file(f) for f in workspace.iterdir() if f.is_file()}

    import yaml

    payload = yaml.safe_load((EXAMPLE_DIR / "workspace_pack.yaml").read_text(encoding="utf-8"))
    payload["workspace_root"] = str(workspace)
    payload["stages"] = payload["stages"][:4]
    graph = TaskGraph.model_validate(payload)

    store = RunStore.create()
    run_taskgraph(graph, run_store=store, approved=True)

    rb = Rollback(workspace_root=workspace, run_store=store)
    outcome = rb.run(store.load_rollback(), force=False)
    assert outcome.success, outcome.conflicts

    restored = {f.name: sha256_file(f) for f in workspace.iterdir() if f.is_file()}
    assert restored == seed_hashes, "Rollback didn't byte-exactly restore the seed state"
