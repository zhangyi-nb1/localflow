"""v0.14.1 — typed source ledger schema.

A ``SourceLedger`` is the JSON-machine-readable counterpart to the
markdown ``SOURCES.md`` that v0.14's Workspace Pack Builder demo
emits via the agent skill. Downstream tooling (a CI script verifying
ledger completeness, a web dashboard rendering provenance, etc.)
needs a stable typed shape — markdown is for humans, this is for
machines.

Schema is closed (Pydantic ``extra="forbid"``) so consumers can
deserialize confidently. Versioned via ``ledger_schema_version`` for
future migrations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LEDGER_SCHEMA_VERSION = 1


class SourceEntry(BaseModel):
    """One file in the ledger."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Workspace-relative path of the file.")
    file_type: str = Field(
        ...,
        description="LocalFlow classifier output: pdf / excel / tabular / image / text / code / etc.",
    )
    size_bytes: int = Field(..., ge=0)
    sha256: str | None = Field(
        default=None, description="Lower-case hex digest. None when hashing was disabled."
    )
    category: str | None = Field(
        default=None,
        description=(
            "Post-organize category dir (e.g. 'papers', 'data', 'misc'). "
            "None when the file remained at the workspace root."
        ),
    )
    role: Literal["seed", "generated", "moved"] = Field(
        default="moved",
        description=(
            "seed: present at task start, unmoved. "
            "moved: present at task start, relocated by a MOVE/RENAME action. "
            "generated: produced by an INDEX / SUMMARIZE / COPY action during this run."
        ),
    )


class SourceLedger(BaseModel):
    """Top-level ledger payload."""

    model_config = ConfigDict(extra="forbid")

    ledger_schema_version: int = Field(default=LEDGER_SCHEMA_VERSION)
    task_id: str | None = Field(
        default=None, description="The run that produced this ledger; None for ad-hoc snapshots."
    )
    workspace_root: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entries: list[SourceEntry] = Field(default_factory=list)

    def by_category(self) -> dict[str, list[SourceEntry]]:
        """Group entries by their category for rendering. Files with
        ``category=None`` land under the key ``"(root)"``."""
        out: dict[str, list[SourceEntry]] = {}
        for e in self.entries:
            out.setdefault(e.category or "(root)", []).append(e)
        return {k: sorted(v, key=lambda e: e.path) for k, v in sorted(out.items())}
