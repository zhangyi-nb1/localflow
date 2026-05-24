"""Phase 27.0 — typed ConfirmationPolicy.

Background: see ``docs/PHASE_27_DESIGN.md``. The old ``ask_approval``
treats approval as a single yes/no for the whole plan. Phase 27
introduces a 4-tier policy that decouples "what counts as risky"
from "should we ask the user about it".

§10.7: application-layer schema only. No kernel imports. The
existing batch-vs-react executor paths consume this through the
approval helper module, NOT through new dispatch logic.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.action import RiskLevel


class ConfirmationPolicyType(str, Enum):
    """Four legal granularity settings for human-in-the-loop approval.

    NEVER  is the historical ``--yes`` / ``auto_approve=True`` behaviour.
    ALWAYS asks for every action (most cautious; pairs well with
    ``--react`` where each LLM decision is a hinge point).
    ON_HIGH_RISK  asks only when ``action.risk_level >= risk_threshold``.
    ON_WRITE  asks for any write-class action (mkdir/move/copy/rename/
    python_compute) regardless of risk_level.
    """

    NEVER = "never"
    ALWAYS = "always"
    ON_HIGH_RISK = "on_high_risk"
    ON_WRITE = "on_write"


class ConfirmationPolicy(BaseModel):
    """User-configurable approval policy.

    Defaults match v0.24.x behaviour (NEVER + auto-approve index) so a
    caller that doesn't construct one explicitly sees zero behaviour
    change. Recipe authors and CLI users opt into stricter policies.
    """

    model_config = ConfigDict(extra="forbid")

    policy_type: ConfirmationPolicyType = Field(
        default=ConfirmationPolicyType.NEVER,
        description=(
            "Which approval granularity to apply. NEVER = current "
            "auto-approve behaviour; ALWAYS = ask per action; "
            "ON_HIGH_RISK = ask only when action.risk_level >= "
            "risk_threshold; ON_WRITE = ask for any write-class action."
        ),
    )

    risk_threshold: RiskLevel = Field(
        default=RiskLevel.HIGH,
        description=(
            "Used only when policy_type=ON_HIGH_RISK. Set to MEDIUM to "
            "ask more often, HIGH to ask only for irreversible-ish ops. "
            "Ignored under other policy_types."
        ),
    )

    auto_approve_index: bool = Field(
        default=True,
        description=(
            "When True (default), INDEX and SUMMARIZE actions are "
            "auto-approved regardless of policy_type. These actions "
            "only write markdown/JSON artefacts that are trivially "
            "rollback-safe — gating them adds friction without safety "
            "value. Set to False to require approval for every artefact "
            "write (useful for highly regulated workflows)."
        ),
    )

    allow_approve_rest: bool = Field(
        default=True,
        description=(
            "When True (default), the per-action prompt exposes an "
            "'Approve all remaining' shortcut so a user who is satisfied "
            "after the first few prompts can flip to NEVER for the rest "
            "of the run without rewinding. Set to False for audit-strict "
            "workflows where every action must be explicitly approved."
        ),
    )
