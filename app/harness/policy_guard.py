from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.schemas import Action, ActionPlan, ActionType, RiskAssessment, RiskLevel, RiskVerdict
from app.schemas.action import WRITE_ACTIONS


class PolicyViolation(Exception):
    """Raised when an action would breach a hard policy rule."""


@dataclass
class PolicyDecision:
    action_id: str
    allowed: bool
    reasons: list[str]


def resolve_inside(workspace_root: Path, candidate: str) -> Path:
    """Resolve ``candidate`` against ``workspace_root`` and verify containment.

    Hard rules:
      * Candidate must be a non-empty *relative* POSIX-style path.
      * The resolved real path must live inside ``workspace_root``.
      * Symlinks pointing outside the workspace are rejected.
    """
    if candidate is None or candidate == "":
        raise PolicyViolation("empty path")
    p = Path(candidate)
    if p.is_absolute() or p.drive:
        raise PolicyViolation(f"absolute path not allowed: {candidate}")
    if any(part == ".." for part in p.parts):
        raise PolicyViolation(f"parent-directory traversal not allowed: {candidate}")

    root = workspace_root.resolve()
    target = (root / p).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise PolicyViolation(f"path escapes workspace: {candidate}")
    return target


def _is_under_forbidden(
    workspace_root: Path, candidate: Path, forbidden_paths: tuple[str, ...]
) -> str | None:
    """Return the matching forbidden entry if ``candidate`` is at or
    under any of them (workspace-relative). Otherwise None.

    Each forbidden entry is resolved through ``resolve_inside`` so we
    catch the same escape attempts here as for action paths — there is
    no way to encode a forbidden_path entry that points outside the
    workspace (those are simply ignored)."""
    for forbidden in forbidden_paths:
        try:
            forbidden_abs = resolve_inside(workspace_root, forbidden)
        except PolicyViolation:
            continue
        try:
            candidate.relative_to(forbidden_abs)
        except ValueError:
            continue
        return forbidden
    return None


def _check_path_fields(
    workspace_root: Path,
    action: Action,
    reasons: list[str],
    forbidden_paths: tuple[str, ...] = (),
) -> None:
    if action.source_path is not None:
        try:
            src_abs = resolve_inside(workspace_root, action.source_path)
        except PolicyViolation as exc:
            reasons.append(f"source_path: {exc}")
        else:
            hit = _is_under_forbidden(workspace_root, src_abs, forbidden_paths)
            if hit is not None:
                reasons.append(
                    f"source_path: blocked by forbidden_paths ({hit!r})"
                )
    if action.target_path is not None:
        try:
            tgt_abs = resolve_inside(workspace_root, action.target_path)
        except PolicyViolation as exc:
            reasons.append(f"target_path: {exc}")
        else:
            hit = _is_under_forbidden(workspace_root, tgt_abs, forbidden_paths)
            if hit is not None:
                reasons.append(
                    f"target_path: blocked by forbidden_paths ({hit!r})"
                )


def _check_required_fields(action: Action, reasons: list[str]) -> None:
    requires_source = {ActionType.COPY, ActionType.MOVE, ActionType.RENAME}
    requires_target = {
        ActionType.MKDIR,
        ActionType.COPY,
        ActionType.MOVE,
        ActionType.RENAME,
        ActionType.INDEX,
        ActionType.CONVERT,
    }
    if action.action_type in requires_source and not action.source_path:
        reasons.append(f"{action.action_type.value} requires source_path")
    if action.action_type in requires_target and not action.target_path:
        reasons.append(f"{action.action_type.value} requires target_path")


def evaluate_action(
    workspace_root: Path,
    action: Action,
    *,
    forbidden_actions: tuple[str, ...] = (),
    forbidden_paths: tuple[str, ...] = (),
) -> PolicyDecision:
    reasons: list[str] = []
    if action.action_type.value in forbidden_actions:
        reasons.append(f"{action.action_type.value} is in forbidden_actions")
    _check_required_fields(action, reasons)
    _check_path_fields(workspace_root, action, reasons, forbidden_paths=forbidden_paths)

    # Irreversible writes must be flagged high risk and require approval.
    if action.is_write() and not action.reversible and action.risk_level != RiskLevel.HIGH:
        reasons.append("irreversible write must be marked risk_level=high")
    if action.is_write() and not action.reversible and not action.requires_approval:
        reasons.append("irreversible write must require approval")

    return PolicyDecision(action_id=action.action_id, allowed=not reasons, reasons=reasons)


def assess_plan(
    workspace_root: Path,
    plan: ActionPlan,
    *,
    forbidden_actions: tuple[str, ...] = (),
    forbidden_paths: tuple[str, ...] = (),
) -> RiskAssessment:
    seen_ids: set[str] = set()
    blocked: list[str] = []
    warnings: list[str] = []
    highest = RiskLevel.LOW

    for action in plan.actions:
        if action.action_id in seen_ids:
            blocked.append(action.action_id)
            warnings.append(f"duplicate action_id: {action.action_id}")
            continue
        seen_ids.add(action.action_id)

        decision = evaluate_action(
            workspace_root,
            action,
            forbidden_actions=forbidden_actions,
            forbidden_paths=forbidden_paths,
        )
        if not decision.allowed:
            blocked.append(action.action_id)
            warnings.extend(f"{action.action_id}: {r}" for r in decision.reasons)
        if action.is_write() and action.action_type in WRITE_ACTIONS:
            if action.risk_level == RiskLevel.HIGH and highest != RiskLevel.HIGH:
                highest = RiskLevel.HIGH
            elif action.risk_level == RiskLevel.MEDIUM and highest == RiskLevel.LOW:
                highest = RiskLevel.MEDIUM

    passed = not blocked
    verdict = RiskVerdict.BLOCKED if not passed else RiskVerdict(highest.value)
    reason = "ok" if passed else f"{len(blocked)} action(s) blocked"
    return RiskAssessment(
        plan_id=plan.plan_id,
        passed=passed,
        blocked_actions=blocked,
        warnings=warnings,
        risk_level=verdict,
        reason=reason,
    )
