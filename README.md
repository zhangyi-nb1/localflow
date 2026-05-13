# LocalFlow Agent

[![CI](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml/badge.svg)](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![Release](https://img.shields.io/github/v/tag/zhangyi-nb1/localflow?label=release)](https://github.com/zhangyi-nb1/localflow/releases)

**A safe, controllable, recoverable, verifiable, extensible execution harness for LLM agents operating on local workspaces.**

LocalFlow is *not* "an agent that organizes files." It's a Harness Engineering project — the differentiator is the **execution scaffold around the LLM**, not the LLM itself. Models produce typed `TaskSpec` / `ActionPlan` / `Action` structures; the harness performs every side effect under strict dry-run + approval + checkpoint + rollback + verifier discipline.

> The model proposes; the harness disposes.

## Status

| Phase | Name | Shipped | New tests |
|-------|------|---------|-----------|
| 0 | Harness skeleton | Rule-based folder organizer, full inspect→plan→dry-run→approve→execute→verify→rollback loop | 53 |
| 1 | LLM planner | OpenAI/Anthropic adapters, strict tool calls, SSE streaming, tool-result repair loop | ~10 |
| 2.1 + 2.2 | Content awareness | PDF/text preview extraction, file-type scan, semantic-rename hooks | 14 |
| 2.3 | Skill ABC + plug-in | `Skill` ABC, `SkillRegistry`, `pdf_indexer` skill, source provenance | 20 |
| 3.1–3.3 | DataOps | `data_reporter` + `data_analyzer` skills, typed `AnalysisSpec`, matplotlib charts | 60+ |
| 4.1 | Filesystem skill discovery | Drop a skill into `~/.localflow/skills/`, it loads at startup | 11 |
| 4.2 | Tool Registry | 15 declarable callable tools, manifest `required_tools` validation | 24 |
| 4.3 | Unified contract test | `run_skill_contract()` — 8-stage lifecycle gauntlet any skill must pass | 10 |
| 5 | Memory MVP | `forbidden_paths` (kernel-side) + `naming_style` (folder_organizer), `localflow memory ...` CLI | 60 |
| 6.1 | MCP server | `localflow mcp-serve` exposes 15 tools over stdio JSON-RPC for Claude Code / other MCP clients | 24 |
| **TOTAL** | | **5 skills, 15 internal tools, 15 MCP tools, full lifecycle** | **249 passing** |

**Deferred** (groundwork laid, not shipped):
- Phase 5.x — directory structure pref, report template, common task recipes
- Phase 6.2 — MCP client (reverse direction: LocalFlow calls external MCP servers)
- Phase 6.3 — WebCollect skill (HTTPS GET → workspace)

## Quick start

```powershell
# 1. Install (venv recommended)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,mcp]"

# 2. Take it for a spin on the bundled messy folder
localflow plan ./examples/messy_downloads --goal "organize by file type" --planner rule
localflow execute --task-id <task_id> --yes
localflow rollback --run-id <task_id> --yes
```

Every run produces a self-contained record under `.localflow/runs/<task_id>/`:

```
task.json                  workspace_snapshot.json
plan.json                  dry_run.md
actions.json               execution_log.jsonl
rollback_manifest.json     verify_report.json
final_report.md            backups/        (when overwrites happen)
```

### Set persistent preferences (Phase 5)

```powershell
localflow memory forbid private/secrets     # kernel-side blocker, no skill can override
localflow memory set naming_style snake_case
localflow memory list
localflow memory audit                       # every mutation logged
```

### Drive LocalFlow from Claude Code / MCP clients (Phase 6.1)

`.mcp.json` config + 15 tools — see [docs/MCP.md](docs/MCP.md).

### Skills & tools introspection (Phase 4)

```powershell
localflow skills           # 5 skills + Phase 4.1 load audit
localflow tools            # 15 callable tools by category, with "used-by"
```

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  CLI (Typer)                MCP Server (stdio JSON-RPC, Phase 6.1)│
└──────┬───────────────────────────────┬──────────────────────────┘
       │                               │
       ▼                               ▼
┌────────────────────────────────────────────────────────────────┐
│  SKILL LAYER (Phase 2.3 + 4)                                   │
│  Skill ABC + SkillRegistry + filesystem discovery (4.1)        │
│  contract test template (4.3)                                  │
│  built-ins: folder_organizer / pdf_indexer / data_reporter /   │
│             data_analyzer  + external plug-ins                 │
└──────┬─────────────────────────────────────────┬───────────────┘
       │ declares required_tools                 │ produces ActionPlan
       ▼                                         ▼
┌──────────────────────┐         ┌──────────────────────────────┐
│  TOOL REGISTRY (4.2) │         │  HARNESS KERNEL              │
│  15 read/transform/  │         │  policy_guard / dry_run /    │
│  render helpers      │         │  approval / executor /       │
│                      │         │  verifier / rollback / audit │
└──────────────────────┘         │  control_loop                │
                                 └──────┬───────────────────────┘
                                        │ reads
                                        ▼
                                ┌──────────────────────────────┐
                                │  MEMORY (Phase 5)            │
                                │  forbidden_paths (kernel)    │
                                │  naming_style (skill)        │
                                │  + audit.jsonl               │
                                └──────────────────────────────┘
```

- **CLI / MCP Server** — two equivalent driver surfaces. MCP wraps the same `control_loop.*` functions the CLI uses; no new actions on the MCP path.
- **Skill Layer** — every task feature is a `Skill` plug-in. Built-ins live in `app/skills/<name>/`; external skills are auto-discovered from `~/.localflow/skills/` or `$LOCALFLOW_SKILLS_DIR`.
- **Tool Registry** — shared callables (`file_scan`, `pdf_ops`, `data_ops`, `chart_ops`, `data_analysis`). Skills *declare* `required_tools`; declarations are validated at skill-register time, so typos / drift fail loudly at startup.
- **Harness Kernel** — the safety machinery. Every action passes `policy_guard.evaluate_action` before IO, every write produces a rollback entry, every run gets an independent verifier pass.
- **Memory** — durable preferences. `forbidden_paths` is universal (read by the kernel before every action); `naming_style` is opt-in (read by `folder_organizer`). All mutations append to `~/.localflow/memory/audit.jsonl`.

Full layer-by-layer detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Security model (read before extending)

LocalFlow's safety machinery (workspace containment, `forbidden_paths`,
dry-run, approval tokens, rollback, verifier, audit) defends against
plans that **the kernel sees**. **External skills are TRUSTED Python
code**: once loaded, an external skill can `import os; os.unlink(...)`
and bypass every primitive listed below.

Treat external skill loading as you would `pip install` from an
unknown source. See [docs/SECURITY.md](docs/SECURITY.md) for the full
threat model, MCP approval-token contract, and the
`LOCALFLOW_MCP_ALLOW_DANGEROUS` flag.

## Design principles (the 8 iron rules)

1. **The model does not execute side effects** — it only emits `TaskSpec` / `ActionPlan` / `Action` / `RepairSuggestion`.
2. **Every action is structured** (Pydantic), never free-form natural language.
3. **Every write action goes through dry-run** before approval.
4. **`delete` is disabled by default** — duplicates are reported, not removed.
5. **Every path must resolve inside the workspace root** + must not intersect `forbidden_paths` (Phase 5).
6. **Existing target files are not overwritten by default** — auto-suffix or explicit `overwrite_existing` flag + backup.
7. **Every write is fully traceable** — action_id, timestamps, hashes, rollback record.
8. **The verifier is independent of the model** — completion is determined by rules, not self-assessment.

## Layout

```
app/
  agent/        LLM planner + repair (Phase 1)
  harness/      policy_guard / dry_run / approval / executor / verifier /
                rollback / audit / control_loop  (the kernel)
  mcp/          MCP server bootstrap + 15 tool handlers (Phase 6.1)
  memory/       MemoryStore + naming transforms + Pydantic schema (Phase 5)
  schemas/      Pydantic data contracts (TaskSpec, ActionPlan, Action, ...)
  skills/       Skill ABC + registry + filesystem loader + contract template
                Built-ins: folder_organizer / pdf_indexer / data_reporter / data_analyzer
  storage/      RunStore + JsonlLogger
  tools/        Shared callable helpers (file_scan, pdf_ops, data_ops, ...)
                + ToolRegistry (Phase 4.2)
  cli.py        Typer entry point
docs/           PHASES.md, ARCHITECTURE.md, MCP.md
examples/       messy_downloads (folder_organizer demo)
                pdf_demo (pdf_indexer demo)
                external_skill_example (Phase 4.1 plug-in pattern + contract test demo)
tests/          249 tests across all layers
localflow_agent_harness_outline.md   Master design document (~1400 lines)
```

## Extend it

- **New skill**: subclass `Skill` (see [app/skills/_base.py](app/skills/_base.py)), drop into `app/skills/<name>/` (built-in) or `~/.localflow/skills/<name>/` (external). Verify via `run_skill_contract()` ([app/skills/_contract.py](app/skills/_contract.py)). Worked example: [examples/external_skill_example/](examples/external_skill_example/).
- **New tool**: register a `ToolSpec` in [app/tools/_registry.py](app/tools/_registry.py); skills opt in via `required_tools`.
- **New memory preference**: add a field to [app/memory/_schema.py](app/memory/_schema.py), wire one CLI command + one consumer site.
- **Drive via MCP**: see [docs/MCP.md](docs/MCP.md).

Detailed per-phase changelog: [docs/PHASES.md](docs/PHASES.md). Full design rationale: [localflow_agent_harness_outline.md](localflow_agent_harness_outline.md).

## Distribution

### Local build

```powershell
pip install build
python -m build
# → dist/localflow_agent-0.6.2-py3-none-any.whl
# → dist/localflow_agent-0.6.2.tar.gz
pip install dist/localflow_agent-0.6.2-py3-none-any.whl
```

### CI / Release automation

| Workflow | Trigger | What it does |
|---|---|---|
| [CI](.github/workflows/ci.yml) | every push & PR | matrix tests on Linux/Windows/macOS × Python 3.11/3.12/3.13 + ruff lint + wheel build |
| [Release](.github/workflows/release.yml) | tag `v*` push, or manual dispatch | builds wheel + sdist, creates a GitHub Release with auto-generated notes and both artifacts attached |

Released wheels are available under the [GitHub Releases](https://github.com/zhangyi-nb1/localflow/releases) page.

Version scheme: `0.<highest_phase>.<sub>`. Current `0.6.2` = Phase 6.1 + Phase 7 security hardening.

## License

MIT — see [pyproject.toml](pyproject.toml).
