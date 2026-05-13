from __future__ import annotations

from pathlib import Path

from app.harness.executor import Executor
from app.harness.verifier import Verifier
from app.schemas import ExecutionStatus
from app.skills.folder_organizer.planner import plan_organization


def test_verifier_passes_after_clean_execution(workspace: Path, task, snapshot, run_store) -> None:
    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    plan = plan_organization(task, snapshot)
    run_store.save_plan(plan)

    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)
    assert outcome.success

    verifier = Verifier(workspace_root=workspace)
    executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
    skipped = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SKIPPED}
    failed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.FAILED}
    result = verifier.verify(
        task_id=task.task_id,
        run_id=outcome.run_id,
        plan=plan,
        manifest=outcome.manifest,
        executed_action_ids=executed,
        skipped_action_ids=skipped,
        failed_action_ids=failed,
        original_snapshot=snapshot,
    )
    assert result.passed, result.failed_checks


def test_verifier_detects_missing_generated_file(
    workspace: Path, task, snapshot, run_store
) -> None:
    run_store.save_task(task)
    run_store.save_workspace(snapshot)
    plan = plan_organization(task, snapshot)
    run_store.save_plan(plan)
    executor = Executor(workspace_root=workspace, run_store=run_store)
    outcome = executor.execute(plan, approved=True)

    # Tamper: delete one of the generated index files behind the verifier's
    # back. A real verify run should now fail.
    if outcome.manifest.generated_files:
        victim = workspace / outcome.manifest.generated_files[0]
        victim.unlink()

        verifier = Verifier(workspace_root=workspace)
        executed = {r.action_id for r in outcome.records if r.status == ExecutionStatus.SUCCESS}
        result = verifier.verify(
            task_id=task.task_id,
            run_id=outcome.run_id,
            plan=plan,
            manifest=outcome.manifest,
            executed_action_ids=executed,
            skipped_action_ids=set(),
            failed_action_ids=set(),
            original_snapshot=snapshot,
        )
        assert not result.passed
        assert any(c.name == "generated_files_present" for c in result.failed_checks)
