from __future__ import annotations

from pydantic import BaseModel, Field


class SkillManifest(BaseModel):
    name: str
    description: str = ""
    version: str = "0.1.0"
    capabilities: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Tool Registry names this skill calls in plan/plan_with_llm "
            "(Phase 4.2). Verified at SkillRegistry.register time."
        ),
    )
    allowed_actions: list[str] = Field(default_factory=list)
    requires_approval: list[str] = Field(default_factory=list)
    supports_dry_run: bool = True
    supports_rollback: bool = True
    supports_verify: bool = True
