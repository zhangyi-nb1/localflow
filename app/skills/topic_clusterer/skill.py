"""v0.14.1 — TopicClustererSkill.

LLM-driven companion to folder_organizer. Reads each file's text
preview, asks the LLM to assign a 1-3 word topic label, then emits
mkdir + move + index.md actions grouping files by discovered topic.

Output layout::

    topics/
      <topic_a>/
        file_a.pdf
        file_b.pdf
        index.md
      <topic_b>/
        ...

The rule planner is a no-op that returns an empty plan with a clear
summary — semantic topic clustering can't be done deterministically
without content analysis.
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


class TopicClustererSkill(Skill):
    """SEMANTIC topic clustering — distinct from folder_organizer's
    extension-based categorization."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="topic_clusterer",
            description=(
                "Group files into semantic topics (topics/<topic>/...) using "
                "LLM-as-judge analysis of each file's text preview. "
                "Companion to folder_organizer (extension-based) and "
                "data_analyzer (data-content) for the Workspace Pack Builder "
                "pipeline. Rule planner is a no-op — semantic clustering "
                "requires --planner llm."
            ),
            version="0.1.0",
            capabilities=[
                "topic_assignment",
                "semantic_grouping",
                "topic_index_generation",
            ],
            required_tools=[],
            allowed_actions=["mkdir", "move", "index"],
            requires_approval=["mkdir", "move"],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        """Rule path is intentionally a no-op — explain why."""
        import uuid

        return ActionPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            task_id=task.task_id,
            summary=(
                "topic_clusterer's rule planner is a no-op: semantic topic "
                "clustering requires reading file contents, which the rule "
                "path can't do safely. Re-run with --planner llm to actually "
                "cluster files."
            ),
            actions=[],
            expected_outputs=[],
            risk_summary="zero risk — no actions emitted",
        )

    def plan_with_llm(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        **kwargs,
    ) -> ActionPlan:
        from app.skills.topic_clusterer.llm_planner import plan_topic_clustering

        return plan_topic_clustering(task, snapshot, **kwargs)

    def validate(self, plan: ActionPlan) -> None:
        """No skill-specific structural constraints beyond Pydantic +
        policy_guard — both already validate move/mkdir/index actions
        in the standard pipeline."""
        return None

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        moves = sum(1 for a in plan.actions if a.action_type.value == "move")
        topics = sorted(
            {
                a.target_path.split("/", 2)[1]
                for a in plan.actions
                if a.target_path
                and a.target_path.startswith("topics/")
                and a.action_type.value == "move"
            }
        )
        lines = [
            "# Topic clustering report",
            "",
            f"_Task `{task.task_id}` — {len(topics)} topic(s) over {moves} file(s)._",
            "",
            "## Discovered topics",
            "",
        ]
        for t in topics:
            lines.append(f"- `topics/{t}/`")
        lines.append("")
        lines.append("**Verifier:** " + ("✓ passed" if verification.passed else "✗ failed"))
        return "\n".join(lines)
