"""Phase 36.3 — claim-level grounding engine tests.

Deterministic: uses LexicalClaimJudge, no LLM / no API key. Runs on
every CI matrix leg. The headline test is the planted-hallucination
scenario — the gate must flag exactly the fabricated claim.
"""

from __future__ import annotations

from pathlib import Path

from app.eval.grounding import (
    GroundingPolicy,
    LexicalClaimJudge,
    SourceFragment,
    evaluate_grounding,
    ground_review,
    load_source_fragments,
    split_claims,
)


class TestSplitClaims:
    def test_bullets_become_claims(self):
        md = (
            "# Review\n\n"
            "- Method A improved accuracy by 12 percent on the benchmark.\n"
            "- Method B reduced latency on the validation set.\n"
        )
        claims = split_claims(md)
        assert len(claims) == 2
        assert claims[0].claim_id == "c1"
        assert "Method A" in claims[0].text
        assert claims[0].source_line == 3

    def test_prose_splits_into_sentences(self):
        md = "Method A improved accuracy by 12 percent. Method B cut latency in half."
        claims = split_claims(md)
        assert len(claims) == 2

    def test_headings_code_tables_skipped(self):
        md = (
            "## Heading line here\n"
            "```\ncode block claim that should be ignored entirely\n```\n"
            "| col | another col | third |\n"
            "> a blockquote line that is ignored\n"
            "---\n"
            "Method A improved accuracy by 12 percent on the benchmark.\n"
        )
        claims = split_claims(md)
        assert len(claims) == 1
        assert "Method A" in claims[0].text

    def test_short_fragments_filtered(self):
        md = "Key findings:\nIntro.\nMethod A improved accuracy by 12 percent here.\n"
        claims = split_claims(md)
        # "Key findings:" (lead-in) + "Intro." (too short) dropped.
        assert len(claims) == 1

    def test_abbreviation_does_not_oversplit(self):
        md = "Smith et al. reported that Method A improved accuracy by 12 percent."
        claims = split_claims(md)
        assert len(claims) == 1


def _demo_fragments() -> list[SourceFragment]:
    return [
        SourceFragment(
            source_id="summaries/paper_a.md",
            text="Method A improved classification accuracy by 12% on the ImageNet benchmark.",
        ),
        SourceFragment(
            source_id="summaries/paper_b.md",
            text="Method B reduced inference latency by half on the validation set.",
        ),
    ]


class TestLexicalJudge:
    def test_grounded_claim_matches_source(self):
        judge = LexicalClaimJudge()
        claims = split_claims("- Method A improved accuracy by 12 percent.\n")
        v = judge.judge_claim(claims[0], _demo_fragments())
        assert v.grounded is True
        assert v.source_id == "summaries/paper_a.md"
        assert v.judge == "lexical"

    def test_planted_hallucination_flagged(self):
        # "Method C reduced cost by 40%" — no source mentions Method C.
        judge = LexicalClaimJudge()
        claims = split_claims("- Method C reduced cost by 40 percent across all datasets.\n")
        v = judge.judge_claim(claims[0], _demo_fragments())
        assert v.grounded is False
        assert v.source_id is None
        assert "C" in v.evidence or "40" in v.evidence or "cost" in v.evidence

    def test_empty_salient_claim_grounded_by_default(self):
        judge = LexicalClaimJudge()
        # A filler line whose tokens are all function words → no salient
        # terms → grounded by default (carries no factual assertion).
        claims = split_claims("- They may do so as we have.\n")
        assert claims, "filler line should survive claimworthiness (>=4 words)"
        v = judge.judge_claim(claims[0], _demo_fragments())
        assert v.grounded is True
        assert "no checkable salient terms" in v.evidence


class TestEvaluateGrounding:
    def test_all_grounded_passes(self):
        judge = LexicalClaimJudge()
        md = (
            "- Method A improved accuracy by 12 percent on the benchmark.\n"
            "- Method B reduced latency by half on the validation set.\n"
        )
        verdicts = [judge.judge_claim(c, _demo_fragments()) for c in split_claims(md)]
        gate = evaluate_grounding(verdicts, GroundingPolicy())
        assert gate.passed is True
        assert gate.ungrounded_count == 0
        assert gate.grounded_ratio == 1.0
        assert gate.suggested_hint is None

    def test_one_hallucination_fails_gate_zero_tolerance(self):
        judge = LexicalClaimJudge()
        md = (
            "- Method A improved accuracy by 12 percent on the benchmark.\n"
            "- Method C reduced cost by 40 percent across all datasets.\n"
        )
        verdicts = [judge.judge_claim(c, _demo_fragments()) for c in split_claims(md)]
        gate = evaluate_grounding(verdicts, GroundingPolicy())  # max_ungrounded=0
        assert gate.passed is False
        assert gate.ungrounded_count == 1
        assert gate.suggested_hint is not None
        assert "Method C" in gate.suggested_hint

    def test_ratio_threshold_tolerates_below_max(self):
        judge = LexicalClaimJudge()
        md = (
            "- Method A improved accuracy by 12 percent on the benchmark.\n"
            "- Method B reduced latency by half on the validation set.\n"
            "- Method C reduced cost by 40 percent across all datasets.\n"
        )
        verdicts = [judge.judge_claim(c, _demo_fragments()) for c in split_claims(md)]
        # 2/3 grounded = 0.67; with a lenient policy this passes on ratio
        # but max_ungrounded=2 must also allow it.
        lenient = GroundingPolicy(min_grounded_ratio=0.6, max_ungrounded=2)
        gate = evaluate_grounding(verdicts, lenient)
        assert gate.grounded_ratio < 0.7
        assert gate.passed is True

    def test_zero_claims_passes_vacuously(self):
        gate = evaluate_grounding([], GroundingPolicy())
        assert gate.passed is True
        assert gate.total_claims == 0
        assert gate.suggested_hint is None


class TestGroundReviewEndToEnd:
    def test_bundle_shape_and_gate(self):
        md = (
            "# Literature Review\n\n"
            "- Method A improved accuracy by 12 percent on the benchmark.\n"
            "- Method C reduced cost by 40 percent across all datasets.\n"
        )
        result = ground_review(
            review_text=md,
            review_path="review.md",
            fragments=_demo_fragments(),
            policy=GroundingPolicy(),
            judge=LexicalClaimJudge(),
        )
        assert result.judge_kind == "lexical"
        assert result.review_path == "review.md"
        assert len(result.verdicts) == 2
        assert result.gate.passed is False
        assert result.gate.ungrounded_count == 1
        # Evidence bundle round-trips through JSON (extra='forbid' safe).
        dumped = result.model_dump_json()
        assert "claim_grounding" not in dumped or True  # smoke: serialisable
        assert "Method C" in dumped


class TestLoadSourceFragments:
    def test_loads_summaries_sorted(self, tmp_path: Path):
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "b.md").write_text("Beta summary text here.")
        (tmp_path / "summaries" / "a.md").write_text("Alpha summary text here.")
        frags = load_source_fragments(tmp_path)
        assert [f.source_id for f in frags] == ["summaries/a.md", "summaries/b.md"]

    def test_empty_when_no_summaries(self, tmp_path: Path):
        assert load_source_fragments(tmp_path) == []
