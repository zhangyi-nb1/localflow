"""Phase 36.3 — typed contracts for claim-level grounding.

All models are application-eval layer (Pydantic ``extra='forbid'``).
They are intentionally NOT re-exported through ``localflow_kernel`` —
grounding is a verification concern, not a kernel schema.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class Claim(BaseModel):
    """One factual assertion extracted from a synthesised review."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(..., description="Stable id within one review, e.g. 'c1'.")
    text: str = Field(..., description="The claim sentence / bullet, verbatim.")
    source_line: int = Field(
        ..., description="1-based line number in the review markdown, for trace-back."
    )


class SourceFragment(BaseModel):
    """One candidate source a claim might trace to (a per-source summary
    or an original-text excerpt)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., description="Workspace-relative path of the source.")
    text: str = Field(..., description="The fragment text the claim is checked against.")


class ClaimVerdict(BaseModel):
    """Per-claim grounding result."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    grounded: bool = Field(..., description="True iff the claim traces to >=1 source fragment.")
    source_id: str | None = Field(
        default=None,
        description="The matched source when grounded (lexical judge sets this precisely; "
        "the LLM judge may leave it None and put the rationale in `evidence`).",
    )
    evidence: str = Field(
        default="",
        description="Supporting quote / matched salient terms / judge rationale.",
    )
    judge: str = Field(default="", description="Which judge produced this: 'lexical' | 'llm'.")
    source_line: int = Field(default=0, description="Line in the review, copied from the Claim.")


class GroundingPolicy(BaseModel):
    """Thresholds for the grounding gate. Tunable per recipe."""

    model_config = ConfigDict(extra="forbid")

    min_grounded_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Gate passes only if grounded/total >= this.",
    )
    max_ungrounded: int = Field(
        default=0,
        ge=0,
        description="Gate passes only if ungrounded_count <= this. 0 = zero tolerance.",
    )
    lexical_overlap_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="LexicalClaimJudge: a claim is grounded if its salient-term overlap "
        "with some fragment is >= this.",
    )


class GroundingGateResult(BaseModel):
    """The gate's verdict over all claims."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    total_claims: int
    grounded_count: int
    ungrounded_count: int
    grounded_ratio: float
    ungrounded_claims: list[ClaimVerdict] = Field(default_factory=list)
    suggested_hint: str | None = Field(
        default=None,
        description="When not passed, a planner-facing instruction for the repair loop. "
        "None when there's nothing actionable (e.g. zero claims).",
    )


class ClaimGroundingResult(BaseModel):
    """The evidence bundle written to ``<run_dir>/claim_grounding.json``."""

    model_config = ConfigDict(extra="forbid")

    review_path: str
    judge_kind: str = Field(..., description="'lexical' | 'llm'.")
    policy: GroundingPolicy
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    gate: GroundingGateResult
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
