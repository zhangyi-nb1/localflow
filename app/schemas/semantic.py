"""Phase 13 — typed semantic verification verdicts.

The structural :class:`~app.schemas.verification.VerificationResult`
answers "did every action execute and every expected file end up on
disk?". A semantic verdict answers "did the *meaning* of the output
match the user's intent?" — a question the kernel cannot adjudicate
deterministically, so it's delegated to LLM-as-judge graders.

This module defines the typed contract for those verdicts. The
verdict surface is *parallel* to structural verification (different
file under ``<run_dir>/semantic_verify.json``), so kernel modules
that read ``verify_report.json`` stay oblivious to Phase 13.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SemanticVerdict(BaseModel):
    """One grader's read on the run's semantic correctness.

    The ``suggested_hint`` is fed *verbatim* into
    :func:`~app.harness.control_loop.run_revise` when the repair loop
    fires — so graders should phrase hints as direct instructions to
    the LLM planner ("regenerate the report so it cites the actual
    file names" rather than "report is generic").
    """

    grader: str = Field(..., description="Name of the grader that produced this verdict.")
    passed: bool = Field(..., description="True iff the output meets the grader's bar.")
    reason: str = Field(default="", description="Short human-readable why.")
    suggested_hint: str | None = Field(
        default=None,
        description=(
            "When passed=False, an instruction phrased for the LLM planner that "
            "would address the rejection. None when the grader can't suggest a fix "
            "(in which case the user must intervene)."
        ),
    )
    duration_ms: int | None = Field(
        default=None,
        description="Wall-clock cost of producing the verdict. None when not measured.",
    )
    token_usage: dict[str, int] = Field(
        default_factory=dict,
        description="Provider-reported token counters (input/output/cached); empty for "
        "deterministic graders that don't call an LLM.",
    )


class SemanticVerificationResult(BaseModel):
    """Aggregate of every applicable semantic verdict for one run.

    ``auto_repair_eligible`` is the gate the repair loop checks before
    firing — at least one verdict must have failed AND carried a
    ``suggested_hint`` (verdicts without a hint signal "this is bad
    but I don't know how to fix it" — surface to the user instead).
    """

    task_id: str
    run_id: str
    passed: bool = Field(
        ..., description="AND of every verdict.passed; True when no grader rejected."
    )
    verdicts: list[SemanticVerdict] = Field(default_factory=list)
    failed_verdicts: list[SemanticVerdict] = Field(default_factory=list)
    summary: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    auto_repair_eligible: bool = Field(
        default=False,
        description=(
            "True iff passed=False AND at least one failed verdict has a "
            "suggested_hint — i.e., the repair loop has something concrete to "
            "feed back to the planner."
        ),
    )
