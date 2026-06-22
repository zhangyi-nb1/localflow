"""Phase 36.3/36.5 — claim_grounding_verifier (the flagship gate) tests.

Deterministic: no LLM key → the verifier uses LexicalClaimJudge. Builds
a workspace with a review + per-source summaries, runs the verifier,
and asserts the gate verdict + the evidence-bundle artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval.recipe_verifiers import RecipeVerifierContext, get, list_names
from app.schemas import RecipeSpec


@pytest.fixture(autouse=True)
def _force_lexical_judge(monkeypatch):
    """Pin the deterministic lexical judge regardless of ambient env.

    The verifier picks ``LLMClaimJudge`` when an LLM client is
    resolvable; another test in the suite can leave one resolvable,
    which would make these deterministic assertions flaky. These tests
    assert the lexical gate's behaviour specifically, so force no
    client → ``LexicalClaimJudge``."""
    monkeypatch.setattr(
        "app.eval.recipe_verifiers.grounding.get_default_client_or_none",
        lambda: None,
    )


def _recipe() -> RecipeSpec:
    return RecipeSpec.model_validate(
        {
            "name": "lit_review",
            "title": "lit review",
            "description": "test",
            "stages": [{"stage_id": "s1", "title": "s1", "skill": "folder_organizer"}],
            "expected_outputs": [],
        }
    )


def _ctx(workspace: Path) -> RecipeVerifierContext:
    return RecipeVerifierContext(
        recipe=_recipe(),
        workspace_path=workspace,
        snapshot_inputs=[],
        moves={},
    )


def _seed_sources(ws: Path) -> None:
    (ws / "summaries").mkdir(parents=True, exist_ok=True)
    (ws / "summaries" / "paper_a.md").write_text(
        "Method A improved classification accuracy by 12% on the ImageNet benchmark.",
        encoding="utf-8",
    )
    (ws / "summaries" / "paper_b.md").write_text(
        "Method B reduced inference latency by half on the validation set.",
        encoding="utf-8",
    )


def test_registered() -> None:
    assert "claim_grounding_verifier" in list_names()


def test_gate_fails_on_planted_hallucination(tmp_path: Path) -> None:
    _seed_sources(tmp_path)
    # review.md: one grounded claim + one fabricated ("Method C ... cost 40%").
    (tmp_path / "review.md").write_text(
        "# Literature Review\n\n"
        "- Method A improved accuracy by 12 percent on the benchmark.\n"
        "- Method C reduced cost by 40 percent across all datasets.\n",
        encoding="utf-8",
    )
    verdict = get("claim_grounding_verifier")(_ctx(tmp_path))

    assert verdict.passed is False
    assert verdict.skipped is False
    assert verdict.suggested_hint is not None
    assert "Method C" in verdict.suggested_hint
    # score = grounded ratio = 1/2
    assert verdict.score == 0.5


def test_evidence_bundle_written(tmp_path: Path) -> None:
    _seed_sources(tmp_path)
    (tmp_path / "review.md").write_text(
        "- Method A improved accuracy by 12 percent on the benchmark.\n"
        "- Method C reduced cost by 40 percent across all datasets.\n",
        encoding="utf-8",
    )
    get("claim_grounding_verifier")(_ctx(tmp_path))

    # Machine-readable evidence.
    bundle_path = tmp_path / "claim_grounding.json"
    assert bundle_path.is_file()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["judge_kind"] == "lexical"
    assert bundle["gate"]["ungrounded_count"] == 1
    assert len(bundle["verdicts"]) == 2

    # Human-review queue.
    queue_path = tmp_path / "review_queue.md"
    assert queue_path.is_file()
    queue = queue_path.read_text(encoding="utf-8")
    assert "Method C" in queue
    assert "human verification" in queue.lower()


def test_gate_passes_when_all_grounded(tmp_path: Path) -> None:
    _seed_sources(tmp_path)
    (tmp_path / "review.md").write_text(
        "- Method A improved accuracy by 12 percent on the benchmark.\n"
        "- Method B reduced latency by half on the validation set.\n",
        encoding="utf-8",
    )
    verdict = get("claim_grounding_verifier")(_ctx(tmp_path))
    assert verdict.passed is True
    assert verdict.skipped is False
    assert verdict.score == 1.0
    # Queue still written, but empty.
    queue = (tmp_path / "review_queue.md").read_text(encoding="utf-8")
    assert "Nothing queued" in queue


def test_skips_when_no_review(tmp_path: Path) -> None:
    _seed_sources(tmp_path)  # sources but no review.md
    verdict = get("claim_grounding_verifier")(_ctx(tmp_path))
    assert verdict.passed is True
    assert verdict.skipped is True
    assert "no review file" in verdict.detail


def test_skips_when_no_sources(tmp_path: Path) -> None:
    (tmp_path / "review.md").write_text(
        "- Method A improved accuracy by 12 percent on the benchmark.\n",
        encoding="utf-8",
    )
    verdict = get("claim_grounding_verifier")(_ctx(tmp_path))
    assert verdict.passed is True
    assert verdict.skipped is True
    # review.md is excluded from the source pool, and there are no
    # summaries/papers/notes/sources to fall back to → still skips.
    assert "no source documents" in verdict.detail
