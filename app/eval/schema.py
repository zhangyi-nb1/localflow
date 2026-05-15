"""Eval task + grader Pydantic models.

A task YAML deserialises into :class:`EvalTask`. The runner executes
it, builds a :class:`GraderContext` from the run artifacts + trace,
and passes it to every grader named in ``task.graders``. Each grader
returns a :class:`GraderVerdict`; the runner aggregates them into an
:class:`EvalResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas import (
    ActionPlan,
    ExecutionRecord,
    RollbackManifest,
    TaskSpec,
    TraceEvent,
    VerificationResult,
    WorkspaceSnapshot,
)


class WorkspaceFile(BaseModel):
    """One seed file. Either text or base64 bytes, not both."""

    path: str
    text: str | None = None
    bytes_b64: str | None = None

    @model_validator(mode="after")
    def _exactly_one_content(self) -> "WorkspaceFile":
        if (self.text is None) == (self.bytes_b64 is None):
            raise ValueError(
                f"WorkspaceFile {self.path!r}: provide exactly one of 'text' or 'bytes_b64'"
            )
        return self


class EvalTask(BaseModel):
    """A single eval task. Loaded from YAML or constructed in tests."""

    task_id: str
    title: str
    goal: str
    skill: str = "folder_organizer"
    planner: Literal["rule", "llm"] = "rule"
    workspace_seed: list[WorkspaceFile] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=lambda: ["delete", "overwrite", "shell"])
    graders: list[str] = Field(default_factory=list)
    must_pass: list[str] = Field(
        default_factory=list,
        description=(
            "Subset of ``graders`` whose failure marks the whole task as failed. "
            "Empty (default) means every grader must pass."
        ),
    )
    notes: str | None = None


class GraderVerdict(BaseModel):
    """One grader's decision for one task. ``score`` is optional —
    Phase 9 ships pass/fail graders only; Phase 12 may add weighted ones."""

    name: str
    passed: bool
    detail: str = ""
    score: float | None = None


@dataclass
class GraderContext:
    """Everything a grader can read about a finished eval run.

    Bundled into one dataclass so graders are pure functions of (task,
    artifacts, trace). They never reach into the filesystem outside
    ``workspace_path`` and never mutate any field — the runner is the
    single owner of state.
    """

    task: EvalTask
    task_spec: TaskSpec
    plan: ActionPlan
    snapshot_before: WorkspaceSnapshot
    snapshot_after: WorkspaceSnapshot | None
    execution_records: list[ExecutionRecord]
    manifest: RollbackManifest
    verification: VerificationResult | None
    trace_events: list[TraceEvent]
    workspace_path: Path
    seed_hashes: dict[str, str]
    """sha256(seed_file_relpath) → hex digest, captured pre-execute."""


class EvalResult(BaseModel):
    """The final per-task verdict the eval report consumes."""

    task_id: str
    title: str
    passed: bool
    grader_verdicts: list[GraderVerdict]
    run_id: str | None = None
    duration_ms: int = 0
    failure_summary: dict[str, int] = Field(
        default_factory=dict,
        description="FailureType.value → count, from the run's trace events.",
    )
    error: str | None = None
    """If the run itself crashed (not just a grader failure), this carries
    the message. Graders are skipped when this is non-None."""
