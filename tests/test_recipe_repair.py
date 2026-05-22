"""Phase 21 — recipe auto-repair loop tests.

Verifies the four halt conditions + the stage_hint wiring without
running an actual replay (which would need a real workspace + LLM).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.eval.recipe_verifiers import (
    RecipeVerification,
    RecipeVerifierVerdict,
)
from app.harness.recipe_repair import (
    RecipeRepairAttempt,
    RecipeRepairResult,
    run_recipe_repair,
)
from app.schemas import RecipeSpec


def _recipe(
    *,
    name: str = "demo",
    verifiers: list[str] | None = None,
    repair_target_map: dict[str, str] | None = None,
    enabled: bool = True,
    max_rounds: int = 2,
    stages: list[dict] | None = None,
) -> RecipeSpec:
    stages = stages or [
        {"stage_id": "s1_organize", "title": "x", "skill": "folder_organizer"},
        {
            "stage_id": "s2_synth",
            "title": "y",
            "skill": "agent",
            "planner": "llm",
        },
    ]
    return RecipeSpec.model_validate(
        {
            "name": name,
            "title": name,
            "description": "test",
            "stages": stages,
            "verifiers": verifiers or [],
            "repair_target_map": repair_target_map or {},
            "repair_policy": {"enabled": enabled, "max_rounds": max_rounds},
        }
    )


def _verification(verdicts: list[RecipeVerifierVerdict]) -> RecipeVerification:
    return RecipeVerification.from_verdicts(
        run_id="t1", recipe_name="demo", verdicts=verdicts
    )


def _failing_verdict(name: str, hint: str = "fix it") -> RecipeVerifierVerdict:
    return RecipeVerifierVerdict(
        name=name, passed=False, detail="failed", suggested_hint=hint
    )


def _passing_verdict(name: str) -> RecipeVerifierVerdict:
    return RecipeVerifierVerdict(name=name, passed=True, detail="ok")


def _skipped_verdict(name: str) -> RecipeVerifierVerdict:
    return RecipeVerifierVerdict(name=name, passed=True, detail="skip", skipped=True)


# ───────────────────────────────────── resolve_repair_target


def test_resolve_repair_target_uses_map_when_present() -> None:
    recipe = _recipe(
        repair_target_map={"coverage_verifier": "s1_organize"},
    )
    assert recipe.resolve_repair_target("coverage_verifier") == "s1_organize"


def test_resolve_repair_target_defaults_to_last_llm_stage() -> None:
    """A failure with no explicit mapping should re-plan the last LLM
    stage — that's the synth step in every shipped flagship."""
    recipe = _recipe()
    assert recipe.resolve_repair_target("any_verifier") == "s2_synth"


def test_resolve_repair_target_returns_none_when_no_llm_stage() -> None:
    """All-rule recipes have no synth step → can't auto-repair."""
    recipe = _recipe(
        stages=[
            {"stage_id": "s1", "title": "x", "skill": "folder_organizer"},
            {"stage_id": "s2", "title": "y", "skill": "data_analyzer"},
        ]
    )
    assert recipe.resolve_repair_target("coverage_verifier") is None


def test_resolve_repair_target_ignores_invalid_map_entry() -> None:
    recipe = _recipe(
        repair_target_map={"coverage_verifier": "nonexistent_stage"},
    )
    # Falls through to last LLM stage default.
    assert recipe.resolve_repair_target("coverage_verifier") == "s2_synth"


# ───────────────────────────────────── run_recipe_repair halt conditions


def test_returns_immediately_when_initial_verification_passed(tmp_path: Path) -> None:
    recipe = _recipe(verifiers=["v"])
    verification = _verification([_passing_verdict("v")])
    # No replay would be needed; we don't even need to mock.
    result = run_recipe_repair(
        recipe=recipe,
        graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
        run_store=_FakeRunStore(),
        initial_verification=verification,
    )
    assert isinstance(result, RecipeRepairResult)
    assert result.repaired is True
    assert result.rounds_used == 0
    assert result.halt_reason == "passed"
    assert result.attempts == []


def test_halts_when_only_skipped_or_hintless_failures(tmp_path: Path) -> None:
    recipe = _recipe(verifiers=["v1", "v2"])
    verification = _verification(
        [
            _skipped_verdict("v1"),
            RecipeVerifierVerdict(
                name="v2", passed=False, detail="bad", suggested_hint=None
            ),
        ]
    )
    result = run_recipe_repair(
        recipe=recipe,
        graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
        run_store=_FakeRunStore(),
        initial_verification=verification,
    )
    assert result.repaired is False
    assert result.rounds_used == 0
    assert result.halt_reason == "no_repairable_failures"


def test_halts_when_no_target_stage_resolvable(tmp_path: Path) -> None:
    """Recipe has zero LLM stages → resolve_repair_target returns
    None → loop halts with no_repairable_failures."""
    recipe = _recipe(
        verifiers=["v1"],
        stages=[
            {"stage_id": "s1", "title": "x", "skill": "folder_organizer"},
        ],
    )
    verification = _verification([_failing_verdict("v1")])
    result = run_recipe_repair(
        recipe=recipe,
        graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
        run_store=_FakeRunStore(),
        initial_verification=verification,
    )
    assert result.repaired is False
    assert result.halt_reason == "no_repairable_failures"


# ───────────────────────────────────── happy path: replay heals on round 1


def test_round_1_replay_heals(tmp_path: Path) -> None:
    """First repair attempt produces a workspace state that passes
    every verifier → halt_reason='passed', rounds_used=1."""
    recipe = _recipe(verifiers=["coverage_verifier"])
    initial = _verification([_failing_verdict("coverage_verifier", "re-plan")])

    fake_store = _FakeRunStore()

    # Mock replay (we don't have a real workspace).
    with patch(
        "app.harness.recipe_repair.replay_from_stage", return_value=None
    ), patch(
        "app.harness.recipe_repair.run_all",
        return_value=[_passing_verdict("coverage_verifier")],
    ), patch(
        "app.harness.recipe_repair._aggregate_moves", return_value={}
    ), patch(
        "app.harness.recipe_repair._aggregate_snapshot_inputs", return_value=[]
    ):
        result = run_recipe_repair(
            recipe=recipe,
            graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
            run_store=fake_store,
            initial_verification=initial,
        )

    assert result.repaired is True
    assert result.rounds_used == 1
    assert result.halt_reason == "passed"
    assert len(result.attempts) == 1
    a: RecipeRepairAttempt = result.attempts[0]
    assert a.triggered_by_verifier == "coverage_verifier"
    assert a.target_stage == "s2_synth"  # default to last LLM stage
    assert a.post_attempt_passed is True


def test_exhausted_after_max_rounds(tmp_path: Path) -> None:
    """Phase 21.1: distinct verifiers each fail and consume a round.
    With max_rounds=2 and 3 distinct failing verifiers, the first two
    are attempted, the loop hits the round cap, halt_reason='exhausted'.
    """
    recipe = _recipe(verifiers=["v1", "v2", "v3"], max_rounds=2)
    initial = _verification(
        [
            _failing_verdict("v1"),
            _failing_verdict("v2"),
            _failing_verdict("v3"),
        ]
    )

    with patch(
        "app.harness.recipe_repair.replay_from_stage", return_value=None
    ), patch(
        "app.harness.recipe_repair.run_all",
        return_value=[
            _failing_verdict("v1"),
            _failing_verdict("v2"),
            _failing_verdict("v3"),
        ],
    ), patch(
        "app.harness.recipe_repair._aggregate_moves", return_value={}
    ), patch(
        "app.harness.recipe_repair._aggregate_snapshot_inputs", return_value=[]
    ):
        result = run_recipe_repair(
            recipe=recipe,
            graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
            run_store=_FakeRunStore(),
            initial_verification=initial,
        )

    assert result.repaired is False
    assert result.rounds_used == 2
    assert result.halt_reason == "exhausted"
    assert len(result.attempts) == 2
    # Each round attempts a different verifier (no monopolisation).
    assert {a.triggered_by_verifier for a in result.attempts} == {"v1", "v2"}


def test_repair_skips_already_attempted_verifier(tmp_path: Path) -> None:
    """Phase 21.1 regression — a verifier that fails again with the
    same suggestion after its replay must NOT monopolise the loop. The
    next failing verifier should be picked instead, even if both are
    failing simultaneously."""
    recipe = _recipe(
        verifiers=["sticky_failure", "fixable"],
        repair_target_map={
            # Force distinct targets so we can tell from attempt history
            # which verifier each round picked.
            "sticky_failure": "s2_synth",
            "fixable": "s1_organize",
        },
        max_rounds=3,
    )
    initial = _verification(
        [_failing_verdict("sticky_failure"), _failing_verdict("fixable")]
    )

    # First replay: nothing changes. Second replay: clear 'fixable'.
    post_states = [
        # round 1 (sticky_failure picked) — both still failing
        [_failing_verdict("sticky_failure"), _failing_verdict("fixable")],
        # round 2 (fixable picked) — both still failing post-replay,
        # but only sticky_failure remains in attempted set
        [_failing_verdict("sticky_failure"), _failing_verdict("fixable")],
    ]
    call_idx = {"n": 0}

    def _next(*_a, **_kw):
        idx = call_idx["n"]
        call_idx["n"] += 1
        return post_states[idx] if idx < len(post_states) else post_states[-1]

    with patch(
        "app.harness.recipe_repair.replay_from_stage", return_value=None
    ), patch(
        "app.harness.recipe_repair.run_all", side_effect=_next
    ), patch(
        "app.harness.recipe_repair._aggregate_moves", return_value={}
    ), patch(
        "app.harness.recipe_repair._aggregate_snapshot_inputs", return_value=[]
    ):
        result = run_recipe_repair(
            recipe=recipe,
            graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
            run_store=_FakeRunStore(),
            initial_verification=initial,
        )

    # 2 rounds executed (one per distinct verifier), then no more
    # repairable failures → halt.
    assert result.rounds_used == 2
    assert result.halt_reason == "no_repairable_failures"
    triggers = [a.triggered_by_verifier for a in result.attempts]
    assert triggers == ["sticky_failure", "fixable"]


def test_replay_error_halts_with_explanation(tmp_path: Path) -> None:
    recipe = _recipe(verifiers=["v1"])
    initial = _verification([_failing_verdict("v1")])

    with patch(
        "app.harness.recipe_repair.replay_from_stage",
        side_effect=RuntimeError("rollback drift detected"),
    ):
        result = run_recipe_repair(
            recipe=recipe,
            graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
            run_store=_FakeRunStore(),
            initial_verification=initial,
        )

    assert result.repaired is False
    assert result.rounds_used == 1
    assert result.halt_reason == "replay_error"
    assert result.attempts[0].error is not None
    assert "rollback drift" in result.attempts[0].error


# ───────────────────────────────────── stage_hints wiring


def test_repair_loop_threads_hint_into_taskgraph(tmp_path: Path) -> None:
    """The hint reaches replay_from_stage via graph.stage_hints[target]."""
    recipe = _recipe(verifiers=["v1"])
    initial = _verification([_failing_verdict("v1", "use category names")])

    captured: dict = {}

    def _capture(*, graph, **_):
        captured["stage_hints"] = dict(graph.stage_hints)

    with patch(
        "app.harness.recipe_repair.replay_from_stage", side_effect=_capture
    ), patch(
        "app.harness.recipe_repair.run_all",
        return_value=[_passing_verdict("v1")],
    ), patch(
        "app.harness.recipe_repair._aggregate_moves", return_value={}
    ), patch(
        "app.harness.recipe_repair._aggregate_snapshot_inputs", return_value=[]
    ):
        run_recipe_repair(
            recipe=recipe,
            graph=recipe.compile_to_taskgraph(workspace_root=str(tmp_path)),
            run_store=_FakeRunStore(),
            initial_verification=initial,
        )

    assert captured["stage_hints"] == {"s2_synth": "use category names"}


def test_taskgraph_stage_hints_field_defaults_empty() -> None:
    """Backward-compat: a TaskGraph without stage_hints serialises +
    runs identically to before Phase 21."""
    from app.schemas import TaskGraph

    tg = TaskGraph(
        user_goal="x",
        workspace_root="/tmp",
        stages=[
            {
                "stage_id": "s1",
                "title": "x",
                "skill": "folder_organizer",
                "planner": "rule",
            }
        ],
    )
    assert tg.stage_hints == {}


# ───────────────────────────────────── helpers


class _FakeRunStore:
    """Minimal RunStore stub for tests that mock the actual filesystem."""

    task_id = "t-test"

    @property
    def stages_root(self) -> Path:
        return Path("/dev/null")

    @property
    def rollback_path(self) -> Path:
        return Path("/dev/null")
