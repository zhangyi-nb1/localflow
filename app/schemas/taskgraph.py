"""Phase 10 — TaskGraph / multi-stage execution schema.

A ``TaskGraph`` is the static counterpart to a ``TaskSpec`` +
``ActionPlan``: instead of one skill producing one plan, a graph
declares a sequence of stages, each driven by a (possibly different)
existing skill. The :class:`app.harness.taskgraph_runner` walks the
stages in order, sharing a workspace and a global rollback manifest.

This sits NEXT to the existing v0.9 ``agent`` meta-skill, not above
it. The agent meta-skill produces ONE LLM-generated ActionPlan;
TaskGraph composes specialist skill invocations statically. Users
pick the right tool — the harness supports both.

Phase 10 is intentionally sequential-only (no parallel, no
conditional branches, no per-stage retry beyond the default 1). The
schema reserves ``max_retries`` and ``failure_policy`` fields so
Phase 12's Repair Loop can wire retry semantics without a schema
bump.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class StageFailurePolicy(str, Enum):
    """How the runner reacts when a stage's verifier fails or its
    plan / dry-run / execute raises.

    Phase 10 introduced ABORT / CONTINUE / SKIP. Phase 13 adds REPAIR
    — wraps the existing dispatch with one automatic retry-with-
    repair cycle before falling through to ABORT (or whatever the
    underlying skill ends up classified as). The set is pinned by
    test_taskgraph_schema.
    """

    ABORT = "abort"
    """Default — stop the graph; mark remaining stages as ABORTED."""

    CONTINUE = "continue"
    """Log the failure + continue to the next stage. Useful for
    diagnostic pipelines (e.g. "always run the audit stage even when
    the optimization stage fails")."""

    SKIP = "skip"
    """This stage's failure is marked SKIPPED on the result; the
    runner proceeds to the next stage. Same execution semantics as
    CONTINUE but the status reads SKIPPED instead of FAILED — useful
    when the stage is genuinely optional."""

    REPAIR = "repair"
    """Phase 13 — when the stage fails (structural OR semantic
    verifier rejection), invoke the auto-repair loop in-place:
    rollback, revise with a grader-derived hint, re-execute, re-verify.
    Bounded by :attr:`StageSpec.max_retries` (default 1). If the
    repair loop exhausts retries, the stage falls through to ABORT
    semantics."""


class StageStatus(str, Enum):
    """Per-stage outcome recorded in :class:`StageResult`."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ABORTED = "aborted"
    """Did not run because an earlier stage failed with ABORT policy."""


class StageSpec(BaseModel):
    """One stage of a TaskGraph.

    A stage is a (skill, planner, expected_outputs, failure_policy)
    tuple. The runner builds a sub-TaskSpec from it + the parent
    graph context and feeds it through the standard
    ``control_loop.run_*`` pipeline. Nothing in the harness kernel
    learns about stages directly — the trace context manager handles
    ``stage_id`` injection at the edge.
    """

    stage_id: str = Field(..., description="Unique within the parent graph (e.g. 's1_organize').")
    title: str
    skill: str = Field(..., description="Registered skill name (folder_organizer, agent, ...).")
    planner: Literal["rule", "llm"] = "rule"
    expected_outputs: list[str] = Field(
        default_factory=list,
        description=(
            "Workspace-relative paths the runner records on the stage's "
            "ActionPlan.expected_outputs. Graders + the verifier consume them."
        ),
    )
    allowed_actions: list[str] | None = Field(
        default=None,
        description=(
            "Restrict this stage to a subset of the skill's allowed_actions. "
            "None means inherit the skill manifest's full list."
        ),
    )
    forbidden_actions: list[str] = Field(
        default_factory=list,
        description="Additive to graph.forbidden_actions for THIS stage only.",
    )
    failure_policy: StageFailurePolicy = StageFailurePolicy.ABORT
    max_retries: int = Field(
        default=1,
        ge=1,
        description=(
            "Phase 10 always uses 1. Phase 12 (Repair Loop) will wire "
            "retry-with-repair semantics here without a schema bump."
        ),
    )
    notes: str | None = None


class TaskGraph(BaseModel):
    """Top-level multi-stage spec.

    Loaded from YAML (CLI `localflow taskgraph run --graph foo.yaml`)
    or constructed inline (eval runner from ``EvalTask.stages``). The
    runner consumes one graph and produces one :class:`TaskGraphResult`.
    """

    task_id: str | None = Field(
        default=None,
        description=(
            "Set by the runner via RunStore.create() when absent — most "
            "graph YAML files omit this and let the runner mint a fresh id."
        ),
    )
    user_goal: str = Field(..., description="One-line human description (recorded in artifacts).")
    workspace_root: str = Field(
        ..., description="Absolute or eval-runner-relative path to the workspace."
    )
    stages: list[StageSpec] = Field(..., min_length=1)
    forbidden_actions: list[str] = Field(
        default_factory=lambda: ["delete", "overwrite", "shell"],
        description="Graph-level forbidden actions; each stage adds its own.",
    )
    forbidden_paths: list[str] = Field(
        default_factory=list,
        description="Graph-level forbidden paths; applies to every stage.",
    )
    preferences: dict[str, Any] = Field(
        default_factory=dict,
        description=("Carried into every stage's TaskSpec.preferences (e.g. naming_style)."),
    )

    @model_validator(mode="after")
    def _unique_stage_ids(self) -> "TaskGraph":
        seen: set[str] = set()
        for s in self.stages:
            if s.stage_id in seen:
                raise ValueError(f"duplicate stage_id in graph: {s.stage_id!r}")
            seen.add(s.stage_id)
        return self


class StageResult(BaseModel):
    """Outcome of a single stage. The runner emits one of these per
    stage even when the stage was aborted (in which case status =
    ABORTED and most fields are empty)."""

    stage_id: str
    status: StageStatus
    plan_id: str | None = None
    action_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    duration_ms: int = 0
    verifier_passed: bool | None = None
    failed_checks: list[str] = Field(default_factory=list)
    error: str | None = None
    """Set when the stage raised (plan() crashed, executor exception, …)."""


class TaskGraphResult(BaseModel):
    """The aggregate verdict the runner returns.

    A graph passes iff every stage's required outcome holds. The
    default rule: PASSED stages are pass, FAILED is fail, SKIPPED is
    pass (skipped is intentional), ABORTED is fail (the graph
    didn't complete).
    """

    task_id: str
    passed: bool
    stages: list[StageResult]
    aggregated_manifest_path: str = Field(
        ...,
        description=(
            "Workspace-relative path to the graph-level rollback_manifest.json "
            "(at <run_dir>/rollback_manifest.json — top-level, not per-stage)."
        ),
    )
    duration_ms: int

    @classmethod
    def from_stages(
        cls,
        *,
        task_id: str,
        stages: list[StageResult],
        aggregated_manifest_path: str,
        duration_ms: int,
    ) -> "TaskGraphResult":
        """Compute ``passed`` from the stage list per the rule above."""
        passed = all(s.status in (StageStatus.PASSED, StageStatus.SKIPPED) for s in stages)
        return cls(
            task_id=task_id,
            passed=passed,
            stages=stages,
            aggregated_manifest_path=aggregated_manifest_path,
            duration_ms=duration_ms,
        )
