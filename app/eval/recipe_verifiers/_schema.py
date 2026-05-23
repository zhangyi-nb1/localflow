"""Phase 19 — schemas for recipe-level Deliverable Verifiers.

Kept deliberately small. A recipe verifier is a pure function

    (RecipeVerifierContext) -> RecipeVerifierVerdict

so the registry maps name → callable with no introspection magic.
``RecipeVerification`` is the on-disk envelope the CLI writes to
``<run_dir>/recipe_verification.json``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.schemas import RecipeSpec, TaskGraphResult
from app.schemas.task import DEFAULT_LOCALE, Locale


class RecipeVerifierContext(BaseModel):
    """Read-only snapshot every verifier sees.

    Pydantic so it serialises cleanly into the trace if a verifier
    wants to log itself, but the field set is closed (``model_config
    forbidden`` — see ``model_config`` below) so contributors don't
    accidentally widen the contract.
    """

    recipe: RecipeSpec = Field(..., description="The compiled recipe.")
    workspace_path: Path = Field(
        ...,
        description="Absolute path to the workspace directory the pack ran against.",
    )
    snapshot_inputs: list[str] = Field(
        default_factory=list,
        description=(
            "Workspace-relative paths of every file that existed BEFORE the "
            "pack ran. Captured by the runner from the first stage's "
            "WorkspaceSnapshot. Used by CoverageVerifier."
        ),
    )
    moves: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of original_path -> final_path for every successfully-"
            "executed MOVE / RENAME action across every stage. The runner "
            "aggregates this from the per-stage rollback manifests."
        ),
    )
    task_graph_result: TaskGraphResult | None = Field(
        default=None,
        description="The runner's verdict; included so verifiers can short-circuit "
        "on aborted runs (e.g. ChartDataConsistency skips if no stage actually "
        "executed).",
    )
    run_id: str | None = None
    locale: Locale = Field(
        default=DEFAULT_LOCALE,
        description=(
            "v0.22 — language for user-facing verifier prose (judge "
            "rationale, suggested_hint). Threaded from the originating "
            "TaskGraph by ``_run_recipe_verifiers``. Defaults to zh-CN."
        ),
    )

    model_config = {"arbitrary_types_allowed": True}


class RecipeVerifierVerdict(BaseModel):
    """One verifier's decision for one pack run.

    Mirrors :class:`app.eval.schema.GraderVerdict` so the CLI can
    render both verdict types with the same code, but lives in its
    own type so the recipe-level layer can evolve independently
    (Phase 20+ may add ``suggested_repair_action`` etc.).
    """

    name: str
    passed: bool
    detail: str = ""
    score: float | None = None
    suggested_hint: str | None = Field(
        default=None,
        description=(
            "Optional one-sentence repair instruction for the planner LLM "
            "(used by the Phase 13 repair loop when a recipe stage opts "
            "into REPAIR failure policy)."
        ),
    )
    skipped: bool = Field(
        default=False,
        description=(
            "True when the verifier short-circuited (missing LLM key, "
            "irrelevant artefact, etc.). A skipped verdict counts as a "
            "pass for aggregation but is reported separately so users see "
            "why coverage is incomplete."
        ),
    )
    repair_target_stage: str | None = Field(
        default=None,
        description=(
            "v0.22.x — optional override for ``RecipeSpec.resolve_repair_target``. "
            "A verifier that can identify the exact stage responsible for the "
            "failure (e.g. ``deliverable_completeness_verifier`` knows which "
            "stage owns the missing path) sets this so the recipe repair loop "
            "doesn't fall back to the last-LLM-stage default. Leave None to "
            "use the recipe's ``repair_target_map`` lookup."
        ),
    )


class RecipeVerification(BaseModel):
    """On-disk envelope at ``<run_dir>/recipe_verification.json``.

    Contains the per-verifier verdicts plus an aggregate ``passed`` so
    the CLI / UI can render a single badge without re-aggregating.
    """

    run_id: str
    recipe_name: str
    passed: bool
    verdicts: list[RecipeVerifierVerdict]
    skipped_count: int = 0
    failed_count: int = 0

    @classmethod
    def from_verdicts(
        cls,
        *,
        run_id: str,
        recipe_name: str,
        verdicts: list[RecipeVerifierVerdict],
    ) -> "RecipeVerification":
        """Compute the aggregate ``passed`` flag.

        A pack passes verification when every non-skipped verdict is
        passed=True. Skipped verdicts don't fail the pack — they
        decrement coverage instead.
        """
        failed = sum(1 for v in verdicts if not v.passed and not v.skipped)
        skipped = sum(1 for v in verdicts if v.skipped)
        return cls(
            run_id=run_id,
            recipe_name=recipe_name,
            passed=failed == 0,
            verdicts=verdicts,
            skipped_count=skipped,
            failed_count=failed,
        )


__all__ = [
    "RecipeVerification",
    "RecipeVerifierContext",
    "RecipeVerifierVerdict",
]
