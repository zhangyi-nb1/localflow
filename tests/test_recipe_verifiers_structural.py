"""Phase 19 — recipe-level structural verifier tests.

Each verifier has at least: a happy-path test, an edge-case skip path,
and a failure path that exercises the specific signal the verifier
detects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.eval.recipe_verifiers import (
    RecipeVerifierContext,
    get,
    list_names,
)
from app.schemas import RecipeSpec


def _recipe(*, name: str = "demo", expected_outputs: list[str] | None = None, **kw) -> RecipeSpec:
    return RecipeSpec.model_validate(
        {
            "name": name,
            "title": name,
            "description": "test",
            "stages": [
                {"stage_id": "s1", "title": "s1", "skill": "folder_organizer"}
            ],
            "expected_outputs": expected_outputs or [],
            **kw,
        }
    )


def _ctx(
    *,
    workspace: Path,
    recipe: RecipeSpec | None = None,
    inputs: list[str] | None = None,
    moves: dict[str, str] | None = None,
) -> RecipeVerifierContext:
    return RecipeVerifierContext(
        recipe=recipe or _recipe(),
        workspace_path=workspace,
        snapshot_inputs=inputs or [],
        moves=moves or {},
    )


# ───────────────────────────────────── registry shape


def test_seven_verifiers_registered() -> None:
    names = set(list_names())
    assert {
        "coverage_verifier",
        "source_ledger_verifier",
        "review_queue_verifier",
        "deliverable_completeness_verifier",
        "summary_grounding_verifier",
        "chart_data_consistency_verifier",
        "topic_coherence_verifier",
    }.issubset(names)


# ───────────────────────────────────── coverage_verifier


def test_coverage_no_inputs_skips(tmp_path: Path) -> None:
    v = get("coverage_verifier")(_ctx(workspace=tmp_path, inputs=[]))
    assert v.passed and v.skipped


def test_coverage_passes_when_file_is_moved(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.pdf").write_bytes(b"%PDF-")
    v = get("coverage_verifier")(
        _ctx(
            workspace=tmp_path,
            inputs=["a.pdf"],
            moves={"a.pdf": "papers/a.pdf"},
        )
    )
    assert v.passed, v.detail
    assert v.score == 1.0


def test_coverage_passes_when_file_is_cited_in_md(tmp_path: Path) -> None:
    (tmp_path / "pdf_index.md").write_text("# Index\n- a.pdf — A paper.\n")
    v = get("coverage_verifier")(_ctx(workspace=tmp_path, inputs=["a.pdf"]))
    assert v.passed
    assert "cited" in v.detail


def test_coverage_fails_when_file_is_neither(tmp_path: Path) -> None:
    v = get("coverage_verifier")(_ctx(workspace=tmp_path, inputs=["lost.pdf"]))
    assert not v.passed
    assert "lost.pdf" in v.detail
    assert v.suggested_hint is not None


# ───────────────────────────────────── source_ledger_verifier


def test_source_ledger_skips_when_no_file(tmp_path: Path) -> None:
    v = get("source_ledger_verifier")(_ctx(workspace=tmp_path))
    assert v.passed and v.skipped


def test_source_ledger_passes_when_citations_resolve(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "SOURCES.md").write_text(
        "## Sources\n\n- `papers/a.pdf` — main paper\n"
    )
    v = get("source_ledger_verifier")(_ctx(workspace=tmp_path))
    assert v.passed
    assert v.score == 1.0


def test_source_ledger_fails_on_missing_citation(tmp_path: Path) -> None:
    (tmp_path / "SOURCES.md").write_text("- `ghost.pdf`\n")
    v = get("source_ledger_verifier")(_ctx(workspace=tmp_path))
    assert not v.passed
    assert "ghost.pdf" in v.detail


def test_source_ledger_skips_when_no_citations(tmp_path: Path) -> None:
    (tmp_path / "SOURCES.md").write_text("(no sources)\n")
    v = get("source_ledger_verifier")(_ctx(workspace=tmp_path))
    assert v.passed and v.skipped


# ───────────────────────────────────── review_queue_verifier


def test_review_queue_skips_when_all_known_ext(tmp_path: Path) -> None:
    v = get("review_queue_verifier")(
        _ctx(workspace=tmp_path, inputs=["a.pdf", "b.csv"])
    )
    assert v.passed and v.skipped


def test_review_queue_passes_when_unclassifiable_routed_to_review(
    tmp_path: Path,
) -> None:
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "weird.xyz").write_text("?")
    v = get("review_queue_verifier")(
        _ctx(
            workspace=tmp_path,
            inputs=["weird.xyz"],
            moves={"weird.xyz": "review/weird.xyz"},
        )
    )
    assert v.passed


def test_review_queue_fails_when_unclassifiable_force_classified(
    tmp_path: Path,
) -> None:
    v = get("review_queue_verifier")(
        _ctx(
            workspace=tmp_path,
            inputs=["weird.xyz"],
            moves={"weird.xyz": "misc/weird.xyz"},
        )
    )
    assert not v.passed
    assert "weird.xyz" in v.detail


# ───────────────────────────────────── deliverable_completeness_verifier


def test_deliverable_completeness_skips_empty_list(tmp_path: Path) -> None:
    v = get("deliverable_completeness_verifier")(_ctx(workspace=tmp_path))
    assert v.passed and v.skipped


def test_deliverable_completeness_passes_when_all_present(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "index.md").write_text("x")
    recipe = _recipe(expected_outputs=["README.md", "data/index.md"])
    v = get("deliverable_completeness_verifier")(
        _ctx(workspace=tmp_path, recipe=recipe)
    )
    assert v.passed
    assert v.score == 1.0


def test_deliverable_completeness_fails_when_missing(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x")
    recipe = _recipe(
        expected_outputs=["README.md", "data/index.md", "missing.md"]
    )
    v = get("deliverable_completeness_verifier")(
        _ctx(workspace=tmp_path, recipe=recipe)
    )
    assert not v.passed
    assert "data/index.md" in v.detail
    assert "missing.md" in v.detail
    assert pytest.approx(v.score, abs=0.01) == 1 / 3


# ───────────────────────────────────── registry edge cases


def test_run_all_handles_unknown_verifier_gracefully(tmp_path: Path) -> None:
    from app.eval.recipe_verifiers import run_all

    verdicts = run_all(
        ["coverage_verifier", "no_such_verifier"], _ctx(workspace=tmp_path)
    )
    assert len(verdicts) == 2
    assert verdicts[0].name == "coverage_verifier"
    assert verdicts[1].name == "no_such_verifier"
    assert not verdicts[1].passed
    assert "not registered" in verdicts[1].detail


def test_recipe_verification_from_verdicts_aggregates() -> None:
    from app.eval.recipe_verifiers import (
        RecipeVerification,
        RecipeVerifierVerdict,
    )

    verdicts = [
        RecipeVerifierVerdict(name="a", passed=True),
        RecipeVerifierVerdict(name="b", passed=True, skipped=True),
        RecipeVerifierVerdict(name="c", passed=False),
    ]
    rv = RecipeVerification.from_verdicts(
        run_id="r1", recipe_name="demo", verdicts=verdicts
    )
    assert rv.passed is False
    assert rv.failed_count == 1
    assert rv.skipped_count == 1
