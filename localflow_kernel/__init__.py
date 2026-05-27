"""LocalFlow Harness Kernel — distributable core.

This package is the stable public surface of the LocalFlow execution
harness. It re-exports the kernel modules so downstream consumers
(scripts, ops tools, future libraries) can depend on a single import
root without pulling in the entire ``app/`` tree (skills, recipes, UI,
eval graders, etc.).

What's in the kernel:

    plan / dry-run / approval / execute / verify / rollback / trace

What's NOT in the kernel (lives in ``app/*``):

    - skills, recipes, CLI, UI, MCP server
    - concrete LLM clients (AnthropicClient, FakeLLMClient)
    - eval graders, recipe verifiers, memory store
    - control_loop orchestration (the top-level recipe runner)

For the boundary rationale see ``docs/PHASE_30_DESIGN.md``.
For end-user import recipes see ``docs/KERNEL_PACKAGE.md``.

Phase 30.1: facade lives here; pure-kernel implementation modules still
live under ``app/`` and are re-exported through the submodules below.
A future Phase 31 may physically relocate the implementations — the
public surface declared here is the stable contract.
"""

from __future__ import annotations

__version__ = "0.28.0.dev0"

# Re-export the small core dataclasses + Protocols at the top level so
# the most common imports stay short:
#
#     from localflow_kernel import Action, ActionPlan, Executor, LocalWorkspace
#
# The full schemas + harness modules are still reachable via
# ``localflow_kernel.schemas`` / ``localflow_kernel.harness`` /
# ``localflow_kernel.workspace`` / ``localflow_kernel.storage`` /
# ``localflow_kernel.llm`` submodules below.

from localflow_kernel.harness import (
    AuditLogger,
    ExecutionOutcome,
    Executor,
    PolicyViolation,
    TraceLogger,
    Verifier,
    assess_plan,
    completed_action_ids,
    evaluate_action,
    render_dry_run_markdown,
    resolve_inside,
    validate_plan_structure,
)
from localflow_kernel.llm import LLMClient, LLMClientError, StructuredResponse
from localflow_kernel.schemas import (
    Action,
    ActionPlan,
    ActionTraceEvent,
    ActionType,
    ComputeAction,
    ConfirmationPolicy,
    ConfirmationPolicyType,
    ExecutionRecord,
    ExecutionStatus,
    FailureType,
    LoopDecision,
    LoopDecisionType,
    ReactConfig,
    RiskAssessment,
    RiskLevel,
    RiskVerdict,
    RollbackEntry,
    RollbackManifest,
    TaskSpec,
    TraceEvent,
    TraceEventType,
    VerificationResult,
)
from localflow_kernel.storage import JsonlLogger, RunStore
from localflow_kernel.workspace import (
    DockerWorkspace,
    LocalWorkspace,
    Workspace,
    parse_workspace_spec,
)

__all__ = [
    # ── version
    "__version__",
    # ── llm
    "LLMClient",
    "LLMClientError",
    "StructuredResponse",
    # ── schemas (subset surfaced at top level)
    "Action",
    "ActionPlan",
    "ActionTraceEvent",
    "ActionType",
    "ComputeAction",
    "ConfirmationPolicy",
    "ConfirmationPolicyType",
    "ExecutionRecord",
    "ExecutionStatus",
    "FailureType",
    "LoopDecision",
    "LoopDecisionType",
    "ReactConfig",
    "RiskAssessment",
    "RiskLevel",
    "RiskVerdict",
    "RollbackEntry",
    "RollbackManifest",
    "TaskSpec",
    "TraceEvent",
    "TraceEventType",
    "VerificationResult",
    # ── harness
    "AuditLogger",
    "Executor",
    "ExecutionOutcome",
    "PolicyViolation",
    "TraceLogger",
    "Verifier",
    "assess_plan",
    "completed_action_ids",
    "evaluate_action",
    "render_dry_run_markdown",
    "resolve_inside",
    "validate_plan_structure",
    # ── workspace
    "DockerWorkspace",
    "LocalWorkspace",
    "Workspace",
    "parse_workspace_spec",
    # ── storage
    "JsonlLogger",
    "RunStore",
]
