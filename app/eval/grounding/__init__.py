"""Phase 36.3 — claim-level grounding engine (application-eval layer).

The flagship "verifiable literature review" gate. A synthesised review
is split into individual claims; each claim is checked against the
source fragments it should trace to; ungrounded claims are flagged and
routed to a human-review queue; if too many are ungrounded the artifact
is gated as not-shippable (verify-as-gate).

§10.7 invariant: this is application-eval plumbing. It does NOT import
from ``app.harness.*`` / ``app.schemas.action`` / ``localflow_kernel``,
and it is NOT re-exported through ``localflow_kernel`` — so it never
enters the kernel boundary graph (``tests/test_kernel_boundary.py``
stays green). Grounding is a post-execute verification concern; it adds
no new ``ActionType`` and touches no kernel module.

Public surface:

    from app.eval.grounding import (
        Claim, SourceFragment, ClaimVerdict, GroundingPolicy,
        GroundingGateResult, ClaimGroundingResult,
        split_claims, load_source_fragments, ground_review,
        LexicalClaimJudge, LLMClaimJudge, ClaimJudge,
    )
"""

from __future__ import annotations

from app.eval.grounding.engine import (
    ClaimJudge,
    LexicalClaimJudge,
    LLMClaimJudge,
    evaluate_grounding,
    ground_review,
    load_source_fragments,
    split_claims,
)
from app.eval.grounding.schema import (
    Claim,
    ClaimGroundingResult,
    ClaimVerdict,
    GroundingGateResult,
    GroundingPolicy,
    SourceFragment,
)

__all__ = [
    "Claim",
    "ClaimGroundingResult",
    "ClaimJudge",
    "ClaimVerdict",
    "GroundingGateResult",
    "GroundingPolicy",
    "LLMClaimJudge",
    "LexicalClaimJudge",
    "SourceFragment",
    "evaluate_grounding",
    "ground_review",
    "load_source_fragments",
    "split_claims",
]
