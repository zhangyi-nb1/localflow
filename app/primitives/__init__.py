"""Phase 18 — Capability Primitives (v0.18.0).

Productisation guide §4.3: a thin layer above ``app.tools`` and below
``app.skills`` that exposes capabilities the LLM Goal Interpreter and
future verifier wrappers can refer to without naming a skill.

Implementations are deliberately minimal in Phase 18 — only the two
primitives the Goal Interpreter actually needs are real code; the
rest live in :mod:`app.primitives.catalog` as documented entries with
pointers to the tool / skill that already provides the behaviour.

See ``docs/CAPABILITIES.md`` for the full taxonomy + roadmap.
"""

from app.primitives._schemas import (
    Classification,
    Content,
    ContentKind,
    ContentRef,
    to_abs,
)
from app.primitives.catalog import (
    PrimitiveEntry,
    get_catalog,
    list_names,
)
from app.primitives.catalog import (
    get as get_primitive,
)
from app.primitives.classify_content import classify_content
from app.primitives.extract_content import extract_content

__all__ = [
    "Classification",
    "Content",
    "ContentKind",
    "ContentRef",
    "PrimitiveEntry",
    "classify_content",
    "extract_content",
    "get_catalog",
    "get_primitive",
    "list_names",
    "to_abs",
]
