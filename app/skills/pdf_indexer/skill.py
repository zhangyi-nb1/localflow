from __future__ import annotations

from app.schemas import (
    ActionPlan,
    SkillManifest,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.skills._base import Skill
from app.skills.pdf_indexer.planner import plan_pdf_index
from app.skills.pdf_indexer.reporter import render_pdf_index_report
from app.skills.pdf_indexer.validator import validate_pdf_index_plan


class PdfIndexerSkill(Skill):
    """Per outline §13.7: DocumentOps-class skill, designed with Open
    Deep Research's research planner / source tracking / synthesis
    pattern in mind.

    Phase 2.3 ships only the rule-based planner — LLM-based summarization
    is a natural follow-up (each per-PDF summary becomes an LLM call) but
    is deferred to a future iteration.
    """

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="pdf_indexer",
            description=(
                "Scan PDFs in a workspace and emit a single Markdown index "
                "with per-file titles and summaries derived from PDF text "
                "previews. Source provenance recorded in action metadata."
            ),
            version="0.1.0",
            capabilities=[
                "scan_pdfs",
                "extract_title_from_preview",
                "synthesize_index",
                "track_provenance",
            ],
            # Phase 2.1 populates FileMeta.text_preview at scan time via
            # pdf_ops.extract_text_preview, so this planner reads previews
            # off the snapshot rather than calling the tool directly.
            required_tools=[],
            allowed_actions=["index"],
            requires_approval=["index"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        return plan_pdf_index(task, snapshot)

    # plan_with_llm intentionally not overridden — Skill.plan_with_llm
    # defaults to raising NotImplementedError with a clear message.

    def validate(self, plan: ActionPlan) -> None:
        validate_pdf_index_plan(plan)

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        return render_pdf_index_report(
            task=task, plan=plan, outcome=outcome, verification=verification
        )
