from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Confirm


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
