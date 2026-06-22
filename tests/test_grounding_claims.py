"""Pins claim-splitter precision (Fix C).

A real LLM review has a conclusions / recommendations / future-work
section. Those sentences are the review's own synthesis — not groundable
factual claims about the sources — so the gate must NOT treat them as
claims (else a clean, non-hallucinated review false-fails: observed at
0.77 < 0.80 on a real run).

Guard: a sentence containing ANY number is never filtered — a statistic
is a checkable assertion (a real finding OR a fabricated stat) and must
face the gate.
"""

from __future__ import annotations

from app.eval.grounding import split_claims
from app.eval.grounding.engine import _is_claimworthy

# Synthesis / recommendation / meta — must be DROPPED (not groundable).
_NONFACTUAL = [
    "Based on this synthesis, several research directions emerge:",
    "The findings collectively point toward a future where agents reason effectively.",
    "All findings are grounded in the source materials documented in SOURCES.md.",
    "**Enhanced reasoning architectures**: Combine ReAct, planning, and reflection approaches.",
    "**Advanced error recovery**: Extend checkpoint-rollback mechanisms to handle failures.",
    "Future work should explore scalable skill learning in real-world domains.",
]

# Factual assertions — must be KEPT (groundable, face the gate).
_FACTUAL = [
    "Method A improved classification accuracy by 12 percent on the benchmark.",
    # Recommendation-shaped but carries a stat → kept (could be a fabricated number).
    "**Grounding**: Even with citation requirements, 76% of claims may still lack grounding.",
    # A fabrication is factual-shaped (named entity + number) → kept so it can be flagged.
    "Framework Helios reduced agent failure rate by 37 percent on the SWE-bench-Live split.",
    "Container isolation reduced host side-effects to zero across 1000 trials.",
]


def test_nonfactual_sentences_are_not_claims() -> None:
    for s in _NONFACTUAL:
        assert _is_claimworthy(s) is False, f"should be dropped: {s!r}"


def test_factual_sentences_are_claims() -> None:
    for s in _FACTUAL:
        assert _is_claimworthy(s) is True, f"should be kept: {s!r}"


def test_split_claims_drops_recommendations_keeps_facts() -> None:
    review = "# Review\n\n## Findings\n\n"
    review += "- Method A improved accuracy by 12 percent on the benchmark.\n"
    review += "- Framework Helios reduced failure rate by 37 percent on a held-out split.\n"
    review += "\n## Recommendations\n\n"
    review += "- **Enhanced reasoning**: Combine ReAct, planning, and reflection approaches.\n"
    review += "- The findings collectively point toward a future of capable agents.\n"

    texts = [c.text for c in split_claims(review)]
    assert any("Method A improved accuracy by 12 percent" in t for t in texts)
    assert any("Framework Helios" in t for t in texts)
    assert not any("Combine ReAct" in t for t in texts)
    assert not any("point toward a future" in t for t in texts)
    assert len(texts) == 2  # only the two factual claims
