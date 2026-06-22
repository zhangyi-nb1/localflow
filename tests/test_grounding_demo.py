"""Option 1 (flagship demo scale-up) — pins the complex-task demo's
honesty + the guard ON/OFF ablation contrast.

Complements ``tests/test_grounding_eval.py`` (which pins recall=1.0 /
FP=0.0 on the lexically-detectable fabrications). Here we pin the parts
that make the demo *honest* and *contrastive*:

  * the demo is genuinely complex (>= 12 sources, >= 12 grounded claims);
  * all four hallucination classes are present and every fabrication is
    flagged by the deterministic gate;
  * the HARD CASE (a same-vocabulary numeric contradiction) is a
    DOCUMENTED lexical blind spot — the lexical judge keeps it (rule F:
    we pin the limitation instead of pretending the baseline is perfect);
  * the guard-OFF recipe drops the grounding gate while the flagship
    keeps it — the ablation control is real.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.eval.grounding import (
    GroundingPolicy,
    LexicalClaimJudge,
    ground_review,
    load_source_fragments,
    split_claims,
)
from app.recipes import RecipeRegistry

_REPO = Path(__file__).resolve().parent.parent
_SEED_PATH = _REPO / "examples" / "literature_review_pack" / "seed.py"
_RECIPES_DIR = _REPO / "recipes"


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("_lit_review_seed_demo", _SEED_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ground(ws: Path):
    review_text = (ws / "review.md").read_text(encoding="utf-8")
    fragments = load_source_fragments(ws)
    return ground_review(
        review_text=review_text,
        review_path="review.md",
        fragments=fragments,
        policy=GroundingPolicy(),
        judge=LexicalClaimJudge(),
    )


def test_demo_is_genuinely_complex(tmp_path: Path) -> None:
    """The whole point of Option 1 is a COMPLEX task — enough sources +
    claims that the gate is doing real work, not a toy."""
    seed = _load_seed_module()
    ws = seed.seed_workspace(tmp_path / "workspace")

    sources = list((ws / "sources").iterdir())
    summaries = list((ws / "summaries").iterdir())
    claims = split_claims((ws / "review.md").read_text(encoding="utf-8"))

    assert len(sources) >= 12
    assert len(summaries) >= 12
    assert len(seed.GROUNDED_CLAIMS) >= 12
    # 12 grounded + 6 fabricated + 1 hard case.
    assert len(claims) == len(seed.GROUNDED_CLAIMS) + len(seed.FABRICATED_CLAIMS) + 1


def test_all_four_hallucination_classes_present_and_flagged(tmp_path: Path) -> None:
    seed = _load_seed_module()
    ws = seed.seed_workspace(tmp_path / "workspace")

    classes = {cls for _, cls in seed.FABRICATIONS}
    assert classes == {"no-source statistic", "fabricated citation", "over-generalisation"}

    result = _ground(ws)
    flagged = {v.text.strip() for v in result.verdicts if not v.grounded}
    # Every fabricated claim, across all classes, must be flagged.
    for fabricated in seed.FABRICATED_CLAIMS:
        assert fabricated in flagged, f"fabrication not flagged: {fabricated!r}"


def test_hard_case_is_a_documented_lexical_blind_spot(tmp_path: Path) -> None:
    """Honesty (rule F): the lexical baseline CANNOT catch a same-vocabulary
    numeric contradiction. We pin that it keeps the hard case (i.e. the
    baseline misses it) so the limitation is in the test, not hidden."""
    seed = _load_seed_module()
    ws = seed.seed_workspace(tmp_path / "workspace")
    result = _ground(ws)

    hard = [v for v in result.verdicts if v.text.strip() == seed.HARD_CASE_CLAIM]
    assert len(hard) == 1, "hard case must appear exactly once in the review"
    # Lexical judge keeps it (grounded=True) — the documented blind spot.
    assert hard[0].grounded is True

    # The contradicting number must really differ from the true source
    # value, so this is a genuine contradiction (92 -> 29), not a typo.
    assert "29 percent" in seed.HARD_CASE_CLAIM
    true_claim = next(c for c in seed.GROUNDED_CLAIMS if "corrupted runs" in c)
    assert "92 percent" in true_claim


def test_check_scorecard_passes_as_designed(tmp_path: Path) -> None:
    """seed.py --check returns 0 only when the gate behaved as designed:
    failed on fabrications, recall 100%, FP < 10%, hard case missed."""
    seed = _load_seed_module()
    ws = seed.seed_workspace(tmp_path / "workspace")
    assert seed._check(ws) == 0


def test_guard_on_off_ablation_recipes_differ() -> None:
    """The ablation control is real: the flagship gates on grounding, the
    nogate control drops exactly that verifier."""
    reg = RecipeRegistry(recipes_dir=_RECIPES_DIR)

    flagship = reg.get("literature_review_pack")
    nogate = reg.get("literature_review_pack_nogate")

    assert "claim_grounding_verifier" in flagship.verifiers
    assert "claim_grounding_verifier" not in nogate.verifiers

    # Otherwise the pipelines match (same stages) so the contrast isolates
    # the gate, not unrelated differences.
    assert [s.stage_id for s in flagship.stages] == [s.stage_id for s in nogate.stages]
