"""Pins the grounding source-pool fallback.

Real ``pack run`` produces ``review.md`` + ``SOURCES.md`` but no
``summaries/`` — the organiser sorts the papers into ``papers/`` /
``notes/`` instead. Before the fallback, ``load_source_fragments`` only
looked under ``summaries/`` and returned nothing, so the grounding gate
SKIPPED on every real run (the deterministic demo masked this by
pre-seeding ``summaries/``).

These tests pin: summaries/ wins when present; otherwise we fall back to
the organised source docs; generated deliverables + index files are
never treated as sources.
"""

from __future__ import annotations

from pathlib import Path

from app.eval.grounding import load_source_fragments


def _write(p: Path, text: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_summaries_pool_wins_when_present(tmp_path: Path) -> None:
    _write(tmp_path / "summaries" / "a.md", "summary a")
    _write(tmp_path / "summaries" / "b.md", "summary b")
    # papers/ also present, but summaries/ must take precedence.
    _write(tmp_path / "papers" / "p1.txt", "paper one")

    frags = load_source_fragments(tmp_path)
    ids = sorted(f.source_id for f in frags)
    assert ids == ["summaries/a.md", "summaries/b.md"]


def test_falls_back_to_organised_sources_when_no_summaries(tmp_path: Path) -> None:
    # Shape of a real pack run: organiser sorted papers + notes, agent
    # wrote review.md / SOURCES.md, folder_organizer wrote index.md files.
    _write(tmp_path / "papers" / "swe_bench.txt", "resolved 41 percent of issues")
    _write(tmp_path / "papers" / "react.txt", "cut hallucinated actions by 30 percent")
    _write(tmp_path / "notes" / "metrics.md", "report recall and false-positive rate")
    _write(tmp_path / "papers" / "index.md", "generated index — NOT a source")
    _write(tmp_path / "notes" / "index.md", "generated index — NOT a source")
    _write(tmp_path / "index.md", "root index — NOT a source")
    _write(tmp_path / "review.md", "the synthesised review — NOT a source")
    _write(tmp_path / "SOURCES.md", "the ledger — NOT a source")

    frags = load_source_fragments(tmp_path)
    ids = sorted(f.source_id for f in frags)

    # Real source docs are picked up...
    assert ids == ["notes/metrics.md", "papers/react.txt", "papers/swe_bench.txt"]
    # ...and generated deliverables / indexes are excluded.
    joined = " ".join(ids)
    assert "review.md" not in joined
    assert "SOURCES.md" not in joined
    assert "index.md" not in joined


def test_empty_workspace_yields_no_fragments(tmp_path: Path) -> None:
    _write(tmp_path / "review.md", "only a generated review, no sources")
    _write(tmp_path / "index.md", "only an index")
    assert load_source_fragments(tmp_path) == []
