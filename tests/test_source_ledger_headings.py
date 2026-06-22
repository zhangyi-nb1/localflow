"""Task 1 — source_ledger_verifier recognises heading-form citations.

Real LLM output (e.g. gpt-5.4-mini) often cites each source as a markdown
heading (``## papers/x.txt``) rather than an inline backticked path. The
verifier must recognise those and still assert they resolve — otherwise
it skips a perfectly valid ledger ("no path citations").
"""

from __future__ import annotations

from pathlib import Path

from app.eval.recipe_verifiers import RecipeVerifierContext, get
from app.schemas import RecipeSpec


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


def test_heading_citations_recognised_and_resolved(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "papers" / "b.txt").write_text("y", encoding="utf-8")
    (tmp_path / "SOURCES.md").write_text(
        "# SOURCES\n\n"
        "## papers/a.txt\n- **Key claim:** something\n\n"
        "## papers/b.txt\n- **Key claim:** something else\n",
        encoding="utf-8",
    )
    v = get("source_ledger_verifier")(_ctx(tmp_path))
    assert v.skipped is False
    assert v.passed is True
    assert "2 citation" in v.detail


def test_heading_citation_must_resolve(tmp_path: Path) -> None:
    (tmp_path / "SOURCES.md").write_text(
        "# SOURCES\n\n## papers/ghost.txt\n- fabricated source\n",
        encoding="utf-8",
    )
    v = get("source_ledger_verifier")(_ctx(tmp_path))
    assert v.passed is False
    assert "ghost.txt" in v.detail


def test_prose_headings_are_not_citations(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "SOURCES.md").write_text(
        "# SOURCES\n\n## Research Papers & Studies\n\n## papers/a.txt\n- claim\n",
        encoding="utf-8",
    )
    v = get("source_ledger_verifier")(_ctx(tmp_path))
    assert v.passed is True
    assert "1 citation" in v.detail  # only the path heading counts, not the prose one


def test_backtick_and_heading_both_count(tmp_path: Path) -> None:
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "n.md").write_text("z", encoding="utf-8")
    (tmp_path / "SOURCES.md").write_text(
        "# SOURCES\n\n## papers/a.txt\n- claim\n\nAlso see `notes/n.md` for context.\n",
        encoding="utf-8",
    )
    v = get("source_ledger_verifier")(_ctx(tmp_path))
    assert v.passed is True
    assert "2 citation" in v.detail
