"""Phase 19 — recipe-level semantic verifier tests.

Three LLM-backed verifiers; tests focus on the **shape** of behaviour
(skip when no client, skip when no relevant artefact) rather than
LLM-quality assertions. The actual judge call is stubbed via the
``app.agent.judge`` helper's monkeypatchable surface.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.agent.judge import JudgeVerdict
from app.eval.recipe_verifiers import RecipeVerifierContext, get
from app.schemas import RecipeSpec


def _recipe(**kw) -> RecipeSpec:
    return RecipeSpec.model_validate(
        {
            "name": kw.get("name", "demo"),
            "title": kw.get("name", "demo"),
            "description": "test",
            "stages": [
                {"stage_id": "s1", "title": "s1", "skill": "folder_organizer"}
            ],
            **{k: v for k, v in kw.items() if k != "name"},
        }
    )


def _ctx(workspace: Path) -> RecipeVerifierContext:
    return RecipeVerifierContext(
        recipe=_recipe(),
        workspace_path=workspace,
        snapshot_inputs=[],
        moves={},
    )


# ───────────────────────────────────── summary_grounding_verifier


def test_summary_grounding_skips_when_no_summary(tmp_path: Path) -> None:
    v = get("summary_grounding_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped


def test_summary_grounding_skips_when_no_llm(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sample\n\nSee data/foo.csv.\n")
    with patch(
        "app.eval.recipe_verifiers.semantic.get_default_client_or_none",
        return_value=None,
    ):
        v = get("summary_grounding_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped


def test_summary_grounding_passes_via_judge(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sample\n\nSee data/foo.csv.\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "foo.csv").write_text("a,b\n1,2\n")
    fake_verdict = JudgeVerdict(
        verdict=True,
        reason="grounded — cites data/foo.csv which exists",
        suggested_hint="—",
        token_usage={},
    )
    with patch(
        "app.eval.recipe_verifiers.semantic.get_default_client_or_none",
        return_value=object(),
    ), patch(
        "app.eval.recipe_verifiers.semantic.judge", return_value=fake_verdict
    ):
        v = get("summary_grounding_verifier")(_ctx(tmp_path))
    assert v.passed and not v.skipped
    assert "grounded" in v.detail


def test_summary_grounding_fails_via_judge(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sample\n\nGeneric placeholder.\n")
    fake_verdict = JudgeVerdict(
        verdict=False,
        reason="generic boilerplate; doesn't name any workspace files",
        suggested_hint="rewrite README to reference real files in the workspace",
        token_usage={},
    )
    with patch(
        "app.eval.recipe_verifiers.semantic.get_default_client_or_none",
        return_value=object(),
    ), patch(
        "app.eval.recipe_verifiers.semantic.judge", return_value=fake_verdict
    ):
        v = get("summary_grounding_verifier")(_ctx(tmp_path))
    assert not v.passed
    assert v.suggested_hint and "real files" in v.suggested_hint


# ───────────────────────────────────── chart_data_consistency_verifier


def test_chart_consistency_skips_when_no_chart(tmp_path: Path) -> None:
    v = get("chart_data_consistency_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped


def test_chart_consistency_skips_when_chart_in_images_dir(tmp_path: Path) -> None:
    """v0.20.0 — workspace overview charts in images/ (file_counts.png
    etc.) are metadata-driven and intentionally excluded. The verifier
    now ONLY inspects analysis_charts/ (data_analyzer's output dir)."""
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "file_counts.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "file_counts_summary.md").write_text("There are 5 files.\n")
    v = get("chart_data_consistency_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped
    assert "analysis_charts" in v.detail


def test_chart_consistency_skips_when_no_caption(tmp_path: Path) -> None:
    """A chart in analysis_charts/ with no caption AND no
    analysis_report.md → skip."""
    (tmp_path / "analysis_charts").mkdir()
    (tmp_path / "analysis_charts" / "chart.png").write_bytes(b"\x89PNG\r\n")
    v = get("chart_data_consistency_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped
    assert "no matching .md captions" in v.detail


def test_chart_consistency_skips_when_no_llm(tmp_path: Path) -> None:
    (tmp_path / "analysis_charts").mkdir()
    (tmp_path / "analysis_charts" / "chart.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "analysis_charts" / "chart.md").write_text(
        "Chart shows category counts.\n"
    )
    with patch(
        "app.eval.recipe_verifiers.semantic.get_default_client_or_none",
        return_value=None,
    ):
        v = get("chart_data_consistency_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped


def test_chart_consistency_uses_analysis_report_md_fallback(
    tmp_path: Path,
) -> None:
    """v0.20.0 — when a chart in analysis_charts/ has no sibling caption,
    the verifier falls back to the workspace's ``analysis_report.md``
    (which data_analyzer always produces and sections by chart)."""
    (tmp_path / "analysis_charts").mkdir()
    (tmp_path / "analysis_charts" / "model_scores.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "analysis_report.md").write_text(
        "## model_scores\nMean accuracy by model.\n"
    )
    fake_verdict = JudgeVerdict(
        verdict=True, reason="consistent", suggested_hint="—", token_usage={}
    )
    with patch(
        "app.eval.recipe_verifiers.semantic.get_default_client_or_none",
        return_value=object(),
    ), patch(
        "app.eval.recipe_verifiers.semantic.judge", return_value=fake_verdict
    ):
        v = get("chart_data_consistency_verifier")(_ctx(tmp_path))
    assert v.passed and not v.skipped


# ───────────────────────────────────── topic_coherence_verifier


def test_topic_coherence_skips_when_no_topic_dir(tmp_path: Path) -> None:
    v = get("topic_coherence_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped


def test_topic_coherence_skips_when_dirs_too_small(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.pdf").write_bytes(b"%PDF")
    # Only 1 file — below the ≥3 threshold.
    v = get("topic_coherence_verifier")(_ctx(tmp_path))
    assert v.passed and v.skipped


def test_topic_coherence_evaluates_first_eligible_dir(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    for i in range(4):
        (papers / f"p{i}.pdf").write_bytes(b"%PDF")
    fake_verdict = JudgeVerdict(
        verdict=True,
        reason="all PDFs about ML",
        suggested_hint="—",
        token_usage={},
    )
    with patch(
        "app.eval.recipe_verifiers.semantic.get_default_client_or_none",
        return_value=object(),
    ), patch(
        "app.eval.recipe_verifiers.semantic.judge", return_value=fake_verdict
    ):
        v = get("topic_coherence_verifier")(_ctx(tmp_path))
    assert v.passed and not v.skipped
    assert "papers/" in v.detail


def test_topic_coherence_descends_into_topics_subdir(tmp_path: Path) -> None:
    """Phase 14.1 introduced topics/<sub>/ layout from topic_clusterer.
    The verifier should pick those up."""
    (tmp_path / "topics").mkdir()
    sub = tmp_path / "topics" / "memory"
    sub.mkdir()
    for i in range(3):
        (sub / f"f{i}.md").write_text("x")
    fake_verdict = JudgeVerdict(
        verdict=False,
        reason="bucket mixes memory + RAG papers",
        suggested_hint="split into separate topic dirs",
        token_usage={},
    )
    with patch(
        "app.eval.recipe_verifiers.semantic.get_default_client_or_none",
        return_value=object(),
    ), patch(
        "app.eval.recipe_verifiers.semantic.judge", return_value=fake_verdict
    ):
        v = get("topic_coherence_verifier")(_ctx(tmp_path))
    assert not v.passed
    assert "memory" in v.detail
