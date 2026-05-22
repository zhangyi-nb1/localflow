"""Phase 17 — RecipeRegistry: directory scanning + error reporting.

The registry must load every valid YAML it finds, skip invalid ones
without crashing, and surface the error list for the CLI / UI to
display.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.recipes import RecipeError, RecipeNotFound, RecipeRegistry


def _write_recipe(dir_: Path, name: str, payload_yaml: str) -> Path:
    path = dir_ / f"{name}.yaml"
    path.write_text(payload_yaml, encoding="utf-8")
    return path


def _valid_yaml(name: str = "demo") -> str:
    return f"""
name: {name}
title: {name.replace('_', ' ').title()}
description: A throwaway recipe.
stages:
  - stage_id: s1
    title: One
    skill: folder_organizer
    planner: rule
"""


def test_registry_loads_all_valid_yaml(tmp_path: Path) -> None:
    _write_recipe(tmp_path, "alpha", _valid_yaml("alpha"))
    _write_recipe(tmp_path, "beta", _valid_yaml("beta"))
    reg = RecipeRegistry(recipes_dir=tmp_path)
    names = reg.list_names()
    assert names == ["alpha", "beta"]
    assert reg.load_errors == []


def test_registry_get_raises_recipe_not_found(tmp_path: Path) -> None:
    _write_recipe(tmp_path, "alpha", _valid_yaml("alpha"))
    reg = RecipeRegistry(recipes_dir=tmp_path)
    with pytest.raises(RecipeNotFound):
        reg.get("does_not_exist")


def test_registry_records_load_errors_without_crashing(tmp_path: Path) -> None:
    _write_recipe(tmp_path, "good", _valid_yaml("good"))
    # Missing required `stages` field.
    _write_recipe(
        tmp_path,
        "bad",
        "name: bad\ntitle: Bad\ndescription: missing stages\n",
    )
    # Empty YAML — not None handling.
    _write_recipe(tmp_path, "empty", "")
    reg = RecipeRegistry(recipes_dir=tmp_path)
    assert reg.list_names() == ["good"]
    err_files = {path.name for path, _ in reg.load_errors}
    assert err_files == {"bad.yaml", "empty.yaml"}


def test_registry_rejects_duplicate_recipe_names(tmp_path: Path) -> None:
    # Two YAMLs claiming the same name.
    _write_recipe(tmp_path, "first", _valid_yaml("same"))
    _write_recipe(tmp_path, "second", _valid_yaml("same"))
    reg = RecipeRegistry(recipes_dir=tmp_path)
    # The first one wins (sorted filename order), the second is logged.
    assert reg.list_names() == ["same"]
    assert any("duplicate" in msg for _, msg in reg.load_errors)


def test_registry_handles_missing_dir_gracefully(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    reg = RecipeRegistry(recipes_dir=missing)
    assert reg.list_names() == []
    assert reg.load_errors == []


def test_registry_reload_resets_cache(tmp_path: Path) -> None:
    reg = RecipeRegistry(recipes_dir=tmp_path)
    assert reg.list_names() == []
    _write_recipe(tmp_path, "alpha", _valid_yaml("alpha"))
    # Without reload, cache is stale.
    assert reg.list_names() == []
    reg.reload()
    assert reg.list_names() == ["alpha"]


def test_repo_recipes_dir_loads_three_flagships() -> None:
    """The three flagship packs that ship with v0.17.0 must always load
    cleanly — they're the product's main artefacts."""
    reg = RecipeRegistry()  # Default = repo's recipes/ dir.
    names = set(reg.list_names())
    assert {"research_pack", "data_report_pack", "project_handoff_pack"}.issubset(names)
    assert reg.load_errors == []


def test_repo_recipes_compile_to_taskgraph_cleanly() -> None:
    """Every shipped recipe must compile to a valid TaskGraph — if a
    schema change breaks compilation, this test surfaces it before a user
    hits `localflow pack run`."""
    reg = RecipeRegistry()
    for recipe in reg.all():
        tg = recipe.compile_to_taskgraph(workspace_root="/tmp/x")
        assert tg.workspace_root == "/tmp/x"
        assert len(tg.stages) == len(recipe.stages)
        assert tg.user_goal  # description fallback always produces something


def test_recipe_error_is_runtime_error() -> None:
    assert issubclass(RecipeError, RuntimeError)
