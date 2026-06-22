"""Phase 38 — stage-level progress / handoff state (the Persist layer).

Externalised task state so a multi-session task survives across runs: a
feature-list state machine (one entry per stage) + a progress file + a
handoff note. Written/read by ``app.harness.stage_progress``.

KB ch12 §跨window接力 / §状态丢失: the model does not natively remember
where it stopped; state must live in a readable, recoverable external
system, not the context window.

§10.7: a NEW schema module. The schema kernel boundary is ONLY the
``ActionType`` enum in ``app/schemas/action.py`` — adding progress models
here is not a kernel-boundary change.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class StageProgressStatus(str, Enum):
    """The KB feature-list state machine (ch12 L122-130, L196-202).

    A stage may only reach ``VERIFIED`` *with evidence* — the
    verification-constrained transition that prevents "false verified" on
    resume. ``IMPLEMENTED`` = ran successfully but no verifier evidence
    bound; ``BLOCKED`` = failed / aborted.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    BLOCKED = "blocked"


_DONE = (StageProgressStatus.IMPLEMENTED, StageProgressStatus.VERIFIED)


class StageProgress(BaseModel):
    """One stage's entry in the feature-list state machine."""

    stage_id: str
    status: StageProgressStatus = StageProgressStatus.PENDING
    verified_evidence: str | None = Field(
        default=None,
        description="Path/ref to the evidence that authorised the VERIFIED transition.",
    )
    note: str | None = None


class ProgressState(BaseModel):
    """The externalised progress file (KB ch12 L319-321 field spec)."""

    task_id: str
    graph_hash: str = Field(
        ...,
        description="Stable hash of the graph's stage ids+skills; resume refuses a mismatched graph.",
    )
    current_goal: str = ""
    stages: list[StageProgress] = Field(default_factory=list)
    failed_attempts: list[str] = Field(
        default_factory=list,
        description="Anti-rework: paths the KB stresses recording so a resume doesn't re-explore dead ends.",
    )
    next_step: str = ""
    notes: str = ""
    updated_at: str = ""

    def done_ids(self) -> set[str]:
        """Stage ids that ran successfully (need no re-run on resume)."""
        return {s.stage_id for s in self.stages if s.status in _DONE}

    def pending_ids(self) -> list[str]:
        """Stage ids still to do (PENDING / IN_PROGRESS / BLOCKED), in order."""
        return [s.stage_id for s in self.stages if s.status not in _DONE]


class HandoffNote(BaseModel):
    """The per-session exit note (KB ch12 L327, L397-409): the 5 fields a
    clean handoff must zero out so the next session never guesses."""

    done: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    verified: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    next_start: str = ""
