# LocalFlow — Architecture

Five layers. Dependencies always point downward. The Harness Kernel
in the middle is the only piece allowed to perform IO. Everything else
either produces typed plans (skills, agent), classifies / transforms
in-memory (tools, memory), or wraps the existing surface (CLI, MCP).

```
┌────────────────────────────────────────────────────────────────────┐
│  DRIVER LAYER                                                      │
│  ┌──────────────────────┐    ┌────────────────────────────────┐   │
│  │  CLI (Typer)         │    │  MCP Server (stdio JSON-RPC)   │   │
│  │  app/cli.py          │    │  app/mcp/  (Phase 6.1)         │   │
│  └──────────┬───────────┘    └───────────────┬────────────────┘   │
└─────────────┼────────────────────────────────┼────────────────────┘
              ▼                                ▼
┌────────────────────────────────────────────────────────────────────┐
│  SKILL LAYER  (Phase 2.3 + 4)                                      │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │  Skill ABC  +  SkillRegistry  +  filesystem loader (4.1)    │ │
│  │  contract test template (4.3)                                │ │
│  │  app/skills/                                                 │ │
│  │  built-ins:  folder_organizer · pdf_indexer ·                │ │
│  │              data_reporter   · data_analyzer                 │ │
│  │  external:   ~/.localflow/skills/<name>/skill.py             │ │
│  └──────────┬─────────────────────────────────────┬─────────────┘ │
└─────────────┼─────────────────────────────────────┼───────────────┘
              │ declares                            │ produces
              │ required_tools                      │ ActionPlan
              ▼                                     ▼
┌────────────────────────┐         ┌────────────────────────────────┐
│  TOOL REGISTRY (4.2)   │         │  HARNESS KERNEL                │
│  app/tools/_registry   │         │  app/harness/                  │
│  ┌──────────────────┐  │         │  ┌──────────────────────────┐ │
│  │ ToolSpec table   │  │         │  │ policy_guard             │ │
│  │ 15 callables:    │  │         │  │   evaluate_action        │ │
│  │   read (11)      │  │         │  │   assess_plan            │ │
│  │   transform (2)  │  │         │  │ dry_run (markdown)       │ │
│  │   render (2)     │  │         │  │ approval                 │ │
│  │ category +       │  │         │  │ executor (does ALL IO)   │ │
│  │ used_by index    │  │         │  │ verifier (independent)   │ │
│  │ file_ops NOT     │  │         │  │ rollback                 │ │
│  │ registered       │  │         │  │ audit (JsonlLogger)      │ │
│  │ (kernel-only)    │  │         │  │ control_loop             │ │
│  └──────────────────┘  │         │  └────────────┬─────────────┘ │
└────────────────────────┘         └───────────────┼────────────────┘
                                                   │ reads
                                                   ▼
                                  ┌────────────────────────────────┐
                                  │  MEMORY  (Phase 5)             │
                                  │  app/memory/                   │
                                  │  ┌──────────────────────────┐ │
                                  │  │ MemoryStore              │ │
                                  │  │  ~/.localflow/memory/    │ │
                                  │  │    prefs.json            │ │
                                  │  │    audit.jsonl           │ │
                                  │  │                          │ │
                                  │  │ Categories shipped:      │ │
                                  │  │  forbidden_paths         │ │
                                  │  │    (kernel-enforced)     │ │
                                  │  │  naming_style            │ │
                                  │  │    (skill-consumed)      │ │
                                  │  └──────────────────────────┘ │
                                  └────────────────────────────────┘
```

## Layer 1 — Driver (CLI + MCP server)

Two equivalent surfaces. Both translate user / external-agent intent
into TaskSpec + skill dispatch and read results.

| Surface | File | Talks to |
|---|---|---|
| CLI | [app/cli.py](../app/cli.py) | terminal users via Typer commands |
| MCP server | [app/mcp/server.py](../app/mcp/server.py) | external MCP clients (Claude Code, Claude Desktop, ...) via stdio JSON-RPC |

**Critical property**: MCP wraps the *same* `control_loop.*` entry
points the CLI uses. No new actions on the MCP path; every safety
primitive (policy guard, dry-run, rollback, verifier, `forbidden_paths`)
applies identically. Implementation: [app/mcp/tools.py](../app/mcp/tools.py)
— each MCP tool handler is a thin wrapper around `control_loop.run_*`
or `MemoryStore` methods.

**Extension**: new CLI command — add `@app.command()` in `cli.py`. New
MCP tool — add a `ToolDef` to `TOOLS` list in `app/mcp/tools.py`.

## Layer 2 — Skill (Phase 2.3, 4.1, 4.2, 4.3)

Every task feature is a `Skill` plug-in. The framework owns the
lifecycle; skills own the planning + validation + reporting.

| Component | File | Role |
|---|---|---|
| `Skill` ABC | [app/skills/_base.py](../app/skills/_base.py) | `manifest / plan / plan_with_llm / validate / report` |
| `SkillRegistry` | same | process-wide name → skill dispatch |
| Filesystem loader | [app/skills/_loader.py](../app/skills/_loader.py) | discovers external skills at startup |
| Contract test | [app/skills/_contract.py](../app/skills/_contract.py) | 8-stage lifecycle gauntlet |

**Discovery paths** (Phase 4.1):

1. `$LOCALFLOW_SKILLS_DIR` (env, multi-path via `os.pathsep`)
2. `<cwd>/.localflow/skills/`
3. `~/.localflow/skills/`

Built-ins register first; external collisions error out and get
logged in the `localflow skills` audit table.

**Manifest `required_tools`** (Phase 4.2): skills declare the
`app/tools/*` helpers they call. `SkillRegistry.register` validates
every name resolves in the Tool Registry — typos / API drift fail at
startup, not runtime.

**Contract test** (Phase 4.3): import `run_skill_contract` and pass
your skill + a workspace seeder. Returns a report covering 8 stages
(manifest validity, empty workspace, happy path, validation positive
+ negative, execute + verify, rollback, report non-empty). Pattern
demoed in [examples/external_skill_example/test_contract.py](../examples/external_skill_example/test_contract.py).

**Built-ins**:

| Name | Phase | Purpose |
|---|---|---|
| `folder_organizer` | 0 | classify files by category, propose moves |
| `pdf_indexer` | 2.3 | extract PDF titles, synthesize `pdf_index.md` |
| `data_reporter` | 3.1+3.2 | per-tabular-file schema + stats + auto chart |
| `data_analyzer` | 3.3 | typed `AnalysisSpec`-driven groupby/agg/filter + chart |

**Extension**: subclass `Skill`, drop into `app/skills/<name>/` (built-in)
or `~/.localflow/skills/<name>/skill.py` (external). Run
`run_skill_contract` to verify lifecycle compatibility. Detailed
walkthrough: [examples/external_skill_example/README.md](../examples/external_skill_example/README.md).

## Layer 3 — Tool Registry (Phase 4.2)

Inventory of shared callable helpers Skills are allowed to use.
Documentation + validation surface, **not** a sandbox (Python imports
remain unconstrained).

**Registered tools** (15, see [app/tools/_registry.py](../app/tools/_registry.py)):

| Category | Tools |
|---|---|
| **read** (11) | `file_scan.scan_workspace` / `file_scan.classify` / `hash_ops.sha256_file` / `pdf_ops.extract_text_preview` / `text_ops.extract_text_preview` / `text_ops.can_preview_as_text` / `data_ops.is_csv_like` / `data_ops.is_excel_like` / `data_ops.is_supported_tabular` / `data_ops.read_tabular` / `data_ops.read_and_describe` |
| **transform** (2) | `data_ops.summarize_dataframe` / `data_analysis.execute_analysis` |
| **render** (2) | `chart_ops.histogram_png` / `chart_ops.bar_png` |

**Not registered** (deliberate): `app/tools/file_ops.*` (`move`, `copy`,
`write_bytes`, `remove_file`, ...). Those are mutating IO primitives —
only the Executor may call them. Registering them would blur the
"Skills produce Actions; Executor performs IO" boundary that iron rules
② / ③ enforce. A unit test pins this decision:
`test_file_ops_intentionally_excluded` in
[tests/test_tool_registry.py](../tests/test_tool_registry.py).

**Extension**: append a `ToolSpec` to `_build_default_registry()`.
Skills opt in via `SkillManifest.required_tools`.

## Layer 4 — Harness Kernel (Phase 0, lightly extended Phase 5)

The safety machinery. Every action — whether produced by a built-in
skill, an external plug-in, an LLM planner, or an MCP client — passes
through here before any side effect lands.

| Module | Role |
|---|---|
| [policy_guard.py](../app/harness/policy_guard.py) | `resolve_inside` (workspace containment), `evaluate_action`, `assess_plan`; checks `forbidden_actions` + `forbidden_paths` |
| [dry_run.py](../app/harness/dry_run.py) | render markdown preview of a plan |
| [approval.py](../app/harness/approval.py) | interactive approval gate (CLI); MCP bypasses with explicit `approved=true` |
| [executor.py](../app/harness/executor.py) | the only module allowed to call `app/tools/file_ops.*` |
| [verifier.py](../app/harness/verifier.py) | rules-based independent verification (NOT LLM-driven) |
| [rollback.py](../app/harness/rollback.py) | replay rollback manifest in reverse, restore backups, sweep empty dirs |
| [audit.py](../app/harness/audit.py) | thin wrapper around JsonlLogger for event records |
| [control_loop.py](../app/harness/control_loop.py) | `run_inspect / run_risk_check / run_dry_run / run_approval / run_execute / run_verify` — what CLI + MCP both call |

**Defense in depth**: `policy_guard.evaluate_action` runs at plan
construction time AND at execute time per action. A plan that passes
the planner's risk check can still be rejected at execute time if
state changed in between.

**Extension**: this layer is **closed to skills**. You should not be
adding to `app/harness/`. If you find yourself wanting to, that's a
strong signal the feature belongs in the Tool Registry or Skill layer
instead. The single documented exception is Phase 5 — see below.

## Layer 5 — Memory (Phase 5)

Durable user preferences, persisted to `~/.localflow/memory/prefs.json`.

| Field | Where consumed | Phase |
|---|---|---|
| `forbidden_paths: list[str]` | **kernel-side** (`policy_guard._is_under_forbidden`) | 5 |
| `naming_style: NamingStyle` | **skill-side** (`folder_organizer.planner`) | 5 |
| `schema_version: int` | future migration handle | 5 |

**Every mutation** appends to `~/.localflow/memory/audit.jsonl` with
ISO timestamp + event + before/after diff. `localflow memory audit`
tails it.

**Extension**: add a field to `MemoryPreferences` in
[app/memory/_schema.py](../app/memory/_schema.py); add one CLI command;
add one consumer site. The framework (load, save, atomic write, audit)
is already there.

## Security caveats

The architecture above describes what the kernel enforces for plans
**it gets to see**. Crucial caveat for Layer 2:

> **External skills are trusted Python code.** Phase 4.1's filesystem
> loader does a full `importlib` execution — once a skill is loaded,
> its code can `import os; os.unlink(...)` and bypass every primitive
> in Layers 3–5. The Tool Registry validates *declared* dependencies
> but does not prevent arbitrary imports.

The Phase 4.3 contract test confirms a skill is *compatible* with the
lifecycle. It does **not** confirm the skill is *safe*. Same trust
level as a `pip install` from the same source.

For MCP-driven flows (Layer 1 right), Phase 7 added:
- **Approval tokens** — `execute_plan` requires a token minted by
  `dry_run`. 10-min TTL, one-shot, drift-sensitive. See
  [app/mcp/approval.py](../app/mcp/approval.py).
- **Dangerous-tool gating** — `memory_unforbid_path` (the only tool
  that *weakens* a safety boundary) is hidden from the MCP tool list
  unless `LOCALFLOW_MCP_ALLOW_DANGEROUS=1` is set in the server's env.

Full threat model + mitigations in [docs/SECURITY.md](SECURITY.md).

## §10.7 ledger — kernel touches per phase

Project rule: adding a new Skill / Tool / Memory category should NOT
require kernel modification. Tracked explicitly:

| Phase | Kernel touch? |
|---|---|
| 1 (LLM) | NO |
| 2.1 + 2.2 (content awareness) | NO |
| 2.3 (Skill ABC) | NO |
| 3.1–3.3 (DataOps) | NO |
| 4.1 (skill discovery) | NO |
| 4.2 (Tool Registry) | NO |
| 4.3 (contract test) | NO |
| **5 (forbidden_paths)** | **YES** — ~25 lines, deliberate. See Phase 5 in [PHASES.md](PHASES.md) |
| 6.1 (MCP server) | NO |

**11 of 12 phases held the rule.** The single exception (Phase 5) is
exactly the case where doing it skill-side would defeat plug-in safety:
a forgetful skill author could silently bypass a user's "never touch X"
rule. Kernel-side enforcement is the only design that holds under
plug-in load.

## How a request flows through the system

End-to-end trace, CLI `localflow execute --task-id <id> --yes`:

```
1. cli.cmd_execute()
   └─→ control_loop.run_risk_check(task, plan)
       └─→ policy_guard.assess_plan(workspace, plan,
              forbidden_actions=..., forbidden_paths=...)   ← Layer 4 + Memory
   └─→ control_loop.run_execute(task, plan, store, approved=True)
       └─→ Executor.__init__(forbidden_paths=...)            ← Memory propagated
       └─→ Executor.execute(plan, approved=True)
           └─→ for each action:
               ├─→ policy_guard.evaluate_action(...)         ← defense-in-depth
               ├─→ if allowed: tools.file_ops.<mkdir/move/...>  ← only Executor reaches file_ops
               └─→ append RollbackEntry to manifest
   └─→ control_loop.run_verify(task, plan, store, outcome)
       └─→ Verifier.verify(...)                              ← rules only, no LLM
```

MCP path is identical: `app.mcp.tools.handle_execute_plan` calls the
same `control_loop.run_execute` and `control_loop.run_verify`. The
safety layers don't know or care which driver sent the request.
