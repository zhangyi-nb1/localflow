"""Phase 5 — Memory preferences schema.

Typed user preferences persisted to ``~/.localflow/memory/prefs.json``.

The MVP ships two categories from outline §14 Phase 5:
  * ``forbidden_paths`` — universal safety primitive (kernel-enforced).
  * ``naming_style``    — folder_organizer's rename target transform.

The other three outline categories (folder structure, report template,
common task recipes) are deferred to Phase 5.x — adding them is a matter
of growing this schema (and bumping ``schema_version``) plus wiring a
new consumer site.

A preference is **never** consumed implicitly: each consumer reads it
explicitly (kernel reads ``forbidden_paths`` from TaskSpec;
folder_organizer reads ``naming_style`` from TaskSpec.preferences). The
CLI logs every applied preference so users see when memory is influencing
a run.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class NamingStyle(str, Enum):
    """Renaming styles supported by folder_organizer.

    Extends with new variants as needed; the schema_version bump handles
    migration of older prefs.json files.
    """

    ORIGINAL = "original"
    SNAKE_CASE = "snake_case"
    KEBAB_CASE = "kebab-case"
    LOWER = "lower"


class MemoryPreferences(BaseModel):
    """Persisted user preferences. Mirrors the on-disk prefs.json shape."""

    forbidden_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Workspace-relative paths the agent must never touch. "
            "Checked kernel-side by policy_guard before every action."
        ),
    )
    naming_style: NamingStyle = Field(
        default=NamingStyle.ORIGINAL,
        description=(
            "How folder_organizer transforms filenames during renames. "
            "Default ORIGINAL preserves the input name verbatim."
        ),
    )
    schema_version: int = Field(
        default=1,
        description="Bump when adding/removing fields to enable migration.",
    )

    def is_default(self) -> bool:
        """True iff the preferences match factory defaults — used by the
        CLI to decide whether to print the 'Applied preferences' header."""
        return (
            not self.forbidden_paths
            and self.naming_style == NamingStyle.ORIGINAL
        )
