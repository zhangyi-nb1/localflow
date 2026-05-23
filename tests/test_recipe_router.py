"""Phase 17 — RecipeRouter scoring + best_match behaviour.

The router is the productisation-guide §6.2 "Delivery Planner" entry
point: takes (user_goal, workspace_snapshot) → recommends a recipe.
Tests pin the deterministic scoring rule so a regression in the
keyword/file-kind weighting surfaces immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.recipes import RecipeRegistry, RecipeRouter
from app.schemas import FileMeta, WorkspaceSnapshot


def _yaml(
    name: str,
    *,
    keywords: list[str] | None = None,
    file_kinds: list[str] | None = None,
    require_any: list[str] | None = None,
    min_files: int = 1,
) -> str:
    keywords = keywords or []
    file_kinds = file_kinds or []
    require_any = require_any or []
    kw_block = "\n".join(f"    - {k}" for k in keywords) or "    []"
    fk_block = "\n".join(f"    - {k}" for k in file_kinds) or "    []"
    ra_block = "\n".join(f"    - {k}" for k in require_any) or "    []"
    return f"""
name: {name}
title: {name}
description: test recipe
input_expectation:
  min_files: {min_files}
  keywords:
{kw_block}
  file_kinds:
{fk_block}
  require_any:
{ra_block}
stages:
  - stage_id: s1
    title: t
    skill: folder_organizer
"""


def _build_router(tmp_path: Path, recipes: dict[str, str]) -> RecipeRouter:
    for name, body in recipes.items():
        (tmp_path / f"{name}.yaml").write_text(body, encoding="utf-8")
    return RecipeRouter(RecipeRegistry(recipes_dir=tmp_path))


def _snapshot(files: list[tuple[str, str]]) -> WorkspaceSnapshot:
    """files: list of (path, file_type) tuples."""
    return WorkspaceSnapshot(
        snapshot_id="x",
        task_id="y",
        root="/tmp",
        files=[
            FileMeta(
                path=p,
                file_type=t,
                size_bytes=10,
                modified_at=datetime.now(timezone.utc),
            )
            for p, t in files
        ],
    )


def test_keyword_hits_increase_score(tmp_path: Path) -> None:
    router = _build_router(
        tmp_path,
        {"alpha": _yaml("alpha", keywords=["research", "paper"])},
    )
    ranked = router.score_all(user_goal="A research paper review")
    assert len(ranked) == 1
    assert ranked[0].score == 4  # two keyword hits × 2
    assert "research" in ranked[0].why[0]


def test_file_kind_matches_score_capped_at_five(tmp_path: Path) -> None:
    router = _build_router(
        tmp_path,
        {"alpha": _yaml("alpha", file_kinds=["pdf", "tabular", "image", "code", "text", "excel"])},
    )
    snap = _snapshot(
        [
            ("a.pdf", "pdf"),
            ("b.csv", "tabular"),
            ("c.png", "image"),
            ("d.py", "code"),
            ("e.md", "text"),
            ("f.xlsx", "excel"),
        ]
    )
    ranked = router.score_all(user_goal="", snapshot=snap)
    # Six kinds matched but cap at 5.
    assert ranked[0].score == 5


def test_min_files_violation_penalises(tmp_path: Path) -> None:
    router = _build_router(
        tmp_path,
        {"alpha": _yaml("alpha", min_files=10, keywords=["research"])},
    )
    snap = _snapshot([("a.txt", "text")])
    ranked = router.score_all(user_goal="research paper", snapshot=snap)
    # Single keyword ("research") × 2 = +2, min_files violated = -10 → net -8.
    assert ranked[0].score == -8
    assert not ranked[0].is_suitable


def test_require_any_violation_penalises(tmp_path: Path) -> None:
    router = _build_router(
        tmp_path,
        {
            "alpha": _yaml(
                "alpha",
                require_any=["pdf"],
                keywords=["paper"],
            )
        },
    )
    snap = _snapshot([("a.txt", "text")])
    ranked = router.score_all(user_goal="paper", snapshot=snap)
    # Keyword hit = +2, require_any failed = -5 → net -3.
    assert ranked[0].score == -3


def test_best_match_returns_none_when_no_suitable(tmp_path: Path) -> None:
    router = _build_router(
        tmp_path,
        {"alpha": _yaml("alpha", keywords=["xyz"])},
    )
    # Nothing matches.
    assert router.best_match(user_goal="hello world") is None


def test_best_match_is_alphabetical_on_ties(tmp_path: Path) -> None:
    router = _build_router(
        tmp_path,
        {
            "zeta": _yaml("zeta", keywords=["paper"]),
            "alpha": _yaml("alpha", keywords=["paper"]),
        },
    )
    best = router.best_match(user_goal="paper")
    assert best is not None
    assert best.recipe.name == "alpha"


def test_repo_recipes_route_correctly(tmp_path: Path) -> None:
    """End-to-end: the three shipped recipes should each win for a
    plausible goal + workspace. Catches regressions where someone
    tweaks keywords and accidentally breaks suggestion."""
    router = RecipeRouter()

    # Research pack — mixed workspace + Chinese keyword.
    snap_research = _snapshot(
        [
            ("paper.pdf", "pdf"),
            ("data.csv", "tabular"),
            ("notes.md", "text"),
        ]
    )
    best = router.best_match(user_goal="整理研究资料", snapshot=snap_research)
    assert best is not None and best.recipe.name == "research_pack"

    # Data report pack — pure tabular + English goal.
    snap_data = _snapshot(
        [
            ("a.csv", "tabular"),
            ("b.xlsx", "excel"),
        ]
    )
    best = router.best_match(user_goal="generate a data analysis report", snapshot=snap_data)
    assert best is not None and best.recipe.name == "data_report_pack"

    # Project handoff — code + notes.
    snap_proj = _snapshot(
        [
            ("a.py", "code"),
            ("b.py", "code"),
            ("readme.md", "text"),
            ("notes.md", "text"),
        ]
    )
    best = router.best_match(user_goal="prepare this project for handoff", snapshot=snap_proj)
    assert best is not None and best.recipe.name == "project_handoff_pack"
