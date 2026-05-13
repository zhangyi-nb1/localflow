"""Phase 6.1 — JSON-safe serialization for MCP tool returns.

Every MCP tool returns a dict (or list/scalar) that the SDK will then
``json.dumps``. Most of LocalFlow's return types are Pydantic, which
handles datetimes/enums via ``model_dump(mode="json")``. The notable
exception is ``ExecutionOutcome`` — a frozen ``dataclasses.dataclass``
from [app/harness/executor.py](app/harness/executor.py). For those we
fall back to ``dataclasses.asdict`` then recursively normalize.

This module is **read-only** with respect to the harness — it never
mutates objects, only inspects them. Stays out of ``app/harness/`` /
``app/schemas/`` to keep Phase 6.1 a zero-kernel-change phase.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime
from enum import Enum
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel


def to_jsonable(obj: Any) -> Any:
    """Recursively convert ``obj`` into something ``json.dumps`` accepts.

    Rules (first match wins):
      * ``None / bool / int / float / str``  → unchanged
      * ``BaseModel``                         → ``model_dump(mode="json")``
                                                (datetimes → ISO, enums → value,
                                                 Path → str, all built-in)
      * ``dataclass`` instance                → ``asdict`` then recurse
      * ``Enum``                              → ``.value``
      * ``datetime / date``                   → ``isoformat()``
      * ``Path / PurePath``                   → ``str(p)``
      * ``Mapping``                           → ``{str(k): to_jsonable(v), ...}``
      * ``list / tuple / set / frozenset``    → list of recursively converted
      * fallback                              → ``str(obj)`` (last-ditch)
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj

    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(dataclasses.asdict(obj))

    if isinstance(obj, Enum):
        return obj.value

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()

    if isinstance(obj, (Path, PurePath)):
        return str(obj)

    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]

    # Last resort — readable repr rather than crash.
    return str(obj)
