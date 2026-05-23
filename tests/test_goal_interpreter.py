"""Phase 18 — GoalInterpreter routing + LLM clarifying behaviour.

Three decision paths to pin:

  1. **Router-confident**: top score ≥ threshold AND margin ≥ 2 →
     return router pick, source='router', no LLM call.
  2. **Router-only fallback**: ambiguous + no LLM client → return
     router top with a low-confidence rationale.
  3. **LLM-driven**: ambiguous + LLM client → call LLM, validate the
     payload, return interpretation with source='llm'. Tests both
     'pick' and 'clarify' decisions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.agent.client import FakeLLMClient, LLMClientError
from app.agent.goal_interpreter import (
    CONFIDENT_MARGIN,
    CONFIDENT_SCORE_THRESHOLD,
    GoalInterpreter,
)
from app.recipes import RecipeRegistry
from app.schemas import FileMeta, WorkspaceSnapshot


def _write_recipe(dir_: Path, name: str, *, keywords=None, file_kinds=None) -> None:
    keywords = keywords or []
    file_kinds = file_kinds or []
    kw = "\n".join(f"    - {k}" for k in keywords) or "    []"
    fk = "\n".join(f"    - {k}" for k in file_kinds) or "    []"
    (dir_ / f"{name}.yaml").write_text(
        f"""
name: {name}
title: {name}
description: test recipe {name}
input_expectation:
  min_files: 1
  keywords:
{kw}
  file_kinds:
{fk}
stages:
  - stage_id: s1
    title: t
    skill: folder_organizer
""",
        encoding="utf-8",
    )


def _snapshot(files: list[tuple[str, str]]) -> WorkspaceSnapshot:
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


def test_router_confident_path_skips_llm(tmp_path: Path) -> None:
    """Strong keyword + file-kind match → router commits without LLM."""
    _write_recipe(
        tmp_path,
        "alpha",
        keywords=["research", "paper", "study"],
        file_kinds=["pdf", "tabular", "text"],
    )
    _write_recipe(tmp_path, "beta", keywords=["xyz"])
    fake = FakeLLMClient([])  # empty queue — must NOT be hit
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=fake)
    snap = _snapshot(
        [
            ("a.pdf", "pdf"),
            ("b.csv", "tabular"),
            ("c.md", "text"),
        ]
    )
    result = gi.interpret(user_goal="research paper study", snapshot=snap)
    assert result.decision == "pick"
    assert result.recipe_name == "alpha"
    assert result.source == "router"
    assert fake.calls == []  # LLM untouched


def test_router_fallback_when_no_llm_client(tmp_path: Path) -> None:
    """Ambiguous goal + no LLM → return router top, source='router'."""
    _write_recipe(tmp_path, "alpha", keywords=["something"])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=None)
    result = gi.interpret(user_goal="vague request", snapshot=None)
    # Low confidence — no LLM — should still pick OR clarify.
    assert result.source == "router"


def test_llm_pick_path(tmp_path: Path) -> None:
    """Ambiguous goal + LLM available → LLM picks; result.source='llm'."""
    _write_recipe(tmp_path, "alpha", keywords=["foo"])
    _write_recipe(tmp_path, "beta", keywords=["foo"])
    fake = FakeLLMClient(
        [
            {
                "decision": "pick",
                "recipe_name": "beta",
                "rationale": "beta matches the user's stated intent better.",
                "clarifying_questions": [],
            }
        ]
    )
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=fake)
    snap = _snapshot([("a.txt", "text")])
    result = gi.interpret(user_goal="do something with foo", snapshot=snap)
    assert result.decision == "pick"
    assert result.recipe_name == "beta"
    assert result.source == "llm"
    assert len(fake.calls) == 1
    # Schema must enum-constrain recipe names. v0.19.x — strict mode
    # requires the constraint to live under an ``anyOf`` (string enum +
    # null branch), not a top-level ``enum`` array.
    schema = fake.calls[0]["tool_schema"]
    any_of = schema["properties"]["recipe_name"]["anyOf"]
    enum_branch = next(b for b in any_of if "enum" in b)
    assert set(enum_branch["enum"]) == {"alpha", "beta"}
    assert any(b.get("type") == "null" for b in any_of)
    # OpenAI strict mode: every property must be listed in `required`.
    assert set(schema["required"]) == set(schema["properties"].keys())
    assert schema["additionalProperties"] is False


def test_llm_clarify_path(tmp_path: Path) -> None:
    """LLM emits clarify decision with questions; we preserve them."""
    _write_recipe(tmp_path, "alpha", keywords=["foo"])
    _write_recipe(tmp_path, "beta", keywords=["foo"])
    fake = FakeLLMClient(
        [
            {
                "decision": "clarify",
                "recipe_name": None,
                "rationale": "Both alpha and beta fit; goal is too vague.",
                "clarifying_questions": [
                    "Do you want a knowledge pack or a data report?",
                    "Should I include code files?",
                ],
            }
        ]
    )
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=fake)
    result = gi.interpret(user_goal="help", snapshot=_snapshot([("a.txt", "text")]))
    assert result.decision == "clarify"
    assert result.recipe_name is None
    assert len(result.clarifying_questions) == 2
    assert result.source == "llm"


def test_llm_failure_degrades_to_router(tmp_path: Path) -> None:
    _write_recipe(tmp_path, "alpha", keywords=["foo"])
    fake = FakeLLMClient([LLMClientError("boom")])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=fake)
    result = gi.interpret(user_goal="vague", snapshot=_snapshot([("a.txt", "text")]))
    assert result.source == "router"
    assert "LLM call failed" in result.rationale


def test_llm_picks_unknown_recipe_falls_back_to_router(tmp_path: Path) -> None:
    """Defense-in-depth: enum-constraining the schema is the main guard,
    but if the LLM somehow returns a name we don't have, we refuse."""
    _write_recipe(tmp_path, "alpha", keywords=["foo"])
    fake = FakeLLMClient(
        [
            {
                "decision": "pick",
                "recipe_name": "ghost",
                "rationale": "I made this up.",
                "clarifying_questions": [],
            }
        ]
    )
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=fake)
    result = gi.interpret(user_goal="something", snapshot=_snapshot([("a.txt", "text")]))
    assert result.recipe_name == "alpha"  # router fallback
    assert "unknown recipe" in result.rationale.lower()


def test_router_scores_audit_trail_attached(tmp_path: Path) -> None:
    _write_recipe(tmp_path, "alpha", keywords=["research"])
    _write_recipe(tmp_path, "beta", keywords=["data"])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=None)
    result = gi.interpret(
        user_goal="research project",
        snapshot=_snapshot([("a.txt", "text")]),
    )
    names = {s["recipe"] for s in result.router_scores}
    assert names == {"alpha", "beta"}


def test_clarifying_round_appends_prior_answer(tmp_path: Path) -> None:
    """Second interpretation call carries the user's first answer."""
    _write_recipe(tmp_path, "alpha", keywords=["foo"])
    _write_recipe(tmp_path, "beta", keywords=["foo"])
    fake = FakeLLMClient(
        [
            {
                "decision": "pick",
                "recipe_name": "alpha",
                "rationale": "user clarified they want alpha.",
                "clarifying_questions": [],
            }
        ]
    )
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=fake)
    result = gi.interpret(
        user_goal="help",
        snapshot=_snapshot([("a.txt", "text")]),
        prior_answers=["I want a knowledge pack"],
    )
    assert result.recipe_name == "alpha"
    # The prior answer should be reflected in the LLM's user prompt.
    user_msg = fake.calls[0]["messages"][0]["content"]
    assert "I want a knowledge pack" in user_msg


def test_thresholds_are_defined_constants() -> None:
    """Pin the public tuning surface — Phase 19 may move these but
    they must remain accessible as module constants."""
    assert CONFIDENT_SCORE_THRESHOLD >= 1
    assert CONFIDENT_MARGIN >= 1


# ─── v0.19.x — rationale_key i18n surface


def test_router_confident_populates_rationale_key(tmp_path: Path) -> None:
    """High-confidence router pick must set rationale_key so the UI can
    render the verdict in the active language."""
    _write_recipe(
        tmp_path,
        "alpha",
        keywords=["research", "paper", "study"],
        file_kinds=["pdf", "tabular", "text"],
    )
    _write_recipe(tmp_path, "beta", keywords=["xyz"])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=None)
    snap = _snapshot([("a.pdf", "pdf"), ("b.csv", "tabular"), ("c.md", "text")])
    result = gi.interpret(user_goal="research paper study", snapshot=snap)
    assert result.rationale_key == "goal_interp.rationale.router_confident"
    assert result.rationale_args["name"] == "alpha"
    assert result.rationale_args["score"].startswith("+")
    assert result.rationale_args["margin"].startswith("+")


def test_no_llm_router_pick_populates_rationale_key(tmp_path: Path) -> None:
    _write_recipe(tmp_path, "alpha", keywords=["foo"])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=None)
    result = gi.interpret(user_goal="foo", snapshot=_snapshot([("a.txt", "text")]))
    # Single-keyword hit → low confidence → no_llm_router_pick branch.
    assert result.rationale_key == "goal_interp.rationale.no_llm_router_pick"
    assert result.rationale_args["name"] == "alpha"


def test_vague_goal_no_llm_clarifies_instead_of_picking(tmp_path: Path) -> None:
    """Phase 21.1 regression — a vague goal (no keyword hits) with no
    LLM client must clarify, not commit to a low-confidence pick driven
    purely by workspace file kinds.

    The user reported: '随便弄一下' + --no-llm picked research_pack
    because the workspace had a PDF — even though nothing in the goal
    actually signalled the user wanted a knowledge pack."""
    _write_recipe(tmp_path, "alpha", keywords=["research"], file_kinds=["pdf"])
    _write_recipe(tmp_path, "beta", keywords=["data"], file_kinds=["tabular"])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=None)
    # Vague Chinese goal — no keyword hit against either recipe's
    # English keyword list. Workspace has a PDF so alpha's file_kinds
    # score still fires.
    result = gi.interpret(
        user_goal="随便弄一下",
        snapshot=_snapshot([("paper.pdf", "pdf")]),
    )
    assert result.decision == "clarify", (
        f"vague goal should clarify, got pick of {result.recipe_name}"
    )
    assert result.recipe_name is None
    assert result.source == "router"
    assert result.rationale_key == "goal_interp.rationale.no_llm_clarify"
    # The audit trail is still preserved so the user can see WHAT the
    # router considered.
    names = {s["recipe"] for s in result.router_scores}
    assert names == {"alpha", "beta"}


def test_keyword_hit_goal_no_llm_still_picks(tmp_path: Path) -> None:
    """Phase 21.1 sanity — adding the keyword-hit gate must NOT lock
    out the normal positive case: a goal that DOES hit a recipe's
    keyword should still produce a router pick when no LLM is available.
    """
    _write_recipe(tmp_path, "alpha", keywords=["research"])
    _write_recipe(tmp_path, "beta", keywords=["data"])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=None)
    result = gi.interpret(
        user_goal="research notes",
        snapshot=_snapshot([("a.pdf", "pdf")]),
    )
    assert result.decision == "pick"
    assert result.recipe_name == "alpha"


def test_llm_failure_populates_rationale_key(tmp_path: Path) -> None:
    _write_recipe(tmp_path, "alpha", keywords=["foo"])
    fake = FakeLLMClient([LLMClientError("boom")])
    gi = GoalInterpreter(registry=RecipeRegistry(recipes_dir=tmp_path), client=fake)
    # Goal contains "foo" → router top.score > 0 → llm_failed_pick branch.
    result = gi.interpret(
        user_goal="foo please",
        snapshot=_snapshot([("a.txt", "text")]),
    )
    assert result.rationale_key == "goal_interp.rationale.llm_failed_pick"
    assert "boom" in result.rationale_args["err"]


def test_every_rationale_key_exists_in_i18n_dict() -> None:
    """No router branch may set a rationale_key that the UI can't
    resolve. Smoke check across every key the interpreter uses."""
    from app.ui._i18n import _DICT

    expected_keys = {
        "goal_interp.rationale.router_confident",
        "goal_interp.rationale.no_llm_router_pick",
        "goal_interp.rationale.no_llm_clarify",
        "goal_interp.rationale.no_recipes_loaded",
        "goal_interp.rationale.llm_failed_pick",
        "goal_interp.rationale.llm_failed_clarify",
        "goal_interp.rationale.llm_invalid_envelope",
        "goal_interp.rationale.llm_unknown_recipe",
    }
    for key in expected_keys:
        assert key in _DICT, f"missing i18n key: {key}"
        assert "en" in _DICT[key] and "zh" in _DICT[key], f"{key} missing locale"


def test_strict_mode_schema_required_includes_every_property() -> None:
    """OpenAI strict mode rejects schemas where `required` doesn't list
    every property. This test pins the invariant so a future field
    add doesn't silently regress LLM clarifying."""
    from app.agent.goal_interpreter import _build_decision_tool_schema

    schema = _build_decision_tool_schema(["alpha", "beta"])
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"].keys())
