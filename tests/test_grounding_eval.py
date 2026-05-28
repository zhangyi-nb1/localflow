"""Phase 36.7 — grounding eval: measurable hallucination recall +
grounded false-positive rate against by-construction ground truth.

Deterministic (LexicalClaimJudge, no API key). This is the reproducible
eval number Phase 37 surfaces publicly: on the flagship demo's planted-
hallucination review, the gate must catch every fabricated claim
(recall = 1.0) without wrongly flagging grounded claims (FP rate = 0.0).

Ground truth = the ``PLANTED_HALLUCINATIONS`` tuple in the demo seed,
loaded directly so the test can't drift from the demo.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.eval.grounding import (
    GroundingPolicy,
    LexicalClaimJudge,
    ground_review,
    load_source_fragments,
)

_SEED_PATH = (
    Path(__file__).resolve().parent.parent / "examples" / "literature_review_pack" / "seed.py"
)


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("_lit_review_seed", _SEED_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _normalise(text: str) -> str:
    # Compare claims to planted-hallucination ground truth tolerant of
    # "12 percent" vs "12%" style differences — match on a salient prefix.
    return " ".join(text.lower().replace("percent", "").split())


def test_grounding_eval_recall_and_false_positive(tmp_path: Path) -> None:
    seed = _load_seed_module()
    ws = seed.seed_workspace(tmp_path / "workspace")

    planted = {_normalise(h) for h in seed.PLANTED_HALLUCINATIONS}

    review_text = (ws / "review.md").read_text(encoding="utf-8")
    fragments = load_source_fragments(ws)
    result = ground_review(
        review_text=review_text,
        review_path="review.md",
        fragments=fragments,
        policy=GroundingPolicy(),
        judge=LexicalClaimJudge(),
    )

    # Partition verdicts against the ground truth.
    planted_flagged = 0
    grounded_wrongly_flagged = 0
    total_grounded_truth = 0

    for v in result.verdicts:
        is_planted = _normalise(v.text) in planted
        if is_planted:
            if not v.grounded:
                planted_flagged += 1
        else:
            total_grounded_truth += 1
            if not v.grounded:
                grounded_wrongly_flagged += 1

    hallucination_recall = planted_flagged / len(planted)
    fp_rate = grounded_wrongly_flagged / total_grounded_truth if total_grounded_truth else 0.0

    # The flagship's headline numbers.
    assert hallucination_recall == 1.0, (
        f"missed a planted hallucination: recall={hallucination_recall}"
    )
    assert fp_rate == 0.0, f"false-positive on a grounded claim: fp_rate={fp_rate}"

    # And the gate refuses to ship (verify-as-gate).
    assert result.gate.passed is False
    assert result.gate.ungrounded_count == len(planted)


def test_all_planted_are_actually_in_review(tmp_path: Path) -> None:
    """Guard: the ground-truth tuple must match real review lines, so the
    eval can't silently pass by measuring nothing."""
    seed = _load_seed_module()
    ws = seed.seed_workspace(tmp_path / "workspace")
    review_text = (ws / "review.md").read_text(encoding="utf-8")
    norm_review = _normalise(review_text)
    for h in seed.PLANTED_HALLUCINATIONS:
        assert _normalise(h) in norm_review
