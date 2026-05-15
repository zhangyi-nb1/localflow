"""Grader registry + the v0.10.0 starter set.

A grader is a pure function ``(GraderContext) -> GraderVerdict``. The
registry is populated by ``@register`` decorators at import time of
the bundled grader modules (currently :mod:`app.eval.graders.structural`).

External graders can register from user code by importing this module
and calling ``register("my_grader")`` themselves. They cross the
trust boundary the same way external skills do (see SECURITY.md);
v0.10.0 doesn't apply any sandboxing.
"""

from __future__ import annotations

from typing import Callable

from app.eval.schema import GraderContext, GraderVerdict

GraderFn = Callable[[GraderContext], GraderVerdict]
_REGISTRY: dict[str, GraderFn] = {}


def register(name: str) -> Callable[[GraderFn], GraderFn]:
    """Decorator: ``@register("safety_no_forbidden_path") def grader(ctx): ...``"""

    def deco(fn: GraderFn) -> GraderFn:
        if name in _REGISTRY:
            raise ValueError(f"grader {name!r} already registered")
        _REGISTRY[name] = fn
        return fn

    return deco


def get(name: str) -> GraderFn:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown grader {name!r}; registered: {', '.join(sorted(_REGISTRY)) or '(none)'}"
        )
    return _REGISTRY[name]


def list_names() -> list[str]:
    return sorted(_REGISTRY)


# Import the built-in graders at module load so ``@register`` fires.
from app.eval.graders import semantic, structural  # noqa: E402,F401
