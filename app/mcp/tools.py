"""Phase 6.1 — MCP tool definitions + handlers.

16 tools wrapping LocalFlow's existing CLI surface. Each handler is a
**sync** function that takes a dict of arguments and returns a
JSON-safe dict. The async MCP server in :mod:`app.mcp.server` calls
each handler directly (no thread offload — every call is fast/local).

Tools fall into three buckets:

* **read-only** (no state changes): inspect_workspace / list_skills /
  list_tools_catalog / list_runs / read_run / read_memory_prefs /
  read_memory_audit
* **state-changing** (always through the harness's normal gates —
  policy_guard, approval-via-explicit-arg, verifier): create_plan /
  dry_run / execute_plan / rollback_run
* **memory mutations**: memory_forbid_path / memory_unforbid_path /
  memory_set_naming_style / memory_unset_naming_style

Two safety contracts:

1. **No new actions, no new IO.** Every tool wraps something the CLI
   already does. The kernel's safety primitives (workspace containment,
   forbidden_paths, forbidden_actions, dry-run, verify, rollback) are
   inherited verbatim — MCP does not re-implement them.
2. **No interactive prompts.** ``execute_plan`` takes ``approved: bool``
   as an explicit argument, matching the CLI's ``--yes`` flag. There is
   no MCP equivalent of mid-execution approval.

Outline §10.7: zero references to ``app/harness/*`` internals here —
only public entry points (``control_loop.run_*``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.harness import control_loop
from app.harness.audit import AuditLogger
from app.harness.rollback import Rollback
from app.harness.trace import TraceLogger
from app.mcp._serialize import to_jsonable
from app.mcp.approval import ApprovalError, mint_token, validate_and_consume
from app.memory import MemoryStore
from app.skills import get_default_registry, get_load_findings
from app.storage.run_store import RunStore, localflow_home
from app.tools import get_default_tool_registry


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    # Phase 7 / Issue 3 fix — `dangerous=True` tools are NOT advertised
    # in the MCP tool list by default. The user can opt them in by
    # setting LOCALFLOW_MCP_ALLOW_DANGEROUS=1 in the server's env.
    # The handler is still importable (CLI uses some of these directly),
    # only the MCP exposure is gated.
    dangerous: bool = False


# Env var name documented in docs/MCP.md and in the error message
# returned when a dangerous tool is unavailable.
DANGEROUS_ENV = "LOCALFLOW_MCP_ALLOW_DANGEROUS"


def _dangerous_enabled() -> bool:
    """Read the gate env var with the usual truthy semantics."""
    import os

    raw = os.environ.get(DANGEROUS_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def visible_tools() -> list["ToolDef"]:
    """Return only the tools the MCP server should advertise to clients
    in the current environment. Used by :mod:`app.mcp.server`."""
    if _dangerous_enabled():
        return list(TOOLS)
    return [t for t in TOOLS if not t.dangerous]


# --------------------------------------------------------------------- helpers


def _require(args: dict[str, Any], key: str, kind: type) -> Any:
    if key not in args:
        raise ValueError(f"missing required argument: {key!r}")
    value = args[key]
    if not isinstance(value, kind):
        raise ValueError(f"argument {key!r} must be {kind.__name__}, got {type(value).__name__}")
    return value


def _run_summary(task_id: str) -> dict[str, Any]:
    """Compact run summary used by ``list_runs`` / ``read_run`` index."""
    store = RunStore(task_id=task_id)
    return {
        "task_id": task_id,
        "has_task": store.exists(store.TASK_JSON),
        "has_workspace": store.exists(store.WORKSPACE_JSON),
        "has_plan": store.exists(store.PLAN_JSON),
        "has_dry_run": store.exists(store.DRY_RUN_MD),
        "has_actions": store.exists(store.ACTIONS_JSON),
        "has_rollback_manifest": store.exists(store.ROLLBACK_JSON),
        "has_verify": store.exists(store.VERIFY_JSON),
        "has_final_report": store.exists(store.FINAL_REPORT_MD),
    }


# --------------------------------------------------------------------- read-only


def handle_inspect_workspace(args: dict[str, Any]) -> dict[str, Any]:
    """Scan a directory and return WorkspaceSnapshot fields. No writes."""
    path = _require(args, "path", str)
    compute_hash = bool(args.get("compute_hash", True))
    compute_preview = bool(args.get("compute_preview", True))
    snapshot = control_loop.run_inspect(
        Path(path),
        task_id="mcp-inspect",
        compute_hash=compute_hash,
        compute_preview=compute_preview,
    )
    return to_jsonable(snapshot)


def handle_list_skills(args: dict[str, Any]) -> dict[str, Any]:
    """List every registered skill (built-in + external from Phase 4.1)."""
    registry = get_default_registry()
    skills: list[dict[str, Any]] = []
    for name in registry.list_names():
        s = registry.require(name)
        m = s.manifest
        origin = "built-in" if type(s).__module__.startswith("app.skills.") else "external"
        skills.append(
            {
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "origin": origin,
                "supports_llm": s.supports_llm(),
                "allowed_actions": list(m.allowed_actions),
                "required_tools": list(m.required_tools),
                "supports_dry_run": m.supports_dry_run,
                "supports_rollback": m.supports_rollback,
                "supports_verify": m.supports_verify,
            }
        )
    return {
        "skills": skills,
        "load_findings": to_jsonable(get_load_findings()),
    }


def handle_list_tools_catalog(args: dict[str, Any]) -> dict[str, Any]:
    """List the Phase 4.2 Tool Registry catalog (the shared helpers
    skills declare in required_tools). NOT the same as MCP tools — these
    are LocalFlow-internal."""
    tool_registry = get_default_tool_registry()
    registry = get_default_registry()
    used_by: dict[str, list[str]] = {}
    for skill_name in registry.list_names():
        s = registry.require(skill_name)
        for t in s.manifest.required_tools:
            used_by.setdefault(t, []).append(skill_name)

    return {
        "tools": [
            {
                "name": spec.name,
                "category": spec.category,
                "module": spec.module,
                "description": spec.description,
                "side_effects": spec.side_effects,
                "used_by": used_by.get(spec.name, []),
            }
            for spec in tool_registry.list_specs()
        ],
    }


def handle_list_runs(args: dict[str, Any]) -> dict[str, Any]:
    """Return summaries of every run in ``~/.localflow/runs/``.

    Implemented in the MCP tool (not in RunStore) so the storage layer
    stays unchanged for Phase 6.1's §10.7 attestation.
    """
    runs_root = localflow_home() / "runs"
    if not runs_root.exists():
        return {"runs": []}
    runs: list[dict[str, Any]] = []
    for entry in sorted(runs_root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            runs.append(_run_summary(entry.name))
        except Exception as exc:
            runs.append({"task_id": entry.name, "error": f"{type(exc).__name__}: {exc}"})
    return {"runs": runs}


def handle_read_run(args: dict[str, Any]) -> dict[str, Any]:
    """Load the JSON artifacts for one run. Missing pieces simply omitted."""
    task_id = _require(args, "task_id", str)
    store = RunStore(task_id=task_id)
    out: dict[str, Any] = {"task_id": task_id, "summary": _run_summary(task_id)}
    if store.exists(store.TASK_JSON):
        out["task"] = to_jsonable(store.load_task())
    if store.exists(store.WORKSPACE_JSON):
        out["workspace_snapshot"] = to_jsonable(store.load_workspace())
    if store.exists(store.PLAN_JSON):
        out["plan"] = to_jsonable(store.load_plan())
    if store.exists(store.ROLLBACK_JSON):
        out["rollback_manifest"] = to_jsonable(store.load_rollback())
    if store.exists(store.VERIFY_JSON):
        out["verification"] = to_jsonable(store.load_verification())
    if store.exists(store.FINAL_REPORT_MD):
        out["final_report_md"] = store.final_report_path.read_text(encoding="utf-8")
    if store.exists(store.DRY_RUN_MD):
        out["dry_run_md"] = store.dry_run_path.read_text(encoding="utf-8")
    return out


def handle_read_memory_prefs(args: dict[str, Any]) -> dict[str, Any]:
    """Return the persisted preferences (defaults if no prefs.json yet)."""
    return to_jsonable(MemoryStore().load())


def handle_read_memory_audit(args: dict[str, Any]) -> dict[str, Any]:
    limit_raw = args.get("limit", 20)
    limit = None if limit_raw in (None, 0) else int(limit_raw)
    return {"entries": MemoryStore().read_audit(limit=limit)}


# --------------------------------------------------------------------- state-changing


def handle_create_plan(args: dict[str, Any]) -> dict[str, Any]:
    """Create a fresh task + run inspect + rule planner + risk check.

    Returns the task_id so subsequent dry_run / execute / rollback can
    reference it. LLM planning is NOT supported via MCP (CLI only).
    """
    from app.memory._schema import NamingStyle
    from app.schemas import TaskSpec
    from app.skills import SkillError

    workspace = _require(args, "workspace", str)
    goal = _require(args, "goal", str)
    skill_name = args.get("skill", "folder_organizer")

    registry = get_default_registry()
    try:
        skill_obj = registry.require(skill_name)
    except SkillError as exc:
        raise ValueError(str(exc))

    # Mirror CLI: load memory prefs and project onto TaskSpec
    prefs = MemoryStore().load()
    preferences: dict[str, Any] = {}
    if prefs.naming_style != NamingStyle.ORIGINAL:
        preferences["naming_style"] = prefs.naming_style.value

    store = RunStore.create()
    task = TaskSpec(
        task_id=store.task_id,
        user_goal=goal,
        workspace_root=str(Path(workspace).resolve()),
        skill=skill_name,
        constraints=[
            "do not delete any file",
            "do not overwrite existing files",
            "all paths must remain inside workspace_root",
        ],
        allowed_actions=list(skill_obj.manifest.allowed_actions),
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=list(prefs.forbidden_paths),
        preferences=preferences,
    )
    store.save_task(task)
    AuditLogger(store.audit_log_path).log(
        "task.created.mcp",
        task_id=task.task_id,
        goal=goal,
        skill=skill_name,
    )

    trace = TraceLogger(store.trace_path)

    snapshot = control_loop.run_inspect(
        Path(task.workspace_root),
        task_id=task.task_id,
        compute_hash=True,
        compute_preview=True,
    )
    store.save_workspace(snapshot)

    plan = skill_obj.plan(task, snapshot)
    skill_obj.validate(plan)
    store.save_plan(plan)

    assessment = control_loop.run_risk_check(task, plan, trace=trace)

    return {
        "task_id": task.task_id,
        "plan_id": plan.plan_id,
        "summary": plan.summary,
        "action_count": len(plan.actions),
        "expected_outputs": list(plan.expected_outputs),
        "risk_level": assessment.risk_level.value,
        "risk_passed": assessment.passed,
        "blocked_actions": list(assessment.blocked_actions),
        "warnings": list(assessment.warnings),
        "applied_preferences": {
            "forbidden_paths": list(prefs.forbidden_paths),
            "naming_style": prefs.naming_style.value,
            "prefer_llm_planner": prefs.prefer_llm_planner,
        },
    }


def handle_dry_run(args: dict[str, Any]) -> dict[str, Any]:
    """Render the dry-run markdown AND mint a one-shot approval token.

    The token (10-minute TTL, bound to plan + dry-run + workspace) is
    what ``execute_plan`` requires — there is no longer a way to
    execute via MCP without going through this step. See
    :mod:`app.mcp.approval` for the rationale and contract.
    """
    task_id = _require(args, "task_id", str)
    store = RunStore(task_id=task_id)
    if not store.exists(store.TASK_JSON):
        raise ValueError(f"unknown task_id: {task_id!r}")
    task = store.load_task()
    plan = store.load_plan()
    trace = TraceLogger(store.trace_path)
    assessment = control_loop.run_risk_check(task, plan, trace=trace)
    md = control_loop.run_dry_run(task, plan, assessment, store, trace=trace)
    # Mint AFTER dry-run files exist on disk (mint reads them to hash).
    token = mint_token(store, workspace_root=task.workspace_root, trace=trace)
    return {
        "task_id": task_id,
        "markdown": md,
        "dry_run_path": str(store.dry_run_path),
        "risk_level": assessment.risk_level.value,
        "approval_token": token.token,
        "approval_expires_at": token.expires_at,
    }


def handle_execute_plan(args: dict[str, Any]) -> dict[str, Any]:
    """Execute a planned task. Requires a valid ``approval_token`` minted
    by a prior ``dry_run`` call.

    Token semantics (see :mod:`app.mcp.approval`):
      * 10-minute TTL from when dry_run minted it
      * One-shot (consumed on success, can't be reused)
      * Bound to the exact plan + dry_run + workspace at mint time —
        any drift invalidates it

    This is intentionally stricter than the CLI's ``execute --yes``,
    because CLI has a human at the keyboard while MCP has an external
    process passing arguments.
    """
    task_id = _require(args, "task_id", str)
    approval_token = _require(args, "approval_token", str)
    store = RunStore(task_id=task_id)
    if not store.exists(store.TASK_JSON):
        raise ValueError(f"unknown task_id: {task_id!r}")
    task = store.load_task()
    trace = TraceLogger(store.trace_path)

    # Validate + consume token BEFORE doing any harness work. If the
    # token is bad, no policy_guard check, no audit log spam.
    try:
        validate_and_consume(store, approval_token, workspace_root=task.workspace_root, trace=trace)
    except ApprovalError as exc:
        raise ValueError(f"approval token rejected: {exc}") from exc

    plan = store.load_plan()
    snapshot = store.load_workspace()
    assessment = control_loop.run_risk_check(task, plan, trace=trace)
    if not assessment.passed:
        return {
            "task_id": task_id,
            "success": False,
            "reason": "policy_guard blocked the plan",
            "blocked_actions": list(assessment.blocked_actions),
            "warnings": list(assessment.warnings),
        }

    outcome = control_loop.run_execute(task, plan, store, approved=True, trace=trace)
    verification = control_loop.run_verify(task, plan, store, outcome, snapshot, trace=trace)
    from app.schemas import ExecutionStatus

    successful = sum(1 for r in outcome.records if r.status == ExecutionStatus.SUCCESS)
    failed = sum(1 for r in outcome.records if r.status == ExecutionStatus.FAILED)
    skipped = sum(1 for r in outcome.records if r.status == ExecutionStatus.SKIPPED)
    return {
        "task_id": task_id,
        "run_id": outcome.run_id,
        "success": outcome.success,
        "executed_count": successful,
        "failed_count": failed,
        "skipped_count": skipped,
        "verification_passed": verification.passed,
        "verification_summary": verification.summary,
        "failed_checks": list(verification.failed_checks),
    }


def handle_rollback_preview(args: dict[str, Any]) -> dict[str, Any]:
    """Read-only preview of what ``rollback_run`` would do.

    For each entry: the inverse op, the target it would touch, and a
    ``drift`` field (None = clean, str = mismatch reason). Lets an MCP
    client present a confirmation page before the destructive call.
    """
    task_id = _require(args, "task_id", str)
    store = RunStore(task_id=task_id)
    if not store.exists(store.ROLLBACK_JSON):
        raise ValueError(f"no rollback manifest for task_id={task_id!r}")
    manifest = store.load_rollback()
    task = store.load_task()
    rb = Rollback(workspace_root=Path(task.workspace_root), run_store=store)
    preview = rb.preview(manifest)
    return {
        "task_id": task_id,
        "run_id": preview.run_id,
        "entry_count": len(preview.entries),
        "has_conflicts": preview.has_conflicts,
        "entries": preview.entries,
    }


def handle_rollback_run(args: dict[str, Any]) -> dict[str, Any]:
    """Undo a previously-executed run.

    Phase 7.1: by default refuses entries whose target file's current
    hash differs from the executor-recorded ``after_hash`` (i.e., the
    user edited the file after execute). Drifted entries are reported
    as ``conflicts`` and skipped. Pass ``force=true`` to override —
    your manual edits will be lost. Call ``rollback_preview`` first to
    see which entries would conflict.
    """
    task_id = _require(args, "task_id", str)
    force = bool(args.get("force", False))
    store = RunStore(task_id=task_id)
    if not store.exists(store.ROLLBACK_JSON):
        raise ValueError(f"no rollback manifest for task_id={task_id!r}")
    manifest = store.load_rollback()
    task = store.load_task()
    trace = TraceLogger(store.trace_path)
    rb = Rollback(workspace_root=Path(task.workspace_root), run_store=store, trace=trace)
    outcome = rb.run(manifest, force=force)
    return {
        "task_id": task_id,
        "success": outcome.success,
        "undone": list(outcome.undone),
        "failed": list(outcome.failed),
        "conflicts": list(outcome.conflicts),
    }


# --------------------------------------------------------------------- memory


def handle_memory_forbid_path(args: dict[str, Any]) -> dict[str, Any]:
    path = _require(args, "path", str)
    result = MemoryStore().add_forbidden_path(path)
    return {"changed": result.changed, "event": result.event, "detail": result.detail}


def handle_memory_unforbid_path(args: dict[str, Any]) -> dict[str, Any]:
    path = _require(args, "path", str)
    result = MemoryStore().remove_forbidden_path(path)
    return {"changed": result.changed, "event": result.event, "detail": result.detail}


def handle_memory_set_naming_style(args: dict[str, Any]) -> dict[str, Any]:
    value = _require(args, "value", str)
    result = MemoryStore().set_naming_style(value)
    return {"changed": result.changed, "event": result.event, "detail": result.detail}


def handle_memory_unset_naming_style(args: dict[str, Any]) -> dict[str, Any]:
    result = MemoryStore().clear_naming_style()
    return {"changed": result.changed, "event": result.event, "detail": result.detail}


def handle_memory_set_prefer_llm_planner(args: dict[str, Any]) -> dict[str, Any]:
    value = _require(args, "value", bool)
    result = MemoryStore().set_prefer_llm_planner(value)
    return {"changed": result.changed, "event": result.event, "detail": result.detail}


def handle_memory_unset_prefer_llm_planner(args: dict[str, Any]) -> dict[str, Any]:
    result = MemoryStore().clear_prefer_llm_planner()
    return {"changed": result.changed, "event": result.event, "detail": result.detail}


# --------------------------------------------------------------------- registry


TOOLS: list[ToolDef] = [
    # ----- read-only ----------------------------------------------------
    ToolDef(
        name="inspect_workspace",
        description=(
            "Scan a directory and return file metadata (categories, sizes, "
            "SHA-256, text previews). No writes. Equivalent to "
            "`localflow inspect <path>`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the workspace directory.",
                },
                "compute_hash": {"type": "boolean", "default": True},
                "compute_preview": {"type": "boolean", "default": True},
            },
            "required": ["path"],
        },
        handler=handle_inspect_workspace,
    ),
    ToolDef(
        name="list_skills",
        description=(
            "List every registered LocalFlow skill (built-in + Phase 4.1 "
            "external skills). Returns name, version, declared tools, "
            "lifecycle capabilities, and load-time audit findings."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handle_list_skills,
    ),
    ToolDef(
        name="list_tools_catalog",
        description=(
            "List the Phase 4.2 Tool Registry — the shared callable "
            "helpers (file_scan, pdf_ops, data_ops, chart_ops, ...) that "
            "skills declare in required_tools. NOT the same as MCP tools."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handle_list_tools_catalog,
    ),
    ToolDef(
        name="list_runs",
        description=(
            "List every task run under `~/.localflow/runs/` with a "
            "per-run completion flag for each artifact (task, plan, "
            "dry_run, actions, rollback_manifest, verify, final_report)."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handle_list_runs,
    ),
    ToolDef(
        name="read_run",
        description=(
            "Load all JSON artifacts for one task (task spec, plan, "
            "rollback manifest, verification, final report markdown). "
            "Missing artifacts are simply omitted from the response."
        ),
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
        handler=handle_read_run,
    ),
    ToolDef(
        name="read_memory_prefs",
        description=(
            "Read persisted user preferences (forbidden_paths, "
            "naming_style). Returns defaults if no prefs.json exists yet."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handle_read_memory_prefs,
    ),
    ToolDef(
        name="read_memory_audit",
        description=("Read the memory mutation audit log. Pass limit=0 or null for the full log."),
        input_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
        handler=handle_read_memory_audit,
    ),
    # ----- state-changing ----------------------------------------------
    ToolDef(
        name="create_plan",
        description=(
            "Create a task, scan the workspace, run the rule-based "
            "planner for the chosen skill, validate the plan, and run "
            "the policy guard risk check. Returns the new task_id you "
            "pass to dry_run / execute_plan / rollback_run. LLM planning "
            "is not supported here — use the CLI for that."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace root path."},
                "goal": {"type": "string", "description": "Free-text task goal."},
                "skill": {
                    "type": "string",
                    "default": "folder_organizer",
                    "description": "Skill name (folder_organizer / pdf_indexer / data_reporter / data_analyzer / external).",
                },
            },
            "required": ["workspace", "goal"],
        },
        handler=handle_create_plan,
    ),
    ToolDef(
        name="dry_run",
        description=(
            "Render the dry-run markdown for an existing planned task "
            "AND mint a one-shot approval_token bound to the plan + "
            "dry-run + workspace. The token is required by execute_plan. "
            "Read-only with respect to the workspace (writes only to "
            "the run's dry_run.md + approval_token.json artifacts)."
        ),
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
        handler=handle_dry_run,
    ),
    ToolDef(
        name="execute_plan",
        description=(
            "Execute a planned task. Requires an approval_token returned "
            "by a prior dry_run call. Token has 10-minute TTL, is "
            "single-use, and is bound to the exact plan + dry_run + "
            "workspace state — any drift invalidates it. Verifier runs "
            "automatically after execute. Returns success + counts + "
            "verifier outcome."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "approval_token": {
                    "type": "string",
                    "description": (
                        "The token string returned by the most recent dry_run "
                        "call for this task. Single-use, expires in 10 min."
                    ),
                },
            },
            "required": ["task_id", "approval_token"],
        },
        handler=handle_execute_plan,
    ),
    ToolDef(
        name="rollback_preview",
        description=(
            "Read-only preview of what rollback_run would do. For each "
            "manifest entry: the inverse op, the file it would touch, "
            "and a ``drift`` flag (None = clean; string = the user has "
            "modified the file since execute and rollback would clobber "
            "their changes). Always call this before rollback_run to "
            "give the user a confirmation page."
        ),
        input_schema={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
        handler=handle_rollback_preview,
    ),
    ToolDef(
        name="rollback_run",
        description=(
            "Undo a previously-executed run using its rollback manifest. "
            "Phase 7.1: by default refuses entries whose target file has "
            "drifted from the executor-recorded hash (user edits after "
            "execute). Pass ``force=true`` to override (manual edits "
            "will be lost). Use rollback_preview first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "force": {
                    "type": "boolean",
                    "description": (
                        "Override drift detection. WARNING: clobbers any "
                        "manual edits made after execute. Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["task_id"],
        },
        handler=handle_rollback_run,
    ),
    # ----- memory mutations --------------------------------------------
    ToolDef(
        name="memory_forbid_path",
        description=(
            "Add a workspace-relative path to forbidden_paths. The kernel "
            "policy guard will reject any future action touching this path."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=handle_memory_forbid_path,
    ),
    ToolDef(
        name="memory_unforbid_path",
        description=(
            "Remove a path from forbidden_paths. **Dangerous** — this "
            "weakens a user-set safety boundary, so it is NOT exposed "
            "over MCP by default. To enable, set "
            "LOCALFLOW_MCP_ALLOW_DANGEROUS=1 in the server's environment. "
            "The CLI ``localflow memory unforbid`` is always available "
            "for the local user."
        ),
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=handle_memory_unforbid_path,
        dangerous=True,
    ),
    ToolDef(
        name="memory_set_naming_style",
        description=(
            "Set the naming_style preference. Valid values: original, "
            "snake_case, kebab-case, lower. Applied by folder_organizer "
            "to rename targets."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "enum": ["original", "snake_case", "kebab-case", "lower"],
                },
            },
            "required": ["value"],
        },
        handler=handle_memory_set_naming_style,
    ),
    ToolDef(
        name="memory_unset_naming_style",
        description="Reset naming_style to default (original).",
        input_schema={"type": "object", "properties": {}},
        handler=handle_memory_unset_naming_style,
    ),
    ToolDef(
        name="memory_set_prefer_llm_planner",
        description=(
            "Set the prefer_llm_planner toggle. When True, the UI auto-detect "
            "routes every LLM-capable skill to the LLM planner regardless of "
            "goal text. Defaults to False."
        ),
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "boolean"}},
            "required": ["value"],
        },
        handler=handle_memory_set_prefer_llm_planner,
    ),
    ToolDef(
        name="memory_unset_prefer_llm_planner",
        description="Reset prefer_llm_planner to default (False).",
        input_schema={"type": "object", "properties": {}},
        handler=handle_memory_unset_prefer_llm_planner,
    ),
]


def get_tool(name: str) -> ToolDef | None:
    """Look up a tool by name from the **currently visible** set.

    Dangerous tools (e.g. memory_unforbid_path) are hidden when
    LOCALFLOW_MCP_ALLOW_DANGEROUS is not set — they look like unknown
    tools to MCP clients. Tests and direct CLI users can still reach
    them via the handler functions directly.
    """
    for t in visible_tools():
        if t.name == name:
            return t
    return None
