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
    prefer_llm_planner: bool = Field(
        default=False,
        description=(
            "When True, the UI auto-detect routes every LLM-capable skill "
            "to the LLM planner regardless of goal text. Compound-goal "
            "detection still runs for non-LLM-capable skills. Defaults to "
            "False — most users want rule planning for simple goals."
        ),
    )
    enable_semantic_verifier: bool = Field(
        default=False,
        description=(
            "Phase 13 — when True, the harness runs LLM-as-judge graders "
            "after structural verify. A rejection can trigger the auto-repair "
            "loop (see max_auto_repairs). Default False because semantic "
            "verification adds LLM cost on every execute and changes a "
            "behaviour pre-existing users wouldn't expect."
        ),
    )
    max_auto_repairs: int = Field(
        default=2,
        ge=0,
        le=5,
        description=(
            "Phase 13 — cap on automatic plan-revise + re-execute cycles "
            "after a semantic verifier rejection. 0 means 'run semantic "
            "verifier in report-only mode'. Hard limit 5 mirrors MAX_REVISIONS."
        ),
    )
    fetch_allowed_domains: list[str] = Field(
        default_factory=list,
        description=(
            "v0.16 — explicit hostname allowlist for the WebCollect skill's "
            "FETCH actions. Empty list = no network fetches allowed. The "
            "policy_guard rejects any FETCH whose URL host is not exactly "
            "on this list (no wildcards). Add via "
            "`localflow memory allow-domain <host>`."
        ),
    )
    workspace_backend_spec: str = Field(
        default="local",
        description=(
            "Phase 34.2 — the Workspace Protocol backend the UI uses for "
            "plan/execute/verify/rollback. Mirrors the CLI ``--workspace`` "
            "flag grammar: ``local`` (default), ``docker:<image>``, "
            "``ssh:<host>[:<port>][:<root>]``. Validated through "
            "``parse_workspace_spec`` on read. Empty string ≡ ``local`` for "
            "back-compat with v4 prefs.json that didn't carry this field."
        ),
    )
    schema_version: int = Field(
        default=5,
        description="Bump when adding/removing fields to enable migration.",
    )

    def is_default(self) -> bool:
        """True iff the preferences match factory defaults — used by the
        CLI to decide whether to print the 'Applied preferences' header."""
        return (
            not self.forbidden_paths
            and self.naming_style == NamingStyle.ORIGINAL
            and self.prefer_llm_planner is False
            and self.enable_semantic_verifier is False
            and self.max_auto_repairs == 2
            and not self.fetch_allowed_domains
            and self.workspace_backend_spec in ("", "local")
        )
