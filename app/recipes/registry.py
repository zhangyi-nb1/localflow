"""Phase 17 — RecipeRegistry (v0.17.0).

Loads ``recipes/*.yaml`` from a directory (repo-default or user-supplied)
and offers list / lookup operations. Mirrors the
:class:`app.skills.SkillRegistry` pattern: lazy-load on first call,
cache, expose ``get`` / ``list_names`` / ``all``.

A "recipe" here means a typed :class:`RecipeSpec` parsed from YAML.
The router lives in a sibling module so the registry stays a pure
catalog with no scoring logic.

§10.7 invariant: this module is application-layer, not kernel — it
imports schemas but never the executor / verifier / rollback.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from app.schemas import RecipeSpec

DEFAULT_RECIPES_DIR = Path(__file__).resolve().parent.parent.parent / "recipes"
"""Repo-level ``recipes/`` directory. User installs can override via the
``LOCALFLOW_RECIPES_DIR`` env var or the ``recipes_dir=`` constructor arg."""


class RecipeError(RuntimeError):
    """Raised when a recipe YAML fails to load or validate."""


class RecipeNotFound(KeyError):
    """Raised by :meth:`RecipeRegistry.get` when no recipe with the
    requested name exists. Kept distinct from RecipeError so callers
    can offer "did you mean…?" suggestions cleanly."""


class RecipeRegistry:
    """In-memory catalog of every :class:`RecipeSpec` in a directory.

    Loading is lazy and idempotent — the registry rescans only when
    :meth:`reload` is called, so a long-running UI process doesn't pay
    for filesystem I/O on every page render.
    """

    def __init__(self, recipes_dir: Path | str | None = None) -> None:
        if recipes_dir is None:
            env = os.environ.get("LOCALFLOW_RECIPES_DIR")
            recipes_dir = Path(env) if env else DEFAULT_RECIPES_DIR
        self.recipes_dir: Path = Path(recipes_dir)
        self._cache: dict[str, RecipeSpec] | None = None
        self._load_errors: list[tuple[Path, str]] = []

    def _load(self) -> dict[str, RecipeSpec]:
        cache: dict[str, RecipeSpec] = {}
        self._load_errors = []
        if not self.recipes_dir.exists():
            self._cache = cache
            return cache
        for path in sorted(self.recipes_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if raw is None:
                    raise RecipeError("empty YAML file")
                spec = RecipeSpec.model_validate(raw)
            except Exception as exc:
                self._load_errors.append((path, str(exc)))
                continue
            if spec.name in cache:
                self._load_errors.append(
                    (path, f"duplicate recipe name {spec.name!r} (already loaded)")
                )
                continue
            cache[spec.name] = spec
        self._cache = cache
        return cache

    def reload(self) -> None:
        """Discard the cache and rescan ``recipes_dir`` on next access."""
        self._cache = None
        self._load_errors = []

    @property
    def load_errors(self) -> list[tuple[Path, str]]:
        """List of (file_path, error_message) pairs from the last load.

        Surfaced by ``localflow pack list`` so a broken YAML doesn't
        silently disappear from the catalog.
        """
        if self._cache is None:
            self._load()
        return list(self._load_errors)

    def all(self) -> list[RecipeSpec]:
        """Every successfully-loaded recipe, sorted by name."""
        if self._cache is None:
            self._load()
        assert self._cache is not None
        return [self._cache[k] for k in sorted(self._cache)]

    def list_names(self) -> list[str]:
        """Just the names — handy for completion / argument validation."""
        if self._cache is None:
            self._load()
        assert self._cache is not None
        return sorted(self._cache)

    def get(self, name: str) -> RecipeSpec:
        """Look up a recipe by name. Raises :class:`RecipeNotFound`."""
        if self._cache is None:
            self._load()
        assert self._cache is not None
        if name not in self._cache:
            raise RecipeNotFound(name)
        return self._cache[name]

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        if self._cache is None:
            self._load()
        assert self._cache is not None
        return name in self._cache


_default_registry: RecipeRegistry | None = None


def get_default_registry() -> RecipeRegistry:
    """Process-wide singleton pointing at the repo's ``recipes/`` dir.

    Tests should construct their own :class:`RecipeRegistry` with a
    custom directory rather than poking this singleton.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = RecipeRegistry()
    return _default_registry
