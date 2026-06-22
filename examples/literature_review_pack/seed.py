"""Seed the literature_review_pack demo — Phase 36 flagship, Option 1 scale-up.

Plants a fresh ``examples/literature_review_pack/workspace/`` that
demonstrates the **claim-level grounding gate** (verify-as-gate) on a
deliberately *complex, multi-source, content-heavy* review:

  sources/    — 12 short "papers" / notes (plain text), each with one
                crisp, checkable finding (a specific entity + number).
  summaries/  — one per-source summary (the grounding source pool).
  review.md   — a synthesised review whose claims are MOSTLY grounded
                but contain deliberately planted hallucinations.

The review mixes (ground truth is fixed by construction, the way eval
suites inject known errors):

  * 12 GROUNDED claims  — each traces to a source summary.
  * 6  FABRICATED claims — four classic hallucination classes, each
        carrying a *novel salient term* (entity / number / citation that
        appears in no source). The deterministic ``LexicalClaimJudge``
        catches every one of these → reproducible 100% recall, no key.
  * 1  HARD CASE         — a same-vocabulary numeric contradiction
        (a source's "92%" rewritten as "29%"). Honesty (rule F): the
        lexical baseline CANNOT catch this — salient-term overlap stays
        high — so it is reported as a *known lexical blind spot*, caught
        only by the LLM judge (real-LLM mode). We pin the limitation
        rather than pretend the baseline is perfect.

Run it:

    python examples/literature_review_pack/seed.py
    python examples/literature_review_pack/seed.py --check

``--check`` runs the deterministic gate (no API key) and prints the
quantified scorecard: claims total / grounded / flagged, hallucination
recall, grounded false-positive rate, the hard-case blind spot, and the
ship-or-rollback decision. This is the resume / interview hook:
"give it a complex review with fabricated claims, the gate catches them,
quantifies recall + false-positive rate, and refuses to ship."

The full LLM-backed pack (recipes/literature_review_pack.yaml) does the
*generation* too; this seed isolates the *gate* so the headline
behaviour is reproducible without an LLM.

Idempotent — wipes any existing workspace/ first.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# --------------------------------------------------------------------- sources
# Each "paper" carries one crisp finding (entity + number) so a claim can
# be checked against it. Theme: evaluating and hardening LLM agents — a
# coherent corpus so the synthesised review does real cross-source work.
_PAPERS: tuple[dict[str, str], ...] = (
    {
        "file": "swe_bench.txt",
        "title": "SWE-bench-Verified baseline",
        "source": (
            "SWE-bench-Verified (2024). A human-validated subset of SWE-bench.\n"
            "On SWE-bench-Verified, a baseline agent resolved 41 percent of issues,\n"
            "establishing the reference number for later comparisons.\n"
        ),
        "claim": "On SWE-bench-Verified, a baseline agent resolved 41 percent of issues.",
    },
    {
        "file": "react.txt",
        "title": "ReAct: reasoning + acting",
        "source": (
            "ReAct (2023). Interleaving chain-of-thought reasoning with tool actions.\n"
            "ReAct interleaving of reasoning and acting cut hallucinated actions by 30 percent\n"
            "on the ALFWorld benchmark.\n"
        ),
        "claim": (
            "ReAct interleaving of reasoning and acting cut hallucinated actions "
            "by 30 percent on ALFWorld."
        ),
    },
    {
        "file": "reflexion.txt",
        "title": "Reflexion: verbal reinforcement",
        "source": (
            "Reflexion (2023). Agents reflect on failures in natural language.\n"
            "Reflexion verbal reinforcement improved success rate by 11 points on HotpotQA.\n"
        ),
        "claim": "Reflexion verbal reinforcement improved success rate by 11 points on HotpotQA.",
    },
    {
        "file": "toolformer.txt",
        "title": "Toolformer: self-supervised tool use",
        "source": (
            "Toolformer (2023). The model teaches itself when to call APIs.\n"
            "Toolformer self-supervised API calls improved zero-shot accuracy by 9 percent\n"
            "on math tasks.\n"
        ),
        "claim": (
            "Toolformer self-supervised API calls improved zero-shot accuracy "
            "by 9 percent on math tasks."
        ),
    },
    {
        "file": "voyager.txt",
        "title": "Voyager: lifelong skill library",
        "source": (
            "Voyager (2023). An open-ended Minecraft agent with a growing skill library.\n"
            "Voyager skill-library reuse increased novel item discovery 3.3 times in Minecraft.\n"
        ),
        "claim": "Voyager skill-library reuse increased novel item discovery 3.3 times in Minecraft.",
    },
    {
        "file": "sandbox.txt",
        "title": "Container isolation of actions",
        "source": (
            "Sandbox isolation study (2024). Agent actions executed inside a container.\n"
            "Container isolation of agent actions reduced host side-effects to zero\n"
            "across 1000 trials.\n"
        ),
        "claim": (
            "Container isolation of agent actions reduced host side-effects to zero "
            "across 1000 trials."
        ),
    },
    {
        "file": "rollback.txt",
        "title": "Checkpoint-rollback recovery",
        "source": (
            "Checkpoint-rollback study (2024). Roll back on failed verification.\n"
            "Rollback on failed verification recovered 92 percent of corrupted runs.\n"
        ),
        "claim": "Rollback on failed verification recovered 92 percent of corrupted runs.",
    },
    {
        "file": "humaneval.txt",
        "title": "HumanEval code generation",
        "source": (
            "HumanEval (2021). A hand-written code-synthesis benchmark.\n"
            "On HumanEval, the evaluated code model reached 67 percent pass at 1.\n"
        ),
        "claim": "On HumanEval, the evaluated code model reached 67 percent pass at 1.",
    },
    {
        "file": "planning.txt",
        "title": "Plan-then-execute decomposition",
        "source": (
            "Task-decomposition study (2024). Plan first, then execute steps.\n"
            "Plan-then-execute decomposition reduced step errors by 18 percent on WebArena.\n"
        ),
        "claim": "Plan-then-execute decomposition reduced step errors by 18 percent on WebArena.",
    },
    {
        "file": "grounding.txt",
        "title": "Citation-grounded reports",
        "source": (
            "Grounding study (2025). Require every report claim to cite a source.\n"
            "Requiring source citations cut unsupported claims by 24 percent in generated reports.\n"
        ),
        "claim": (
            "Requiring source citations cut unsupported claims by 24 percent in generated reports."
        ),
    },
    {
        "file": "note_eval_metrics.md",
        "title": "Note: how to score a gate",
        "source": (
            "# Note — evaluation metrics\n\n"
            "Agent evaluation should report recall and false-positive rate rather than\n"
            "model self-assessment.\n"
        ),
        "claim": (
            "Agent evaluation should report recall and false-positive rate rather than "
            "model self-assessment."
        ),
    },
    {
        "file": "note_failure_modes.md",
        "title": "Note: common failure modes",
        "source": (
            "# Note — failure modes\n\n"
            "Six common agent failure modes include goal drift, false completion, "
            "and context rot.\n"
        ),
        "claim": (
            "Six common agent failure modes include goal drift, false completion, and context rot."
        ),
    },
)

# Ground truth: the grounded claims are exactly the per-paper claims.
GROUNDED_CLAIMS: tuple[str, ...] = tuple(p["claim"] for p in _PAPERS)

# Fabricated claims — four hallucination classes. Each carries a NOVEL
# salient term (entity / number / citation) absent from every source, so
# the deterministic lexical judge flags it. (text, class).
FABRICATIONS: tuple[tuple[str, str], ...] = (
    (
        "Framework Helios reduced agent failure rate by 37 percent on the SWE-bench-Live split.",
        "no-source statistic",
    ),
    (
        "Okafor and Reyes (2023) showed that memory compression doubled long-horizon throughput.",
        "fabricated citation",
    ),
    (
        "The Atlas planner reached a 95 percent task-completion rate on the GAIA benchmark.",
        "no-source statistic",
    ),
    (
        "Reflexion guarantees correct completion on every conceivable benchmark.",
        "over-generalisation",
    ),
    (
        "Quantized 4-bit agents matched full-precision accuracy while using 80 percent less memory.",
        "no-source statistic",
    ),
    (
        "Following Tanaka et al. (2022), retrieval augmentation eliminated all factual errors.",
        "fabricated citation",
    ),
)
FABRICATED_CLAIMS: tuple[str, ...] = tuple(text for text, _ in FABRICATIONS)

# Back-compat alias: the Phase 36.7 eval (tests/test_grounding_eval.py)
# reads this as the lexically-detectable ground-truth set. It is exactly
# the fabricated claims — NOT the hard case (which lexical can't catch).
PLANTED_HALLUCINATIONS: tuple[str, ...] = FABRICATED_CLAIMS

# Hard case — a same-vocabulary numeric CONTRADICTION (rollback's true
# "92%" rewritten as "29%"). The lexical judge MISSES this (overlap stays
# high). Reported as a known blind spot; the LLM judge catches it.
HARD_CASE_CLAIM: str = "Rollback on failed verification recovered 29 percent of corrupted runs."

# The synthesised review, built from the SAME constants so ground truth
# never drifts. Claims are interleaved for a realistic narrative; the gate
# is order-independent.
_REVIEW_ORDER: tuple[str, ...] = (
    GROUNDED_CLAIMS[0],
    GROUNDED_CLAIMS[1],
    FABRICATED_CLAIMS[0],  # no-source statistic
    GROUNDED_CLAIMS[2],
    GROUNDED_CLAIMS[3],
    FABRICATED_CLAIMS[1],  # fabricated citation
    GROUNDED_CLAIMS[4],
    GROUNDED_CLAIMS[5],
    FABRICATED_CLAIMS[2],  # no-source statistic
    GROUNDED_CLAIMS[6],
    HARD_CASE_CLAIM,  # numeric contradiction — lexical blind spot
    GROUNDED_CLAIMS[7],
    FABRICATED_CLAIMS[3],  # over-generalisation
    GROUNDED_CLAIMS[8],
    GROUNDED_CLAIMS[9],
    FABRICATED_CLAIMS[4],  # no-source statistic
    GROUNDED_CLAIMS[10],
    GROUNDED_CLAIMS[11],
    FABRICATED_CLAIMS[5],  # fabricated citation
)


def _build_review() -> str:
    lines = [
        "# Literature Review: evaluating and hardening LLM agents",
        "",
        "This review synthesises twelve sources on agent evaluation, safe "
        "execution, and failure recovery.",
        "",
        "## Findings",
        "",
    ]
    lines += [f"- {claim}" for claim in _REVIEW_ORDER]
    return "\n".join(lines) + "\n"


def seed_workspace(workspace: Path) -> Path:
    """Create the demo workspace. Returns the workspace path."""
    if workspace.exists():
        shutil.rmtree(workspace)
    (workspace / "sources").mkdir(parents=True)
    (workspace / "summaries").mkdir(parents=True)

    for paper in _PAPERS:
        (workspace / "sources" / paper["file"]).write_text(paper["source"], encoding="utf-8")
        # Per-source summary = the grounding pool. Each contains its claim
        # verbatim so the grounded claims trace cleanly.
        stem = Path(paper["file"]).stem
        summary = f"# {paper['title']}\n\n{paper['claim']}\n"
        (workspace / "summaries" / f"{stem}.md").write_text(summary, encoding="utf-8")

    (workspace / "review.md").write_text(_build_review(), encoding="utf-8")
    return workspace


def _check(workspace: Path) -> int:
    """Run the deterministic grounding gate + print a quantified scorecard."""
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

    grounded_set = set(GROUNDED_CLAIMS)
    fabricated_set = set(FABRICATED_CLAIMS)

    flagged_fabrications = 0
    fpr_misflags = 0
    hard_case_flagged = False

    print(f"\nGrounding gate (judge: {result.judge_kind})")
    print("=" * 72)
    for v in result.verdicts:
        text = v.text.strip()
        if text in fabricated_set:
            tag = "FABRICATED "
            if not v.grounded:
                flagged_fabrications += 1
        elif text == HARD_CASE_CLAIM:
            tag = "HARD-CASE  "
            hard_case_flagged = not v.grounded
        elif text in grounded_set:
            tag = "grounded   "
            if not v.grounded:
                fpr_misflags += 1
        else:
            tag = "?          "
        mark = "✓ kept     " if v.grounded else "✗ FLAGGED  "
        print(f"  [{v.claim_id}] {tag}{mark} {text}")
    print("=" * 72)

    g = result.gate
    total_fab = len(FABRICATED_CLAIMS)
    total_grounded = len(GROUNDED_CLAIMS)
    recall = flagged_fabrications / total_fab if total_fab else 1.0
    fpr = fpr_misflags / total_grounded if total_grounded else 0.0

    print("Scorecard")
    print(f"  claims total          : {g.total_claims}")
    print(f"  kept (grounded)        : {g.grounded_count}")
    print(f"  flagged (ungrounded)   : {g.ungrounded_count}")
    print(
        f"  hallucination recall   : {flagged_fabrications}/{total_fab} "
        f"= {recall * 100:.0f}%   (target 100%)"
    )
    print(
        f"  grounded false-pos rate: {fpr_misflags}/{total_grounded} "
        f"= {fpr * 100:.0f}%   (target < 10%)"
    )
    blind = "MISSED (expected — lexical blind spot)" if not hard_case_flagged else "flagged"
    print(f"  hard-case contradiction: {blind}; caught only by the LLM judge")
    print(
        f"  decision               : {'SHIP' if g.passed else 'ROLLBACK / not-shippable (exit 3)'}"
    )
    print("=" * 72)

    if g.ungrounded_count:
        print(f"\nrouted to human review ({g.ungrounded_count}):")
        for v in g.ungrounded_claims:
            print(f"  - {v.text}")

    # Demo passes when the gate behaved as designed: it FAILED on the
    # planted fabrications, caught all lexically-detectable ones, did not
    # misflag real claims, and the hard case is the documented blind spot.
    ok = (not g.passed) and recall == 1.0 and fpr < 0.10 and (not hard_case_flagged)
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed + (optionally check) the demo.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="After seeding, run the deterministic grounding gate + print the scorecard.",
    )
    args = parser.parse_args()

    workspace = Path(__file__).parent / "workspace"
    seed_workspace(workspace)
    print(f"Seeded {workspace}")
    print(f"  sources/   {len(_PAPERS)} papers + notes")
    print(f"  summaries/ {len(_PAPERS)} summaries")
    print(
        f"  review.md  {len(_REVIEW_ORDER)} claims "
        f"({len(FABRICATED_CLAIMS)} fabricated + 1 hard-case contradiction)"
    )

    if args.check:
        raise SystemExit(_check(workspace))


if __name__ == "__main__":
    main()
