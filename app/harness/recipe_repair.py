"""Phase 21 — recipe-level auto-repair loop (v0.21.0).

Closes the loop the productisation guide §2.5 flagged: Phase 19's
deliverable verifiers attach a typed ``suggested_hint`` to every
failure, but those hints used to be display-only. The recipe repair
loop now consumes them:

  1. ``pack run`` finishes; recipe verification has failed verdicts.
  2. ``recipe.repair_policy.enabled`` is true (or the user passed
     ``--enable-repair``).
  3. For each round (capped by ``repair_policy.max_rounds``, ≤ 3):
       a. Pick the first non-skipped fail verdict with a hint.
       b. Resolve the target stage via
          ``RecipeSpec.resolve_repair_target(verifier_name)``.
       c. Build a TaskGraph copy with ``stage_hints[target] = hint``.
       d. Call ``replay_from_stage`` — rolls back affected entries +
          replays the stage with ``skill.plan_with_llm(user_hint=...)``.
       e. Re-run every recipe verifier listed in
          ``recipe.verifiers``. If everything passes, halt.
  4. Persist ``<run_dir>/recipe_repair.json`` with the full attempt
     history.

§10.7 invariant: pure orchestration over existing kernel primitives
(rollback, taskgraph_runner). Zero edits to executor / verifier /
policy_guard. **28th** zero-kernel-touch phase target.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.eval.recipe_verifiers import (
    RecipeVerification,
    RecipeVerifierContext,
    run_all,
)
from app.harness.taskgraph_runner import replay_from_stage
from app.schemas import RollbackManifest
from app.schemas.rollback import RollbackOpType

if TYPE_CHECKING:
    from app.harness.trace import TraceLogger
    from app.schemas import RecipeSpec, TaskGraph
    from app.storage.run_store import RunStore


class RecipeRepairAttempt(BaseModel):
    """One round of the recipe-level repair loop."""

    attempt: int = Field(..., ge=1)
    """1-indexed round number (matches the user-facing display)."""

    triggered_by_verifier: str
    """Name of the verifier whose failure kicked off this round."""

    suggested_hint: str
    """The hint we fed to the planner as ``user_hint``."""

    target_stage: str
    """The stage_id we replayed from."""

    pre_attempt_passed: bool = False
    post_attempt_passed: bool = False
    failed_after_attempt: list[str] = Field(
        default_factory=list,
        description="Verifier names still failing after this round.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Set when the replay raised — e.g. drift-halt from "
            "replay_from_stage. The loop halts and reports."
        ),
    )
    duration_ms: int = 0


class RecipeRepairResult(BaseModel):
    """Aggregate result of :func:`run_recipe_repair`."""

    repaired: bool
    """True iff the FINAL recipe verification passed."""

    rounds_used: int
    """Number of repair rounds executed (0 when verification already
    passed, ≤ ``repair_policy.max_rounds`` otherwise)."""

    halt_reason: str
    """Human-readable. Values:
      - 'passed' (verification cleared, repaired=True)
      - 'exhausted' (max_rounds reached without converging)
      - 'no_hint_or_target' (fail verdicts emit no suggested_hint, or
         no LLM stage exists to replay)
      - 'all_attempted_without_repair' (every unique failing verifier
         has been tried once and the failure persists — v0.22.1 split
         from the old catch-all 'no_repairable_failures')
      - 'no_repairable_failures' (defensive catch-all)
      - 'replay_error' (a stage replay raised)
    """

    attempts: list[RecipeRepairAttempt] = Field(default_factory=list)
    final_verification: RecipeVerification | None = None
    """The verification result after the last round. None when no
    rounds ran (e.g. nothing to repair)."""

    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


def _aggregate_moves(run_store: "RunStore") -> dict[str, str]:
    """Reproduce the CLI's move aggregation logic in one place so the
    repair loop's RecipeVerifierContext matches what `pack run`
    rendered originally."""
    moves: dict[str, str] = {}
    if not run_store.rollback_path.exists():
        return moves
    try:
        manifest = run_store.read_model(run_store.rollback_path, RollbackManifest)
    except Exception:
        return moves
    for entry in manifest.entries:
        if (
            entry.op is RollbackOpType.MOVE_BACK
            and entry.source_path
            and entry.target_path
        ):
            moves[entry.target_path] = entry.source_path
    return moves


def _aggregate_snapshot_inputs(run_store: "RunStore") -> list[str]:
    """Read the first stage's pre-execute workspace snapshot (same
    source the CLI uses for ``snapshot_inputs`` on first run)."""
    if not run_store.stages_root.exists():
        return []
    stage_dirs = sorted(
        d for d in run_store.stages_root.iterdir() if d.is_dir()
    )
    if not stage_dirs:
        return []
    snap_path = stage_dirs[0] / "workspace_snapshot.json"
    if not snap_path.exists():
        return []
    try:
        from app.schemas import WorkspaceSnapshot

        snap = run_store.read_model(snap_path, WorkspaceSnapshot)
    except Exception:
        return []
    return [f.path for f in snap.files]


def _build_context(
    *,
    recipe: "RecipeSpec",
    graph: "TaskGraph",
    run_store: "RunStore",
    task_graph_result=None,
) -> RecipeVerifierContext:
    """Reconstruct the verifier context after a repair round so the
    next verifier pass sees the post-repair workspace state + moves."""
    return RecipeVerifierContext(
        recipe=recipe,
        workspace_path=Path(graph.workspace_root).resolve(),
        snapshot_inputs=_aggregate_snapshot_inputs(run_store),
        moves=_aggregate_moves(run_store),
        task_graph_result=task_graph_result,
        run_id=run_store.task_id,
        # v0.22 — graph already carries the locale (TaskGraph schema
        # field); propagate it so the post-repair verifier pass uses
        # the same language as the user-facing prose.
        locale=graph.locale,
    )


def _pick_repair_target(
    recipe: "RecipeSpec",
    verification: RecipeVerification,
    attempted_verifiers: set[str] | None = None,
) -> tuple["RecipeVerifierVerdict_TriggerInfo | None", str]:
    """Pick the first repairable verdict and resolve its target stage.

    'Repairable' = passed=False AND skipped=False AND suggested_hint
    is non-empty AND a target stage can be resolved AND the verifier
    wasn't already attempted in a prior round. Returns ``(None, reason)``
    when nothing repairable remains — the loop halts cleanly.

    Phase 21.1: ``attempted_verifiers`` tracks verifier names that
    have already triggered a replay. Without this, one persistently
    failing verifier (e.g. ``deliverable_completeness_verifier`` when
    no LLM key is configured) would monopolise every round and starve
    other repairable failures (e.g. ``review_queue_verifier``). With
    it, each verifier gets one shot — if its replay didn't clear it,
    the loop moves to the next repairable failure instead of looping
    on the same suggestion.

    v0.22.1: when nothing repairable is found, a categorised reason is
    returned so the caller can distinguish "verifiers fail but emit no
    hints / no target stage" (genuinely unactionable) from "every fail
    verdict has been tried once" (the loop ran out of fresh angles).
    """
    attempted = attempted_verifiers or set()
    fail_verdicts = [v for v in verification.verdicts if not v.passed and not v.skipped]
    if not fail_verdicts:
        return None, "passed"  # caller short-circuits earlier; defensive.
    hint_or_target_missing = False
    all_attempted = True
    valid_stage_ids = {s.stage_id for s in recipe.stages}
    for verdict in fail_verdicts:
        if verdict.name in attempted:
            continue
        all_attempted = False
        hint = (verdict.suggested_hint or "").strip()
        if not hint:
            hint_or_target_missing = True
            continue
        # v0.22.x — prefer the verdict's typed ``repair_target_stage``
        # when the verifier can identify the exact producer of the
        # failing artefact (e.g. deliverable_completeness_verifier).
        # Falls back to ``recipe.resolve_repair_target`` (repair_target_map
        # or last-LLM-stage default) when the verdict doesn't pin a stage.
        target = (
            verdict.repair_target_stage
            if verdict.repair_target_stage in valid_stage_ids
            else recipe.resolve_repair_target(verdict.name)
        )
        if target is None:
            hint_or_target_missing = True
            continue
        return (
            RecipeVerifierVerdict_TriggerInfo(
                verifier_name=verdict.name,
                hint=hint,
                target_stage=target,
            ),
            "ok",
        )
    if all_attempted:
        return None, "all_attempted_without_repair"
    if hint_or_target_missing:
        return None, "no_hint_or_target"
    return None, "no_repairable_failures"


class RecipeVerifierVerdict_TriggerInfo:
    """Small internal record returned by :func:`_pick_repair_target`."""

    __slots__ = ("verifier_name", "hint", "target_stage")

    def __init__(self, *, verifier_name: str, hint: str, target_stage: str) -> None:
        self.verifier_name = verifier_name
        self.hint = hint
        self.target_stage = target_stage


def run_recipe_repair(
    *,
    recipe: "RecipeSpec",
    graph: "TaskGraph",
    run_store: "RunStore",
    initial_verification: RecipeVerification,
    trace: "TraceLogger | None" = None,
) -> RecipeRepairResult:
    """Drive the recipe-level repair loop.

    Returns a :class:`RecipeRepairResult` describing every attempt and
    the final verification. The caller is responsible for persisting
    the result + re-rendering the verdict table.

    Halt conditions (any one ends the loop):
      - ``initial_verification.passed`` already true → 0 rounds run,
        halt_reason='passed'.
      - All fail verdicts are skipped or hint-less or have no
        resolvable target → halt_reason='no_repairable_failures'.
      - max_rounds rounds executed without converging →
        halt_reason='exhausted'.
      - Any round's replay raised → halt_reason='replay_error'.
    """
    result = RecipeRepairResult(
        repaired=False,
        rounds_used=0,
        halt_reason="passed",
        attempts=[],
        final_verification=initial_verification,
    )

    if initial_verification.passed:
        result.repaired = True
        result.completed_at = datetime.now(timezone.utc)
        return result

    max_rounds = recipe.repair_policy.max_rounds
    current_verification = initial_verification
    attempted_verifiers: set[str] = set()

    for round_idx in range(1, max_rounds + 1):
        trigger, no_trigger_reason = _pick_repair_target(
            recipe, current_verification, attempted_verifiers
        )
        if trigger is None:
            # v0.22.1 — surface a more specific halt_reason so the user
            # knows whether the verifiers were unactionable, every fix
            # had been tried, or repair just plain ran out of work.
            result.halt_reason = no_trigger_reason
            break

        attempted_verifiers.add(trigger.verifier_name)
        round_started = datetime.now(timezone.utc)
        # Build a graph with the hint plumbed in for the target stage.
        hinted_graph = graph.model_copy(
            update={
                "stage_hints": {
                    **dict(graph.stage_hints),
                    trigger.target_stage: trigger.hint,
                }
            }
        )

        attempt = RecipeRepairAttempt(
            attempt=round_idx,
            triggered_by_verifier=trigger.verifier_name,
            suggested_hint=trigger.hint,
            target_stage=trigger.target_stage,
            pre_attempt_passed=False,
        )

        try:
            replay_from_stage(
                graph=hinted_graph,
                run_store=run_store,
                from_stage=trigger.target_stage,
                trace=trace,
            )
        except Exception as exc:  # noqa: BLE001 — record + halt cleanly.
            attempt.error = f"{type(exc).__name__}: {exc}"
            attempt.duration_ms = int(
                (datetime.now(timezone.utc) - round_started).total_seconds() * 1000
            )
            result.attempts.append(attempt)
            result.rounds_used = round_idx
            result.halt_reason = "replay_error"
            result.completed_at = datetime.now(timezone.utc)
            return result

        # Re-run verifiers against the post-replay state.
        post_ctx = _build_context(
            recipe=recipe, graph=hinted_graph, run_store=run_store
        )
        post_verdicts = run_all(list(recipe.verifiers), post_ctx)
        current_verification = RecipeVerification.from_verdicts(
            run_id=run_store.task_id,
            recipe_name=recipe.name,
            verdicts=post_verdicts,
        )

        attempt.post_attempt_passed = current_verification.passed
        attempt.failed_after_attempt = [
            v.name
            for v in current_verification.verdicts
            if not v.passed and not v.skipped
        ]
        attempt.duration_ms = int(
            (datetime.now(timezone.utc) - round_started).total_seconds() * 1000
        )
        result.attempts.append(attempt)
        result.rounds_used = round_idx
        result.final_verification = current_verification

        if current_verification.passed:
            result.repaired = True
            result.halt_reason = "passed"
            result.completed_at = datetime.now(timezone.utc)
            return result

    # Loop exited without converging.
    if result.halt_reason == "passed":
        result.halt_reason = "exhausted"
    result.final_verification = current_verification
    result.completed_at = datetime.now(timezone.utc)
    return result
