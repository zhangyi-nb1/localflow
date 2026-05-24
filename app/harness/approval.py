from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Confirm

from app.schemas.action import Action, ActionType, RiskLevel
from app.schemas.approval import ConfirmationPolicy, ConfirmationPolicyType


@dataclass
class ApprovalDecision:
    approved: bool
    reason: str = ""


def ask_approval(
    *,
    risk_level: str,
    write_action_count: int,
    auto_approve: bool = False,
    console: Console | None = None,
) -> ApprovalDecision:
    """Prompt the user to approve a plan, or auto-approve when configured.

    For the harness this is the *one* place writes can be unlocked. The
    executor must refuse to run unless ``approved=True``.
    """
    if auto_approve:
        return ApprovalDecision(approved=True, reason="auto-approved")

    if write_action_count == 0:
        return ApprovalDecision(approved=True, reason="no write actions")

    console = console or Console()
    console.print(
        f"\n[bold yellow]Approval needed[/]: {write_action_count} write "
        f"action(s) at risk level [bold]{risk_level}[/]."
    )
    ok = Confirm.ask("Proceed with execution?", default=False)
    return ApprovalDecision(approved=ok, reason="user accepted" if ok else "user rejected")


# ─────────────────────────────────── Phase 27.0 — ConfirmationPolicy

# Action types that produce only artefact-class writes (markdown, JSON,
# PNG). Auto-approved when ``ConfirmationPolicy.auto_approve_index=True``
# regardless of policy_type — gating these adds friction without safety
# value because they're trivially rollback-safe.
_INDEX_LIKE_ACTIONS = frozenset({ActionType.INDEX, ActionType.SUMMARIZE})

# Risk level ordering for ON_HIGH_RISK threshold comparison.
_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


def policy_requires_confirmation(
    action: Action,
    policy: ConfirmationPolicy,
) -> bool:
    """Return True iff ``action`` should pause for explicit user approval
    under ``policy``. Pure function; safe to call from any layer.

    Decision tree:
      1. ``auto_approve_index`` short-circuit for INDEX / SUMMARIZE
      2. policy_type=NEVER → False (the current auto-approve path)
      3. policy_type=ALWAYS → True
      4. policy_type=ON_WRITE → True iff action.is_write()
      5. policy_type=ON_HIGH_RISK → True iff
         action.risk_level >= policy.risk_threshold AND action.is_write()
    """
    # 1. Index/summarize short-circuit.
    if policy.auto_approve_index and action.action_type in _INDEX_LIKE_ACTIONS:
        return False

    pt = policy.policy_type
    if pt == ConfirmationPolicyType.NEVER:
        return False
    if pt == ConfirmationPolicyType.ALWAYS:
        return True
    if pt == ConfirmationPolicyType.ON_WRITE:
        return action.is_write()
    if pt == ConfirmationPolicyType.ON_HIGH_RISK:
        if not action.is_write():
            return False
        return _RISK_RANK[action.risk_level] >= _RISK_RANK[policy.risk_threshold]
    # Defensive — unreachable while ConfirmationPolicyType has 4 values.
    return True


def ask_action_approval(
    action: Action,
    *,
    policy: ConfirmationPolicy,
    console: Console | None = None,
) -> ApprovalDecision:
    """Per-action approval prompt under ``policy``.

    If the policy doesn't require confirmation for this action, returns
    immediately as auto-approved. Otherwise shows the action summary
    and asks Y/N (and optionally an "Approve all remaining" shortcut
    when ``policy.allow_approve_rest=True`` — the caller can read the
    returned reason to detect that response).
    """
    if not policy_requires_confirmation(action, policy):
        return ApprovalDecision(
            approved=True,
            reason=f"auto-approved by policy={policy.policy_type.value}",
        )

    console = console or Console()
    console.print(
        f"\n[bold yellow]Approval needed[/] for action "
        f"[bold]{action.action_id}[/] "
        f"({action.action_type.value}, risk={action.risk_level.value}):"
    )
    if action.source_path:
        console.print(f"  source: {action.source_path}")
    if action.target_path:
        console.print(f"  target: {action.target_path}")
    if action.reason:
        console.print(f"  reason: {action.reason[:300]}")

    if policy.allow_approve_rest:
        console.print("  [dim](Y = approve this, N = reject, A = approve all remaining)[/]")
        resp = console.input("[bold]Approve?[/] [y/N/a]: ").strip().lower()
        if resp == "a":
            return ApprovalDecision(approved=True, reason="user accepted (approve all)")
        ok = resp == "y"
    else:
        ok = Confirm.ask("Proceed with this action?", default=False)

    return ApprovalDecision(
        approved=ok,
        reason="user accepted" if ok else "user rejected",
    )
