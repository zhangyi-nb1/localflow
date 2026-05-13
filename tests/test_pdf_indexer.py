"""End-to-end tests for the pdf_indexer skill (Phase 2.3 / outline §13.7
DocumentOps reference)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.executor import Executor
from app.harness.verifier import Verifier
from app.schemas import ExecutionStatus, TaskSpec
from app.skills.pdf_indexer import (
    PdfIndexerSkill,
    plan_pdf_index,
)
from app.skills.pdf_indexer.validator import (
    PdfIndexerValidationError,
    validate_pdf_index_plan,
)
from app.tools.file_scan import scan_workspace
from tests.test_content_extraction import _make_real_pdf


@pytest.fixture()
def pdf_workspace(tmp_path: Path) -> Path:
    """A workspace with two real PDFs (so previews actually populate)."""
    root = tmp_path / "pdfs"
    root.mkdir()
    _make_real_pdf(root / "agent_memory_survey.pdf", "Agent Memory: A Comprehensive Survey")
    _make_real_pdf(root / "transformers.pdf", "Attention Is All You Need")
    # Add a fake PDF so we exercise the no-preview fallback path too.
    (root / "scanned_only.pdf").write_text("not a real PDF", encoding="utf-8")
    return root


@pytest.fixture()
def pdf_task(pdf_workspace: Path) -> TaskSpec:
    return TaskSpec(
        task_id="t-pdf",
        user_goal="Index all PDFs",
        workspace_root=str(pdf_workspace),
        skill="pdf_indexer",
        constraints=["do not delete any file"],
        allowed_actions=["index"],
        forbidden_actions=["delete", "overwrite", "shell"],
    )


# --------------------------------------------------------------------- planner


def test_planner_produces_single_index_action(pdf_workspace, pdf_task) -> None:
    snap = scan_workspace(pdf_workspace, pdf_task.task_id, compute_preview=True)
    plan = plan_pdf_index(pdf_task, snap)
    assert len(plan.actions) == 1
    a = plan.actions[0]
    assert a.action_type.value == "index"
    assert a.target_path == "pdf_index.md"
    assert a.metadata["content"]
    assert "provenance" in a.metadata


def test_planner_extracts_titles_from_real_pdfs(pdf_workspace, pdf_task) -> None:
    snap = scan_workspace(pdf_workspace, pdf_task.task_id, compute_preview=True)
    plan = plan_pdf_index(pdf_task, snap)
    content = plan.actions[0].metadata["content"]
    assert "Agent Memory" in content
    assert "Attention Is All You Need" in content


def test_planner_falls_back_to_filename_when_no_preview(pdf_workspace, pdf_task) -> None:
    snap = scan_workspace(pdf_workspace, pdf_task.task_id, compute_preview=True)
    plan = plan_pdf_index(pdf_task, snap)
    content = plan.actions[0].metadata["content"]
    # The fake PDF has no extractable preview — index should still include it
    # using a humanized filename as title and a no-preview note.
    assert "scanned only" in content.lower() or "scanned_only" in content
    assert "no text preview" in content.lower()


def test_planner_records_provenance(pdf_workspace, pdf_task) -> None:
    snap = scan_workspace(pdf_workspace, pdf_task.task_id, compute_preview=True)
    plan = plan_pdf_index(pdf_task, snap)
    prov = plan.actions[0].metadata["provenance"]
    assert prov["synthesis_kind"] == "pdf_index"
    sources = prov["sources"]
    assert len(sources) == 3
    paths = {s["path"] for s in sources}
    assert paths == {
        "agent_memory_survey.pdf",
        "transformers.pdf",
        "scanned_only.pdf",
    }
    preview_flags = {s["path"]: s["has_preview"] for s in sources}
    assert preview_flags["agent_memory_survey.pdf"] is True
    assert preview_flags["scanned_only.pdf"] is False


def test_planner_empty_workspace_produces_noop_plan(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    snap = scan_workspace(empty, "t-empty", compute_preview=True)
    task = TaskSpec(task_id="t-empty", user_goal="x", workspace_root=str(empty))
    plan = plan_pdf_index(task, snap)
    assert plan.actions == []
    assert "No PDF" in plan.summary


# --------------------------------------------------------------------- validator


def test_validator_accepts_well_formed_plan(pdf_workspace, pdf_task) -> None:
    snap = scan_workspace(pdf_workspace, pdf_task.task_id, compute_preview=True)
    plan = plan_pdf_index(pdf_task, snap)
    validate_pdf_index_plan(plan)  # should not raise


def test_validator_accepts_empty_plan() -> None:
    from app.schemas import ActionPlan

    plan = ActionPlan(plan_id="p", task_id="t", summary="empty", actions=[])
    validate_pdf_index_plan(plan)


def test_validator_rejects_missing_provenance(pdf_workspace, pdf_task) -> None:
    snap = scan_workspace(pdf_workspace, pdf_task.task_id, compute_preview=True)
    plan = plan_pdf_index(pdf_task, snap)
    # Strip provenance to simulate a buggy planner.
    plan.actions[0].metadata.pop("provenance")
    with pytest.raises(PdfIndexerValidationError, match="provenance"):
        validate_pdf_index_plan(plan)


# --------------------------------------------------------------------- end-to-end through harness


def test_pdf_indexer_runs_through_executor(pdf_workspace, pdf_task, run_store) -> None:
    """Phase 2.3 / outline §10.7: a new skill executes through the harness
    without any Kernel changes."""
    snap = scan_workspace(pdf_workspace, pdf_task.task_id, compute_preview=True)
    run_store.save_task(pdf_task)
    run_store.save_workspace(snap)

    skill = PdfIndexerSkill()
    plan = skill.plan(pdf_task, snap)
    skill.validate(plan)
    run_store.save_plan(plan)

    executor = Executor(workspace_root=pdf_workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    success = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    assert success == 1

    # Verifier — independent of the skill — must pass.
    verifier = Verifier(workspace_root=pdf_workspace)
    executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
    result = verifier.verify(
        task_id=pdf_task.task_id,
        run_id=outcome.run_id,
        plan=plan,
        manifest=outcome.manifest,
        executed_action_ids=executed,
        skipped_action_ids=set(),
        failed_action_ids=set(),
        original_snapshot=snap,
    )
    assert result.passed, result.failed_checks

    # The index file was actually written and contains the synthesized content.
    index_file = pdf_workspace / "pdf_index.md"
    assert index_file.exists()
    body = index_file.read_text(encoding="utf-8")
    assert "PDF Index" in body
    assert "Agent Memory" in body

    # Reporter produces a valid markdown report citing sources.
    report = skill.report(task=pdf_task, plan=plan, outcome=outcome, verification=result)
    assert "pdf_indexer report" in report
    assert "Agent Memory" in report
