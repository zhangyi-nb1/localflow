"""Phase 4.3 — Unified Skill Contract Test Template.

Parametrizes :func:`app.skills.run_skill_contract` over every built-in
skill so each one is driven through the canonical 8-stage lifecycle:

  1. manifest_valid
  2. plan_empty_workspace
  3. plan_happy_path
  4. validate_accepts_own_plan
  5. validate_rejects_garbage
  6. execute_and_verify
  7. rollback_restores
  8. report_non_empty

This file also gives ``folder_organizer`` its first dedicated E2E
coverage (the existing folder_organizer tests only exercise
planner / validator in isolation — execute / verify / rollback were
covered indirectly via the harness tests).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.skills import (
    ContractReport,
    DataAnalyzerSkill,
    DataReporterSkill,
    FolderOrganizerSkill,
    PdfIndexerSkill,
    StageResult,
    run_skill_contract,
)
from app.storage.run_store import RunStore
from tests.test_content_extraction import _make_real_pdf

# --------------------------------------------------------------- seeders


def seed_folder_organizer(root: Path) -> None:
    """Mixed-type workspace: triggers classify + propose_moves."""
    (root / "report.pdf").write_text("not really a pdf but classify uses extension", encoding="utf-8")
    (root / "data.csv").write_text("col1,col2\n1,2\n3,4\n", encoding="utf-8")
    (root / "notes.txt").write_text("notes\n", encoding="utf-8")
    (root / "image.jpg").write_bytes(b"\xff\xd8fakejpg")
    (root / "song.mp3").write_bytes(b"\x49\x44\x33fakemp3")


def seed_pdf_indexer(root: Path) -> None:
    """Real PDFs (so pdf_ops actually extracts previews) + one fake to
    exercise the no-preview fallback path."""
    _make_real_pdf(root / "memory_survey.pdf", "Agent Memory: A Comprehensive Survey")
    _make_real_pdf(root / "transformers.pdf", "Attention Is All You Need")
    (root / "scanned_only.pdf").write_text("not a real PDF", encoding="utf-8")


def seed_tabular_workspace(root: Path) -> None:
    """Shared by data_reporter and data_analyzer: at least one CSV with
    one categorical column + one numeric column so both skills' rule
    heuristics produce a non-empty plan."""
    (root / "sales.csv").write_text(
        "region,amount,quantity\n"
        "north,100,5\n"
        "south,200,8\n"
        "north,150,4\n"
        "east,180,6\n"
        "south,220,9\n"
        "east,140,3\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------- built-in contract suite


BUILTIN_CONTRACT_CASES = [
    pytest.param(FolderOrganizerSkill, seed_folder_organizer, id="folder_organizer"),
    pytest.param(PdfIndexerSkill, seed_pdf_indexer, id="pdf_indexer"),
    pytest.param(DataReporterSkill, seed_tabular_workspace, id="data_reporter"),
    pytest.param(DataAnalyzerSkill, seed_tabular_workspace, id="data_analyzer"),
]


@pytest.mark.parametrize("skill_cls,seeder", BUILTIN_CONTRACT_CASES)
def test_builtin_skill_passes_contract(skill_cls, seeder, tmp_path: Path) -> None:
    """Every built-in Skill must pass the canonical 8-stage lifecycle."""
    ws = tmp_path / "ws"
    rs = RunStore.create(home=tmp_path / ".localflow")

    report = run_skill_contract(
        skill_cls(),
        workspace_seeder=seeder,
        workspace_root=ws,
        run_store=rs,
    )

    if not report.all_passed:
        failed = "\n".join(f"  - {s}" for s in report.failed_stages())
        pytest.fail(
            f"Contract failed for {skill_cls.__name__}:\n{failed}\n\nFull report:\n{report}"
        )


# --------------------------------------------------------------- runner unit tests
#
# These exercise the contract runner itself — the 4 built-ins above
# verify it from the OUTSIDE; these verify the internals.


class _AlwaysGreenSkill:
    """Minimal in-memory Skill stand-in that produces a trivially-valid
    plan (one index action). Used to assert the contract runner correctly
    reports an all-green outcome."""

    @property
    def manifest(self):
        from app.schemas import SkillManifest
        return SkillManifest(
            name="contract_test_green",
            description="contract runner test fixture",
            version="0.0.0",
            required_tools=[],
            allowed_actions=["index"],
            requires_approval=["index"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task, snapshot):
        from app.schemas import ActionPlan
        from app.schemas.action import Action, ActionType, RiskLevel
        # Always emit ONE index action that writes a tiny markdown file.
        if not snapshot.files:
            return ActionPlan(plan_id="p-empty", task_id=task.task_id, summary="empty")
        return ActionPlan(
            plan_id="p-001",
            task_id=task.task_id,
            summary="one action",
            actions=[
                Action(
                    action_id="a-001",
                    action_type=ActionType.INDEX,
                    target_path="contract_test_green.md",
                    reason="contract runner happy path",
                    risk_level=RiskLevel.LOW,
                    reversible=True,
                    requires_approval=True,
                    metadata={"content": "hello\n", "overwrite_existing": True},
                ),
            ],
        )

    def plan_with_llm(self, task, snapshot, **kwargs):
        raise NotImplementedError

    def supports_llm(self) -> bool:
        return False

    def validate(self, plan) -> None:
        for a in plan.actions:
            if a.action_type.value != "index":
                raise ValueError("only index actions allowed")

    def report(self, *, task, plan, outcome, verification) -> str:
        return f"# contract_test_green report — {len(plan.actions)} action(s)"


class _MissingPlanSkill(_AlwaysGreenSkill):
    """A skill whose plan() returns None — should fail plan_happy_path."""

    @property
    def manifest(self):
        from app.schemas import SkillManifest
        return SkillManifest(
            name="contract_test_broken",
            required_tools=[],
            allowed_actions=["index"],
        )

    def plan(self, task, snapshot):
        return None  # deliberately broken


class _NoRollbackSkill(_AlwaysGreenSkill):
    """A skill that declares supports_rollback=False so the rollback
    stage should record 'skipped' rather than fail."""

    @property
    def manifest(self):
        from app.schemas import SkillManifest
        return SkillManifest(
            name="contract_test_no_rollback",
            required_tools=[],
            allowed_actions=["index"],
            supports_rollback=False,
        )


def _seed_one_file(root: Path) -> None:
    (root / "anything.txt").write_text("x", encoding="utf-8")


def test_contract_all_green(tmp_path: Path) -> None:
    rs = RunStore.create(home=tmp_path / ".localflow")
    report = run_skill_contract(
        _AlwaysGreenSkill(),
        workspace_seeder=_seed_one_file,
        workspace_root=tmp_path / "ws",
        run_store=rs,
    )
    assert report.all_passed, "\n".join(str(s) for s in report.stages)
    assert all(isinstance(s, StageResult) for s in report.stages)
    assert isinstance(report, ContractReport)


def test_contract_collects_failures_without_short_circuiting(tmp_path: Path) -> None:
    """A skill that returns ``None`` from plan() should fail multiple
    stages, but the runner must record ALL of them — not bail on the
    first one. This is the value-add over a single integration test."""
    rs = RunStore.create(home=tmp_path / ".localflow")
    report = run_skill_contract(
        _MissingPlanSkill(),
        workspace_seeder=_seed_one_file,
        workspace_root=tmp_path / "ws",
        run_store=rs,
    )
    assert not report.all_passed
    failed_names = {s.name for s in report.failed_stages()}
    # plan_happy_path must fail (None is not ActionPlan); downstream
    # stages must be reported (skipped or failed) — never omitted.
    assert "plan_happy_path" in failed_names
    expected_stages = {
        "manifest_valid",
        "plan_empty_workspace",
        "plan_happy_path",
        "validate_accepts_own_plan",
        "validate_rejects_garbage",
        "execute_and_verify",
        "rollback_restores",
        "report_non_empty",
    }
    actual_stage_names = {s.name for s in report.stages}
    assert expected_stages == actual_stage_names


def test_contract_skips_rollback_when_manifest_disables_it(tmp_path: Path) -> None:
    rs = RunStore.create(home=tmp_path / ".localflow")
    report = run_skill_contract(
        _NoRollbackSkill(),
        workspace_seeder=_seed_one_file,
        workspace_root=tmp_path / "ws",
        run_store=rs,
    )
    # Find the rollback stage entry — must have passed with a 'skipped' detail.
    rollback_stage = next(s for s in report.stages if s.name == "rollback_restores")
    assert rollback_stage.passed
    assert "skipped" in rollback_stage.detail.lower()
    assert "supports_rollback" in rollback_stage.detail
