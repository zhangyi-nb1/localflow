"""FolderOrganizerSkill — wraps the existing planner/validator/reporter
functions in the Skill ABC contract.

This is a thin adapter — the actual logic stays in planner.py /
validator.py / reporter.py for backward compatibility with existing
callers and tests.
"""

from __future__ import annotations

from app.schemas import (
    ActionPlan,
    SkillManifest,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.skills._base import Skill
from app.skills.folder_organizer.planner import plan_organization
from app.skills.folder_organizer.reporter import render_final_report
from app.skills.folder_organizer.validator import validate_folder_organizer_plan


class FolderOrganizerSkill(Skill):
    """Per outline §13.7: FileOps-class skill, referencing MCP Filesystem
    + Open Interpreter design ideas."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="folder_organizer",
            description=(
                "Organize a local folder by file category with dry-run, rollback, and verification."
            ),
            version="0.1.0",
            capabilities=[
                "scan_files",
                "classify_files",
                "propose_moves",
                "generate_index",
                "detect_duplicate_candidates",
            ],
            required_tools=[],
            allowed_actions=["mkdir", "move", "rename", "copy", "index"],
            requires_approval=["mkdir", "move", "rename", "copy"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        return plan_organization(task, snapshot)

    def plan_with_llm(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        **kwargs,
    ) -> ActionPlan:
        # Import inside the method to avoid the top-level circular import
        # path skills -> agent -> skills (planner.py references SYSTEM_PROMPT
        # which currently encodes folder-organizer rules).
        from app.agent import plan_with_llm as agent_plan_with_llm

        return agent_plan_with_llm(task, snapshot, **kwargs)

    def validate(self, plan: ActionPlan) -> None:
        validate_folder_organizer_plan(plan)

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        return render_final_report(task=task, plan=plan, outcome=outcome, verification=verification)
