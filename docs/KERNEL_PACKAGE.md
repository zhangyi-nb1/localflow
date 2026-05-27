# `localflow_kernel` — the distributable harness core

**Status**: shipped Phase 30 (v0.28.0)
**Audience**: developers embedding the LocalFlow harness in their own
tooling without needing the rest of the project (CLI, UI, skills,
recipes, eval, MCP server, etc.).

---

## TL;DR

```python
from localflow_kernel import (
    Action, ActionPlan, ActionType, RiskLevel,
    Executor, Verifier, LocalWorkspace, DockerWorkspace,
    RunStore,
)

# Build a plan, run it through the same kernel LocalFlow uses.
plan = ActionPlan(
    plan_id="my-plan",
    task_id="my-task-1",
    summary="kernel-only usage",
    actions=[
        Action(
            action_id="a1",
            action_type=ActionType.MKDIR,
            target_path="outputs/",
            reason="set up output dir",
            risk_level=RiskLevel.LOW,
            reversible=True,
            requires_approval=False,
        )
    ],
)
run_store = RunStore.create(home=Path(".localflow"))
ws = LocalWorkspace(Path("/tmp/my-workspace"))
ex = Executor(workspace_root=ws.root, run_store=run_store, workspace=ws)
outcome = ex.execute(plan, approved=True)
```

That's it. No `app.*` imports needed. The kernel guarantees you get the
same plan / dry-run / approval / execute / verify / rollback contract
that LocalFlow's CLI uses — including policy enforcement, rollback
manifest, and trace events.

---

## What's in the kernel

The kernel is the **safety + execution spine**. It does NOT include the
planner, the CLI, the UI, skills, recipes, or evaluators — those are
LocalFlow's application layer and live under `app/*`.

### Modules

| Submodule | Re-exports from | Purpose |
| --- | --- | --- |
| `localflow_kernel.schemas`     | `app.schemas.*`                | Pydantic models: `Action`, `ActionPlan`, `TaskSpec`, `RollbackManifest`, `TraceEvent`, etc. |
| `localflow_kernel.harness`     | `app.harness.{executor,policy_guard,verifier,rollback,dry_run,trace,approval,sandbox,react_loop,...}` | Plan → execute → verify spine. |
| `localflow_kernel.workspace`   | `app.tools.{workspace,docker_workspace}` | Pluggable filesystem backends: `LocalWorkspace`, `DockerWorkspace`. |
| `localflow_kernel.storage`     | `app.storage.{run_store,jsonl_logger}` | Per-task on-disk artefact layout. |
| `localflow_kernel.llm`         | (canonical home — moved Phase 30.1) | `LLMClient` Protocol, `StructuredResponse`, `LLMClientError`. |
| `localflow_kernel.react_prompts` | (canonical home — moved Phase 30.1) | System prompt + tool schema for the react loop. |

The most-used names are also re-exported at the package root, so the
short imports work:

```python
from localflow_kernel import Action, Executor, LocalWorkspace
```

### What's NOT in the kernel

- **Concrete LLM clients**: `AnthropicClient`, `FakeLLMClient` live in
  `app/agent/client.py`. They depend on the `anthropic` SDK (and on
  test fakes) and stay application-layer. The kernel only provides the
  `LLMClient` Protocol that those concrete clients implement.
- **Skills, recipes, planner**: `app.skills.*`, `app.recipes.*`,
  `app.agent.planner` — the LocalFlow product features. Build your own
  on top of the kernel; LocalFlow's CLI is one such consumer.
- **Eval graders, recipe verifiers**: `app.eval.*`. Application-layer
  validation that depends on test fixtures + grader catalogues.
- **CLI, UI, MCP server**: `app.cli`, `app.ui`, `app.mcp`. Front-ends
  for the kernel, not part of it.
- **Orchestration**: `app.harness.control_loop` ties the planner,
  skill, and executor together. The kernel exposes the building
  blocks; consumers wire them up themselves.

---

## Boundary guarantee

Phase 30.2 added `tests/test_kernel_boundary.py`. It walks every module
reachable from `localflow_kernel.*` (and every underlying `app.*`
implementation module that the facade re-exports from), parses its
import declarations with `ast`, and asserts that **none of them
reference application-layer packages**:

```
forbidden_prefixes = (
    "app.skills", "app.recipes", "app.cli", "app.ui", "app.eval",
    "app.memory", "app.primitives", "app.templates", "app.mcp",
    "app.main", "app.agent.client", "app.agent.react_prompts",
    "app.agent.prompts", "app.agent.planner", "app.agent.preview",
)
```

If anyone adds an application-layer import to a kernel module, CI
fails. This is the durable invariant — the boundary won't drift
silently.

---

## Versioning

`localflow_kernel.__version__` tracks the kernel surface. Pre-1.0 the
kernel ships in lockstep with the LocalFlow application package (same
git tag), but the version string is independent so a future PyPI split
(Phase 32 candidate) becomes mechanical.

---

## Future direction

| Phase | Step | Status |
| --- | --- | --- |
| 30.0 | Boundary identification + design doc       | ✅ shipped 2026-05-26 |
| 30.1 | `localflow_kernel/` facade + LLMClient move | ✅ shipped 2026-05-27 |
| 30.2 | Boundary test + user docs                   | ✅ shipped 2026-05-27 |
| 31 (candidate) | Physically relocate implementation modules from `app/` into `localflow_kernel/`; drop the back-compat re-exports | not committed |
| 32 (candidate) | Split `localflow_kernel` into its own PyPI-publishable distribution; the `py.typed` marker is already in place | not committed |

Per CLAUDE.md §C, Phase 31+ stays uncommitted until evidence (a
downstream consumer, a packaging request, an integration ask) makes it
the obvious next step. The boundary lint is the prerequisite: as long
as it stays green, any future physical move is mechanical.

---

## Example: building a custom orchestrator on the kernel

If you want LocalFlow's safety guarantees but a different planner /
approval UX / verifier than the project ships with:

```python
from pathlib import Path

from localflow_kernel import (
    Action, ActionPlan, ActionType,
    Executor, RunStore,
    LocalWorkspace, RiskLevel,
)
from localflow_kernel.harness import (
    assess_plan, ask_approval, render_dry_run_markdown, Verifier,
)
from localflow_kernel.schemas import TaskSpec, WorkspaceSnapshot

def my_orchestrator(task: TaskSpec, plan: ActionPlan) -> bool:
    workspace_root = Path(task.workspace_root)
    run_store = RunStore.create()
    ws = LocalWorkspace(workspace_root)

    # 1. Risk assessment — same engine LocalFlow uses.
    assessment = assess_plan(workspace_root, plan)

    # 2. Dry-run preview.
    dry_run_md = render_dry_run_markdown(plan, workspace_root, assessment)
    print(dry_run_md)

    # 3. Your own approval UX — could be a TUI, an HTTP form, an SSE stream.
    decision = ask_approval(
        risk_level=assessment.risk_level.value,
        write_action_count=sum(1 for a in plan.actions if a.is_write()),
        auto_approve=False,
    )
    if not decision.approved:
        return False

    # 4. Execute through the kernel.
    ex = Executor(workspace_root=workspace_root, run_store=run_store, workspace=ws)
    outcome = ex.execute(plan, approved=True)

    # 5. Verify.
    snapshot = WorkspaceSnapshot(
        snapshot_id="orchestrator-snap",
        task_id=task.task_id,
        root=str(workspace_root),
    )
    verifier = Verifier(workspace_root=workspace_root)
    result = verifier.verify(
        task_id=task.task_id,
        run_id=outcome.run_id,
        plan=plan,
        manifest=outcome.manifest,
        executed_action_ids={r.action_id for r in outcome.records if r.status.value == "success"},
        skipped_action_ids=set(),
        failed_action_ids=set(),
        original_snapshot=snapshot,
    )
    return result.passed
```

You've built your own orchestrator. LocalFlow's CLI is doing exactly
this internally, plus skills/recipes/UI on top.
