"""Phase 19 — Recipe-level Deliverable Verifiers (v0.19.0).

Productisation guide §10 lists 7 verifiers as the highest-leverage
next-step investment ("不要只扩大 Skill，更要扩大 Verifier"). This
package ships all 7, named exactly as the guide requested:

  Structural (deterministic, no LLM):
    1. coverage_verifier              — every input file moved OR cited
    2. source_ledger_verifier         — cited sources resolve to real files
    3. review_queue_verifier          — low-confidence files not force-classified
    4. deliverable_completeness_verifier — recipe.expected_outputs all present

  Semantic (LLM-as-judge; graceful skip without a client):
    5. summary_grounding_verifier     — summary claims traceable to source files
    6. chart_data_consistency_verifier — chart numbers match CSV statistics
    7. topic_coherence_verifier       — topic dirs semantically coherent

§10.7 invariant: zero kernel changes. Recipe verifiers live next to
the v0.10 eval graders and are wired into pack execution via
``app.recipes`` (Phase 17) + the CLI, not the harness kernel.

The recipe-level layer is intentionally separate from the eval-runner
GraderContext: a pack run produces a TaskGraph result (multiple per-
stage plans, one aggregated rollback), so the per-skill GraderContext
shape doesn't fit. The new :class:`RecipeVerifierContext` carries just
the workspace + the recipe + the run artifacts — verifiers inspect
files, never plan internals.
"""

# Import side-effect: registers every built-in verifier on package load.
# Phase 36 adds ``grounding`` (claim_grounding_verifier — the flagship gate).
from app.eval.recipe_verifiers import grounding, semantic, structural  # noqa: E402,F401
from app.eval.recipe_verifiers._registry import (
    RecipeVerifierError,
    RecipeVerifierNotFound,
    get,
    list_names,
    register,
    run_all,
)
from app.eval.recipe_verifiers._schema import (
    RecipeVerification,
    RecipeVerifierContext,
    RecipeVerifierVerdict,
)

__all__ = [
    "RecipeVerification",
    "RecipeVerifierContext",
    "RecipeVerifierError",
    "RecipeVerifierNotFound",
    "RecipeVerifierVerdict",
    "get",
    "list_names",
    "register",
    "run_all",
]
