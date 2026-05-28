"""Phase 36.3/36.5 — claim-level grounding recipe verifier (the gate).

This is the flagship's load-bearing verifier. It plugs the Phase 36.3
grounding engine into the recipe-verification gate: after a literature
review pack runs, it splits the synthesised review into claims, checks
each against the per-source summaries, and gates the artifact —
``passed=False`` (with a planner hint) when too many claims have no
traceable source.

Verify-as-gate: a failed verdict flows into the existing recipe gate
(``recipe_verification.json`` + ``pack run`` exit code 3) and, when
``repair_policy.enabled``, triggers a replay of the synthesise stage
(``repair_target_map: { claim_grounding_verifier: <synthesize stage> }``).

Evidence bundle (36.5): writes ``claim_grounding.json`` (machine) +
``review_queue.md`` (human-review list of ungrounded claims) into the
workspace root. These are verification reports, not plan actions —
same tier as ``recipe_verification.json``; the kernel + rollback are
untouched.

§10.7: application-eval layer. No kernel import; no new ActionType.
"""

from __future__ import annotations

from pathlib import Path

from app.agent.judge import get_default_client_or_none
from app.eval.grounding import (
    GroundingPolicy,
    LexicalClaimJudge,
    LLMClaimJudge,
    ground_review,
    load_source_fragments,
)
from app.eval.recipe_verifiers._registry import register
from app.eval.recipe_verifiers._schema import (
    RecipeVerifierContext,
    RecipeVerifierVerdict,
)

# Review file discovery order. The flagship writes review.md; we fall
# back to the common synthesis filenames so the verifier works against
# research_pack-style outputs too.
_REVIEW_CANDIDATES = ("review.md", "README.md", "summary.md", "literature_review.md")

_EVIDENCE_JSON = "claim_grounding.json"
_REVIEW_QUEUE_MD = "review_queue.md"


def _find_review(ws: Path) -> Path | None:
    for name in _REVIEW_CANDIDATES:
        p = ws / name
        if p.is_file():
            return p
    return None


def _write_evidence(ws: Path, result) -> None:
    """Write the machine + human evidence artifacts into the workspace."""
    try:
        (ws / _EVIDENCE_JSON).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    except OSError:
        pass

    lines = ["# Review queue — claims needing human verification", ""]
    if not result.gate.ungrounded_claims:
        lines.append("_All claims traced to a source. Nothing queued._")
    else:
        lines.append(
            f"{result.gate.ungrounded_count} of {result.gate.total_claims} claims "
            f"could not be traced to a source fragment "
            f"(judge: {result.judge_kind}). Verify each manually:"
        )
        lines.append("")
        for v in result.gate.ungrounded_claims:
            lines.append(f"- **[{v.claim_id}]** (review line {v.source_line}) {v.text}")
            lines.append(f"  - _{v.evidence}_")
    try:
        (ws / _REVIEW_QUEUE_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


@register("claim_grounding_verifier")
def claim_grounding_verifier(ctx: RecipeVerifierContext) -> RecipeVerifierVerdict:
    """Phase 36 — claim-level grounding gate.

    Skips (passed=True, skipped=True) when there's no review to check or
    no source fragments to check against (infra / degraded run) — never
    fails the pack on a missing artifact, matching the verifier
    convention. Fails (passed=False + suggested_hint) when claims are
    ungrounded beyond the policy threshold.
    """
    name = "claim_grounding_verifier"
    ws = ctx.workspace_path

    review_path = _find_review(ws)
    if review_path is None:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail="no review file (review.md / README.md / summary.md) produced",
            skipped=True,
        )

    review_text = review_path.read_text(encoding="utf-8", errors="replace")
    fragments = load_source_fragments(ws)
    if not fragments:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail="no source fragments under summaries/ to ground against; "
            "skipping (likely the summarise stage did not produce summaries)",
            skipped=True,
        )

    # Production path uses the LLM judge; without a key, fall back to the
    # deterministic lexical judge (also the eval baseline).
    client = get_default_client_or_none()
    judge = LLMClaimJudge(client=client) if client is not None else LexicalClaimJudge()

    policy = GroundingPolicy()  # defaults; recipe-level override is a future slice
    result = ground_review(
        review_text=review_text,
        review_path=review_path.relative_to(ws).as_posix(),
        fragments=fragments,
        policy=policy,
        judge=judge,
    )

    _write_evidence(ws, result)

    gate = result.gate
    if gate.passed:
        return RecipeVerifierVerdict(
            name=name,
            passed=True,
            detail=(
                f"{gate.grounded_count}/{gate.total_claims} claims grounded "
                f"(ratio {gate.grounded_ratio:.2f}, judge {result.judge_kind})"
            ),
            score=gate.grounded_ratio,
        )

    return RecipeVerifierVerdict(
        name=name,
        passed=False,
        detail=(
            f"{gate.ungrounded_count}/{gate.total_claims} claims have no traceable "
            f"source (judge {result.judge_kind}); see review_queue.md"
        ),
        score=gate.grounded_ratio,
        suggested_hint=gate.suggested_hint,
    )


__all__ = ["claim_grounding_verifier"]
