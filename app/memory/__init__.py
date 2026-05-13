"""Phase 5 — Memory & user preferences.

Lightweight, opt-in persistence of user preferences across LocalFlow
runs. Inspired by Mem0 (outline §13.7) but **deliberately scoped**:
LocalFlow's iron rule is "memory must be opt-in, visible in plan,
audited, rollbackable" — not "remember anything the LLM noticed".

MVP categories (outline §14 Phase 5):
  * forbidden_paths — universal safety primitive (kernel-enforced)
  * naming_style    — folder_organizer rename target transform

Deferred to Phase 5.x: folder structure pref, report template, common
task recipes — each adds a new field to MemoryPreferences plus one
consumer wiring.
"""
from app.memory._schema import MemoryPreferences, NamingStyle
from app.memory._store import (
    MemoryStore,
    MemoryStoreError,
    MutationResult,
)
from app.memory.naming import apply_naming_style

__all__ = [
    "MemoryPreferences",
    "MemoryStore",
    "MemoryStoreError",
    "MutationResult",
    "NamingStyle",
    "apply_naming_style",
]
