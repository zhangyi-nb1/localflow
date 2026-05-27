"""Phase 30.1 — re-export of the kernel harness modules.

Mirrors the pure-kernel modules under ``app/harness/`` (see
``docs/PHASE_30_DESIGN.md`` §2.1 for the boundary list). Excluded
intentionally: ``control_loop``, ``repair_loop``, ``semantic_verifier``,
``recipe_repair``, ``taskgraph_runner`` — these orchestration modules
depend on application-layer fixtures (``Skill``, ``MemoryStore``,
``EvalGrader``) and live outside the kernel.

Downstream consumers building their own orchestrators should:

    from localflow_kernel.harness import Executor, Verifier, Rollback
    from localflow_kernel.schemas import Action, ActionPlan
    from localflow_kernel.workspace import LocalWorkspace

and assemble plan → execute → verify themselves; the kernel's
guarantees (policy enforcement, rollback manifest integrity, trace
shape) hold across whatever planner you wire in.
"""

from __future__ import annotations

from app.harness.action_validator import (
    PlanValidationError,
    validate_plan_structure,
)
from app.harness.approval import (
    ApprovalDecision,
    ask_action_approval,
    ask_approval,
    policy_requires_confirmation,
)
from app.harness.audit import AuditLogger
from app.harness.checkpoint import completed_action_ids
from app.harness.dry_run import render_dry_run_markdown, simulate_action
from app.harness.executor import ExecutionOutcome, Executor
from app.harness.policy_guard import (
    PolicyDecision,
    PolicyViolation,
    assess_plan,
    evaluate_action,
    resolve_inside,
)
from app.harness.react_loop import run_react_loop
from app.harness.rollback import (
    Rollback,
    RollbackConflict,
    RollbackOutcome,
    RollbackPreview,
    filter_manifest_to_stage,
)
from app.harness.sandbox import SandboxRuntime
from app.harness.trace import TraceLogger
from app.harness.verifier import Verifier

__all__ = [
    "ApprovalDecision",
    "AuditLogger",
    "ExecutionOutcome",
    "Executor",
    "PlanValidationError",
    "PolicyDecision",
    "PolicyViolation",
    "Rollback",
    "RollbackConflict",
    "RollbackOutcome",
    "RollbackPreview",
    "SandboxRuntime",
    "TraceLogger",
    "Verifier",
    "ask_action_approval",
    "ask_approval",
    "assess_plan",
    "completed_action_ids",
    "evaluate_action",
    "filter_manifest_to_stage",
    "policy_requires_confirmation",
    "render_dry_run_markdown",
    "resolve_inside",
    "run_react_loop",
    "simulate_action",
    "validate_plan_structure",
]
