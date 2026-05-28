"""Seed the literature_review_pack demo — Phase 36 flagship.

Plants a fresh ``examples/literature_review_pack/workspace/`` that
demonstrates the **claim-level grounding gate** (verify-as-gate):

  sources/        — 3 short "papers" (plain text), each with a clear
                    factual finding.
  summaries/      — one per-source summary (the grounding source pool).
  review.md       — a synthesised review that is MOSTLY grounded but
                    contains TWO deliberately planted hallucinations
                    (claims with no support in any source).

Run the grounding gate over the seeded workspace and watch it flag
exactly the two fabricated claims:

    python examples/literature_review_pack/seed.py
    python examples/literature_review_pack/seed.py --check

``--check`` runs the deterministic ``LexicalClaimJudge`` (no API key
needed) and prints the per-claim verdicts + the gate decision. This is
the resume / interview hook: "give it a review with fabricated
citations, the gate catches them and refuses to ship."

The full LLM-backed pack (recipes/literature_review_pack.yaml) does the
*generation* too; this seed isolates the *gate* so the headline
behaviour is reproducible without an LLM.

Idempotent — wipes any existing workspace/ first.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# Three sources, each with one crisp finding.
_SOURCES: dict[str, str] = {
    "paper_alpha.txt": (
        "Paper Alpha (2024). We introduce Method A, a convolutional approach.\n"
        "Method A improved classification accuracy by 12% on the ImageNet benchmark\n"
        "compared to the prior baseline.\n"
    ),
    "paper_beta.txt": (
        "Paper Beta (2025). Method B is a distillation technique.\n"
        "Method B reduced inference latency by half on the validation set,\n"
        "with no measurable loss in accuracy.\n"
    ),
    "paper_gamma.txt": (
        "Paper Gamma (2025). We study data augmentation.\n"
        "Random cropping increased robustness to distribution shift by 8 points\n"
        "on the corrupted-image test set.\n"
    ),
}

# Per-source summaries (the grounding pool). In the full pack these are
# produced by the summarise stage; here we pre-write them so the gate
# is demonstrable without an LLM.
_SUMMARIES: dict[str, str] = {
    "paper_alpha.md": (
        "# Paper Alpha\n\n"
        "Method A improved classification accuracy by 12% on the ImageNet benchmark.\n"
    ),
    "paper_beta.md": (
        "# Paper Beta\n\n"
        "Method B reduced inference latency by half on the validation set "
        "without accuracy loss.\n"
    ),
    "paper_gamma.md": (
        "# Paper Gamma\n\n"
        "Random cropping increased robustness to distribution shift by 8 points "
        "on the corrupted-image test set.\n"
    ),
}

# The synthesised review. The first three bullets are grounded; the last
# two are PLANTED HALLUCINATIONS — no source mentions "Method C", a
# "40% cost reduction", or a "transformer architecture".
_REVIEW = """# Literature Review: efficient and robust image models

This review synthesises three recent papers on efficiency and robustness.

- Method A improved classification accuracy by 12 percent on the ImageNet benchmark.
- Method B reduced inference latency by half on the validation set with no accuracy loss.
- Random cropping increased robustness to distribution shift by 8 points on corrupted images.
- Method C reduced training cost by 40 percent across all datasets.
- A transformer architecture achieved state-of-the-art results on every benchmark tested.
"""

# Which review claims are fabricated, by construction — the ground truth
# the eval (test) checks the gate against.
PLANTED_HALLUCINATIONS = (
    "Method C reduced training cost by 40 percent across all datasets.",
    "A transformer architecture achieved state-of-the-art results on every benchmark tested.",
)


def seed_workspace(workspace: Path) -> Path:
    """Create the demo workspace. Returns the workspace path."""
    if workspace.exists():
        shutil.rmtree(workspace)
    (workspace / "sources").mkdir(parents=True)
    (workspace / "summaries").mkdir(parents=True)

    for name, text in _SOURCES.items():
        (workspace / "sources" / name).write_text(text, encoding="utf-8")
    for name, text in _SUMMARIES.items():
        (workspace / "summaries" / name).write_text(text, encoding="utf-8")
    (workspace / "review.md").write_text(_REVIEW, encoding="utf-8")
    return workspace


def _check(workspace: Path) -> int:
    """Run the deterministic grounding gate + print the verdicts."""
    from app.eval.grounding import (
        GroundingPolicy,
        LexicalClaimJudge,
        ground_review,
        load_source_fragments,
    )

    review_text = (workspace / "review.md").read_text(encoding="utf-8")
    fragments = load_source_fragments(workspace)
    result = ground_review(
        review_text=review_text,
        review_path="review.md",
        fragments=fragments,
        policy=GroundingPolicy(),
        judge=LexicalClaimJudge(),
    )

    print(f"\nGrounding gate (judge: {result.judge_kind})")
    print("=" * 60)
    for v in result.verdicts:
        mark = "✓ grounded" if v.grounded else "✗ UNGROUNDED"
        src = f" → {v.source_id}" if v.source_id else ""
        print(f"  [{v.claim_id}] {mark}{src}")
        print(f"        {v.text}")
    print("=" * 60)
    g = result.gate
    print(
        f"gate: {'PASS' if g.passed else 'FAIL'}  "
        f"({g.grounded_count}/{g.total_claims} grounded, ratio {g.grounded_ratio:.2f})"
    )
    if not g.passed:
        print(f"\nflagged for human review ({g.ungrounded_count}):")
        for v in g.ungrounded_claims:
            print(f"  - {v.text}")
    # Demo's point: the gate fails on the planted hallucinations.
    return 0 if not g.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed + (optionally check) the demo.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="After seeding, run the deterministic grounding gate + print verdicts.",
    )
    args = parser.parse_args()

    workspace = Path(__file__).parent / "workspace"
    seed_workspace(workspace)
    print(f"Seeded {workspace}")
    print(f"  sources/   {len(_SOURCES)} papers")
    print(f"  summaries/ {len(_SUMMARIES)} summaries")
    print("  review.md  5 claims (2 planted hallucinations)")

    if args.check:
        raise SystemExit(_check(workspace))


if __name__ == "__main__":
    main()
