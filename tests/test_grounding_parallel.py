"""Task 2 — ground_review judges claims concurrently.

The parallel path must be (a) identical to sequential (order preserved,
each claim judged independently) and (b) call the judge exactly once per
claim. The LLM judge is I/O-bound, so this turns a minutes-long gate into
seconds without changing the verdict.
"""

from __future__ import annotations

import threading

from app.eval.grounding import GroundingPolicy, ground_review
from app.eval.grounding.schema import ClaimVerdict

# 10 list items → 10 claims (each has a digit so the Fix-C non-factual
# filter keeps them; odd = grounded ("ok"), even = ungrounded).
_REVIEW = "# Review\n\n" + "\n".join(
    (
        f"- finding number {i} is ok and clearly grounded in a source"
        if i % 2
        else f"- finding number {i} has no support anywhere in the corpus"
    )
    for i in range(10)
)


class _CountingJudge:
    kind = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def judge_claim(self, claim, fragments):
        with self._lock:
            self.calls += 1
        return ClaimVerdict(
            claim_id=claim.claim_id,
            text=claim.text,
            grounded="ok" in claim.text.lower(),
            source_id=None,
            evidence="",
            judge=self.kind,
            source_line=claim.source_line,
        )


def _run(workers: int):
    judge = _CountingJudge()
    res = ground_review(
        review_text=_REVIEW,
        review_path="review.md",
        fragments=[],
        policy=GroundingPolicy(),
        judge=judge,
        max_workers=workers,
    )
    return judge.calls, [(v.claim_id, v.grounded) for v in res.verdicts]


def test_parallel_matches_sequential_and_one_call_per_claim() -> None:
    calls_seq, seq = _run(1)
    calls_par, par = _run(8)

    n = len(seq)
    assert n >= 8, "need enough claims to exercise the pool"
    assert par == seq, "parallel verdicts/order must match sequential"
    assert calls_seq == n, "sequential: one judge call per claim"
    assert calls_par == n, "parallel: one judge call per claim (no dup/drop)"
