"""Phase 17 — Recipe / Pack System (v0.17.0).

Top-level entry points re-exported here so callers can do::

    from app.recipes import RecipeRegistry, get_default_registry, RecipeRouter

without having to remember submodule paths. See ``docs/RECIPES.md``
for the user-facing product concept and ``app/schemas/recipe.py`` for
the schema.
"""

from app.recipes.registry import (
    DEFAULT_RECIPES_DIR,
    RecipeError,
    RecipeNotFound,
    RecipeRegistry,
    get_default_registry,
)
from app.recipes.router import RecipeRouter, RecipeScore

__all__ = [
    "DEFAULT_RECIPES_DIR",
    "RecipeError",
    "RecipeNotFound",
    "RecipeRegistry",
    "RecipeRouter",
    "RecipeScore",
    "get_default_registry",
]
