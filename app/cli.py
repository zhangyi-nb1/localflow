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
from typing import Optional

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
    assessment = control_loop.run_risk_check(task, plan)
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
    assessment = control_loop.run_risk_check(task, plan)
    md = control_loop.run_dry_run(task, plan, assessment, store)
    console.print(Markdown(md))
    console.print(
        f"\n[dim]Wrote: {store.dry_run_path}[/]\n"
        f"Next: [bold]localflow execute --task-id {task_id}[/]"
    )


# --------------------------------------------------------------------- execute


@app.command("execute")
def cmd_execute(
    task_id: str = typer.Option(..., "--task-id", help="Task identifier."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive approval prompt."),
    resume: bool = typer.Option(
        False, "--resume", help="Resume from checkpoint, skipping completed actions."
    ),
) -> None:
    """Approve and execute the plan. Records every change for rollback."""
    store = RunStore(task_id=task_id)
    if not store.plan_path.exists():
        raise typer.BadParameter(f"no plan found for task {task_id}")
    task = store.load_task()
    plan = store.load_plan()
    snapshot = store.load_workspace()
    assessment = control_loop.run_risk_check(task, plan)

    if assessment.risk_level.value == "blocked":
        console.print("[red]Plan blocked by policy guard. Aborting.[/]")
        for w in assessment.warnings:
            console.print(f"  • {w}")
        raise typer.Exit(code=2)

    # Always render a fresh dry-run preview before asking for approval.
    md = control_loop.run_dry_run(task, plan, assessment, store)
    console.print(Markdown(md))

    write_count = sum(1 for a in plan.actions if a.is_write())
    decision = ask_approval(
        risk_level=assessment.risk_level.value,
        write_action_count=write_count,
        auto_approve=yes,
        console=console,
    )
    AuditLogger(store.audit_log_path).log(
        "approval.decision",
        approved=decision.approved,
        reason=decision.reason,
    )
    if not decision.approved:
        console.print("[yellow]Execution cancelled.[/]")
        raise typer.Exit(code=1)

    outcome = control_loop.run_execute(task, plan, store, approved=True, resume=resume)
    verification = control_loop.run_verify(task, plan, store, outcome, snapshot)

    # Skill-specific final_report: each Skill renders its own markdown.
    skill_obj = get_default_registry().require(task.skill)
    report = skill_obj.report(task=task, plan=plan, outcome=outcome, verification=verification)
    store.write_text(store.final_report_path, report)

    badge = "[green]OK[/]" if outcome.success and verification.passed else "[red]FAIL[/]"
    console.print(
        f"\n{badge}  executed: {len(outcome.records)} actions  ·  "
        f"verify: {'passed' if verification.passed else 'failed'}\n"
        f"[dim]Report: {store.final_report_path}[/]\n"
        f"To undo: [bold]localflow rollback --run-id {task_id}[/]"
    )


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
) -> None:
    """Undo a previously-executed run using its rollback manifest.

    Phase 7.1: by default, rollback refuses to clobber files the user
    has edited since execute (detected via sha256 drift against the
    executor's recorded ``after_hash``). Drifted entries are reported
    as **conflicts** and skipped. Pass ``--force`` to override.
    """
    store = RunStore(task_id=run_id)
    if not store.rollback_path.exists():
        raise typer.BadParameter(f"no rollback manifest for run {run_id}")
    task = store.load_task()
    manifest = store.load_rollback()

    if not yes:
        confirm = typer.confirm(
            f"Roll back {len(manifest.entries)} change(s) in {task.workspace_root}?"
        )
        if not confirm:
            console.print("[yellow]Rollback cancelled.[/]")
            raise typer.Exit(code=1)

    rollback = Rollback(workspace_root=Path(task.workspace_root), run_store=store)
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
    table.add_row("schema_version", str(prefs.schema_version))
    console.print(table)
    if prefs.is_default():
        console.print("\n[dim]All values are defaults; nothing persisted to influence runs.[/]")


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


_SCALAR_KEYS = ("naming_style", "prefer_llm_planner")


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
        ..., help="Preference key. Supported: 'naming_style' or 'prefer_llm_planner'."
    ),
    value: str = typer.Argument(
        ...,
        help=(
            "Value. For naming_style: original / snake_case / kebab-case / lower. "
            "For prefer_llm_planner: true / false."
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
        else:  # prefer_llm_planner
            result = store.set_prefer_llm_planner(_parse_bool_arg(value))
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

    results = []
    failed = 0
    for task in tasks:
        console.print(f"[cyan]→[/] running {task.task_id} — {task.title}")
        result = run_eval(task, home)
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


if __name__ == "__main__":
    app()
