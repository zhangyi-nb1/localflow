"""Phase 19 — recipe verifier registry.

Mirror of :mod:`app.eval.graders` minus the eval-runner coupling.
A recipe verifier is a pure ``(RecipeVerifierContext) ->
RecipeVerifierVerdict`` function registered via the ``@register``
decorator.
"""

from __future__ import annotations

from typing import Callable

from app.eval.recipe_verifiers._schema import (
    RecipeVerifierContext,
    RecipeVerifierVerdict,
)

VerifierFn = Callable[[RecipeVerifierContext], RecipeVerifierVerdict]

_REGISTRY: dict[str, VerifierFn] = {}


class RecipeVerifierError(RuntimeError):
    """Raised when registering or running a verifier hits a bug."""


class RecipeVerifierNotFound(KeyError):
    """Raised by :func:`get` when no verifier with the requested name
    exists. Kept distinct from RecipeVerifierError so callers can
    surface "did you mean…?" hints cleanly."""


def register(name: str) -> Callable[[VerifierFn], VerifierFn]:
    """Decorator: ``@register("coverage_verifier")``."""

    def deco(fn: VerifierFn) -> VerifierFn:
        if name in _REGISTRY:
            raise RecipeVerifierError(f"recipe verifier {name!r} already registered")
        _REGISTRY[name] = fn
        return fn

    return deco


def get(name: str) -> VerifierFn:
    if name not in _REGISTRY:
        raise RecipeVerifierNotFound(name)
    return _REGISTRY[name]


def list_names() -> list[str]:
    return sorted(_REGISTRY)


def run_all(
    names: list[str], ctx: RecipeVerifierContext
) -> list[RecipeVerifierVerdict]:
    """Run every named verifier against the same context.

    Unknown names produce a failed verdict with a clear ``detail`` —
    we don't raise, so a typo in a recipe.verifiers list doesn't
    abort the entire pack verification phase.
    """
    out: list[RecipeVerifierVerdict] = []
    for name in names:
        try:
            fn = get(name)
        except RecipeVerifierNotFound:
            out.append(
                RecipeVerifierVerdict(
                    name=name,
                    passed=False,
                    detail=(
                        f"verifier {name!r} not registered; "
                        f"available: {', '.join(list_names())}"
                    ),
                )
            )
            continue
        try:
            verdict = fn(ctx)
        except Exception as exc:  # noqa: BLE001 — surface every bug
            verdict = RecipeVerifierVerdict(
                name=name,
                passed=False,
                detail=f"verifier raised: {type(exc).__name__}: {exc}",
            )
        out.append(verdict)
    return out
