"""Phase 17 — RecipeRouter (v0.17.0).

Maps a user goal + (optionally) a workspace snapshot to the most
likely Recipe. Implements the productisation guide §6.2 "Delivery
Planner" entry point: *"根据用户目标选择 recipe"*.

Phase 17 is **deterministic**: keyword matches against
``InputExpectation.keywords`` plus a file-type fit score against the
workspace. No LLM. The LLM clarifying-question path lives in Phase 18
(Goal Interpreter); the router stays simple here so it's testable +
free.

Scoring rule (intentionally easy to audit):
    score = (keyword hits × 2)
          + (file-kind matches, capped at 5)
          - (10 if min_files violated)
          - (5  if require_any violated)

A negative score means "unsuitable"; positive means "consider". The
top scorer wins, ties broken by recipe name (alphabetical) for
determinism.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.recipes.registry import RecipeRegistry, get_default_registry
from app.schemas import RecipeSpec, WorkspaceSnapshot


@dataclass(frozen=True)
class RecipeScore:
    """One recipe's fit assessment. ``why`` is a list of one-line
    rationales the CLI / UI surfaces under "Suggested pack: …"."""

    recipe: RecipeSpec
    score: int
    why: list[str] = field(default_factory=list)

    @property
    def is_suitable(self) -> bool:
        """Whether the router considers this recipe applicable.

        Strictly positive — a score of 0 means no signals lined up,
        which the UI should NOT surface as a recommendation."""
        return self.score > 0


class RecipeRouter:
    """Stateless keyword + file-type matcher over a RecipeRegistry.

    Used by ``localflow pack run --auto`` and the UI's "Suggest a pack"
    button. Holds no state between calls so it's safe to share across
    threads / requests.
    """

    def __init__(self, registry: RecipeRegistry | None = None) -> None:
        self.registry = registry or get_default_registry()

    def score_all(
        self,
        *,
        user_goal: str,
        snapshot: WorkspaceSnapshot | None = None,
    ) -> list[RecipeScore]:
        """Rank every loaded recipe against (goal, snapshot).

        Both arguments are advisory: an empty goal still produces
        scores (file-type signals only), and a missing snapshot still
        produces scores (keyword signals only).
        """
        goal_lower = (user_goal or "").lower()
        kind_counts: Counter[str] = Counter()
        total_files = 0
        if snapshot is not None:
            for f in snapshot.files:
                kind_counts[f.file_type] += 1
            total_files = len(snapshot.files)

        scores: list[RecipeScore] = []
        for recipe in self.registry.all():
            score = 0
            why: list[str] = []
            exp = recipe.input_expectation

            # 1. Keyword hits against the user's goal text.
            hits = [kw for kw in exp.keywords if kw and kw.lower() in goal_lower]
            if hits:
                score += 2 * len(hits)
                why.append(f"goal mentions: {', '.join(hits)}")

            # 2. File-kind matches against the workspace snapshot.
            if snapshot is not None and exp.file_kinds:
                matched = [k for k in exp.file_kinds if kind_counts.get(k, 0) > 0]
                if matched:
                    score += min(len(matched), 5)
                    counts = ", ".join(f"{k}={kind_counts[k]}" for k in matched)
                    why.append(f"workspace has: {counts}")

            # 3. Hard penalties for unmet requirements.
            if snapshot is not None and total_files < exp.min_files:
                score -= 10
                why.append(f"only {total_files} file(s) (min {exp.min_files})")
            if snapshot is not None and exp.require_any:
                if not any(kind_counts.get(k, 0) > 0 for k in exp.require_any):
                    score -= 5
                    why.append(
                        f"missing one of: {', '.join(exp.require_any)}"
                    )

            scores.append(RecipeScore(recipe=recipe, score=score, why=why))

        # Determinism: high score first, then alphabetical by name.
        scores.sort(key=lambda s: (-s.score, s.recipe.name))
        return scores

    def best_match(
        self,
        *,
        user_goal: str,
        snapshot: WorkspaceSnapshot | None = None,
    ) -> RecipeScore | None:
        """Return the single top recipe, or ``None`` if no recipe is suitable.

        Suitable = score > 0. We deliberately refuse to silently pick a
        zero-score recipe — the CLI surfaces "no recipe matched, list
        with `localflow pack list`" instead."""
        ranked = self.score_all(user_goal=user_goal, snapshot=snapshot)
        if not ranked:
            return None
        top = ranked[0]
        return top if top.is_suitable else None
