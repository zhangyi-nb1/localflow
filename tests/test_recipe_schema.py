"""Phase 17 — RecipeSpec schema + compile_to_taskgraph behaviour.

Pins the contract between Recipe layer and the TaskGraph runner. If
fields move or compile semantics shift, these tests force a clear
schema-bump decision.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import RecipeSpec, RecipeStage, RepairPolicy
from app.schemas.taskgraph import StageFailurePolicy


def _minimal_recipe(**overrides) -> dict:
    """Helper — minimal valid recipe payload for model_validate."""
    base = {
        "name": "demo",
        "title": "Demo",
        "description": "A throwaway recipe used in tests.",
        "stages": [
            {
                "stage_id": "s1",
                "title": "Step one",
                "skill": "folder_organizer",
                "planner": "rule",
            }
        ],
    }
    base.update(overrides)
    return base


def test_minimal_recipe_validates() -> None:
    recipe = RecipeSpec.model_validate(_minimal_recipe())
    assert recipe.name == "demo"
    assert len(recipe.stages) == 1
    # Defaults should be filled in.
    assert recipe.repair_policy.enabled is False
    assert recipe.input_expectation.min_files == 1
    assert recipe.verifiers == []


def test_recipe_requires_at_least_one_stage() -> None:
    with pytest.raises(ValidationError):
        RecipeSpec.model_validate(_minimal_recipe(stages=[]))


def test_recipe_rejects_duplicate_stage_ids() -> None:
    bad = _minimal_recipe(
        stages=[
            {"stage_id": "s1", "title": "A", "skill": "folder_organizer"},
            {"stage_id": "s1", "title": "B", "skill": "folder_organizer"},
        ]
    )
    with pytest.raises(ValidationError):
        RecipeSpec.model_validate(bad)


def test_compile_to_taskgraph_round_trips_basic_fields() -> None:
    recipe = RecipeSpec.model_validate(
        _minimal_recipe(
            stages=[
                {
                    "stage_id": "s1",
                    "title": "Categorise",
                    "skill": "folder_organizer",
                    "expected_outputs": ["papers/index.md"],
                },
                {
                    "stage_id": "s2",
                    "title": "Synth",
                    "skill": "agent",
                    "planner": "llm",
                    "failure_policy": "skip",
                },
            ]
        )
    )
    tg = recipe.compile_to_taskgraph(workspace_root="/tmp/x")
    assert len(tg.stages) == 2
    assert tg.workspace_root == "/tmp/x"
    assert tg.stages[0].expected_outputs == ["papers/index.md"]
    assert tg.stages[1].planner == "llm"
    assert tg.stages[1].failure_policy is StageFailurePolicy.SKIP


def test_compile_promotes_abort_stages_when_repair_enabled() -> None:
    recipe = RecipeSpec.model_validate(
        _minimal_recipe(
            stages=[
                {"stage_id": "s1", "title": "A", "skill": "folder_organizer"},  # ABORT
                {
                    "stage_id": "s2",
                    "title": "B",
                    "skill": "agent",
                    "failure_policy": "skip",
                },
            ],
            repair_policy={"enabled": True, "max_rounds": 2},
        )
    )
    tg = recipe.compile_to_taskgraph(workspace_root="/tmp")
    # s1 (ABORT) → REPAIR with max_retries=2.
    assert tg.stages[0].failure_policy is StageFailurePolicy.REPAIR
    assert tg.stages[0].max_retries == 2
    # s2 (SKIP) preserved — repair only promotes ABORT.
    assert tg.stages[1].failure_policy is StageFailurePolicy.SKIP


def test_compile_accepts_overrides() -> None:
    recipe = RecipeSpec.model_validate(_minimal_recipe())
    tg = recipe.compile_to_taskgraph(
        workspace_root="/w",
        user_goal="Custom override",
        forbidden_actions=["shell"],
        forbidden_paths=["secrets/"],
        preferences={"naming_style": "snake_case"},
    )
    assert tg.user_goal == "Custom override"
    # User-supplied "shell" is preserved; Phase 24 auto-appends
    # python_compute because the recipe didn't opt into ComputeAction.
    assert "shell" in tg.forbidden_actions
    assert "python_compute" in tg.forbidden_actions
    assert tg.forbidden_paths == ["secrets/"]
    assert tg.preferences == {"naming_style": "snake_case"}


def test_recipe_stage_translates_to_stage_spec() -> None:
    rs = RecipeStage(
        stage_id="s1",
        title="t",
        skill="folder_organizer",
        planner="rule",
        expected_outputs=["a.md"],
        max_retries=3,
    )
    spec = rs.to_stage_spec()
    assert spec.stage_id == "s1"
    assert spec.expected_outputs == ["a.md"]
    assert spec.max_retries == 3


def test_repair_policy_caps_max_rounds_at_three() -> None:
    with pytest.raises(ValidationError):
        RepairPolicy(enabled=True, max_rounds=4)
    # Boundary OK.
    rp = RepairPolicy(enabled=True, max_rounds=3)
    assert rp.max_rounds == 3


# v0.20.0 — auto-propagated preferences


def test_review_queue_verifier_auto_enables_review_routing_pref() -> None:
    """A recipe that lists ``review_queue_verifier`` clearly wants
    unclassifiable files routed to review/. The compile step propagates
    that intent into preferences so the user doesn't have to set the
    memory toggle manually (Phase 19 verifier reported this gap)."""
    recipe = RecipeSpec.model_validate(
        {
            **_minimal_recipe(),
            "verifiers": ["review_queue_verifier"],
        }
    )
    tg = recipe.compile_to_taskgraph(workspace_root="/tmp/x")
    assert tg.preferences.get("route_low_confidence_to_review") is True


def test_recipe_without_review_verifier_does_not_set_pref() -> None:
    recipe = RecipeSpec.model_validate(
        {**_minimal_recipe(), "verifiers": ["coverage_verifier"]}
    )
    tg = recipe.compile_to_taskgraph(workspace_root="/tmp/x")
    assert "route_low_confidence_to_review" not in tg.preferences


def test_user_preferences_win_over_auto_propagated() -> None:
    """An explicit user override of ``route_low_confidence_to_review=False``
    must beat the auto-propagation — the recipe is a default, not a lock."""
    recipe = RecipeSpec.model_validate(
        {
            **_minimal_recipe(),
            "verifiers": ["review_queue_verifier"],
        }
    )
    tg = recipe.compile_to_taskgraph(
        workspace_root="/tmp/x",
        preferences={"route_low_confidence_to_review": False},
    )
    assert tg.preferences["route_low_confidence_to_review"] is False


# v0.23 — Phase 24 — capability-first ComputeAction opt-in


def test_recipe_default_forbids_python_compute_in_taskgraph() -> None:
    """A vanilla recipe must compile a TaskGraph whose forbidden_actions
    includes ``python_compute`` so accidental emission gets blocked at
    the policy guard layer."""
    recipe = RecipeSpec.model_validate(_minimal_recipe())
    assert recipe.allow_compute_action is False
    tg = recipe.compile_to_taskgraph(workspace_root="/tmp/x")
    assert "python_compute" in tg.forbidden_actions


def test_recipe_rejects_python_compute_in_stage_allowed_actions_when_not_opt_in() -> None:
    """Declaring ``python_compute`` in a stage's allowed_actions without
    opting in at the recipe level is a schema-level error."""
    with pytest.raises(ValidationError) as exc:
        RecipeSpec.model_validate(
            _minimal_recipe(
                stages=[
                    {
                        "stage_id": "s1",
                        "title": "x",
                        "skill": "agent",
                        "allowed_actions": ["python_compute"],
                    }
                ],
            )
        )
    msg = str(exc.value)
    assert "allow_compute_action" in msg
    assert "python_compute" in msg


def test_recipe_opt_in_allows_python_compute_stage_declaration() -> None:
    """When ``allow_compute_action=True``, a stage may declare
    ``python_compute`` in its allowed_actions."""
    recipe = RecipeSpec.model_validate(
        _minimal_recipe(
            allow_compute_action=True,
            stages=[
                {
                    "stage_id": "s1",
                    "title": "compute",
                    "skill": "agent",
                    "allowed_actions": ["python_compute", "move"],
                }
            ],
        )
    )
    assert recipe.allow_compute_action is True
    tg = recipe.compile_to_taskgraph(workspace_root="/tmp/x")
    # When opted in, python_compute is NOT in the graph-level forbidden_actions.
    assert "python_compute" not in tg.forbidden_actions
    # Stage-level allowed_actions carry through.
    assert "python_compute" in (tg.stages[0].allowed_actions or [])


def test_user_supplied_forbidden_actions_still_get_compute_appended() -> None:
    """An explicit CLI ``--forbidden-actions shell`` must still get
    python_compute added when the recipe didn't opt in, otherwise the
    user override would silently widen the surface area."""
    recipe = RecipeSpec.model_validate(_minimal_recipe())
    tg = recipe.compile_to_taskgraph(
        workspace_root="/tmp/x",
        forbidden_actions=["shell"],
    )
    assert "python_compute" in tg.forbidden_actions
    assert "shell" in tg.forbidden_actions


def test_user_forbidden_actions_already_listing_compute_does_not_dup() -> None:
    """Idempotency — if the user passed python_compute manually, the
    auto-append must not duplicate it."""
    recipe = RecipeSpec.model_validate(_minimal_recipe())
    tg = recipe.compile_to_taskgraph(
        workspace_root="/tmp/x",
        forbidden_actions=["python_compute", "shell"],
    )
    assert tg.forbidden_actions.count("python_compute") == 1
