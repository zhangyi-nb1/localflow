# LocalFlow

[![CI](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml/badge.svg)](https://github.com/zhangyi-nb1/localflow/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/zhangyi-nb1/localflow/blob/main/pyproject.toml)
[![Release](https://img.shields.io/github/v/tag/zhangyi-nb1/localflow?label=release)](https://github.com/zhangyi-nb1/localflow/releases)

> **A local-first Agent Execution Harness.** The LLM proposes typed actions;
> the kernel controls execution. Every write goes through plan → dry-run →
> approval → policy-checked dispatch → independent verifier → drift-aware
> rollback, with a structured trace.jsonl per run.

```
plan ──► dry-run ──► approval ──► execute ──► verify ──► (rollback)
                                     │
                                     └── react loop: LLM consulted between
                                         actions (drift budget bounded);
                                         per-action approval gate (4 tiers);
                                         Workspace facade (Local + Docker
                                         shipped; Remote planned)
                                         decoupled from the kernel.
```

**Branch status** — `main` is **v0.28.x-dev**. Tagged releases:
[`v0.28.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.28.0)
(`localflow_kernel` — distributable kernel package) ·
[`v0.27.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.27.0)
(DockerWorkspace — container-isolated runtime) ·
[`v0.26.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.26.0)
(Workspace abstraction) · [`v0.25.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.25.0)
(ConfirmationPolicy) · [`v0.24.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.24.0)
(React Loop) · [`v0.23.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.23.0)
(Sandboxed ComputeAction). **935 tests passing.** CI across macOS / Linux /
Windows × Python 3.11 / 3.12 / 3.13.

> **Embedding the harness in your own tool?** The kernel is now a standalone
> package (`localflow_kernel`) with its own boundary lint — see
> [`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md).

---

## Why a harness, not a naive tool-call agent?

The default "LLM with tools" pattern hands the model `shell(cmd)` or
`delete(path)` directly. One hallucination or prompt-injection later,
your files are gone with no preview, no approval, no undo.

LocalFlow inverts that. The model only emits a Pydantic `ActionPlan`;
the kernel is the only code allowed to touch disk; every safety surface
is independently testable:

| Property | Naive tool-call agent | LocalFlow |
|---|---|---|
| Dry-run before any write | ✗ | ✓ markdown preview + approval token |
| Workspace boundary enforced | weak (path prefix) | ✓ `policy_guard.resolve_inside` is sole authority |
| Per-action approval granularity | binary or none | ✓ 4-tier `ConfirmationPolicy` (`never` / `always` / `on_high_risk` / `on_write`) |
| Single-command rollback of a whole run | ✗ | ✓ `RollbackManifest`, drift-aware, sha-256 verified |
| Independent verifier (rules + LLM-as-judge) | ✗ | ✓ 6 structural + 7 deliverable + critic_result on each action |
| LLM-mediated mid-execute adaptation | tool-call free-for-all | ✓ react loop with bounded drift budget; LLM decisions still pass policy_guard |
| Sandboxed code execution | ad-hoc shell | ✓ typed `PYTHON_COMPUTE`, output-to-scratch, isolated rollback |
| Action trace, audit-ready | partial | ✓ `trace.jsonl` per run, single rich `ActionTraceEvent` shape |
| Filesystem backend swappable | hard-coded | ✓ `Workspace` Protocol (LocalWorkspace today; Docker / Remote planned) |

---

## Three lifecycle shapes

**A. Plan-once batch (default, `v0.23.x` behaviour).** Plan → dry-run →
approval → execute every action in order → verify. Reproducible; the
shape CI / regulated workflows want.

**B. React mode (`v0.24.0`, opt-in).** Same plan/dry-run/approval/verify
spine, but the executor consults the LLM between actions and may apply
five legal next-step shapes within a bounded drift budget:

```
localflow execute --task-id <id> --yes --react --react-max-drift 3
```

The LLM picks one of `CONTINUE` / `REPLACE` / `INSERT` / `SKIP` / `ABORT`
per turn. Every dispatched action — original-planned, REPLACE substitute,
INSERT addition — still passes through `policy_guard.evaluate_action`
before disk. Drift-exhausted → forced CONTINUE. LLM error → fallback
to batch. See [`docs/REACT_LOOP.md`](docs/REACT_LOOP.md).

**C. Per-action approval gate (`v0.25.0`, orthogonal to A/B).** Layer a
`ConfirmationPolicy` over either A or B to pause on each gated action
instead of approving the whole plan once:

```
localflow execute --task-id <id> --yes --confirm-policy on_high_risk
```

Four tiers (`never` / `always` / `on_high_risk` / `on_write`); auto-approve
INDEX/SUMMARIZE; "approve all remaining" shortcut. See
[`docs/CONFIRMATION_POLICY.md`](docs/CONFIRMATION_POLICY.md).

---

## Architecture

Five layers, dependencies point downward, the **kernel** is the only
piece allowed to touch the filesystem (through the Workspace facade):

```
Drivers          (CLI · MCP server · Streamlit UI)
  └─ Skills      (agent meta-skill · folder_organizer · pdf_indexer
                  data_reporter · data_analyzer · workspace_visualizer
                  topic_clusterer · webcollect · external plug-ins)
       └─ Tool Registry            │   Kernel
          (typed helpers,          │   policy_guard / dry_run / approval
           Workspace facade)       │   executor / react_loop / verifier
                                   │   rollback / trace / control_loop
            └─ Memory             ─┘   (forbidden_paths · naming_style ·
                                        prefer_llm_planner ·
                                        confirmation_policy)
```

The kernel boundary is the **8 iron rules** documented in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Every breach is recorded
as a **deliberate §10.7 exception** in [`docs/PHASES.md`](docs/PHASES.md);
the ledger currently reads **4 exceptions across 35 deliveries**:

- Phase 5 — `forbidden_paths` (memory)
- Phase 16 — `ActionType.FETCH` (webcollect)
- Phase 23 — `ActionType.PYTHON_COMPUTE` (sandboxed compute)
- Phase 26 — `Executor.react_mode` (LLM-mediated execute stage)

Phases 25 (ActionTraceEvent), 27 (ConfirmationPolicy), and 28 (Workspace
abstraction) all ship **zero-kernel-touch** — they're application-layer
plumbing built on existing kernel primitives.

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[all]"             # everything: dev + mcp + ui + data + pdf + openai
```

Or pick what you need:

```bash
pip install -e .                    # base: harness + folder_organizer + LLM clients
pip install -e ".[data]"            # + pandas / matplotlib / openpyxl
pip install -e ".[pdf]"             # + pypdf
pip install -e ".[ui]"              # + Streamlit
pip install -e ".[mcp]"             # + MCP SDK
pip install -e ".[dev]"             # + pytest / ruff + all of the above
```

**One-time hook activation** (mirrors CI locally — terminates the
"local pass, CI red" pattern):

```bash
git config core.hooksPath .githooks
```

Pre-push then runs `ruff check` + `ruff format --check` + `pytest -q`
before every `git push`. Bypass via `--no-verify` only for confirmed CI
fix PRs.

---

## Quickstart A — CLI (the canonical interface)

The 6-stage lifecycle as discrete commands. Every command corresponds
to one stage; `dry-run` + `verify` are explicit so you can wire it into
automation.

```bash
# 1. PLAN — typed ActionPlan from a workspace scan + a goal. Nothing on disk yet.
localflow plan ./examples/messy_downloads --goal "organize by file type" --planner rule
# → Task created: 2026-05-25-001  ·  Actions: 40  ·  Risk: medium

# 2. DRY-RUN — render the plan as markdown. Mints the approval token.
localflow dry-run --task-id 2026-05-25-001

# 3. EXECUTE — the ONLY stage that mutates the workspace. Three opt-in modes:
localflow execute --task-id 2026-05-25-001 --yes                          # batch
localflow execute --task-id 2026-05-25-001 --yes --confirm-policy on_high_risk
localflow execute --task-id 2026-05-25-001 --yes --react --react-max-drift 3

# 4. VERIFY — rule-based completion checks, independent of the LLM.
localflow verify --task-id 2026-05-25-001

# 5. ROLLBACK — replay the manifest in reverse. Bit-exact restoration.
localflow rollback --run-id 2026-05-25-001 --yes
```

Inspect what happened:

```bash
localflow trace summary --task-id 2026-05-25-001      # event histogram
localflow trace show --task-id 2026-05-25-001 --show-observation
localflow status                                       # all runs
```

A literal trace of every artifact a real run produces — before/after
file trees, plan JSON, dry-run markdown, verify report, rollback result —
is in [`docs/demo_walkthrough.md`](docs/demo_walkthrough.md).

| Stage | Mutates | Notes |
|---|---|---|
| `plan` | only `<task_id>/task.json` + `plan.json` | LLM or rule planner |
| `dry-run` | only `dry_run.md` + `approval_token.json` | drift-sensitive token |
| `execute` | workspace (gated) | the only stage allowed to write user files; every action → `policy_guard` → `_run_one` → `RollbackEntry` |
| `verify` | none | rule-based checks against on-disk state + manifest |
| `rollback` | workspace (reverse) | sha-256 hash-check; refuses on drift unless `--force` |

---

## Quickstart B — Browser UI (for demos)

```bash
localflow ui-serve
# → http://127.0.0.1:8501
```

The UI walks the same 6 stages with workspace picker, plan preview,
approval checkbox, execute progress, run history with verify status,
and drift-aware rollback preview.

Full walkthrough: [`docs/UI.md`](docs/UI.md) (EN) ·
[`docs/UI_zh.md`](docs/UI_zh.md) (中文).

---

## Application layer: deliverable packs

Beyond the harness primitives, three ready-made packs compose multiple
stages into a single user goal:

| Pack | Fits | Produces |
|---|---|---|
| **Research Pack** | mixed PDFs + data + notes + images | per-category indexes, per-PDF summaries, analysis report, overview chart, README, SOURCES |
| **Data Report Pack** | CSV / Excel only | per-CSV analysis with charts, executive README, SOURCES |
| **Project Handoff Pack** | mid-project code + notes + data | organized code/notes/data layout, project README, setup notes, SOURCES |

```bash
localflow pack run research_pack --workspace ./my_messy_dir/
localflow goal "整理我的研究资料" --workspace ./my_messy_dir/    # router picks the pack
```

The packs are deliberately **not the project's headline value** — they're
the demo of what the harness can compose. The harness primitives stand
on their own.

---

## Evaluation

LocalFlow's reliability is measured by an eval suite, not just unit tests.
Ten starter tasks under [`evals/workspace_pack/`](evals/workspace_pack/)
drive a real workspace end-to-end and grade the result.

| Surface | What it asserts | Quantity (v0.26.0) |
|---|---|---|
| Unit tests | Code-level correctness across 5 OS × Python matrix in CI | **900 passing** |
| Eval tasks | Task-level success on realistic seeded workspaces | **10 tasks** |
| Structural verifiers | Per-run completion checks (rule-based, kernel-side) | 6 checks |
| Recipe verifiers | Deliverable quality — 4 structural + 3 LLM-as-judge | 7 checks |
| Rollback restoration | Bit-exact undo + drift detection | covered by `task_001`–`task_007` |
| Policy enforcement | `forbidden_paths` + `forbidden_actions` + `ConfirmationPolicy` | `task_003` + `task_004` + new |

```bash
localflow eval run evals/workspace_pack/ --output report.md
# exit 0 = green; exit 1 = pipeline crash; exit 3 = ran clean, deliverable check failed
```

Per-task grader API + trace event schema + how to add a grader:
[`docs/EVAL.md`](docs/EVAL.md).

---

## Safety model

The model never executes side effects — it only emits a typed
`ActionPlan`. Every write goes through dry-run → approval → executor
(routed through `Workspace`) → `policy_guard.resolve_inside` (path
authority) → optional `ConfirmationPolicy` per-action gate → `_run_one`
dispatch → `RollbackManifest` entry → trace.jsonl event.

MCP `execute_plan` additionally requires a drift-sensitive one-shot
approval token minted by `dry_run`. Rollback hash-checks every target
before touching it and refuses to clobber drift unless `--force`.

Full threat model + per-mitigation tests:
[`docs/SECURITY.md`](docs/SECURITY.md) ·
[`docs/security_test_matrix.md`](docs/security_test_matrix.md).

---

## Documentation map

### Strategic / direction
- [`docs/PROJECT_DIRECTION.md`](docs/PROJECT_DIRECTION.md) — harness-first project direction, the locked Route B decision
- [`docs/PHASES.md`](docs/PHASES.md) — full per-phase changelog + §10.7 ledger (4 deliberate kernel exceptions / 37 deliveries / 33 zero-kernel-touch)
- [`docs/research/OPENHANDS_HARNESS_STUDY.md`](docs/research/OPENHANDS_HARNESS_STUDY.md) — the 26 KB source-evidence study that motivated v0.24+

### Per-phase design / user-facing
- [`docs/PHASE_23_PLAN.md`](docs/PHASE_23_PLAN.md) · [`docs/COMPUTE_ACTION.md`](docs/COMPUTE_ACTION.md) — Phase 23 (Sandboxed ComputeAction; isolation, not security sandbox)
- [`docs/PHASE_25_PLAN.md`](docs/PHASE_25_PLAN.md) — Phase 25 ActionTraceEvent refactor
- [`docs/PHASE_26_DESIGN.md`](docs/PHASE_26_DESIGN.md) · [`docs/REACT_LOOP.md`](docs/REACT_LOOP.md) — Phase 26 react loop
- [`docs/PHASE_27_DESIGN.md`](docs/PHASE_27_DESIGN.md) · [`docs/CONFIRMATION_POLICY.md`](docs/CONFIRMATION_POLICY.md) — Phase 27 ConfirmationPolicy
- [`docs/PHASE_28_DESIGN.md`](docs/PHASE_28_DESIGN.md) · [`docs/WORKSPACE.md`](docs/WORKSPACE.md) — Phase 28 Workspace abstraction
- [`docs/DOCKER_WORKSPACE.md`](docs/DOCKER_WORKSPACE.md) — Phase 29 DockerWorkspace user manual
- [`docs/PHASE_30_DESIGN.md`](docs/PHASE_30_DESIGN.md) · [`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md) — Phase 30 `localflow_kernel` package

### Architecture / extension
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 5-layer breakdown + 8 iron rules + extension guide
- [`docs/RECIPES.md`](docs/RECIPES.md) — author a recipe / pack
- [`docs/PACK_BUILDER.md`](docs/PACK_BUILDER.md) — pack lifecycle (5 stages end-to-end)
- [`docs/TASKGRAPH.md`](docs/TASKGRAPH.md) — drive a multi-stage graph by hand (YAML)
- [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) — goal interpreter + typed primitives
- [`docs/MCP.md`](docs/MCP.md) — drive LocalFlow as an MCP server

### Operations
- [`docs/UI.md`](docs/UI.md) · [`docs/UI_zh.md`](docs/UI_zh.md) — Streamlit UI walkthrough
- [`docs/EVAL.md`](docs/EVAL.md) — eval suite + grader API + trace schema
- [`docs/SECURITY.md`](docs/SECURITY.md) · [`docs/security_test_matrix.md`](docs/security_test_matrix.md) — threat model + per-mitigation tests
- [`docs/VERIFIERS.md`](docs/VERIFIERS.md) — 7 deliverable verifiers
- [`docs/SEMANTIC_VERIFIER.md`](docs/SEMANTIC_VERIFIER.md) — auto-repair (LLM-as-judge)
- [`docs/REFINE.md`](docs/REFINE.md) — manual `localflow revise` loop
- [`docs/demo_walkthrough.md`](docs/demo_walkthrough.md) — literal end-to-end CLI trace

Releases (with verified wheel artifacts) under
[GitHub Releases](https://github.com/zhangyi-nb1/localflow/releases).

---

## License

MIT — see [`pyproject.toml`](pyproject.toml).
