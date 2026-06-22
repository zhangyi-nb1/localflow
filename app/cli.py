from __future__ import annotations

# Load .env BEFORE any module-level os.environ.get(...) call below — Typer
# evaluates Option() defaults at import time, so the file has to be loaded
# before those lines run. override=False keeps shell-set vars winning over
# the file, so the precedence is: shell env > .env > code default.
from dotenv import find_dotenv as _find_dotenv
from dotenv import load_dotenv as _load_dotenv

_dotenv_path = _find_dotenv(usecwd=True)
if _dotenv_path:
    _load_dotenv(_dotenv_path, override=False)

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Windows consoles default to legacy code pages (cp936 / cp1252) that can't
# encode the Unicode glyphs Rich emits when rendering Markdown. Promote
# stdout/stderr to UTF-8 if the runtime allows it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from app.agent import (
    AnthropicClient,
    LLMClient,
    LLMClientError,
    OpenAIClient,
    PlannerFailure,
)
from app.harness import control_loop
from app.harness.approval import ask_approval
from app.harness.audit import AuditLogger
from app.harness.rollback import Rollback
from app.harness.trace import TraceLogger
from app.memory import (
    MemoryPreferences,
    MemoryStore,
    MemoryStoreError,
    NamingStyle,
)
from app.schemas import TaskSpec
from app.skills import SkillError, get_default_registry
from app.storage.run_store import RunStore

app = typer.Typer(
    add_completion=False,
    help="LocalFlow — safe automation harness for personal workspaces.",
    no_args_is_help=True,
)
console = Console()


# Phase 34.0 — F-1 fix. Root-level ``--version`` callback. Sources the
# version from ``localflow_kernel.__version__`` so the kernel package
# is the single source of truth; the CLI just prints what the kernel
# says it is.
def _print_version_and_exit(value: bool) -> None:
    if not value:
        return
    try:
        from localflow_kernel import __version__ as _kernel_version
    except Exception:
        _kernel_version = "unknown"
    console.print(f"localflow {_kernel_version}")
    raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        callback=_print_version_and_exit,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """Root callback — runs before any subcommand. Hosts global flags
    (currently just ``--version``). The Typer no_args_is_help flag set
    on ``app`` above means bare ``localflow`` still prints help.

    Phase 36.x — auto-load a project ``.env`` here (the one entry point
    that fires for every CLI command + is inherited by the ``ui-serve``
    / ``mcp-serve`` subprocesses via ``os.environ``). ``setdefault``
    semantics mean an already-exported var always wins; pytest runs
    skip the load so the test suite stays key-independent. Set
    ``LOCALFLOW_NO_DOTENV=1`` to opt out."""
    from app.runtime_env import load_project_dotenv

    load_project_dotenv()
    return None


# --------------------------------------------------------------------- inspect


@app.command("inspect")
def cmd_inspect(
    workspace: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, resolve_path=True
    ),
    no_hash: bool = typer.Option(
        False, "--no-hash", help="Skip SHA-256 hashing (faster, but no duplicate detection)."
    ),
    no_preview: bool = typer.Option(
        False,
        "--no-preview",
        help="Skip content extraction (faster, no Phase 2 semantic awareness).",
    ),
) -> None:
    """Scan a workspace and print a summary. No writes."""
    snapshot = control_loop.run_inspect(
        workspace,
        task_id="adhoc",
        compute_hash=not no_hash,
        compute_preview=not no_preview,
    )
    by_type: dict[str, int] = {}
    by_size: dict[str, int] = {}
    with_preview = 0
    for f in snapshot.files:
        by_type[f.file_type] = by_type.get(f.file_type, 0) + 1
        by_size[f.file_type] = by_size.get(f.file_type, 0) + f.size_bytes
        if f.text_preview:
            with_preview += 1

    table = Table(title=f"Workspace: {workspace}")
    table.add_column("Category", style="cyan")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    for cat in sorted(by_type):
        table.add_row(cat, str(by_type[cat]), _fmt_size(by_size[cat]))
    console.print(table)
    console.print(
        f"[dim]Total: {snapshot.total_files} file(s), "
        f"{_fmt_size(snapshot.total_size_bytes)}  ·  "
        f"text previews: {with_preview}/{snapshot.total_files}[/]"
    )


# --------------------------------------------------------------------- plan


@app.command("plan")
def cmd_plan(
    workspace: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, resolve_path=True
    ),
    goal: str = typer.Option(
        ..., "--goal", "-g", help="Natural-language description of what to do."
    ),
    skill: str = typer.Option(
        "folder_organizer",
        "--skill",
        help="Which skill to use. Available: folder_organizer, pdf_indexer, data_reporter.",
    ),
    planner: str = typer.Option(
        "rule",
        "--planner",
        help=(
            "`rule` (deterministic, ~0.3s — default, use this for file-type "
            "sorting) or `llm` (~20s on a typical proxy; use ONLY when the "
            "goal requires semantic understanding, e.g. content-based renames "
            "or topic clustering)."
        ),
    ),
    llm_provider: str = typer.Option(
        os.environ.get("LOCALFLOW_LLM_PROVIDER", "openai"),
        "--llm-provider",
        help="LLM provider (only used when --planner=llm): `openai` or `anthropic`.",
    ),
    llm_model: Optional[str] = typer.Option(
        None,
        "--llm-model",
        help="Override default model. Falls back to env var or code default.",
    ),
    no_hash: bool = typer.Option(
        False, "--no-hash", help="Skip SHA-256 hashing (no duplicate detection)."
    ),
    no_preview: bool = typer.Option(
        False,
        "--no-preview",
        help=(
            "Skip content extraction (PDF text / .md / .txt / code first-N chars). "
            "Faster scan, but the LLM planner loses semantic awareness. "
            "Rule planner is unaffected."
        ),
    ),
    max_repair: int = typer.Option(
        3, "--max-repair", help="Max LLM repair attempts (llm planner only)."
    ),
) -> None:
    """Create a task, scan the workspace, and produce a structured ActionPlan."""
    registry = get_default_registry()
    try:
        skill_obj = registry.require(skill)
    except SkillError as exc:
        raise typer.BadParameter(str(exc))
    if planner not in {"rule", "llm"}:
        raise typer.BadParameter(f"planner must be 'rule' or 'llm', got {planner!r}")
    if planner == "llm" and not skill_obj.supports_llm():
        raise typer.BadParameter(
            f"skill {skill!r} does not support --planner llm; use --planner rule "
            f"(or pick a skill that does)"
        )
    if llm_provider not in {"anthropic", "openai"}:
        raise typer.BadParameter(
            f"llm-provider must be 'anthropic' or 'openai', got {llm_provider!r}"
        )

    store = RunStore.create()

    # Phase 5: load user preferences from memory store and project them onto
    # the TaskSpec. Defaults (empty list / ORIGINAL) leave behavior identical
    # to a pre-Phase-5 run.
    prefs = _load_memory_prefs_safe()
    preferences: dict = {}
    if prefs.naming_style != NamingStyle.ORIGINAL:
        preferences["naming_style"] = prefs.naming_style.value

    task = TaskSpec(
        task_id=store.task_id,
        user_goal=goal,
        workspace_root=str(workspace),
        skill=skill,
        constraints=[
            "do not delete any file",
            "do not overwrite existing files",
            "all paths must remain inside workspace_root",
        ],
        allowed_actions=["mkdir", "copy", "move", "rename", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=list(prefs.forbidden_paths),
        preferences=preferences,
    )
    if not prefs.is_default():
        _print_applied_prefs(prefs)
    store.save_task(task)
    audit = AuditLogger(store.audit_log_path)
    audit.log("task.created", task_id=task.task_id, goal=goal, planner=planner)
    # v0.10.1: every CLI-driven run gets a trace stream by default.
    # Trace emission is observation-only (additive — §10.7 invariant
    # held by the optional-kwarg pattern from Phase 9).
    trace = TraceLogger(store.trace_path)

    snapshot = control_loop.run_inspect(
        workspace,
        task_id=task.task_id,
        compute_hash=not no_hash,
        compute_preview=not no_preview,
    )
    store.save_workspace(snapshot)

    if planner == "rule":
        plan = skill_obj.plan(task, snapshot)
    else:
        try:
            client = _build_llm_client(llm_provider, llm_model)
        except LLMClientError as exc:
            console.print(f"[red]LLM client error:[/] {exc}")
            raise typer.Exit(code=2)

        effective_model = llm_model or _resolve_default_model(llm_provider, client)
        endpoint = _endpoint_for(llm_provider, client)
        console.print(
            f"[dim]LLM call →[/] provider=[cyan]{llm_provider}[/]  "
            f"model=[cyan]{effective_model}[/]  endpoint=[cyan]{endpoint}[/]  "
            f"max_repair=[cyan]{max_repair}[/]"
        )

        try:
            plan = _stream_plan(
                console=console,
                task=task,
                snapshot=snapshot,
                client=client,
                max_repair=max_repair,
                provider=llm_provider,
                model=effective_model,
                skill_obj=skill_obj,
                trace=trace,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Aborted by user (Ctrl+C).[/]")
            raise typer.Exit(code=130)
        except LLMClientError as exc:
            console.print(f"[red]LLM client error:[/] {exc}")
            raise typer.Exit(code=2)
        except PlannerFailure as exc:
            console.print(f"[red]LLM planner failed after {max_repair} attempt(s):[/]")
            console.print(str(exc))
            audit.log(
                "plan.failed",
                planner="llm",
                provider=llm_provider,
                attempts=[a.to_dict() for a in exc.attempts],
            )
            raise typer.Exit(code=2)

    skill_obj.validate(plan)
    assessment = control_loop.run_risk_check(task, plan, trace=trace)
    store.save_plan(plan)
    audit.log(
        "plan.created",
        plan_id=plan.plan_id,
        action_count=len(plan.actions),
        risk=assessment.risk_level.value,
        planner=planner,
    )

    planner_label = planner if planner == "rule" else f"llm:{llm_provider}"
    console.print(
        Panel.fit(
            f"[bold green]Task created[/]: [cyan]{task.task_id}[/]\n"
            f"Planner: [cyan]{planner_label}[/]  ·  "
            f"Plan: [cyan]{plan.plan_id}[/]  ·  "
            f"Actions: [bold]{len(plan.actions)}[/]  ·  "
            f"Risk: [bold]{assessment.risk_level.value}[/]\n"
            f"Files scanned: {snapshot.total_files}  ·  Goal: {goal}",
            title="LocalFlow",
            border_style="green",
        )
    )
    if assessment.warnings:
        console.print("[yellow]Warnings:[/]")
        for w in assessment.warnings:
            console.print(f"  • {w}")
    console.print(f"\nNext: [bold]localflow dry-run --task-id {task.task_id}[/]")


# --------------------------------------------------------------------- dry-run


@app.command("dry-run")
def cmd_dry_run(
    task_id: str = typer.Option(..., "--task-id", help="Task identifier (from `plan`)."),
) -> None:
    """Render and display the dry-run preview without touching the filesystem."""
    store = RunStore(task_id=task_id)
    if not store.plan_path.exists():
        raise typer.BadParameter(f"no plan found for task {task_id} — run `localflow plan` first")
    task = store.load_task()
    plan = store.load_plan()
    trace = TraceLogger(store.trace_path)
    assessment = control_loop.run_risk_check(task, plan, trace=trace)
    md = control_loop.run_dry_run(task, plan, assessment, store, trace=trace)
    console.print(Markdown(md))
    console.print(
        f"\n[dim]Wrote: {store.dry_run_path}[/]\n"
        f"Next: [bold]localflow execute --task-id {task_id}[/]"
    )


# --------------------------------------------------------------------- ledger (Phase 14.x)


@app.command("ledger")
def cmd_ledger(
    workspace: Path = typer.Argument(..., help="Workspace root to inventory."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write JSON to this path (default: stdout).",
    ),
    no_hash: bool = typer.Option(
        False, "--no-hash", help="Skip sha256 computation (faster on large workspaces)."
    ),
) -> None:
    """v0.14.1 — emit a typed ``source_ledger.json`` for the workspace.

    Walks every file under ``workspace``, classifies it via the
    standard LocalFlow file_type map, and produces a
    :class:`SourceLedger` payload with sha256 + size + top-level
    category. Useful as a standalone audit step or as the basis for
    the agent's pack-synthesis stage.
    """
    from app.tools.source_ledger_ops import build_from_workspace

    if not workspace.exists() or not workspace.is_dir():
        raise typer.BadParameter(f"workspace not found or not a directory: {workspace}")

    ledger = build_from_workspace(workspace, compute_hash=not no_hash)
    payload = ledger.model_dump_json(indent=2)
    if output is None:
        console.print_json(payload)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        console.print(f"[green]Wrote[/] {output}  ·  {len(ledger.entries)} entries")


# --------------------------------------------------------------------- revise


@app.command("revise")
def cmd_revise(
    task_id: str = typer.Option(..., "--task-id", help="Task identifier from a prior `plan`."),
    hint: str = typer.Option(
        ...,
        "--hint",
        "-h",
        help=(
            "Clarification for the LLM — what the previous plan got wrong "
            "or what you actually want."
        ),
    ),
) -> None:
    """Generate a revised plan v(N+1) for an existing task — Phase 11.

    No execution happens. The harness re-asks the skill's LLM planner
    with your prior plan + the hint, validates the new plan, and writes
    ``plans/plan_v<n>.json`` + mirrors to ``plan.json`` so a subsequent
    ``localflow execute --task-id <id>`` runs the refined version. The
    workspace stays untouched. Capped at 5 revisions per task.
    """
    store = RunStore(task_id=task_id)
    if not store.plan_path.exists():
        raise typer.BadParameter(f"no plan found for task {task_id} — run `localflow plan` first")
    task = store.load_task()
    prior_plan = store.load_plan()
    snapshot = store.load_workspace()
    skill_obj = get_default_registry().require(task.skill)
    trace = TraceLogger(store.trace_path)
    audit = AuditLogger(store.audit_log_path)

    try:
        with console.status(f"Asking {task.skill} to revise plan…"):
            new_plan, new_version = control_loop.run_revise(
                task,
                snapshot,
                prior_plan,
                hint,
                skill=skill_obj,
                run_store=store,
                trace=trace,
                audit=audit,
            )
    except SkillError as exc:
        console.print(f"[red]Revise failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Revised:[/] task {task_id} now at plan v{new_version}  "
        f"·  {len(new_plan.actions)} actions\n"
        f"[dim]Saved: {store.plan_version_path(new_version)}[/]\n"
        f"Next: [bold]localflow dry-run --task-id {task_id}[/]"
        f" then [bold]localflow execute --task-id {task_id}[/]"
    )


# --------------------------------------------------------------------- execute


@app.command("execute")
def cmd_execute(
    task_id: str = typer.Option(..., "--task-id", help="Task identifier."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive approval prompt."),
    resume: bool = typer.Option(
        False, "--resume", help="Resume from checkpoint, skipping completed actions."
    ),
    no_auto_repair: bool = typer.Option(
        False,
        "--no-auto-repair",
        help=(
            "Phase 13 — skip the semantic verifier + auto-repair loop "
            "even when memory pref enable_semantic_verifier is True."
        ),
    ),
    react: bool = typer.Option(
        False,
        "--react",
        help=(
            "Phase 26 — opt into the execute-stage react loop. The "
            "executor consults the LLM between actions and may apply "
            "REPLACE / INSERT / SKIP within a 3-step drift budget. "
            "Uses the configured LLM provider (LOCALFLOW_LLM_PROVIDER, "
            "openai by default); see docs/REACT_LOOP.md for the safety model."
        ),
    ),
    react_max_drift: int = typer.Option(
        3,
        "--react-max-drift",
        min=0,
        max=20,
        help=(
            "Phase 26 — drift budget for --react. Higher = more LLM "
            "latitude to deviate from the approved plan; default 3."
        ),
    ),
    confirm_policy: Optional[str] = typer.Option(
        None,
        "--confirm-policy",
        help=(
            "Phase 27 — per-action approval granularity. One of "
            "``never`` / ``always`` / ``on_high_risk`` / ``on_write``. "
            "Defaults to ``never`` (= --yes behaviour) when omitted. "
            "Use ``on_high_risk`` to pause only on HIGH-risk actions, "
            "``on_write`` to pause on every mkdir / move / copy / "
            "rename. See docs/PHASE_27_DESIGN.md."
        ),
    ),
    workspace_spec: Optional[str] = typer.Option(
        None,
        "--workspace",
        help=(
            "Phase 29 — workspace backend. ``local`` (default) runs "
            "against the host filesystem. ``docker:<image>`` runs the "
            "workspace inside a Docker container; user files stay "
            "isolated from the host until promoted. Requires Docker "
            "installed. Example: ``--workspace docker:python:3.12-slim``. "
            "See docs/DOCKER_WORKSPACE.md."
        ),
    ),
) -> None:
    """Approve and execute the plan. Records every change for rollback."""
    store = RunStore(task_id=task_id)
    if not store.plan_path.exists():
        raise typer.BadParameter(f"no plan found for task {task_id}")
    task = store.load_task()
    plan = store.load_plan()
    snapshot = store.load_workspace()
    trace = TraceLogger(store.trace_path)
    audit = AuditLogger(store.audit_log_path)
    assessment = control_loop.run_risk_check(task, plan, trace=trace)

    if assessment.risk_level.value == "blocked":
        console.print("[red]Plan blocked by policy guard. Aborting.[/]")
        for w in assessment.warnings:
            console.print(f"  • {w}")
        raise typer.Exit(code=2)

    # Always render a fresh dry-run preview before asking for approval.
    md = control_loop.run_dry_run(task, plan, assessment, store, trace=trace)
    console.print(Markdown(md))

    write_count = sum(1 for a in plan.actions if a.is_write())
    decision = ask_approval(
        risk_level=assessment.risk_level.value,
        write_action_count=write_count,
        auto_approve=yes,
        console=console,
    )
    audit.log(
        "approval.decision",
        approved=decision.approved,
        reason=decision.reason,
    )
    if not decision.approved:
        console.print("[yellow]Execution cancelled.[/]")
        raise typer.Exit(code=1)

    # Phase 27.0 schema + Phase 27.1 per-action wiring — parse +
    # validate --confirm-policy, then thread the policy + an
    # interactive approver callback through run_execute to the
    # executor's per-action gate.
    confirm_policy_obj = None
    action_approver = None
    if confirm_policy is not None:
        from app.harness.approval import ask_action_approval
        from app.schemas import ConfirmationPolicy, ConfirmationPolicyType

        try:
            _policy_type = ConfirmationPolicyType(confirm_policy)
        except ValueError as exc:
            valid = ", ".join(v.value for v in ConfirmationPolicyType)
            console.print(
                f"[red]Invalid --confirm-policy {confirm_policy!r}.[/] Choose one of: {valid}."
            )
            raise typer.Exit(code=2) from exc
        confirm_policy_obj = ConfirmationPolicy(policy_type=_policy_type)
        audit.log(
            "confirmation_policy.selected",
            policy_type=confirm_policy_obj.policy_type.value,
            risk_threshold=confirm_policy_obj.risk_threshold.value,
        )
        console.print(
            f"[cyan]confirm_policy={confirm_policy_obj.policy_type.value}[/] "
            "— per-action prompts will appear for gated actions "
            "(see docs/PHASE_27_DESIGN.md)"
        )

        def action_approver(action):  # noqa: E306,F811
            return ask_action_approval(
                action,
                policy=confirm_policy_obj,
                console=console,
            )

    skill_obj = get_default_registry().require(task.skill)

    # Phase 13 — read memory prefs to decide whether to enable the
    # semantic verifier + auto-repair loop. The --no-auto-repair CLI
    # flag forces opt-out for this single run.
    prefs = MemoryStore().load()
    enable_semantic = prefs.enable_semantic_verifier and not no_auto_repair
    max_auto_repairs = prefs.max_auto_repairs if not no_auto_repair else 0

    if enable_semantic:
        plan, outcome, verification, semantic, repair_outcome = control_loop.run_with_auto_repair(
            task,
            plan,
            snapshot,
            skill=skill_obj,
            run_store=store,
            approved=True,
            enable_semantic=True,
            max_auto_repairs=max_auto_repairs,
            resume=resume,
            trace=trace,
            audit=audit,
        )
    else:
        # Phase 26 — build optional react inputs. The control_loop
        # passes them through to executor.execute; when react=False
        # (the default) the kwargs no-op and the batch path runs.
        react_cfg = None
        llm_client = None
        if react:
            from app.agent.judge import get_default_client_or_none
            from app.schemas import ReactConfig

            react_cfg = ReactConfig(enabled=True, max_drift=react_max_drift)
            # Provider-aware: honour LOCALFLOW_LLM_PROVIDER (openai by
            # default) like every other LLM path. The previous hard-coded
            # AnthropicClient() made --react unreachable in any non-Anthropic
            # setup — it raised "ANTHROPIC_API_KEY not set" even when an
            # OpenAI-compatible client was configured. (R4 finding.)
            llm_client = get_default_client_or_none()
            if llm_client is None:
                console.print(
                    "[red]--react requires a working LLM client[/] "
                    "(set LOCALFLOW_LLM_PROVIDER + the provider's API key, "
                    "or drop --react to use the deterministic batch executor)."
                )
                raise typer.Exit(code=2)
            console.print(
                f"[cyan]react_mode=ON[/]  drift_budget={react_max_drift}  (see docs/REACT_LOOP.md)"
            )

        # Phase 29.2 — optional --workspace docker:<image> backend.
        # When supplied, parse + lifecycle-manage the workspace; when
        # omitted, control_loop builds a default LocalWorkspace.
        workspace_obj = None
        if workspace_spec is not None:
            from app.tools.docker_workspace import (
                DockerUnavailable,
                DockerWorkspace,
            )
            from app.tools.workspace import parse_workspace_spec

            try:
                workspace_obj = parse_workspace_spec(
                    workspace_spec,
                    workspace_root=Path(task.workspace_root),
                )
            except ValueError as exc:
                console.print(f"[red]Invalid --workspace spec:[/] {exc}")
                raise typer.Exit(code=2) from exc

            # DockerWorkspace needs explicit lifecycle; LocalWorkspace
            # is just-a-helper-object and needs no start/close.
            if isinstance(workspace_obj, DockerWorkspace):
                console.print(
                    f"[cyan]workspace={workspace_spec}[/]  "
                    "(workspace runs inside a Docker container; "
                    "see docs/DOCKER_WORKSPACE.md)"
                )
                try:
                    workspace_obj.start()
                except DockerUnavailable as exc:
                    console.print(f"[red]Docker not available:[/] {exc}")
                    raise typer.Exit(code=2) from exc

        try:
            outcome = control_loop.run_execute(
                task,
                plan,
                store,
                approved=True,
                resume=resume,
                trace=trace,
                react_mode=react,
                react_config=react_cfg,
                llm_client=llm_client,
                confirmation_policy=confirm_policy_obj,
                action_approver=action_approver,
                workspace=workspace_obj,
            )
            verification = control_loop.run_verify(
                task, plan, store, outcome, snapshot, trace=trace
            )
        finally:
            # Phase 29.2 — always tear down a started DockerWorkspace,
            # even on exception, so a crashed exec doesn't leave a
            # container running. LocalWorkspace's close is a no-op.
            if workspace_obj is not None and hasattr(workspace_obj, "close"):
                try:
                    workspace_obj.close()
                except Exception:
                    pass
        semantic = None
        repair_outcome = None

    # Skill-specific final_report: each Skill renders its own markdown.
    report = skill_obj.report(task=task, plan=plan, outcome=outcome, verification=verification)
    store.write_text(store.final_report_path, report)

    badge = "[green]OK[/]" if outcome.success and verification.passed else "[red]FAIL[/]"
    console.print(
        f"\n{badge}  executed: {len(outcome.records)} actions  ·  "
        f"verify: {'passed' if verification.passed else 'failed'}"
    )
    if semantic is not None:
        sem_badge = "[green]OK[/]" if semantic.passed else "[red]FAIL[/]"
        console.print(f"semantic: {sem_badge}  ·  {semantic.summary}")
        if repair_outcome is not None and repair_outcome.attempts > 0:
            verb = "auto-repaired" if repair_outcome.repaired else "auto-repair attempted"
            console.print(f"{verb} {repair_outcome.attempts}× (halt: {repair_outcome.halt_reason})")
    console.print(
        f"[dim]Report: {store.final_report_path}[/]\n"
        f"To undo: [bold]localflow rollback --run-id {task_id}[/]"
    )


# --------------------------------------------------------------------- verify-semantic (Phase 13)


@app.command("verify-semantic")
def cmd_verify_semantic(
    task_id: str = typer.Option(..., "--task-id", help="Task identifier (from `execute`)."),
) -> None:
    """Phase 13 — run LLM-as-judge semantic graders against an existing
    run's outputs. Report-only: no rollback, no re-execute, no repair.

    Exit code 0 when every grader passes, 1 otherwise. Useful for
    grading a completed run after the fact or in CI."""
    from app.harness.semantic_verifier import SemanticVerifier

    store = RunStore(task_id=task_id)
    if not store.plan_path.exists():
        raise typer.BadParameter(f"no plan found for task {task_id}")
    task = store.load_task()
    plan = store.load_plan()
    snapshot = store.load_workspace()
    structural = store.load_verification() if store.exists(store.VERIFY_JSON) else None
    if structural is None:
        console.print("[red]No verify_report.json — run `localflow execute` first.[/]")
        raise typer.Exit(code=2)

    trace = TraceLogger(store.trace_path)
    verifier = SemanticVerifier(Path(task.workspace_root), trace=trace)
    # Reconstruct ExecutionRecord + manifest from on-disk artifacts.
    manifest = store.load_rollback() if store.exists(store.ROLLBACK_JSON) else None
    execution_records: list = []
    if store.exists(store.ACTIONS_JSON):
        from app.schemas import ExecutionRecord

        raw = store.read_json(store.actions_path)
        if isinstance(raw, list):
            execution_records = [ExecutionRecord.model_validate(r) for r in raw]
    if manifest is None:
        from app.schemas import RollbackManifest

        manifest = RollbackManifest(
            task_id=task.task_id, run_id=task.task_id, entries=[], file_hashes_before={}
        )

    result = verifier.verify(
        task=task,
        plan=plan,
        execution_records=execution_records,
        manifest=manifest,
        snapshot_before=snapshot,
        snapshot_after=None,
        structural=structural,
        run_id=task.task_id,
    )
    store.write_model(store.semantic_verify_path, result)

    table = Table(title=f"Semantic verdicts — task {task_id}")
    table.add_column("Grader", style="cyan")
    table.add_column("Passed")
    table.add_column("Reason")
    for v in result.verdicts:
        badge = "[green]✓[/]" if v.passed else "[red]✗[/]"
        table.add_row(v.grader, badge, v.reason[:80])
    console.print(table)
    console.print(
        f"\n{'[green]PASSED[/]' if result.passed else '[red]FAILED[/]'} — {result.summary}"
    )
    raise typer.Exit(code=0 if result.passed else 1)


# --------------------------------------------------------------------- repair (Phase 13)


@app.command("repair")
def cmd_repair(
    task_id: str = typer.Option(..., "--task-id", help="Task identifier."),
    max_attempts: int | None = typer.Option(
        None,
        "--max-attempts",
        help=(
            "Max repair iterations. Default: memory pref max_auto_repairs. "
            "0 means 'run semantic verifier in report-only mode'."
        ),
    ),
) -> None:
    """Phase 13 — manually drive one auto-repair cycle on an existing
    task. Runs the semantic verifier; on rejection, rolls back the
    most recent execution, calls revise with a grader-derived hint,
    re-executes + re-verifies.

    Different from ``localflow revise``: revise needs a user hint and
    only generates a new plan version; repair runs the FULL rollback +
    re-execute pipeline driven by an auto-generated semantic hint."""
    from app.harness.repair_loop import run_repair_loop
    from app.harness.semantic_verifier import SemanticVerifier

    store = RunStore(task_id=task_id)
    if not store.exists(store.PLAN_JSON):
        raise typer.BadParameter(f"no plan found for task {task_id}")
    task = store.load_task()
    plan = store.load_plan()
    snapshot = store.load_workspace()
    if not store.exists(store.VERIFY_JSON):
        console.print("[red]No verify_report.json — run `localflow execute` first.[/]")
        raise typer.Exit(code=2)
    structural = store.load_verification()
    if not store.exists(store.ROLLBACK_JSON):
        console.print(
            "[red]No rollback_manifest.json — task wasn't executed; nothing to repair.[/]"
        )
        raise typer.Exit(code=2)
    manifest = store.load_rollback()
    from app.harness.executor import ExecutionOutcome
    from app.schemas import ExecutionRecord

    raw = store.read_json(store.actions_path) if store.exists(store.ACTIONS_JSON) else []
    execution_records = (
        [ExecutionRecord.model_validate(r) for r in raw] if isinstance(raw, list) else []
    )
    outcome = ExecutionOutcome(
        run_id=task.task_id,
        records=execution_records,
        manifest=manifest,
        success=structural.passed,
    )

    trace = TraceLogger(store.trace_path)
    audit = AuditLogger(store.audit_log_path)
    sem_verifier = SemanticVerifier(Path(task.workspace_root), trace=trace)
    semantic = sem_verifier.verify(
        task=task,
        plan=plan,
        execution_records=execution_records,
        manifest=manifest,
        snapshot_before=snapshot,
        snapshot_after=None,
        structural=structural,
        run_id=task.task_id,
    )
    store.write_model(store.semantic_verify_path, semantic)

    if semantic.passed:
        console.print("[green]Nothing to repair — semantic verdicts all passed.[/]")
        return

    prefs = MemoryStore().load()
    attempts = max_attempts if max_attempts is not None else prefs.max_auto_repairs
    if attempts <= 0:
        console.print(
            "[yellow]Semantic verifier rejected but max_attempts=0 — report-only mode.[/]"
        )
        for v in semantic.failed_verdicts:
            console.print(f"  • {v.grader}: {v.reason}")
        raise typer.Exit(code=1)

    skill_obj = get_default_registry().require(task.skill)
    with console.status(f"Auto-repairing (up to {attempts}×)…"):
        _, state, repair_outcome = run_repair_loop(
            task,
            snapshot=snapshot,
            current_plan=plan,
            current_outcome=outcome,
            current_structural=structural,
            current_semantic=semantic,
            skill=skill_obj,
            run_store=store,
            max_attempts=attempts,
            trace=trace,
            audit=audit,
        )

    badge = "[green]REPAIRED[/]" if repair_outcome.repaired else "[red]STILL FAILING[/]"
    console.print(
        f"\n{badge} — {repair_outcome.attempts} attempt(s)  ·  halt: {repair_outcome.halt_reason}"
    )
    if state.semantic is not None:
        console.print(f"final semantic verdict: {state.semantic.summary}")
    raise typer.Exit(code=0 if repair_outcome.repaired else 1)


# --------------------------------------------------------------------- verify


@app.command("verify")
def cmd_verify(
    task_id: str = typer.Option(..., "--task-id", help="Task identifier."),
) -> None:
    """Re-run the independent verifier against current workspace state."""
    store = RunStore(task_id=task_id)
    if not store.verify_path.exists():
        raise typer.BadParameter(
            f"no verification record for task {task_id} — run `localflow execute` first"
        )
    result = store.load_verification()
    badge = "[green]PASSED[/]" if result.passed else "[red]FAILED[/]"
    console.print(f"{badge}  {result.summary}\n")
    table = Table(title="Verifier checks")
    table.add_column("Check", style="cyan")
    table.add_column("Result")
    table.add_column("Detail", style="dim")
    for c in result.checks:
        table.add_row(c.name, "ok" if c.passed else "FAIL", c.detail)
    console.print(table)


# --------------------------------------------------------------------- rollback


@app.command("rollback")
def cmd_rollback(
    run_id: str = typer.Option(
        ..., "--run-id", help="Run identifier (same as task_id in Phase 0)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Proceed even if files have been modified since execute "
            "(hash drift). Use with care — this clobbers your manual edits."
        ),
    ),
    stage: str | None = typer.Option(
        None,
        "--stage",
        help=(
            "v0.15 — Roll back only one stage of a TaskGraph run. "
            "The stage_id must match a stage of the run; only entries "
            "whose action_id starts with ``<stage_id>.`` are replayed. "
            "Useful when one stage of a multi-stage graph misbehaved "
            "and you want to retry just that stage."
        ),
    ),
) -> None:
    """Undo a previously-executed run using its rollback manifest.

    Phase 7.1: by default, rollback refuses to clobber files the user
    has edited since execute (detected via sha256 drift against the
    executor's recorded ``after_hash``). Drifted entries are reported
    as **conflicts** and skipped. Pass ``--force`` to override.

    Phase 15: ``--stage <id>`` filters the aggregated TaskGraph
    manifest to just one stage's entries before replaying. The other
    stages stay applied — use this to surgically retry a single stage
    of a multi-stage graph.
    """
    from app.harness.rollback import filter_manifest_to_stage
    from app.schemas import TaskGraph

    store = RunStore(task_id=run_id)
    if not store.rollback_path.exists():
        raise typer.BadParameter(f"no rollback manifest for run {run_id}")
    # Phase 21.1: pack / TaskGraph runs only write taskgraph.json (no
    # root task.json). Fall back to reading workspace_root from the
    # graph so rollback works for both run shapes.
    if store.task_path.exists():
        workspace_root = store.load_task().workspace_root
    elif store.taskgraph_path.exists():
        workspace_root = store.read_model(store.taskgraph_path, TaskGraph).workspace_root
    else:
        raise typer.BadParameter(
            f"run {run_id} has neither task.json nor taskgraph.json — "
            "cannot determine workspace root"
        )
    manifest = store.load_rollback()

    if stage is not None:
        manifest = filter_manifest_to_stage(manifest, stage)
        if not manifest.entries:
            console.print(
                f"[yellow]No rollback entries match stage `{stage}` "
                f"(checked {run_id}'s manifest).[/]"
            )
            raise typer.Exit(code=1)

    if not yes:
        scope = f"stage `{stage}` in " if stage else ""
        confirm = typer.confirm(
            f"Roll back {len(manifest.entries)} change(s) {scope}{workspace_root}?"
        )
        if not confirm:
            console.print("[yellow]Rollback cancelled.[/]")
            raise typer.Exit(code=1)

    trace = TraceLogger(store.trace_path)
    rollback = Rollback(workspace_root=Path(workspace_root), run_store=store, trace=trace)
    outcome = rollback.run(manifest, force=force)
    badge = "[green]OK[/]" if outcome.success else "[red]PARTIAL[/]"
    console.print(
        f"{badge}  undone: {len(outcome.undone)}  ·  "
        f"failed: {len(outcome.failed)}  ·  conflicts: {len(outcome.conflicts)}"
    )
    for failure in outcome.failed:
        console.print(f"  [red]• failed:[/] {failure}")
    for conflict in outcome.conflicts:
        console.print(
            f"  [yellow]• conflict:[/] {conflict['action_id']} ({conflict['op']}) "
            f"on {conflict['target_path']!r} — {conflict['reason']}"
        )
    if outcome.conflicts and not force:
        console.print(
            "\n[yellow]Conflicts above were skipped to protect your edits.[/]\n"
            "[dim]Pass [bold]--force[/] to override and rollback anyway "
            "(your manual edits will be lost).[/]"
        )


# --------------------------------------------------------------------- status


@app.command("status")
def cmd_status(
    task_id: Optional[str] = typer.Option(None, "--task-id", help="Show details for one task."),
) -> None:
    """List runs in the LocalFlow store, or show one task in detail."""
    from app.storage.run_store import localflow_home

    if task_id:
        store = RunStore(task_id=task_id)
        files = [
            store.TASK_JSON,
            store.WORKSPACE_JSON,
            store.PLAN_JSON,
            store.DRY_RUN_MD,
            store.ACTIONS_JSON,
            store.EXECUTION_LOG,
            store.ROLLBACK_JSON,
            store.VERIFY_JSON,
            store.FINAL_REPORT_MD,
        ]
        table = Table(title=f"Task {task_id}")
        table.add_column("Artifact", style="cyan")
        table.add_column("Present")
        for name in files:
            table.add_row(name, "yes" if store.exists(name) else "—")
        console.print(table)
        return

    runs_root = localflow_home() / "runs"
    if not runs_root.exists():
        console.print("[dim]No runs yet.[/]")
        return
    table = Table(title=f"Runs at {runs_root}")
    table.add_column("Task ID", style="cyan")
    table.add_column("Has plan")
    table.add_column("Executed")
    table.add_column("Verified")
    for run_dir in sorted(runs_root.iterdir()):
        if not run_dir.is_dir():
            continue
        store = RunStore(task_id=run_dir.name)
        table.add_row(
            run_dir.name,
            "yes" if store.exists(store.PLAN_JSON) else "—",
            "yes" if store.exists(store.ACTIONS_JSON) else "—",
            "yes" if store.exists(store.VERIFY_JSON) else "—",
        )
    console.print(table)


# --------------------------------------------------------------------- skills


skills_app = typer.Typer(
    help="v0.16 — skill registry + external skill signing.",
    no_args_is_help=True,
)


@skills_app.command("sign")
def cmd_skills_sign(
    skill_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="External skill directory (must contain skill.py).",
    ),
) -> None:
    """v0.16 — compute + persist HMAC-SHA256 signature for an external skill.

    Reads the signing key from ``LOCALFLOW_SKILL_SIGNING_KEY`` or
    ``~/.localflow/memory/skill_signing_key``. Writes ``signature.txt``
    in the skill dir. After signing, the loader will only accept this
    exact skill.py + skill.yaml content under
    ``LOCALFLOW_REQUIRE_SIGNED_SKILLS=1``.
    """
    from app.skills.signing import load_signing_key, write_signature

    key = load_signing_key()
    if key is None:
        console.print(
            "[red]No signing key configured.[/] Set "
            "LOCALFLOW_SKILL_SIGNING_KEY (hex) or write "
            "~/.localflow/memory/skill_signing_key."
        )
        raise typer.Exit(code=2)
    if not (skill_dir / "skill.py").exists():
        console.print(f"[red]No skill.py at {skill_dir}.[/]")
        raise typer.Exit(code=2)
    digest = write_signature(skill_dir, key)
    console.print(f"[green]Signed[/] {skill_dir}  ·  digest [dim]{digest}[/]")


@skills_app.command("verify")
def cmd_skills_verify(
    skill_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="External skill directory to verify.",
    ),
) -> None:
    """v0.16 — verify the on-disk signature against a fresh HMAC.

    Exit 0 = valid, 1 = invalid, 2 = no key / no signature file.
    """
    from app.skills.signing import (
        compute_signature,
        load_signing_key,
        read_signature,
    )

    key = load_signing_key()
    if key is None:
        console.print("[red]No signing key configured.[/]")
        raise typer.Exit(code=2)
    expected = read_signature(skill_dir)
    if expected is None:
        console.print(f"[yellow]No signature.txt in {skill_dir}.[/]")
        raise typer.Exit(code=2)
    actual = compute_signature(skill_dir, key)
    if expected == actual:
        console.print(f"[green]Valid[/]  ·  digest {actual}")
        return
    console.print(
        f"[red]MISMATCH[/]\n  expected: {expected}\n  actual:   {actual}\n\n"
        "Re-sign with `localflow skills sign <dir>` after auditing the changes."
    )
    raise typer.Exit(code=1)


app.add_typer(skills_app, name="skills-sig")


@app.command("skills")
def cmd_skills(
    show_findings: bool = typer.Option(
        True,
        "--findings/--no-findings",
        help="Show the external skill discovery audit (where LocalFlow looked, what it found).",
    ),
) -> None:
    """List every registered skill (built-in + external) plus the
    Phase 4.1 external-skill discovery audit.

    Use this to verify a custom skill in ``.localflow/skills/`` was
    actually picked up — and if not, see WHY (path missing, no
    skill.py, import error, name collision, etc.).
    """
    from app.skills import default_external_skill_dirs, get_load_findings

    registry = get_default_registry()
    table = Table(title="Registered skills")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="dim")
    table.add_column("LLM?")
    table.add_column("Class")
    table.add_column("Tools", style="yellow")
    table.add_column("Description", style="dim")
    for name in registry.list_names():
        skill = registry.require(name)
        m = skill.manifest
        cls = type(skill).__name__
        # Built-ins live under app.skills.*; externals are loaded from
        # a synthesized module name. Flag the source visibly.
        origin = "built-in" if type(skill).__module__.startswith("app.skills.") else "external"
        tools = m.required_tools
        if not tools:
            tools_cell = "—"
        elif len(tools) <= 4:
            tools_cell = ", ".join(tools)
        else:
            tools_cell = f"{len(tools)} tools: {', '.join(tools[:2])}, …"
        table.add_row(
            m.name,
            m.version,
            "yes" if skill.supports_llm() else "no",
            f"{cls} ({origin})",
            tools_cell,
            (m.description or "").splitlines()[0][:60] if m.description else "",
        )
    console.print(table)

    if not show_findings:
        return

    findings = get_load_findings()
    console.print()
    console.print("[bold]Phase 4.1 external skill search paths[/]:")
    for p in default_external_skill_dirs():
        marker = "✓" if p.exists() else "·"
        console.print(f"  {marker}  [dim]{p}[/]")

    if not findings:
        console.print("\n[dim]No external skill directories scanned (none configured).[/]")
        return
    audit = Table(title="External skill load audit")
    audit.add_column("Source", style="dim")
    audit.add_column("Status")
    audit.add_column("Skill / class")
    audit.add_column("Detail", style="dim")
    for f in findings:
        status_style = {
            "registered": "[green]registered[/]",
            "skipped": "[yellow]skipped[/]",
            "error": "[red]error[/]",
        }.get(f.status, f.status)
        target = f.skill_name or f.class_name or ""
        audit.add_row(
            f.source_dir,
            status_style,
            target,
            f.error or "",
        )
    console.print(audit)


# --------------------------------------------------------------------- tools


@app.command("tools")
def cmd_tools(
    category: Optional[str] = typer.Option(
        None,
        "--category",
        help="Filter by category (read / transform / render).",
    ),
) -> None:
    """List every tool in the Phase 4.2 Tool Registry.

    Each tool is a shared callable Skills may depend on. Skills declare
    their dependencies in ``SkillManifest.required_tools`` and the
    registry verifies them at register time, so typos surface here
    rather than at runtime.
    """
    from app.tools import get_default_tool_registry

    tool_registry = get_default_tool_registry()
    registry = get_default_registry()

    # Build used-by index: tool_name → list[skill_name]
    used_by: dict[str, list[str]] = {}
    for skill_name in registry.list_names():
        skill = registry.require(skill_name)
        for t in skill.manifest.required_tools:
            used_by.setdefault(t, []).append(skill_name)

    specs = tool_registry.list_specs()
    if category:
        specs = [s for s in specs if s.category == category]

    table = Table(title=f"Tool Registry ({len(specs)} tool{'s' if len(specs) != 1 else ''})")
    table.add_column("Name", style="cyan")
    table.add_column("Category")
    table.add_column("Module", style="dim")
    table.add_column("Used by", style="yellow")
    table.add_column("Description", style="dim")
    for spec in specs:
        cat_color = {"read": "green", "transform": "blue", "render": "magenta"}.get(
            spec.category, "white"
        )
        users = used_by.get(spec.name, [])
        users_cell = ", ".join(users) if users else "—"
        table.add_row(
            spec.name,
            f"[{cat_color}]{spec.category}[/]",
            spec.module,
            users_cell,
            spec.description,
        )
    console.print(table)

    # Footer summary
    all_specs = tool_registry.list_specs()
    cat_counts: dict[str, int] = {}
    for s in all_specs:
        cat_counts[s.category] = cat_counts.get(s.category, 0) + 1
    summary = ", ".join(f"{c}={cat_counts.get(c, 0)}" for c in ("read", "transform", "render"))
    console.print(
        f"\n[dim]Total: {len(all_specs)} tool(s) — {summary}. "
        f"Declared by {len([s for s in registry.list_names() if registry.require(s).manifest.required_tools])} "
        f"of {len(registry)} skill(s).[/]"
    )


# --------------------------------------------------------------------- ui-serve


@app.command("ui-serve")
def cmd_ui_serve(
    port: int = typer.Option(8501, "--port", help="Browser port. Default 8501."),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help=(
            "Bind address. Default 127.0.0.1 (localhost only — safe). "
            "Pass 0.0.0.0 to expose on the LAN, but only if you know "
            "what you're doing."
        ),
    ),
) -> None:
    """Launch the Streamlit UI for LocalFlow (Phase 8.0 / v0.7.0).

    Opens a browser at http://<host>:<port>. The UI provides visual
    plan / dry-run / execute / verify / rollback / memory pages,
    sandboxed to ``./sandbox/`` subdirectories by default.

    Install the UI optional dep first::

        pip install -e ".[ui]"

    To allow paths outside the sandbox, visit the UI with ``?unsafe=1``
    in the URL — a banner will surface acknowledging the override.
    The kernel's policy_guard still enforces workspace containment.
    """
    try:
        import streamlit  # noqa: F401
    except ImportError:
        console.print(
            "[red]Streamlit not installed.[/] Install with: "
            '[cyan]pip install "streamlit>=1.30,<2.0"[/] '
            'or [cyan]pip install -e ".[ui]"[/]'
        )
        raise typer.Exit(code=2)

    import subprocess

    from app.ui import main_path

    # First-run hygiene: Streamlit interactively asks "Email:" on its
    # very first invocation per-user. With subprocess.run inheriting
    # stdin, that prompt blocks server start indefinitely and the user
    # sees a connection-refused page. Pre-creating an empty
    # credentials file makes Streamlit skip the prompt.
    _ensure_streamlit_credentials()

    # v0.19.0 hygiene: warn when the port is already bound. Stale
    # Streamlit processes from a previous Ctrl+C that didn't fully
    # detach will serve OLD module bytecode + cause "ImportError:
    # cannot import name X" on pages added since the stale start —
    # confusing to debug. Detect cheaply via a connect probe.
    import socket as _socket

    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    probe.settimeout(0.3)
    try:
        probe.connect((host if host != "0.0.0.0" else "127.0.0.1", port))
        probe.close()
        console.print(
            f"[yellow]Warning:[/] port [bold]{port}[/] is already in use. "
            "You probably have an orphan Streamlit from a previous "
            "Ctrl+C — it still serves the OLD code, so new pages will "
            "fail to import.\n"
            f"  Kill it: [cyan]netstat -ano | findstr :{port}[/] → "
            f"[cyan]taskkill /F /PID <pid>[/]\n"
            "  Or pass [cyan]--port 8502[/] to bind a fresh port."
        )
    except OSError:
        pass  # port free — happy path

    console.print(
        f"[cyan]Starting LocalFlow UI[/] on [bold]http://{host}:{port}[/]  "
        f"(sandbox: [dim]./sandbox/[/])"
    )

    try:
        # stdin=DEVNULL is belt-and-suspenders — if any future Streamlit
        # version reintroduces an interactive prompt, we won't hang.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(main_path()),
                "--server.port",
                str(port),
                "--server.address",
                host,
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            stdin=subprocess.DEVNULL,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]UI server stopped.[/]")


def _ensure_streamlit_credentials() -> None:
    """Pre-populate ``~/.streamlit/credentials.toml`` so Streamlit skips
    the first-run interactive email prompt. Idempotent — never
    overwrites an existing file.

    Streamlit checks this file at startup; if present it doesn't ask.
    Writing an empty email opts out of their newsletter without
    blocking server boot.
    """
    cred_path = Path.home() / ".streamlit" / "credentials.toml"
    if cred_path.exists():
        return
    try:
        cred_path.parent.mkdir(parents=True, exist_ok=True)
        cred_path.write_text('[general]\nemail = ""\n', encoding="utf-8")
    except OSError:
        # If the user's home is unwritable for whatever reason, fall
        # through — Streamlit will block on the prompt and the user
        # sees a clearer message than a silent failure here.
        pass


# --------------------------------------------------------------------- mcp-serve


mcp_clients_app = typer.Typer(
    help="v0.16 — manage external MCP servers (LocalFlow as MCP client).",
    no_args_is_help=True,
)


@mcp_clients_app.command("list")
def cmd_mcp_clients_list() -> None:
    """Show every registered external MCP server + its last-probe state."""
    from app.mcp.catalog import load

    catalog = load()
    if not catalog.entries:
        console.print(
            "[dim]No external MCP servers registered. Add one with "
            "`localflow mcp-clients add <name> '<command>'`.[/]"
        )
        return
    table = Table(title="External MCP servers")
    table.add_column("Name", style="cyan")
    table.add_column("Command")
    table.add_column("Last probe")
    table.add_column("Tools")
    for e in catalog.entries:
        if e.last_probed_ok is None:
            status = "[dim]never[/]"
        elif e.last_probed_ok:
            status = "[green]OK[/]"
        else:
            status = f"[red]FAIL[/]: {(e.last_probed_error or '')[:40]}"
        table.add_row(e.name, e.command, status, str(len(e.tools)))
    console.print(table)


@mcp_clients_app.command("add")
def cmd_mcp_clients_add(
    name: str = typer.Argument(..., help="Short label for the external server."),
    command: str = typer.Argument(
        ...,
        help="Shell command that spawns the server's stdio process "
        "(e.g. 'npx @modelcontextprotocol/server-filesystem /some/dir').",
    ),
) -> None:
    """v0.16 — register an external MCP server in the catalog."""
    from app.mcp.catalog import add_entry, load, save

    catalog = load()
    entry = add_entry(catalog, name, command)
    save(catalog)
    console.print(
        f"[green]Registered[/] external MCP server `{entry.name}` → {entry.command!r}.\n"
        f"[dim]Run `localflow mcp-clients probe {entry.name}` to verify connectivity.[/]"
    )


@mcp_clients_app.command("remove")
def cmd_mcp_clients_remove(
    name: str = typer.Argument(..., help="Name of the server to remove."),
) -> None:
    """v0.16 — drop an external server from the catalog."""
    from app.mcp.catalog import load, remove_entry, save

    catalog = load()
    if not remove_entry(catalog, name):
        console.print(f"[yellow]No server named `{name}` in catalog.[/]")
        raise typer.Exit(code=1)
    save(catalog)
    console.print(f"[green]Removed[/] `{name}` from external MCP catalog.")


@mcp_clients_app.command("probe")
def cmd_mcp_clients_probe(
    name: str = typer.Argument(
        ..., help="Name of a registered server (use `localflow mcp-clients list` to see)."
    ),
    timeout: float = typer.Option(20.0, "--timeout", help="Probe timeout in seconds (default 20)."),
) -> None:
    """v0.16 — spawn the server, list its tools, persist the inventory.

    Updates the catalog with the discovered tool names + their input
    schemas. Future phases can use this catalog to surface external
    tools to LocalFlow skills.
    """
    from app.mcp.catalog import load, save
    from app.mcp.client import probe

    catalog = load()
    entry = next((e for e in catalog.entries if e.name == name), None)
    if entry is None:
        console.print(f"[red]No server `{name}` in catalog. Register with `mcp-clients add`.[/]")
        raise typer.Exit(code=2)

    with console.status(f"probing {entry.name} ({entry.command})..."):
        outcome = probe(entry.name, entry.command, timeout=timeout)

    entry.last_probed_ok = outcome.success
    entry.last_probed_error = outcome.error
    entry.tools = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in outcome.tools
    ]
    save(catalog)

    if outcome.success:
        console.print(
            f"[green]Probe OK[/]  ·  {len(outcome.tools)} tool(s) advertised by `{entry.name}`."
        )
        for t in outcome.tools:
            console.print(f"  • [cyan]{t.name}[/] — {t.description[:80]}")
    else:
        console.print(f"[red]Probe failed[/]: {outcome.error}")
        raise typer.Exit(code=1)


app.add_typer(mcp_clients_app, name="mcp-clients")


@app.command("mcp-serve")
def cmd_mcp_serve() -> None:
    """Start LocalFlow as an MCP server on stdio.

    Lets Claude Code or any MCP client drive LocalFlow over JSON-RPC.
    Reuses every existing safety primitive (policy guard, dry-run,
    rollback, verifier) — MCP only wraps the existing CLI surface,
    it never invents new actions.

    Install the MCP optional dep first::

        pip install -e ".[mcp]"

    Then add to your client config (Claude Code / Claude Desktop)::

        {
          "mcpServers": {
            "localflow": {
              "command": "python",
              "args": ["-m", "app.cli", "mcp-serve"],
              "cwd": "C:\\\\path\\\\to\\\\localflow"
            }
          }
        }
    """
    # Check for the optional SDK up-front. ``app.mcp.server`` itself
    # imports ``mcp.*`` lazily inside its run function, so importing it
    # doesn't tell us whether the dep is installed — probe directly.
    try:
        import mcp  # noqa: F401
    except ImportError:
        console.print(
            "[red]MCP SDK not installed.[/] Install with: "
            '[cyan]pip install "mcp>=1.6,<2.0"[/] '
            'or [cyan]pip install -e ".[mcp]"[/]'
        )
        raise typer.Exit(code=2)

    import asyncio

    from app.mcp.server import run_mcp_server

    try:
        asyncio.run(run_mcp_server())
    except KeyboardInterrupt:
        # Normal shutdown when stdin closes / user hits Ctrl+C — don't
        # print a stack trace into the JSON-RPC channel.
        pass


# --------------------------------------------------------------------- memory


memory_app = typer.Typer(
    help="Manage persistent user preferences (forbidden paths, naming style).",
    no_args_is_help=True,
)
app.add_typer(memory_app, name="memory")


def _load_memory_prefs_safe() -> MemoryPreferences:
    """Load memory prefs and fall back to defaults with a warning on
    corruption. Returning silent defaults on missing file is fine —
    they ARE the default — but a corrupt file is a user error we
    should surface."""
    try:
        return MemoryStore().load()
    except MemoryStoreError as exc:
        console.print(f"[yellow]memory disabled:[/] {exc}")
        return MemoryPreferences()


def _print_applied_prefs(prefs: MemoryPreferences) -> None:
    """One-line summary so users see when memory influences a run."""
    parts: list[str] = []
    if prefs.forbidden_paths:
        parts.append(f"{len(prefs.forbidden_paths)} forbidden_paths")
    if prefs.naming_style != NamingStyle.ORIGINAL:
        parts.append(f"naming_style={prefs.naming_style.value}")
    if parts:
        console.print(f"[dim]Applied preferences from memory: {', '.join(parts)}[/]")


@memory_app.command("list")
def cmd_memory_list() -> None:
    """Print the current persisted preferences."""
    store = MemoryStore()
    try:
        prefs = store.load()
    except MemoryStoreError as exc:
        console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(code=1)

    table = Table(title=f"Memory preferences — {store.prefs_path}")
    table.add_column("Key", style="cyan")
    table.add_column("Value")
    table.add_row("forbidden_paths", "\n".join(prefs.forbidden_paths) or "[dim]—[/]")
    table.add_row("naming_style", prefs.naming_style.value)
    table.add_row("prefer_llm_planner", str(prefs.prefer_llm_planner).lower())
    table.add_row("enable_semantic_verifier", str(prefs.enable_semantic_verifier).lower())
    table.add_row("max_auto_repairs", str(prefs.max_auto_repairs))
    table.add_row("schema_version", str(prefs.schema_version))
    console.print(table)
    if prefs.is_default():
        console.print("\n[dim]All values are defaults; nothing persisted to influence runs.[/]")


@memory_app.command("allow-domain")
def cmd_memory_allow_domain(
    host: str = typer.Argument(
        ..., help="Hostname to add to fetch_allowed_domains (bare host, no scheme)."
    ),
) -> None:
    """v0.16 — add a hostname to ``fetch_allowed_domains``.

    The WebCollect skill's FETCH actions are blocked by policy_guard
    unless the URL's host is exactly on this allowlist. Use this when
    you want a TaskGraph stage to download specific URLs.
    """
    store = MemoryStore()
    try:
        result = store.add_fetch_allowed_domain(host)
    except ValueError as exc:
        console.print(f"[red]invalid host:[/] {exc}")
        raise typer.Exit(code=2) from exc
    style = "green" if result.changed else "dim"
    console.print(f"[{style}]{result.detail}[/]")


@memory_app.command("disallow-domain")
def cmd_memory_disallow_domain(
    host: str = typer.Argument(..., help="Hostname to remove from fetch_allowed_domains."),
) -> None:
    """v0.16 — remove a hostname from ``fetch_allowed_domains``."""
    store = MemoryStore()
    try:
        result = store.remove_fetch_allowed_domain(host)
    except ValueError as exc:
        console.print(f"[red]invalid host:[/] {exc}")
        raise typer.Exit(code=2) from exc
    style = "green" if result.changed else "dim"
    console.print(f"[{style}]{result.detail}[/]")


@memory_app.command("forbid")
def cmd_memory_forbid(
    path: str = typer.Argument(..., help="Workspace-relative path the agent must never touch."),
) -> None:
    """Add a workspace-relative path to ``forbidden_paths``."""
    store = MemoryStore()
    try:
        result = store.add_forbidden_path(path)
    except ValueError as exc:
        console.print(f"[red]invalid path:[/] {exc}")
        raise typer.Exit(code=2)
    style = "green" if result.changed else "dim"
    console.print(f"[{style}]{result.detail}[/]")


@memory_app.command("unforbid")
def cmd_memory_unforbid(
    path: str = typer.Argument(..., help="Workspace-relative path to remove from forbidden_paths."),
) -> None:
    """Remove a path from ``forbidden_paths``."""
    store = MemoryStore()
    try:
        result = store.remove_forbidden_path(path)
    except ValueError as exc:
        console.print(f"[red]invalid path:[/] {exc}")
        raise typer.Exit(code=2)
    style = "green" if result.changed else "dim"
    console.print(f"[{style}]{result.detail}[/]")


_SCALAR_KEYS = (
    "naming_style",
    "prefer_llm_planner",
    "enable_semantic_verifier",
    "max_auto_repairs",
)


def _parse_bool_arg(value: str) -> bool:
    """Parse a CLI bool argument. Accept the truthy/falsy strings the
    rest of LocalFlow uses (matches LOCALFLOW_MCP_ALLOW_DANGEROUS,
    LOCALFLOW_DISABLE_EXTERNAL_SKILLS, ?unsafe=1)."""
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"expected true/false (or 1/0/yes/no/on/off), got {value!r}")


@memory_app.command("set")
def cmd_memory_set(
    key: str = typer.Argument(
        ...,
        help=(
            "Preference key: 'naming_style' | 'prefer_llm_planner' | "
            "'enable_semantic_verifier' | 'max_auto_repairs'."
        ),
    ),
    value: str = typer.Argument(
        ...,
        help=(
            "Value. For naming_style: original / snake_case / kebab-case / lower. "
            "For prefer_llm_planner / enable_semantic_verifier: true / false. "
            "For max_auto_repairs: 0..5."
        ),
    ),
) -> None:
    """Set a scalar preference. Rejects unknown keys / values."""
    store = MemoryStore()
    if key not in _SCALAR_KEYS:
        console.print(
            f"[red]unknown preference key {key!r}.[/] "
            f"Supported: {', '.join(_SCALAR_KEYS)}. (Lists like forbidden_paths "
            "use the dedicated forbid/unforbid commands.)"
        )
        raise typer.Exit(code=2)
    try:
        if key == "naming_style":
            result = store.set_naming_style(value)
        elif key == "prefer_llm_planner":
            result = store.set_prefer_llm_planner(_parse_bool_arg(value))
        elif key == "enable_semantic_verifier":
            result = store.set_enable_semantic_verifier(_parse_bool_arg(value))
        else:  # max_auto_repairs
            try:
                int_value = int(value)
            except ValueError as exc:
                raise ValueError(f"expected integer 0..5, got {value!r}") from exc
            result = store.set_max_auto_repairs(int_value)
    except ValueError as exc:
        console.print(f"[red]invalid value:[/] {exc}")
        raise typer.Exit(code=2)
    style = "green" if result.changed else "dim"
    console.print(f"[{style}]{result.detail}[/]")


@memory_app.command("unset")
def cmd_memory_unset(
    key: str = typer.Argument(..., help="Preference key to reset to its default."),
) -> None:
    """Reset a scalar preference to its default."""
    store = MemoryStore()
    if key not in _SCALAR_KEYS:
        console.print(
            f"[red]unknown preference key {key!r}.[/] Supported: {', '.join(_SCALAR_KEYS)}."
        )
        raise typer.Exit(code=2)
    if key == "naming_style":
        result = store.clear_naming_style()
    else:
        result = store.clear_prefer_llm_planner()
    style = "green" if result.changed else "dim"
    console.print(f"[{style}]{result.detail}[/]")


@memory_app.command("audit")
def cmd_memory_audit(
    limit: int = typer.Option(
        20, "--limit", help="Number of most recent entries (use --all for everything)."
    ),
    all_: bool = typer.Option(False, "--all", help="Show every audit entry."),
) -> None:
    """Tail the memory mutation audit log."""
    store = MemoryStore()
    entries = store.read_audit(limit=None if all_ else limit)
    if not entries:
        console.print("[dim]No memory audit entries yet.[/]")
        return

    table = Table(title=f"Memory audit — {store.audit_path} ({len(entries)} entries)")
    table.add_column("Timestamp", style="dim")
    table.add_column("Event", style="cyan")
    table.add_column("Detail")
    for e in entries:
        ts = e.get("ts", "")
        event = e.get("event", "")
        # Show the most interesting fields; the user can `cat` the file
        # for the full record if they need everything.
        detail_parts: list[str] = []
        for k in ("path", "key", "before", "after"):
            if k in e:
                detail_parts.append(f"{k}={e[k]!r}")
        table.add_row(ts, event, " ".join(detail_parts))
    console.print(table)


# --------------------------------------------------------------------- helpers


def _build_llm_client(provider: str, model: Optional[str]) -> LLMClient:
    if provider == "anthropic":
        return AnthropicClient(model=model) if model else AnthropicClient()
    if provider == "openai":
        return OpenAIClient(model=model) if model else OpenAIClient()
    raise LLMClientError(f"unknown llm provider: {provider!r}")


def _resolve_default_model(provider: str, client: LLMClient) -> str:
    return getattr(client, "model", f"<{provider} default>")


def _endpoint_for(provider: str, client: LLMClient) -> str:
    """Best-effort introspection so the spinner header tells the user which
    URL the call is going to (useful when a proxy hangs)."""
    inner = getattr(client, "_client", None)
    if inner is None:
        return f"<{provider} default>"
    base_url = getattr(inner, "base_url", None)
    if base_url is None:
        return f"<{provider} default>"
    return str(base_url)


def _stream_plan(
    *,
    console: Console,
    task: TaskSpec,
    snapshot,
    client: LLMClient,
    max_repair: int,
    provider: str,
    model: str,
    skill_obj=None,
    trace: TraceLogger | None = None,
):
    """Run ``skill.plan_with_llm`` with a Rich Live display that shows
    streaming tool-call arguments as they arrive.

    Why bother: the LLM call itself is ~10-20s of token generation. Without
    streaming the user stares at a static spinner the whole time. With
    streaming they see the JSON plan being assembled in real time and
    can verify it looks right (or Ctrl+C if it's going off the rails).

    Phase 3.3b: dispatches through the Skill so each skill can wire its
    own LLM planner (folder_organizer → ActionPlan; data_analyzer →
    AnalysisSpec → ActionPlan).
    """
    streamed: list[str] = []
    attempt_holder = {"n": 1}
    started = time.monotonic()
    tail_chars = 1400  # how many chars of streamed text to show

    def render() -> Group:
        text = "".join(streamed)
        elapsed = time.monotonic() - started
        header = Text(
            f"streaming {len(text):>5} chars  ·  attempt {attempt_holder['n']}/{max_repair}  "
            f"·  elapsed {elapsed:5.1f}s  ·  Ctrl+C to abort",
            style="bold cyan",
        )
        tail = text[-tail_chars:] if len(text) > tail_chars else text
        # Show the streaming text in a soft color so it visually separates
        # from the final Panel summary that follows.
        body = Text(tail or "[waiting for first token…]", style="white")
        return Group(header, Text(""), body)

    with Live(render(), console=console, refresh_per_second=12, transient=True) as live:

        def on_delta(chunk: str) -> None:
            streamed.append(chunk)
            live.update(render())

        def on_attempt(n: int) -> None:
            attempt_holder["n"] = n
            # On repair, clear the buffer so the user sees the fresh
            # attempt rather than concatenated history of failed JSON.
            if n > 1:
                streamed.clear()
            live.update(render())

        if skill_obj is not None:
            plan = skill_obj.plan_with_llm(
                task,
                snapshot,
                client=client,
                max_attempts=max_repair,
                on_delta=on_delta,
                on_attempt=on_attempt,
                trace=trace,
            )
        else:
            # Legacy path retained for any direct caller; new code goes
            # through the skill object so each skill picks its own LLM
            # planner (folder_organizer → ActionPlan, data_analyzer →
            # AnalysisSpec → ActionPlan, …).
            from app.agent import plan_with_llm as _legacy_plan

            plan = _legacy_plan(
                task,
                snapshot,
                trace=trace,
                client=client,
                max_attempts=max_repair,
                on_delta=on_delta,
                on_attempt=on_attempt,
            )

    elapsed = time.monotonic() - started
    total = sum(len(c) for c in streamed)
    console.print(
        f"[dim]stream complete:[/] {total} chars  ·  "
        f"{elapsed:.1f}s  ·  ~{int(total / max(elapsed, 0.001))} chars/s"
    )
    return plan


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# --------------------------------------------------------------------- eval

eval_app = typer.Typer(
    help="Phase 9 (v0.10.0) — run eval tasks and report task-level success.",
    no_args_is_help=True,
)
app.add_typer(eval_app, name="eval")


@eval_app.command("list")
def cmd_eval_list(
    target: Path = typer.Argument(
        ...,
        help="A single .yaml file or a directory of eval tasks (e.g. evals/workspace_pack/).",
    ),
) -> None:
    """List eval tasks discoverable at ``target``."""
    from app.eval import discover_tasks

    try:
        tasks = discover_tasks(target)
    except Exception as exc:
        console.print(f"[red]error loading tasks:[/] {exc}")
        raise typer.Exit(code=2)

    if not tasks:
        console.print(f"[yellow]no eval tasks found at[/] {target}")
        raise typer.Exit(code=0)

    table = Table(title=f"Eval tasks @ {target}")
    table.add_column("task_id", style="cyan")
    table.add_column("title")
    table.add_column("skill")
    table.add_column("planner")
    table.add_column("graders")
    for t in tasks:
        table.add_row(
            t.task_id,
            t.title,
            t.skill,
            t.planner,
            ", ".join(t.graders) or "[dim](none)[/]",
        )
    console.print(table)


@eval_app.command("run")
def cmd_eval_run(
    target: Path = typer.Argument(
        ...,
        help="A single .yaml file or a directory of eval tasks.",
    ),
    output_md: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the markdown report to this path (also printed to stdout).",
    ),
    eval_home: Path | None = typer.Option(
        None,
        "--eval-home",
        help=(
            "Where to plant isolated workspaces + the eval RunStore. "
            "Defaults to ``./.localflow-eval/``."
        ),
    ),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop at the first failed task."),
    enable_repair: bool = typer.Option(
        False,
        "--enable-repair",
        help=(
            "Phase 13 — wire the semantic verifier + auto-repair loop "
            "into every task. Off by default to preserve v0.12 baseline."
        ),
    ),
    compare_repair: bool = typer.Option(
        False,
        "--compare-repair",
        help=(
            "Phase 13 — run each task TWICE (baseline + with auto-repair) "
            "and produce a side-by-side comparison report."
        ),
    ),
    max_auto_repairs: int = typer.Option(
        2,
        "--max-auto-repairs",
        help="Cap on repair iterations per task (only when --enable-repair / --compare-repair).",
    ),
) -> None:
    """Run one or more eval tasks. Exit code = number of failed tasks."""
    from app.eval import discover_tasks, render_eval_report, run_eval

    home = eval_home or (Path.cwd() / ".localflow-eval")
    try:
        tasks = discover_tasks(target)
    except Exception as exc:
        console.print(f"[red]error loading tasks:[/] {exc}")
        raise typer.Exit(code=2)
    if not tasks:
        console.print(f"[yellow]no eval tasks found at[/] {target}")
        raise typer.Exit(code=0)

    if compare_repair:
        return _cmd_eval_run_compare(
            tasks=tasks,
            home=home,
            output_md=output_md,
            fail_fast=fail_fast,
            max_auto_repairs=max_auto_repairs,
        )

    results = []
    failed = 0
    for task in tasks:
        label = "auto-repair" if enable_repair else "baseline"
        console.print(f"[cyan]→[/] running {task.task_id} — {task.title} [{label}]")
        result = run_eval(
            task,
            home,
            enable_auto_repair=enable_repair,
            max_auto_repairs=max_auto_repairs,
        )
        results.append(result)
        if result.passed:
            console.print(
                f"  [green]PASS[/]  {len(result.grader_verdicts)}/"
                f"{len(result.grader_verdicts)} graders  ·  {result.duration_ms} ms"
            )
        else:
            failed += 1
            console.print(
                f"  [red]FAIL[/]  "
                f"{sum(1 for v in result.grader_verdicts if v.passed)}/"
                f"{len(result.grader_verdicts)} graders  ·  {result.duration_ms} ms"
                + (f"  (error: {result.error[:80]})" if result.error else "")
            )
            if fail_fast:
                break

    report_md = render_eval_report(results)
    if output_md is not None:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(report_md, encoding="utf-8")
        console.print(f"[dim]report written to[/] {output_md}")

    console.rule(f"{len(results) - failed}/{len(results)} eval tasks passed")
    raise typer.Exit(code=failed)


def _cmd_eval_run_compare(
    *,
    tasks,
    home: Path,
    output_md: Path | None,
    fail_fast: bool,
    max_auto_repairs: int,
) -> None:
    """Phase 13 — `localflow eval run ... --compare-repair`.

    Runs each task twice (baseline + with auto-repair) and emits a
    side-by-side markdown table so the user can measure how much the
    auto-repair loop improves the pass rate."""
    from app.eval import run_eval as _run_eval

    rows: list[dict] = []
    failed_after = 0
    for task in tasks:
        console.print(f"[cyan]→[/] {task.task_id} (baseline)")
        baseline = _run_eval(task, home, enable_auto_repair=False)
        console.print(f"[cyan]→[/] {task.task_id} (repair)")
        repaired = _run_eval(
            task,
            home,
            enable_auto_repair=True,
            max_auto_repairs=max_auto_repairs,
        )
        delta = "—"
        if baseline.passed != repaired.passed:
            delta = "↑ repaired" if repaired.passed and not baseline.passed else "↓ regressed"
        rows.append(
            {
                "task": task.task_id,
                "title": task.title,
                "baseline": baseline.passed,
                "repaired": repaired.passed,
                "delta": delta,
            }
        )
        if not repaired.passed:
            failed_after += 1
            if fail_fast:
                break

    lines: list[str] = [
        "# Eval comparison — baseline vs. auto-repair",
        "",
        "| Task | Title | Baseline | After Repair | Δ |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['task']}` | {row['title']} | "
            f"{'✓' if row['baseline'] else '✗'} | "
            f"{'✓' if row['repaired'] else '✗'} | {row['delta']} |"
        )
    report_md = "\n".join(lines) + "\n"
    console.print(report_md)
    if output_md is not None:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(report_md, encoding="utf-8")
        console.print(f"[dim]comparison written to[/] {output_md}")
    raise typer.Exit(code=failed_after)


# --------------------------------------------------------------------- taskgraph

taskgraph_app = typer.Typer(
    help="Phase 10 (v0.11.0) — drive multi-stage tasks via static graph YAML.",
    no_args_is_help=True,
)
app.add_typer(taskgraph_app, name="taskgraph")


@taskgraph_app.command("describe")
def cmd_taskgraph_describe(
    graph: Path = typer.Argument(..., help="Path to a TaskGraph YAML file."),
) -> None:
    """Print the parsed TaskGraph spec (stages + skills + policies).

    Doesn't run anything; useful for reviewing a graph before
    invoking ``localflow taskgraph run`` on it.
    """
    import yaml

    from app.schemas import TaskGraph

    try:
        raw = yaml.safe_load(graph.read_text(encoding="utf-8"))
        tg = TaskGraph.model_validate(raw)
    except Exception as exc:
        console.print(f"[red]invalid graph YAML:[/] {exc}")
        raise typer.Exit(code=2)

    table = Table(title=f"TaskGraph @ {graph}")
    table.add_column("#", style="dim", justify="right")
    table.add_column("stage_id", style="cyan")
    table.add_column("title")
    table.add_column("skill")
    table.add_column("planner")
    table.add_column("policy", style="yellow")
    for i, s in enumerate(tg.stages, 1):
        table.add_row(
            str(i),
            s.stage_id,
            s.title,
            s.skill,
            s.planner,
            s.failure_policy.value,
        )
    console.print(table)
    console.print(
        f"[dim]Goal:[/] {tg.user_goal}\n"
        f"[dim]Workspace:[/] {tg.workspace_root}\n"
        f"[dim]Forbidden actions:[/] {', '.join(tg.forbidden_actions) or '(none)'}\n"
        f"[dim]Forbidden paths:[/] {', '.join(tg.forbidden_paths) or '(none)'}"
    )


@taskgraph_app.command("run")
def cmd_taskgraph_run(
    graph: Path = typer.Argument(..., help="Path to a TaskGraph YAML file."),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help=(
            "Override the graph's workspace_root. Useful when the YAML uses a "
            "placeholder path. Defaults to the graph's declared workspace_root."
        ),
    ),
    locale: Optional[str] = typer.Option(
        None,
        "--locale",
        help=(
            "Override the graph's language for user-facing generated content. "
            "One of: zh-CN, en-US. Defaults to the graph's declared locale "
            "(zh-CN if unset)."
        ),
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Approve the graph spec without prompting."
    ),
) -> None:
    """Plan + execute every stage of a TaskGraph end-to-end.

    Single approval ceremony at the start: the user approves the
    GRAPH SPEC (which stages, which skills, which planners). Per-stage
    ActionPlans are generated just-in-time as previous stages
    complete; per-stage dry-runs are written + traced but not
    prompted on. This is the only way multi-stage works in a
    non-interactive (CI / MCP) context.
    """
    import yaml

    from app.harness.taskgraph_runner import run_taskgraph
    from app.schemas import TaskGraph

    try:
        raw = yaml.safe_load(graph.read_text(encoding="utf-8"))
        tg = TaskGraph.model_validate(raw)
    except Exception as exc:
        console.print(f"[red]invalid graph YAML:[/] {exc}")
        raise typer.Exit(code=2)

    if workspace is not None:
        tg = tg.model_copy(update={"workspace_root": str(workspace.resolve())})

    if locale is not None:
        if locale not in {"zh-CN", "en-US"}:
            console.print(f"[red]invalid --locale {locale!r}; expected zh-CN or en-US[/]")
            raise typer.Exit(code=2)
        tg = tg.model_copy(update={"locale": locale})

    # Render the spec for the user to confirm.
    console.print(
        Panel.fit(
            f"[bold]TaskGraph: {graph.name}[/]\n"
            f"Goal: {tg.user_goal}\n"
            f"Workspace: {tg.workspace_root}\n"
            f"Locale: {tg.locale}\n"
            f"Stages: {len(tg.stages)}\n"
            + "\n".join(
                f"  {i + 1}. [{s.failure_policy.value}] {s.stage_id} — {s.skill} ({s.planner})"
                for i, s in enumerate(tg.stages)
            ),
            title="Approval required",
            border_style="cyan",
        )
    )
    if not yes:
        confirm = typer.confirm("Run this graph?")
        if not confirm:
            console.print("[yellow]TaskGraph cancelled.[/]")
            raise typer.Exit(code=1)

    store = RunStore.create()
    trace = TraceLogger(store.trace_path)
    result = run_taskgraph(tg, store, trace=trace, approved=True)

    table = Table(title=f"TaskGraph run: {store.task_id}")
    table.add_column("stage_id", style="cyan")
    table.add_column("status")
    table.add_column("actions", justify="right")
    table.add_column("verifier")
    table.add_column("duration", justify="right")
    for s in result.stages:
        badge = {
            "passed": "[green]PASSED[/]",
            "failed": "[red]FAILED[/]",
            "skipped": "[dim]SKIPPED[/]",
            "aborted": "[yellow]ABORTED[/]",
        }.get(s.status.value, s.status.value)
        verifier = (
            "—"
            if s.verifier_passed is None
            else ("[green]ok[/]" if s.verifier_passed else "[red]fail[/]")
        )
        table.add_row(
            s.stage_id,
            badge,
            str(s.action_count),
            verifier,
            f"{s.duration_ms} ms",
        )
    console.print(table)
    badge = "[green]PASSED[/]" if result.passed else "[red]FAILED[/]"
    console.print(
        f"{badge}  total {result.duration_ms} ms  ·  "
        f"run_id [bold]{result.task_id}[/]  ·  "
        f"to undo: [bold]localflow rollback --run-id {result.task_id}[/]"
    )
    if not result.passed:
        raise typer.Exit(code=1)


@taskgraph_app.command("replay")
def cmd_taskgraph_replay(
    graph: Path = typer.Argument(..., help="Path to the SAME TaskGraph YAML used for the run."),
    run_id: str = typer.Option(..., "--run-id", help="Existing run identifier."),
    from_stage: str = typer.Option(
        ...,
        "--from-stage",
        help="stage_id to replay from (inclusive). Every downstream stage also replays.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """v0.15 — cross-stage repair: roll back from ``--from-stage`` and
    every downstream stage, then replay just that range.

    Use this when one stage's failure traces back to an earlier
    stage's wrong output. Upstream stages are left alone; downstream
    ones get a fresh execute after rolling back the affected range.

    Conflicts halt the replay — pass through to the standard
    ``localflow rollback --force`` first if you need to override
    user-side drift.
    """
    import yaml

    from app.harness.taskgraph_runner import replay_from_stage
    from app.schemas import TaskGraph

    store = RunStore(task_id=run_id)
    if not store.rollback_path.exists():
        raise typer.BadParameter(f"no rollback manifest for run {run_id}")
    try:
        raw = yaml.safe_load(graph.read_text(encoding="utf-8"))
        tg = TaskGraph.model_validate(raw)
    except Exception as exc:
        console.print(f"[red]invalid graph YAML:[/] {exc}")
        raise typer.Exit(code=2) from exc
    if from_stage not in [s.stage_id for s in tg.stages]:
        console.print(f"[red]stage `{from_stage}` not in graph[/]")
        raise typer.Exit(code=2)

    if not yes:
        affected = [
            s.stage_id
            for s in tg.stages[
                [i for i, s in enumerate(tg.stages) if s.stage_id == from_stage][0] :
            ]
        ]
        confirm = typer.confirm(
            f"Roll back + replay {len(affected)} stage(s) from `{from_stage}` onwards: {affected}?"
        )
        if not confirm:
            console.print("[yellow]Replay cancelled.[/]")
            raise typer.Exit(code=1)

    try:
        result = replay_from_stage(graph=tg, run_store=store, from_stage=from_stage)
    except Exception as exc:
        console.print(f"[red]replay failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    badge = "[green]REPLAYED[/]" if result.passed else "[red]REPLAY PARTIAL[/]"
    console.print(
        f"{badge}  affected stages re-ran  ·  run_id [bold]{run_id}[/]  ·  "
        f"see [bold]localflow status --task-id {run_id}[/] for the merged result"
    )


# --------------------------------------------------------------------- pack (Phase 17)

pack_app = typer.Typer(
    help=(
        "Phase 17 (v0.17.0) — Recipe / Pack System. Recipes are product-level "
        "deliverable packs (Research Pack, Data Report Pack, Project Handoff "
        "Pack) that compile down to a TaskGraph. Users pick a pack instead "
        "of having to know individual skill names."
    ),
    no_args_is_help=True,
)
app.add_typer(pack_app, name="pack")


@pack_app.command("list")
def cmd_pack_list() -> None:
    """Show every loaded recipe with its title + stage count.

    Reports load errors as a separate warning section so a broken YAML
    doesn't silently disappear from the catalog.
    """
    from app.recipes import get_default_registry

    reg = get_default_registry()
    recipes = reg.all()
    if not recipes:
        console.print(
            "[yellow]No recipes loaded.[/] Check that the ``recipes/`` "
            f"directory exists at {reg.recipes_dir}, or set "
            "LOCALFLOW_RECIPES_DIR."
        )
    else:
        # v0.22.1: a 6-column table at full Rich box-density blew past
        # 100 cols on a typical Windows terminal, so Rich crushed the
        # middle columns to zero width and left only top-T separators
        # (┬┬┬) where text should have been. Slimmer 5-column layout
        # without no_wrap lets every cell breathe; tags collapse into
        # the description line so the verb count stays the same.
        table = Table(title="Recipe catalog", show_lines=False, pad_edge=False)
        table.add_column("name", style="cyan")
        table.add_column("title")
        table.add_column("description", style="dim", overflow="fold", ratio=2)
        table.add_column("stages", justify="right")
        table.add_column("outputs", justify="right")
        for r in recipes:
            desc_lines = (r.description or "").strip().splitlines()
            desc = desc_lines[0] if desc_lines else ""
            if len(desc) > 90:
                desc = desc[:87] + "..."
            if r.tags:
                desc = (desc + "  " if desc else "") + " ".join(
                    f"[bold]#{tag}[/]" for tag in r.tags
                )
            table.add_row(
                r.name,
                r.title,
                desc or "—",
                str(len(r.stages)),
                str(len(r.expected_outputs)),
            )
        console.print(table)

    if reg.load_errors:
        console.print()
        warn = Table(title="Recipe load errors", border_style="yellow")
        warn.add_column("file", style="yellow")
        warn.add_column("error")
        for path, err in reg.load_errors:
            warn.add_row(path.name, err)
        console.print(warn)


@pack_app.command("describe")
def cmd_pack_describe(
    name: str = typer.Argument(..., help="Recipe name (e.g. 'research_pack')."),
) -> None:
    """Print a recipe's full spec: stages, expected outputs, verifiers, repair policy."""
    from app.recipes import RecipeNotFound, get_default_registry

    reg = get_default_registry()
    try:
        recipe = reg.get(name)
    except RecipeNotFound:
        console.print(
            f"[red]No recipe named {name!r}.[/] Available: "
            f"{', '.join(reg.list_names()) or '(none)'}"
        )
        raise typer.Exit(code=2) from None

    console.print(
        Panel.fit(
            f"[bold cyan]{recipe.title}[/] ([dim]{recipe.name}[/])\n\n"
            f"{recipe.description.strip()}\n\n"
            f"[dim]Tags:[/] {', '.join(recipe.tags) or '(none)'}",
            title="Pack",
            border_style="cyan",
        )
    )

    stage_table = Table(title="Stages")
    stage_table.add_column("#", style="dim", justify="right")
    stage_table.add_column("stage_id", style="cyan")
    stage_table.add_column("title")
    stage_table.add_column("skill")
    stage_table.add_column("planner")
    stage_table.add_column("policy", style="yellow")
    for i, s in enumerate(recipe.stages, 1):
        stage_table.add_row(str(i), s.stage_id, s.title, s.skill, s.planner, s.failure_policy.value)
    console.print(stage_table)

    console.print(
        "\n[bold]Expected deliverables:[/]\n"
        + "\n".join(f"  - {p}" for p in recipe.expected_outputs)
    )

    if recipe.verifiers:
        console.print(
            f"\n[bold]Recipe-level verifiers (Phase 19):[/] {', '.join(recipe.verifiers)}"
        )

    rp = recipe.repair_policy
    console.print(f"\n[bold]Repair policy:[/] enabled={rp.enabled}, max_rounds={rp.max_rounds}")

    # Phase 21.1: surface repair_target_map so users can see which
    # stage each verifier will replay when repair fires. Without this,
    # the user has no way to predict the auto-repair behaviour short of
    # reading the YAML.
    if recipe.repair_target_map:
        rt_table = Table(title="Repair target map", border_style="dim")
        rt_table.add_column("verifier", style="cyan")
        rt_table.add_column("→")
        rt_table.add_column("replays stage", style="yellow")
        for verifier_name, stage_id in recipe.repair_target_map.items():
            rt_table.add_row(verifier_name, "→", stage_id)
        console.print(rt_table)
    elif recipe.verifiers and rp.enabled:
        console.print(
            "\n[dim]No explicit repair_target_map — each failing verifier "
            "defaults to replaying the last LLM stage.[/]"
        )

    exp = recipe.input_expectation
    if exp.file_kinds or exp.keywords or exp.require_any:
        bits = []
        if exp.file_kinds:
            bits.append(f"file_kinds={exp.file_kinds}")
        if exp.require_any:
            bits.append(f"require_any={exp.require_any}")
        if exp.min_files:
            bits.append(f"min_files={exp.min_files}")
        if exp.keywords:
            bits.append(f"keywords={exp.keywords}")
        console.print("\n[bold]Input expectation:[/] " + ", ".join(bits))


@pack_app.command("suggest")
def cmd_pack_suggest(
    workspace: Path = typer.Argument(..., help="Workspace directory to scan."),
    goal: str = typer.Option(
        "",
        "--goal",
        "-g",
        help="Free-text user goal (e.g. 'build a research pack'). Optional.",
    ),
) -> None:
    """Rank every loaded recipe against a workspace + (optional) user goal.

    Useful before running a pack to see whether the router's first
    pick matches your intent. No execution, no writes.
    """
    from app.recipes import RecipeRouter, get_default_registry
    from app.tools.file_scan import scan_workspace

    if not workspace.exists() or not workspace.is_dir():
        console.print(f"[red]Workspace not found:[/] {workspace}")
        raise typer.Exit(code=2)

    snapshot = scan_workspace(workspace, task_id="suggest", compute_hash=False)
    reg = get_default_registry()
    router = RecipeRouter(reg)
    ranked = router.score_all(user_goal=goal, snapshot=snapshot)

    if not ranked:
        console.print("[yellow]No recipes loaded.[/]")
        raise typer.Exit(code=1)

    table = Table(title=f"Recipe fit for {workspace}")
    table.add_column("rank", justify="right", style="dim")
    table.add_column("recipe", style="cyan")
    table.add_column("score", justify="right")
    table.add_column("why")
    for i, s in enumerate(ranked, 1):
        table.add_row(
            str(i),
            s.recipe.name,
            f"{s.score:+d}",
            "; ".join(s.why) or "(no signals)",
        )
    console.print(table)

    best = router.best_match(user_goal=goal, snapshot=snapshot)
    if best is None:
        console.print(
            "\n[yellow]No recipe scored above zero. Pick one manually: `localflow pack list`.[/]"
        )
    else:
        console.print(
            f"\n[bold green]Suggested:[/] [cyan]{best.recipe.name}[/]  ·  "
            f"`localflow pack run {best.recipe.name} --workspace {workspace}`"
        )


@pack_app.command("run")
def cmd_pack_run(
    name: str = typer.Argument(..., help="Recipe name (use `localflow pack list`)."),
    workspace: Path = typer.Option(
        ...,
        "--workspace",
        "-w",
        help="Workspace directory the pack will operate on.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the approval prompt."),
    enable_repair: bool = typer.Option(
        False,
        "--enable-repair",
        help=(
            "Promote ABORT stages to REPAIR with the recipe's max_rounds. "
            "Equivalent to authoring repair_policy.enabled=true. The semantic "
            "verifier must also be enabled via "
            "`localflow memory set enable_semantic_verifier true`."
        ),
    ),
    locale: str = typer.Option(
        "zh-CN",
        "--locale",
        help=(
            "Language for user-facing generated content (README / SOURCES, "
            "verifier rationales, repair hints). One of: zh-CN, en-US. "
            "Defaults to zh-CN."
        ),
    ),
) -> None:
    """Compile a recipe to a TaskGraph and run it end-to-end.

    Internally this is exactly ``localflow taskgraph run`` against a
    generated graph: same approval ceremony, same runner, same single
    aggregated rollback. The difference is *which* graph the user is
    approving — pack is the product-level pitch.
    """
    from app.harness.taskgraph_runner import run_taskgraph
    from app.recipes import RecipeNotFound, get_default_registry

    if not workspace.exists() or not workspace.is_dir():
        console.print(f"[red]Workspace not found:[/] {workspace}")
        raise typer.Exit(code=2)

    reg = get_default_registry()
    try:
        recipe = reg.get(name)
    except RecipeNotFound:
        console.print(
            f"[red]No recipe named {name!r}.[/] Available: "
            f"{', '.join(reg.list_names()) or '(none)'}"
        )
        raise typer.Exit(code=2) from None

    if enable_repair:
        recipe = recipe.model_copy(
            update={"repair_policy": recipe.repair_policy.model_copy(update={"enabled": True})}
        )

    if locale not in ("zh-CN", "en-US"):
        console.print(f"[red]Unknown locale {locale!r};[/] expected zh-CN or en-US.")
        raise typer.Exit(code=2)
    tg = recipe.compile_to_taskgraph(workspace_root=str(workspace.resolve()), locale=locale)

    console.print(
        Panel.fit(
            f"[bold]Pack: {recipe.title}[/] ([dim]{recipe.name}[/])\n"
            f"Workspace: {workspace}\n"
            f"Stages: {len(tg.stages)}\n"
            f"Repair: {recipe.repair_policy.enabled} "
            f"(max_rounds={recipe.repair_policy.max_rounds})\n\n"
            + "\n".join(
                f"  {i + 1}. [{s.failure_policy.value}] {s.stage_id} — {s.skill} ({s.planner})"
                for i, s in enumerate(tg.stages)
            ),
            title="Approval required",
            border_style="cyan",
        )
    )
    if not yes:
        confirm = typer.confirm("Run this pack?")
        if not confirm:
            console.print("[yellow]Pack run cancelled.[/]")
            raise typer.Exit(code=1)

    store = RunStore.create()
    trace = TraceLogger(store.trace_path)
    result = run_taskgraph(tg, store, trace=trace, approved=True)

    table = Table(title=f"Pack `{recipe.name}` run: {store.task_id}")
    table.add_column("stage_id", style="cyan")
    table.add_column("status")
    table.add_column("actions", justify="right")
    table.add_column("verifier")
    table.add_column("duration", justify="right")
    for s in result.stages:
        badge = {
            "passed": "[green]PASSED[/]",
            "failed": "[red]FAILED[/]",
            "skipped": "[dim]SKIPPED[/]",
            "aborted": "[yellow]ABORTED[/]",
        }.get(s.status.value, s.status.value)
        verifier = (
            "—"
            if s.verifier_passed is None
            else ("[green]ok[/]" if s.verifier_passed else "[red]fail[/]")
        )
        table.add_row(s.stage_id, badge, str(s.action_count), verifier, f"{s.duration_ms} ms")
    console.print(table)

    # Phase 19 — run recipe-level verifiers if the recipe declares any.
    verification = _run_recipe_verifiers(
        recipe=recipe, store=store, workspace=workspace, result=result, locale=locale
    )
    if verification is not None:
        _render_recipe_verification(verification)

    # Phase 21 — auto-repair loop. Triggers when:
    #   (a) every stage PASSED (otherwise the run isn't ready to repair —
    #       a structural failure goes through Phase 13's stage-level loop),
    #   (b) at least one recipe verifier FAILED (not skipped, not passed),
    #   (c) the recipe's repair_policy.enabled is true (CLI flag mirrors
    #       this into the recipe model_copy above).
    repair_result = None
    if (
        verification is not None
        and not verification.passed
        and result.passed
        and recipe.repair_policy.enabled
    ):
        repair_result = _run_recipe_repair(
            recipe=recipe, graph=tg, store=store, verification=verification
        )
        if repair_result is not None:
            _render_recipe_repair(repair_result)
            if repair_result.final_verification is not None:
                verification = repair_result.final_verification

    badge = "[green]PASSED[/]" if result.passed else "[red]FAILED[/]"
    console.print(
        f"{badge}  pack `{recipe.name}` total {result.duration_ms} ms  ·  "
        f"run_id [bold]{result.task_id}[/]  ·  "
        f"to undo: [bold]localflow rollback --run-id {result.task_id}[/]"
    )
    exit_code = 0 if result.passed else 1
    # Verifier failures are a softer signal: stages PASSED but a
    # deliverable verifier rejected — surface as exit 3 so CI distinguishes
    # "pipeline crashed" (1) from "delivered but failed quality checks" (3).
    if verification is not None and not verification.passed and result.passed:
        exit_code = 3
    if exit_code:
        raise typer.Exit(code=exit_code)


def _run_recipe_repair(*, recipe, graph, store, verification):
    """Phase 21 — drive the recipe auto-repair loop + persist its result."""
    from app.harness.recipe_repair import run_recipe_repair

    trace = TraceLogger(store.trace_path)
    try:
        repair_result = run_recipe_repair(
            recipe=recipe,
            graph=graph,
            run_store=store,
            initial_verification=verification,
            trace=trace,
        )
    except Exception as exc:  # noqa: BLE001 — never crash the pack run.
        console.print(f"[red]Auto-repair loop raised:[/] {type(exc).__name__}: {exc}")
        return None

    repair_path = store.path("recipe_repair.json")
    store.write_model(repair_path, repair_result)
    # Re-persist the FINAL verification too so future tools read the
    # post-repair verdict, not the original pack-run one.
    if repair_result.final_verification is not None:
        store.write_model(
            store.path("recipe_verification.json"),
            repair_result.final_verification,
        )
    return repair_result


def _render_recipe_repair(repair_result) -> None:
    """Phase 21 — render the repair loop's attempts + verdict."""
    if repair_result.rounds_used == 0:
        return  # nothing to show — verification already passed
    badge = (
        "[green]REPAIRED[/]"
        if repair_result.repaired
        else f"[yellow]NOT REPAIRED[/] ({repair_result.halt_reason})"
    )
    console.print(
        f"\n[bold]Auto-repair: {badge}[/]  "
        f"({repair_result.rounds_used}/{repair_result.attempts[0].attempt + 0} round(s) used)"
        if repair_result.attempts
        else f"\n[bold]Auto-repair: {badge}[/]"
    )

    table = Table(title="Repair attempts")
    table.add_column("#", style="dim", justify="right")
    table.add_column("triggered by", style="cyan")
    table.add_column("target stage")
    table.add_column("result")
    table.add_column("ms", justify="right")
    for a in repair_result.attempts:
        if a.error:
            status = f"[red]error: {a.error}[/]"
        elif a.post_attempt_passed:
            status = "[green]pack now passes all verifiers[/]"
        else:
            still = ", ".join(a.failed_after_attempt) or "(none)"
            status = f"[yellow]still failing:[/] {still}"
        table.add_row(
            str(a.attempt),
            a.triggered_by_verifier,
            a.target_stage,
            status,
            str(a.duration_ms),
        )
    console.print(table)

    # Echo the hint that was used (small but useful — tells the user
    # what the planner LLM saw).
    for a in repair_result.attempts:
        console.print(
            f"  [dim]Hint for round {a.attempt} → `{a.target_stage}`:[/] {a.suggested_hint}"
        )


def _run_recipe_verifiers(
    *,
    recipe,
    store,
    workspace,
    result,
    locale: str | None = None,
):
    """Phase 19 — run every verifier declared in ``recipe.verifiers``.

    Builds a :class:`RecipeVerifierContext` from the run artifacts (so
    verifiers see the same workspace, recipe, and aggregated moves the
    user sees), runs each verifier through the registry, writes the
    bundle to ``<run_dir>/recipe_verification.json``, and returns the
    :class:`RecipeVerification` envelope.

    Returns ``None`` when ``recipe.verifiers`` is empty (no overhead
    for recipes that opt out).
    """
    if not recipe.verifiers:
        return None

    from app.eval.recipe_verifiers import (
        RecipeVerification,
        RecipeVerifierContext,
        run_all,
    )
    from app.schemas import RollbackManifest
    from app.schemas.rollback import RollbackOpType

    # Aggregate moves from the rollback manifest the runner produced.
    # MOVE_BACK entries record the INVERSE op, so:
    #   entry.target_path = original (pre-execute) location
    #   entry.source_path = final (post-execute) location
    # We want original -> final, so swap.
    moves: dict[str, str] = {}
    if store.rollback_path.exists():
        try:
            manifest = store.read_model(store.rollback_path, RollbackManifest)
            for entry in manifest.entries:
                if entry.op is RollbackOpType.MOVE_BACK and entry.source_path and entry.target_path:
                    moves[entry.target_path] = entry.source_path
        except Exception:  # noqa: BLE001 — verifier should never crash on a manifest issue
            moves = {}

    # Inputs: read the first stage's workspace snapshot (captured by the
    # runner before any move happened). If unavailable, fall back to
    # scanning the workspace directory.
    inputs: list[str] = []
    if store.stages_root.exists():
        stage_dirs = sorted(d for d in store.stages_root.iterdir() if d.is_dir())
        if stage_dirs:
            snap_path = stage_dirs[0] / "workspace_snapshot.json"
            if snap_path.exists():
                try:
                    from app.schemas import WorkspaceSnapshot

                    snap = store.read_model(snap_path, WorkspaceSnapshot)
                    inputs = [f.path for f in snap.files]
                except Exception:  # noqa: BLE001
                    inputs = []

    ctx_kwargs: dict[str, Any] = {
        "recipe": recipe,
        "workspace_path": Path(workspace).resolve(),
        "snapshot_inputs": inputs,
        "moves": moves,
        "task_graph_result": result,
        "run_id": result.task_id,
    }
    if locale is not None:
        ctx_kwargs["locale"] = locale
    ctx = RecipeVerifierContext(**ctx_kwargs)

    verdicts = run_all(list(recipe.verifiers), ctx)
    verification = RecipeVerification.from_verdicts(
        run_id=result.task_id,
        recipe_name=recipe.name,
        verdicts=verdicts,
    )
    # Persist for the rollback / status / UI flows.
    bundle_path = store.path("recipe_verification.json")
    store.write_model(bundle_path, verification)
    return verification


def _render_recipe_verification(verification) -> None:
    """Phase 19 — render the verifier verdict table."""
    badge = (
        "[green]PASSED[/]"
        if verification.passed
        else f"[red]FAILED[/] ({verification.failed_count})"
    )
    table = Table(title=f"Deliverable verifiers: {badge}")
    table.add_column("verifier", style="cyan")
    table.add_column("status")
    table.add_column("detail")
    for v in verification.verdicts:
        if v.skipped:
            status = "[dim]skipped[/]"
        elif v.passed:
            status = "[green]pass[/]"
        else:
            status = "[red]fail[/]"
        table.add_row(v.name, status, v.detail)
    console.print(table)
    if verification.skipped_count:
        console.print(
            f"[dim]{verification.skipped_count} verifier(s) skipped "
            "(no LLM key, no relevant artefacts, etc.)[/]"
        )
    failed = [v for v in verification.verdicts if not v.passed and not v.skipped]
    if failed:
        console.print()
        for v in failed:
            if v.suggested_hint:
                console.print(f"  [yellow]Hint for `{v.name}`:[/] {v.suggested_hint}")


# --------------------------------------------------------------------- trace (Phase 25.2)


trace_app = typer.Typer(
    help="Phase 25.2 — inspect the trace.jsonl event stream for a run.",
    no_args_is_help=True,
)
app.add_typer(trace_app, name="trace")


def _resolve_task_id(positional: Optional[str], option: Optional[str]) -> str:
    """Phase 34.0 — F-2 helper. The trace commands accept ``task_id``
    as either a positional argument (the convention CLI users expect)
    OR a ``--task-id`` flag (the previous-only shape, kept for
    backward compatibility). Caller passes both; this resolves to the
    one that's set OR raises a clean error.
    """
    if positional and option:
        if positional != option:
            console.print(
                f"[red]Conflicting task_id values:[/] positional {positional!r} vs --task-id {option!r}",
            )
            raise typer.Exit(code=2)
        return positional
    chosen = positional or option
    if not chosen:
        console.print(
            "[red]Missing task_id.[/] Provide it positionally "
            "(``localflow trace show <task_id>``) or via ``--task-id``.",
        )
        raise typer.Exit(code=2)
    return chosen


@trace_app.command("show")
def cmd_trace_show(
    # Phase 34.0 — F-2 fix. Accept ``task_id`` as a positional argument
    # OR via ``--task-id``. Typer doesn't natively support both shapes on
    # the same parameter; we expose two parameters and resolve to one.
    task_id_pos: Optional[str] = typer.Argument(
        None,
        metavar="[TASK_ID]",
        help="Task ID to inspect. Equivalent to --task-id.",
    ),
    task_id_opt: Optional[str] = typer.Option(
        None, "--task-id", help="Task ID to inspect (alias for positional argument)."
    ),
    event_type: Optional[str] = typer.Option(
        None,
        "--event-type",
        help=(
            "Filter to one event type (e.g. action.end, llm.call.end, "
            "compute.action.end). Matches the on-disk ``event`` field."
        ),
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        "-n",
        help="Show at most this many most-recent rows. Defaults to 50.",
    ),
    show_thought: bool = typer.Option(
        False,
        "--show-thought",
        help=(
            "Print the LLM thought / reasoning for ACTION_* rows when "
            "present (Phase 25.1 ActionTraceEvent). Off by default to "
            "keep the summary scannable."
        ),
    ),
    show_observation: bool = typer.Option(
        False,
        "--show-observation",
        help=(
            "Print the action's observation dict for ACTION_END rows "
            "(Phase 25.1 ActionTraceEvent). Useful for debugging failed "
            "actions — the observation includes the error string + paths."
        ),
    ),
) -> None:
    """Phase 25.2 — pretty-print one run's trace.jsonl.

    Each ACTION_* row produced by v0.23.x+ kernels is an
    ``ActionTraceEvent`` carrying the LLM's thought / reasoning /
    raw tool_use plus the action's observation (action_type +
    source/target + hashes + rollback_entry, or the failure error).
    This command makes those fields visible without forcing the user
    to ``cat`` the raw JSONL.
    """
    import json as _json

    from app.storage.run_store import RunStore

    # Phase 34.0 — F-2 fix. Accept the task_id from either the
    # positional argument or the ``--task-id`` flag. Conflict =
    # explicit error so we don't silently pick the wrong one.
    task_id = _resolve_task_id(task_id_pos, task_id_opt)

    store = RunStore(task_id=task_id)
    trace_path = store.trace_path
    if not trace_path.exists():
        console.print(f"[yellow]No trace.jsonl for task {task_id!r}.[/] (Looked at {trace_path})")
        raise typer.Exit(code=1)

    rows: list[dict] = []
    with trace_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue

    if event_type:
        rows = [r for r in rows if r.get("event") == event_type]
    if limit and limit > 0:
        rows = rows[-limit:]

    if not rows:
        console.print(f"[dim]No matching rows in {trace_path}.[/]")
        return

    table = Table(title=f"trace.jsonl  ·  {task_id}  ·  {len(rows)} row(s)")
    table.add_column("ts", style="dim", no_wrap=True)
    table.add_column("event", style="cyan", no_wrap=True)
    table.add_column("action_id", style="green", no_wrap=True)
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for row in rows:
        payload = row.get("payload") or {}
        action_id = payload.get("action_id") or ""
        status = payload.get("status") or ""
        status_style = ""
        if status == "fail" or status == "blocked":
            status_style = "[red]"
        elif status == "ok":
            status_style = "[green]"
        detail = (payload.get("detail") or "")[:120]
        ts_short = (row.get("ts") or "")[11:19]  # HH:MM:SS
        table.add_row(
            ts_short,
            row.get("event", ""),
            action_id,
            f"{status_style}{status}[/]" if status_style else status,
            detail,
        )
    console.print(table)

    if show_thought or show_observation:
        for row in rows:
            payload = row.get("payload") or {}
            event = row.get("event", "")
            if not event.startswith("action."):
                continue
            action_id = payload.get("action_id") or "?"
            console.print()
            console.print(f"[cyan]── {event}  ·  {action_id} ──[/]")
            if show_thought and payload.get("thought"):
                console.print(f"[bold]thought[/]:  {payload['thought']}")
            if show_observation and payload.get("observation"):
                obs = payload["observation"]
                console.print("[bold]observation[/]:")
                for k, v in obs.items():
                    if v is None:
                        continue
                    val = _json.dumps(v) if isinstance(v, (dict, list)) else v
                    console.print(f"  · [dim]{k}[/]: {val}")


@trace_app.command("summary")
def cmd_trace_summary(
    task_id_pos: Optional[str] = typer.Argument(
        None,
        metavar="[TASK_ID]",
        help="Task ID to summarise. Equivalent to --task-id.",
    ),
    task_id_opt: Optional[str] = typer.Option(
        None, "--task-id", help="Task ID to summarise (alias for positional argument)."
    ),
) -> None:
    """Phase 25.2 — one-line-per-event-type histogram for a run.

    Quick sanity check: did the kernel emit what you expected? E.g.
    if you ran a 5-action plan, you should see action.start = 5,
    action.end = 5, policy.check = 0 (no rejections).
    """
    import json as _json
    from collections import Counter

    from app.storage.run_store import RunStore

    # Phase 34.0 — F-2 fix. Same dual-shape resolution as cmd_trace_show.
    task_id = _resolve_task_id(task_id_pos, task_id_opt)

    store = RunStore(task_id=task_id)
    trace_path = store.trace_path
    if not trace_path.exists():
        console.print(f"[yellow]No trace.jsonl for task {task_id!r}.[/] (Looked at {trace_path})")
        raise typer.Exit(code=1)

    by_event: Counter = Counter()
    by_status: Counter = Counter()
    rich_rows = 0  # ActionTraceEvent shape — rows that carry the new fields
    failures = 0
    with trace_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            event = row.get("event", "")
            by_event[event] += 1
            payload = row.get("payload") or {}
            by_status[payload.get("status") or "(none)"] += 1
            if (
                payload.get("thought") is not None
                or payload.get("observation") is not None
                or payload.get("tool_call_raw") is not None
            ):
                rich_rows += 1
            if payload.get("status") in ("fail", "blocked"):
                failures += 1

    if not by_event:
        console.print(f"[dim]Empty trace at {trace_path}.[/]")
        return

    table = Table(title=f"trace.jsonl summary  ·  {task_id}")
    table.add_column("event_type", style="cyan", no_wrap=True)
    table.add_column("count", justify="right")
    for event in sorted(by_event):
        table.add_row(event, str(by_event[event]))
    console.print(table)

    console.print()
    console.print(f"Total rows:           [bold]{sum(by_event.values())}[/]")
    console.print(f"ActionTraceEvent rows (Phase 25.1 shape): [bold]{rich_rows}[/]")
    console.print(f"Failed / blocked rows: [bold]{failures}[/]")


# --------------------------------------------------------------------- goal (Phase 18)


@app.command("goal")
def cmd_goal(
    user_goal: str = typer.Argument(..., help="What you want, in natural language."),
    workspace: Path = typer.Option(
        ...,
        "--workspace",
        "-w",
        help="Workspace directory the pack will operate on.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help=(
            "Force the router-only path even when an LLM key is configured. "
            "Useful for CI / dry runs."
        ),
    ),
    run: bool = typer.Option(
        False,
        "--run",
        help=(
            "When the interpreter picks a confident recipe, kick off "
            "`pack run` against it immediately (still prompts for approval "
            "unless --yes is also passed)."
        ),
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip approval when --run is used."),
    locale: str = typer.Option(
        "zh-CN",
        "--locale",
        help=(
            "Language for the interpreter's rationale + clarifying questions "
            "AND for the downstream pack run (when --run is set). One of: "
            "zh-CN, en-US. Defaults to zh-CN."
        ),
    ),
) -> None:
    """Phase 18 — natural-language entry point.

    Calls the :class:`GoalInterpreter` against the workspace + goal.
    On a confident pick, prints the suggested pack (and runs it when
    ``--run`` is set). On an ambiguous goal, the LLM may emit
    clarifying questions which you answer at the prompt; the
    interpreter is re-invoked with your answer.
    """
    from app.agent.goal_interpreter import GoalInterpreter
    from app.tools.file_scan import scan_workspace

    if not workspace.exists() or not workspace.is_dir():
        console.print(f"[red]Workspace not found:[/] {workspace}")
        raise typer.Exit(code=2)

    if locale not in ("zh-CN", "en-US"):
        console.print(f"[red]Unknown locale {locale!r};[/] expected zh-CN or en-US.")
        raise typer.Exit(code=2)

    with console.status("Scanning workspace…"):
        snapshot = scan_workspace(workspace, task_id="goal", compute_hash=False)

    client = None
    if not no_llm:
        try:
            from app.agent.planner import _default_client

            client = _default_client()
        except Exception:  # noqa: BLE001 — graceful: degrade to router only.
            client = None

    interpreter = GoalInterpreter(client=client, locale=locale)
    answers: list[str] = []
    max_rounds = 2
    interpretation = None
    for round_idx in range(max_rounds):
        interpretation = interpreter.interpret(
            user_goal=user_goal,
            snapshot=snapshot,
            prior_answers=answers,
        )
        if interpretation.decision == "pick":
            break
        # decision == "clarify" — print questions, capture an answer.
        console.print(
            Panel.fit(
                "[bold]Decision:[/] [yellow]clarify[/]\n\n"
                "[bold]I need a bit more context:[/]\n"
                + "\n".join(
                    f"  {i + 1}. {q}" for i, q in enumerate(interpretation.clarifying_questions)
                )
                + f"\n\n[dim]Rationale: {interpretation.rationale}[/]",
                title=f"Clarifying — round {round_idx + 1}/{max_rounds}",
                border_style="yellow",
            )
        )
        if round_idx == max_rounds - 1:
            console.print(
                "[yellow]Max clarification rounds reached. "
                "Re-run with a clearer goal or use `localflow pack list` "
                "to pick directly.[/]"
            )
            raise typer.Exit(code=1)
        try:
            answer = typer.prompt("Your answer", default="", show_default=False).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("[yellow]Aborted.[/]")
            raise typer.Exit(code=1) from None
        if not answer:
            console.print("[yellow]No answer — aborting.[/]")
            raise typer.Exit(code=1)
        answers.append(answer)

    assert interpretation is not None
    assert interpretation.decision == "pick" and interpretation.recipe_name is not None

    # Render the verdict. Phase 21.1: lead with an explicit Decision: /
    # Recipe: pair so scripts + humans can grep the output without
    # parsing the prose rationale.
    score_lines = "\n".join(
        f"  {s['recipe']:25} score {s['score']:+d}  ·  " + ("; ".join(s["why"]) or "(no signals)")
        for s in interpretation.router_scores
    )
    console.print(
        Panel.fit(
            f"[bold]Decision:[/] [green]pick[/]\n"
            f"[bold]Recipe:[/] [cyan]{interpretation.recipe_name}[/]\n"
            f"[bold]Source:[/] {interpretation.source}\n"
            f"[bold]Rationale:[/] {interpretation.rationale}\n\n"
            f"[bold]Router ranking:[/]\n{score_lines}",
            title="Goal interpretation",
            border_style="green",
        )
    )

    if not run:
        console.print(
            f"\nRun it: [bold]localflow pack run {interpretation.recipe_name} "
            f"--workspace {workspace}[/]"
        )
        return

    # Chain into pack run.
    cmd_pack_run(
        name=interpretation.recipe_name,
        workspace=workspace,
        yes=yes,
        enable_repair=False,
        locale=locale,
    )


if __name__ == "__main__":
    app()
