# LocalFlow Agent

[![CI](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml/badge.svg)](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![Release](https://img.shields.io/github/v/tag/zhangyi-nb1/localflow?label=release)](https://github.com/zhangyi-nb1/localflow/releases)

**A safe execution harness for LLM agents operating on local workspaces.**

> The model proposes; the harness disposes.

LocalFlow is *not* "an agent that organizes files." It is the **harness** around the LLM — the part that turns model output into safe, reversible filesystem changes via structured action plans, dry-run preview, policy guards, rollback manifests, and an independent verifier.

---

## The problem with naive LLM agents

A typical "LLM that calls tools" pattern hands the model a `shell(command)` or `delete(path)` function. The model is one prompt injection / hallucination away from `rm -rf ~/`. There is no preview, no approval gate, no rollback, no proof that what ran is what was asked for.

LocalFlow inverts this. The LLM never executes side effects. It only emits a typed `ActionPlan`. The **harness kernel** is the only code allowed to touch the filesystem, and every action it touches has already passed:

```
  ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐
  │  plan   │───▶│ dry-run  │───▶│ approval │───▶│ execute │───▶│ verify  │───▶│ rollback │
  │  (LLM   │    │ (preview │    │ (token   │    │ (kernel │    │ (rules- │    │  (replay │
  │  or rule│    │  written │    │  CLI     │    │  IO)    │    │  based, │    │   in     │
  │  based) │    │  to .md) │    │  --yes / │    │         │    │  not    │    │   reverse│
  │         │    │          │    │  MCP)    │    │         │    │  LLM)   │    │ )        │
  └─────────┘    └──────────┘    └──────────┘    └─────────┘    └─────────┘    └──────────┘
```

Every action is a Pydantic struct (never a free-form string). Every write produces a `RollbackEntry`. The verifier is deterministic — it never asks the model "did it work?".

---

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[all]"           # everything (dev + mcp + ui + data + pdf + openai)
```

Or pick what you need (v0.9.1 split):

```powershell
pip install -e .                  # base: harness + folder_organizer + LLM clients
pip install -e ".[data]"          # + pandas / matplotlib / openpyxl — chart skills
pip install -e ".[pdf]"           # + pypdf — pdf_indexer
pip install -e ".[ui]"            # + Streamlit (auto-pulls pandas)
pip install -e ".[mcp]"           # + MCP SDK for `localflow mcp-serve`
pip install -e ".[dev]"           # + pytest / ruff + every data / pdf lib for CI
```

## Quickstart A — Browser UI (recommended for demos)

```powershell
localflow ui-serve
# → opens http://127.0.0.1:8501 in your default browser
```

1. **Pick a workspace** in the left sidebar (or visit `?unsafe=1` to pick a path outside `./sandbox/`).
2. **Plan page** — type a goal ("organize by file type", "整理文件并画柱状图统计", anything compound). The agent auto-decomposes it; you see the planned actions + risk badge.
3. **Execute page** — render the dry-run markdown, tick the approval box, hit *Execute*. The verifier runs automatically.
4. **Rollback page** — drift-aware preview + safe/force rollback if you want to undo.

Full walkthrough: [**docs/UI.md**](docs/UI.md) (EN) · [**docs/UI_zh.md**](docs/UI_zh.md) (中文)

## Quickstart B — CLI (developers / scripts / CI)

The 6-stage lifecycle as discrete commands — every command corresponds to one stage of the harness, with `dry-run` + `verify` explicit so you can wire it into automation:

```powershell
# 1. PLAN  — the LLM (or rule planner) emits a structured ActionPlan; nothing on disk yet.
localflow plan ./examples/messy_downloads --goal "organize by file type" --planner rule
# → Task created: 2026-05-13-001  ·  Actions: 40  ·  Risk: medium

# 2. DRY-RUN  — render a markdown preview of every action; still read-only.
#    Also mints the approval_token consumed by `execute`.
localflow dry-run --task-id 2026-05-13-001

# 3. EXECUTE  — the ONLY stage that mutates the workspace. `--yes` = explicit approval.
localflow execute --task-id 2026-05-13-001 --yes
# → executed: 40 actions  ·  verify: passed

# 4. VERIFY  — the verifier is rule-based and independent of the model.
localflow verify --task-id 2026-05-13-001
# → PASSED — All 6 checks passed.

# 5. ROLLBACK  — replay the rollback manifest in reverse. Bit-exact restoration.
localflow rollback --run-id 2026-05-13-001 --yes
# → undone: 40  ·  failed: 0
```

A literal trace of every artifact produced by this run — before/after file trees, plan JSON, dry-run table, verify report, rollback result — is in [**docs/demo_walkthrough.md**](docs/demo_walkthrough.md).

| Stage | What it does | What can it touch |
|---|---|---|
| `plan` | Produce a Pydantic `ActionPlan` from a workspace scan + a goal | reads workspace, writes only `<task_id>/task.json` and `plan.json` |
| `dry-run` | Render the plan as human-readable markdown + mint MCP approval token | writes only `dry_run.md` and `approval_token.json` |
| `execute` | The **only** stage allowed to perform real filesystem IO | runs every action through `policy_guard` first; appends a `RollbackEntry` per write |
| `verify` | Run rule-based completion checks against on-disk state and the manifest | read-only |
| `rollback` | Replay the manifest in reverse order; restore backups for overwrites | hash-checks each target before touching it; refuses on drift unless `--force` |

Every run produces a self-contained record at `.localflow/runs/<task_id>/`:

```
task.json                  workspace_snapshot.json
plan.json                  dry_run.md
actions.json               execution_log.jsonl
rollback_manifest.json     verify_report.json
final_report.md            approval_token.json  (consumed on execute)
backups/                   (created when overwrites happen)
```

---

## Safety model

The kernel defends against everything a *plan* can encode. It does **not** sandbox arbitrary Python code loaded as an external skill — that's documented honestly. Highlights:

- **Workspace containment** (`policy_guard.resolve_inside`) — every action path must resolve under `workspace_root`. No absolute paths. No `..` traversal. No symlink escape.
- **Forbidden paths** (Phase 5, kernel-side) — user-set `forbidden_paths` are checked before every action, by the kernel, not by skills. One skill can't accidentally bypass another user's "never touch X" rule.
- **MCP approval tokens** (Phase 7) — `execute_plan` over MCP requires a one-shot token minted by a prior `dry_run`. 10-minute TTL, bound to plan + dry-run + workspace hashes. Drift = invalid.
- **Dangerous-tool gating** — `memory_unforbid_path` (the only MCP tool that *weakens* a safety boundary) is hidden from clients unless `LOCALFLOW_MCP_ALLOW_DANGEROUS=1` is set.
- **Rollback hash guard** (Phase 7.1) — before restoring a file, the kernel hashes its current state and refuses if the user has modified it since execute. `--force` overrides.
- **Independent verifier** — 6+ deterministic checks per run. NEVER asks the LLM "did it work?".

Full threat model + per-mitigation tests: [**docs/SECURITY.md**](docs/SECURITY.md) · [**docs/security_test_matrix.md**](docs/security_test_matrix.md)

---

## What's shipped

```
Core harness:    full lifecycle (plan / dry-run / approval / execute / verify / rollback)
                 + plan refinement loop (v0.12.0) — `localflow revise`
                 keeps the task_id, generates plan_v(N+1) under plans/,
                 no execute / no rollback, capped at 5 iterations
                 + semantic verifier + auto-repair (v0.13.0) — LLM-as-judge
                 graders run after structural verify; on rejection the
                 harness automatically rolls back, revises, re-executes,
                 up to max_auto_repairs cycles (opt-in via memory pref)
Trace + Eval:    structured trace.jsonl stream emitted by every CLI + MCP
                 + eval run (v0.10.1) · eval suite with 7 starter tasks +
                 `localflow eval run evals/workspace_pack/` → markdown report
                 with per-task grader verdicts + failure-type histogram
TaskGraph:       multi-stage execution (v0.11.0) — YAML graph of skill
                 invocations, per-stage failure policy, aggregated rollback.
                 `localflow taskgraph run my_graph.yaml --yes`
Skills:          agent (v0.9.0 default — LLM-driven one-shot compound execution)
                 + folder_organizer · pdf_indexer · data_reporter
                 · data_analyzer · workspace_visualizer (specialists, CLI/MCP)
                 + filesystem plug-in loader (Phase 4.1)
Routing:         auto-detect (v0.12.0) routes data-analysis goals
                 (Chinese / English verbs) to data_analyzer when the
                 workspace contains .xlsx / .csv; everything else still
                 flows to the agent meta-skill
Data preview:    file_scan (v0.12.0) extracts the first ~10 rows of every
                 .xlsx / .csv into FileMeta.text_preview as a markdown
                 table, so the LLM reads cell content instead of guessing
                 from the filename
Chart kinds:     bar · histogram · line · pie (v0.12.0) — covers the
                 common AnalysisSpec output shapes; pie auto-picked for
                 ≤6-category groupby, line auto-picked for datetime+numeric
Tool Registry:   15 declarable callable helpers, manifest-validated at register time
Memory:          forbidden_paths (kernel-side) · naming_style · prefer_llm_planner
MCP server:      stdio JSON-RPC, 18 tools, approval-token gated execute
UI (v0.9.0):     Streamlit browser UI · EN/中文 toggle · goal-only Plan page
                 routing every compound goal through the agent meta-skill;
                 specialist skills remain CLI/MCP-only. Radio-driven workspace
                 picker with sticky ?unsafe=1 · soft-sandboxed to ./sandbox/
                 + refine expander (v0.12.0): one-click re-plan with a
                 clarifying hint before the user approves anything
Pack demo:       Workspace Pack Builder (v0.14.0) — 5-stage TaskGraph
                 at `examples/research_pack/workspace_pack.yaml` turning
                 a messy research workspace (PDFs + CSV + images + notes)
                 into a deliverable knowledge pack via one
                 `localflow taskgraph run --yes`. Composes
                 folder_organizer + pdf_indexer + data_analyzer +
                 workspace_visualizer + agent; stage 5 (LLM) uses
                 failure_policy: skip so CI without an API key still
                 produces stages 1-4 outputs.
Tests:           518 passing across 5 OS × Python matrix in CI
```

v0.14.1 polish adds: typed `SourceLedger` schema + `localflow ledger`
CLI; folder_organizer's `route_low_confidence_to_review` task pref
(opt-in routing of `other`-typed files to `review/` with an
unresolved_files.md triage list); new `topic_clusterer` skill
(LLM-driven semantic topic grouping under `topics/<topic>/`, distinct
from folder_organizer's extension-based categories).

Three equivalent driver layers, same kernel:

```powershell
localflow plan ... && localflow execute --task-id ...  # 1. CLI
localflow mcp-serve                                    # 2. MCP (Claude Code etc.)
localflow ui-serve                                     # 3. Streamlit UI — http://127.0.0.1:8501
```

UI walkthrough: [**docs/UI.md**](docs/UI.md) (EN) · [**docs/UI_zh.md**](docs/UI_zh.md) (中文用户指南). Plan refinement loop walkthrough: [**docs/REFINE.md**](docs/REFINE.md). Semantic verifier + auto-repair walkthrough: [**docs/SEMANTIC_VERIFIER.md**](docs/SEMANTIC_VERIFIER.md). Workspace Pack Builder demo: [**docs/PACK_BUILDER.md**](docs/PACK_BUILDER.md). Eval suite + grader API + trace schema: [**docs/EVAL.md**](docs/EVAL.md). TaskGraph schema + multi-stage CLI: [**docs/TASKGRAPH.md**](docs/TASKGRAPH.md). Full per-phase changelog and `§10.7` kernel-touch ledger: [**docs/PHASES.md**](docs/PHASES.md)

---

## Design principles (the 8 iron rules)

1. **The model does not execute side effects** — it only emits `TaskSpec` / `ActionPlan` / `Action`.
2. **Every action is structured** (Pydantic), never free-form natural language.
3. **Every write action goes through dry-run** before approval.
4. **`delete` is disabled by default** — duplicates are reported, not removed.
5. **Every path must resolve inside the workspace root** + must not intersect `forbidden_paths`.
6. **Existing target files are not overwritten by default** — auto-suffix or explicit `overwrite_existing` flag + backup.
7. **Every write is fully traceable** — action_id, timestamps, hashes, rollback record.
8. **The verifier is independent of the model** — completion is determined by rules, not self-assessment.

---

## Architecture (5 layers, top-down)

```
┌────────────────────────────────────────────────────────────────────┐
│  Drivers:   CLI (Typer)         MCP Server (stdio JSON-RPC)        │
├────────────────────────────────────────────────────────────────────┤
│  Skills:    Skill ABC + Registry + filesystem loader + contract    │
│             test  (built-in: agent (v0.9.0 default meta-skill) +   │
│             folder_organizer / pdf_indexer / data_reporter /       │
│             data_analyzer / workspace_visualizer specialists ·     │
│             external: plug-ins, opt-in)                            │
├────────────────────────────────────────────────────────────────────┤
│  Tool Registry (15 declarable helpers)   |   Harness Kernel        │
│  + Memory (forbidden_paths / naming_style)│  (policy_guard / dry_run│
│                                          │   / approval / executor /│
│                                          │   verifier / rollback /  │
│                                          │   audit / control_loop)  │
└────────────────────────────────────────────────────────────────────┘
```

Detailed layer-by-layer breakdown + extension guide: [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md)

---

## Extending it

- **New skill**: subclass `Skill` ([app/skills/_base.py](app/skills/_base.py)), drop into `app/skills/<name>/` (built-in) or `~/.localflow/skills/<name>/skill.py` (external — see [docs/SECURITY.md](docs/SECURITY.md) for the trust model). Verify with `run_skill_contract()` ([app/skills/_contract.py](app/skills/_contract.py)). Worked example: [examples/external_skill_example/](examples/external_skill_example/).
- **New tool**: register a `ToolSpec` in [app/tools/_registry.py](app/tools/_registry.py); skills opt in via `required_tools`.
- **New memory preference**: add a field to [app/memory/_schema.py](app/memory/_schema.py); add one CLI command; add one consumer site.
- **Drive via MCP**: [docs/MCP.md](docs/MCP.md).

---

## Layout

```
app/
  agent/      LLM planner + repair (Phase 1)
  harness/    policy_guard / dry_run / approval / executor / verifier /
              rollback / audit / control_loop  (the kernel)
  mcp/        MCP server bootstrap + 18 tool handlers (Phase 6.1 +
              memory mutations through Phase 8.2)
              + approval-token machinery (Phase 7)
  memory/     MemoryStore + naming transforms + Pydantic schema (Phase 5)
  schemas/    Pydantic data contracts (TaskSpec, ActionPlan, Action, ...)
  skills/     Skill ABC + registry + filesystem loader + contract template
  storage/    RunStore + JsonlLogger
  tools/      Shared callable helpers + ToolRegistry (Phase 4.2)
  cli.py      Typer entry point
docs/         PHASES.md · ARCHITECTURE.md · SECURITY.md · MCP.md
              · demo_walkthrough.md · security_test_matrix.md
examples/     messy_downloads (folder_organizer demo)
              pdf_demo (pdf_indexer demo)
              external_skill_example (Phase 4.1 plug-in pattern + contract test)
app/eval/     Trace + eval harness (Phase 9): TraceEvent schema,
              TraceLogger, grader registry, runner, markdown report.
              Drives task-level success measurement.
evals/        Eval task YAMLs (workspace_pack/ holds the v0.10.0 starter set)
tests/        430 tests across all layers
```

---

## Distribution

```powershell
pip install build
python -m build
# → dist/localflow_agent-0.11.0-py3-none-any.whl  +  .tar.gz
```

| Workflow | Trigger | What it does |
|---|---|---|
| [CI](.github/workflows/ci.yml) | push / PR | matrix tests on Linux/Windows/macOS × Python 3.11/3.12/3.13 + ruff lint + ruff format check + wheel build |
| [Release](.github/workflows/release.yml) | tag `v*` push or manual dispatch | builds wheel + sdist, creates a GitHub Release with auto-generated notes and both artifacts attached |

Releases (with verified wheel artifacts) under [**GitHub Releases**](https://github.com/zhangyi-nb1/localflow/releases).

Version scheme: `0.<highest_phase>.<sub>`. Current `0.14.0` = Phase 6.1 + Phase 7 hardening + 8.0–8.3.1 UI / agent / hygiene + Phase 9 Trace + Eval Harness + Phase 9.1 trace coverage + Phase 10 TaskGraph + Phase 11 Plan Refinement + Data-Aware Routing + Phase 13 Semantic Verifier + Auto-Repair Loop + **Phase 14 Workspace Pack Builder** (canonical 5-stage demo composing every layer above: `examples/research_pack/workspace_pack.yaml` + 1 new structural grader + 1 new eval task).

---

## Roadmap

- **v0.14.x** — typed `source_ledger.json` schema; dedicated
  `review/` dir for low-confidence files; semantic topic clustering
  (skills that group by content not extension).
- **Phase 15 (v0.15.0)** — Vision-based `chart_accurate` grader
  (image → LLM judge); MCP `verify_semantic` + `repair_run` tools
  + `taskgraph_run` MCP tool; cross-stage repair (rollback to upstream
  stage + replay); per-stage rollback CLI
  (`localflow rollback --stage <id>`).
- **Future** — Skill manifest signing; per-skill capability scoping
  in the LLM tool schema; WebCollect skill; MCP client.

Recently shipped:
- **v0.14.0 — Phase 14 Workspace Pack Builder.** The canonical
  composition demo: 5-stage TaskGraph at
  `examples/research_pack/workspace_pack.yaml` turning a messy
  research workspace (3 PDFs + CSV + XLSX + images + notes + an
  unknown-type stub) into a deliverable knowledge pack via one
  command. Stages 1-4 are rule-planned (folder_organizer →
  pdf_indexer → data_analyzer → workspace_visualizer); stage 5 is
  LLM-planned (agent synthesises README + sources ledger) with
  `failure_policy: skip` so CI without an API key still produces
  stages 1-4 outputs. New `every_input_accounted_for` structural
  grader closes the Phase-14 coverage gap. New eval task
  `task_010_workspace_pack` runs in `--compare-repair` mode to
  measure v0.13's auto-repair impact on this realistic workload.
  No new harness primitive — pure composition proving v0.10-v0.13
  substrate stacks. 8 new tests (495 → 503).
- **v0.13.0 — Phase 13 Semantic Verifier + Auto-Repair Loop.**
  Closes the *automatic* counterpart to v0.12's manual refine: LLM-
  as-judge graders run after structural verify; on rejection, the
  harness automatically rolls back, calls `run_revise` with a
  grader-derived hint, re-executes, re-verifies — up to
  `max_auto_repairs` (default 2). Three starter graders
  (`output_addresses_goal`, `summary_grounded`,
  `analysis_result_nonempty`). New `localflow verify-semantic` +
  `repair` CLI; `failure_policy: repair` finally wires Phase 10's
  reserved `max_retries`; eval `--compare-repair` mode renders a
  side-by-side baseline vs. auto-repair markdown table. Opt-in via
  `enable_semantic_verifier` memory pref (default off — adds LLM
  cost per execute). 30 new tests (465 → 495).
- **v0.12.0 — Phase 11 Plan Refinement Loop + Data-Aware Routing.**
  Two-track release driven by a real-world UI bug report. (Track A)
  Excel files now get a markdown-table preview in the workspace
  snapshot so the LLM sees real cell content; auto-detect routes
  goals like "分析这个 Excel" to `data_analyzer` (which reads cells
  via pandas); chart_ops gained pie + line kinds; data_analyzer's
  rule planner picks pie for ≤6-category groupby and line for
  datetime + numeric. (Track B) `localflow revise --hint "..."`
  + UI refine expander let the user supply a clarification and get
  `plans/plan_v(N+1).json` without executing or rolling back —
  capped at 5 iterations per task. New `Skill.revise` ABC method,
  new `control_loop.run_revise`, new `TraceEventType.PLAN_REVISED`,
  new `revisions.jsonl` audit log. 35 new tests (430 → 465).
- **v0.11.0 — Phase 10 TaskGraph.** Multi-stage execution: a YAML
  graph of skill invocations driven through the standard harness
  pipeline, with per-stage failure policy and aggregated rollback.
  New `localflow taskgraph describe/run` CLI. `EvalTask.stages`
  opens the same path through the eval suite. The v0.9-original
  "整理然后画图" compound goal can now be solved deterministically
  via static composition (no LLM required).
- **v0.10.1 — Phase 9.1 trace coverage + eval suite growth.**
  CLI and MCP now construct a TraceLogger per run; every
  `localflow plan/execute/rollback` produces a `trace.jsonl` next
  to the existing artifacts. Starter eval suite grew 3 → 6 tasks
  (forbidden-action / empty workspace / duplicate files).
- **v0.10.0 — Phase 9 Trace + Eval Harness.** Structured `trace.jsonl`
  stream emitted at 7 kernel sites (LLM / policy / dry-run / token /
  action / verifier / rollback) + new `app/eval/` package with grader
  registry, runner, markdown report. 4 structural graders +
  3 starter eval tasks. `localflow eval run evals/workspace_pack/`
  is the foundation Phases 10–12 will measure against.
- v0.9.1 — External skills are now opt-in via
  `LOCALFLOW_ENABLE_EXTERNAL_SKILLS=1`; `[data]` / `[pdf]` extras;
  README split into WebUI / CLI Quickstarts; agent meta-skill
  integration tests.

Deferred since groundwork is in place: directory-structure preference, report-template preference, common-task recipes (Phase 5.x).

## License

MIT — see [pyproject.toml](pyproject.toml).
