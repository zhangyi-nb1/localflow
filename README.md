# LocalFlow

**A local-first Agent Execution Harness.** Plans become explicit
artefacts before any file is touched, every action is previewable,
approvable, traceable, verifiable, and undoable, and the model never
gets a direct shell.

> 🇨🇳 [中文版说明书 → README.zh-CN.md](README.zh-CN.md)

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   goal ──→ plan ──→ dry-run ──→ approval ──→ execute ──┐        │
│                                                        ▼        │
│                                            verify ◄── trace     │
│                                              │                  │
│                                              ▼                  │
│                                       rollback (always)         │
│                                                                 │
│   ⇧ react loop: per-action LLM decisions (CONTINUE / REPLACE   │
│      / INSERT / SKIP / ABORT) bounded by a drift budget         │
│   ⇧ Workspace facade: LocalWorkspace · DockerWorkspace ·        │
│      RemoteWorkspace · AgentServerWorkspace                     │
│   ⇧ ConfirmationPolicy: 4-tier per-action approval gate         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Branch status** — `main` is **v0.34.x-dev**. Tagged releases:
[`v0.34.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.34.0)
(flagship vertical — verifiable literature review with a claim-level
grounding gate) ·
[`v0.33.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.33.0)
(direction refinement — flagship = verifiable LLM-artifact pipeline /
verify-as-gate; UI backend honest CLI bridge) ·
[`v0.32.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.32.0)
(UI parity with v0.31 CLI surface — Workspace backend selector, Plan
planner toggle, `--version`, positional `trace show`) ·
[`v0.31.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.31.0)
(DockerWorkspace + RemoteWorkspace agent-server integration) ·
[`v0.30.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.30.0)
(HTTP agent-server) · [`v0.29.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.29.0)
(RemoteWorkspace via SSH) · [`v0.28.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.28.0)
(`localflow_kernel` distributable package) · [`v0.27.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.27.0)
(DockerWorkspace) · [`v0.26.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.26.0)
(Workspace abstraction) · [`v0.25.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.25.0)
(ConfirmationPolicy) · [`v0.24.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.24.0)
(React Loop) · [`v0.23.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.23.0)
(Sandboxed ComputeAction). **1093 tests passing.** CI across macOS / Linux /
Windows × Python 3.11 / 3.12 / 3.13.

> **Embedding the harness in your own tool?** The kernel is a standalone
> package (`localflow_kernel`) with its own AST boundary lint — see
> [`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md).

---

## Table of contents

1. [TL;DR — what is LocalFlow?](#1-tldr--what-is-localflow)
2. [What LocalFlow is — and what it isn't](#2-what-localflow-is--and-what-it-isnt)
3. [Why a harness, not a naive tool-call agent?](#3-why-a-harness-not-a-naive-tool-call-agent)
4. [Five-minute tour](#4-five-minute-tour)
5. [Core concepts](#5-core-concepts)
6. [Three ways to use LocalFlow](#6-three-ways-to-use-localflow)
7. [Feature catalog](#7-feature-catalog)
8. [Workspace backends](#8-workspace-backends)
9. [Configuration & persistence](#9-configuration--persistence)
10. [Important caveats (honesty discipline)](#10-important-caveats-honesty-discipline)
11. [Troubleshooting](#11-troubleshooting)
12. [Project status](#12-project-status)
13. [Documentation map](#13-documentation-map)
14. [Development & contributing](#14-development--contributing)
15. [License](#15-license)

---

## 1. TL;DR — what is LocalFlow?

LocalFlow is a local **Agent Execution Harness** whose flagship is a
**verifiable LLM-artifact pipeline**: a harness-constrained generation
step (typed plan → dry-run → approval → rollback) produces an artifact,
and an **independent verifier acts as an execution gate** that decides
whether to ship it or roll back — not a post-hoc dashboard.

The model only emits a Pydantic `ActionPlan`. The kernel — and only
the kernel — touches disk. Every safety surface (preview, approval,
verify, rollback, trace) is independently testable.

> **Flagship demo — literature review with provenance verification.**
> Feed in a batch of paper PDFs; LocalFlow summarises each, synthesises
> a review, then a **grounding gate** splits the review into individual
> claims and checks each one traces back to a source fragment. Claims
> that don't trace are flagged and routed to human review; if too many
> are ungrounded, the artifact is gated as *not shippable* and rolled
> back. (Generation can be imperfect — the harness is what makes it
> usable, auditable, and recoverable.) This is the answer to the 2025–26
> reality that even 3–5 expert reviewers miss fabricated citations in
> accepted papers — see [`docs/PHASE_35_PLAN.md`](docs/PHASE_35_PLAN.md) §4.

```bash
# Install (editable) — recommended for development
pip install -e .

# 30-second smoke test
.venv/bin/localflow pack list           # list bundled deliverable packs
.venv/bin/localflow ui-serve            # open the Streamlit UI

# CLI happy path — start with the simplest deterministic task
.venv/bin/localflow plan ./my-folder --goal "organise by file type" --planner rule
.venv/bin/localflow dry-run  --task-id <task_id>
.venv/bin/localflow execute  --task-id <task_id> --yes
.venv/bin/localflow rollback --run-id  <task_id> --yes
```

> "Organise a messy folder by file type" is the **simplest** task to
> learn the plan → execute → rollback loop on. It is a *starter
> example*, not the point — the point is the harness that makes any
> LLM-driven generation safe, gated, and undoable.

---

## 2. What LocalFlow is — and what it isn't

### LocalFlow IS

- A **safe-by-default execution harness** for local automation tasks
  (file organisation, document indexing, data reports, project
  hand-offs).
- A **kernel + facade architecture** — the kernel is independently
  importable as `localflow_kernel` (PEP 561 typed); the facade
  (`app/*`) layers CLI, UI, skills, recipes, eval graders, MCP server
  on top.
- A **multi-backend Workspace abstraction** — the same plan runs on
  the host filesystem, inside a Docker container, on a remote Linux
  host over SSH, or against an in-container HTTP agent for ~10× speed
  on hot paths.
- **An audit trail by construction** — every run produces an
  append-only `trace.jsonl` carrying the LLM's thought / reasoning /
  raw tool_use plus the action's on-disk observation. The trace is
  what `localflow verify` + the auto-repair loop consume.

### LocalFlow is NOT

- An **arbitrary code executor**. The model has no `shell()` or
  `eval()` tool. It can author a Python script (`PYTHON_COMPUTE`)
  but the script runs in a scratch workspace under subprocess
  confinement, with a timeout cap and env scrub — see
  [`docs/COMPUTE_ACTION.md`](docs/COMPUTE_ACTION.md). This is
  **isolation, not security sandbox** (CLAUDE.md rule F).
- A **fully autonomous agent**. The point of a harness is the
  approval gate. Some workflows can run with `--yes` for unattended
  runs, but the project ships with `requires_approval` on every
  HIGH-risk action by default.
- A **cloud service**. Everything runs against your local machine
  (or a remote Linux host **you control**). No data leaves your
  network unless you wire the WebCollect skill explicitly with an
  allowlist.

---

## 3. Why a harness, not a naive tool-call agent?

The default "LLM with tools" pattern hands the model `shell(cmd)` or
`delete(path)` directly. One hallucination or prompt-injection later,
your files are gone with no preview, no approval, no undo.

LocalFlow inverts that. The model only emits a Pydantic
`ActionPlan`; the kernel is the only code allowed to touch disk;
every safety surface is independently testable:

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
| Filesystem backend swappable | hard-coded | ✓ `Workspace` Protocol — LocalWorkspace + DockerWorkspace + RemoteWorkspace shipped |

The §10.7 ledger (`docs/PHASES.md`) tracks every kernel touch:
**4 deliberate exceptions across 43 deliveries, 39 zero-kernel-touch**.
That ratio is the project's identity contract.

---

## 4. Five-minute tour

### 4.1 Install

```bash
# Clone + create a virtualenv
git clone https://github.com/zhangyi-nb1/localflow.git
cd localflow
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Activate the pre-push hook (mirrors CI: ruff + pytest)
git config core.hooksPath .githooks
```

Optional dependencies:

```bash
# To use the LLM planner (Phase 18 goal-interpreter or LLM-mode skills)
export ANTHROPIC_API_KEY=sk-ant-...

# To use the Docker workspace backend
# (any Docker Desktop / Docker Engine on Linux containers mode)
docker --version

# To use the SSH workspace backend
# (passwordless ssh to the remote — BatchMode=yes is enforced)
ssh -o BatchMode=yes user@host true
```

### 4.2 Pick a deliverable pack and run it

```bash
# Bundled flagship packs — turn a folder into a deliverable.
.venv/bin/localflow pack list
.venv/bin/localflow pack describe research_pack
.venv/bin/localflow pack run research_pack --workspace ./my-research-folder
```

Each pack is a recipe that compiles to a TaskGraph (multi-stage plan).
The CLI prints risk per stage, asks for approval, executes, verifies,
and saves everything under `.localflow/runs/<task_id>/`.

### 4.3 CLI: plan → execute → rollback

```bash
# 1. Build a plan
.venv/bin/localflow plan ./messy-folder \
    --goal "organise files by type" \
    --planner rule

# CLI prints: Task created: 2026-05-28-001 · Plan: ... · Actions: 11 · Risk: medium

# 2. Preview the dry-run (markdown table of every action)
.venv/bin/localflow dry-run --task-id 2026-05-28-001

# 3. Execute (--yes skips the interactive approval prompt)
.venv/bin/localflow execute --task-id 2026-05-28-001 --yes

# 4. Inspect what happened
.venv/bin/localflow trace summary 2026-05-28-001
.venv/bin/localflow trace show 2026-05-28-001 --show-observation

# 5. Undo the whole run (bit-for-bit)
.venv/bin/localflow rollback --run-id 2026-05-28-001 --yes
```

### 4.4 UI: open the Streamlit app

```bash
.venv/bin/localflow ui-serve --port 8501
# Opens a browser at http://127.0.0.1:8501
```

Sidebar navigation lists 8 pages: **Home / Create Pack / Workspace /
Runs / Settings / Plan / Execute / Rollback**. The Workspace backend
badge in the sidebar shows your active backend (Local / Docker /
Remote).

---

## 5. Core concepts

These are the Pydantic types you'll encounter — every CLI / UI / MCP
caller produces or consumes them.

### `TaskSpec` — what the user asked for

Captures the user's `goal`, target `workspace_root`, chosen `skill`,
`allowed_actions`, `forbidden_actions`, `forbidden_paths`, and any
runtime preferences. Persisted to `task.json`.

### `ActionPlan` — what the planner produced

A list of `Action` objects, each typed (`MKDIR` / `MOVE` / `COPY` /
`INDEX` / `FETCH` / `PYTHON_COMPUTE`), each carrying `source_path`,
`target_path`, `risk_level`, `requires_approval`, and a human-
readable `reason`. The planner is either deterministic (`rule`,
~300ms) or LLM-backed (`llm`, ~20s; uses the Anthropic API).

### `RiskAssessment` — what `policy_guard` thinks

Per-action verdicts (`allow` / `block`) + a plan-level summary
(`risk_level`, `warnings`). Built BEFORE the user is asked to
approve, so the user sees `risk=medium` next to the count of blocked
actions.

### `ConfirmationPolicy` — when to pause

4-tier enum: `NEVER` / `ALWAYS` / `ON_HIGH_RISK` / `ON_WRITE`.
Default is `ON_HIGH_RISK`. The executor consults this before each
action; user-supplied `action_approver` callback (default = CLI
prompt or UI dialog) decides per-action.

### `RollbackManifest` — how to undo

An append-only ledger of inverse operations. Every successful action
writes one (or more) entries; failed actions still get
`DELETE_SCRATCH_DIR` entries for `PYTHON_COMPUTE`. `localflow
rollback` replays the manifest in reverse with sha-256 drift detection
on every file it restores.

### `TraceEvent` / `ActionTraceEvent` — what actually happened

JSONL stream (`trace.jsonl`) with one event per kernel decision.
`ActionTraceEvent` (Phase 25.1) extends `TraceEvent` with the LLM's
`thought` / `reasoning` / raw `tool_call_raw` plus the action's
on-disk `observation` (size / hash / parent_created / error).
`localflow trace show` and `summary` consume this.

### `Workspace` (Protocol) — the filesystem facade

Phase 28 abstraction. Defines `exists / stat / sha256 / list_dir /
read_text / read_bytes / mkdir / move / copy / write_text /
write_bytes / safe_target_rel`. Four implementations ship:
`LocalWorkspace`, `DockerWorkspace`, `RemoteWorkspace`,
`AgentServerWorkspace`.

---

## 6. Three ways to use LocalFlow

### 6.1 CLI — full power, scriptable

The reference driver. Every kernel capability is reachable via
`localflow <subcommand>`:

| Subcommand | What it does |
|---|---|
| `localflow --version` | Print the kernel version |
| `localflow plan <ws> --goal "..."` | Build an ActionPlan |
| `localflow dry-run --task-id <id>` | Render the markdown preview |
| `localflow execute --task-id <id> [--yes]` | Approve + execute |
| `localflow verify --task-id <id>` | Re-run the structural verifier |
| `localflow rollback --run-id <id> [--yes]` | Undo a previous run |
| `localflow status [<task_id>]` | List runs / inspect one task |
| `localflow trace show <task_id>` | Pretty-print trace.jsonl |
| `localflow trace summary <task_id>` | Event-type histogram |
| `localflow goal "..."` | Phase 18 natural-language entry point |
| `localflow pack {list,describe,suggest,run}` | Deliverable packs |
| `localflow taskgraph run <yaml>` | Drive a multi-stage TaskGraph |
| `localflow eval run` | Run the eval suite |
| `localflow memory {list,forbid,allow-domain,...}` | Persistent prefs |
| `localflow skills-sig {sign,verify}` | HMAC skill manifest signing |
| `localflow mcp-clients {list,add,probe}` | External MCP servers |
| `localflow mcp-serve` | LocalFlow as an MCP server (stdio) |
| `localflow ui-serve [--port]` | Streamlit UI |

Run any subcommand with `--help` for full flag documentation.

### 6.2 UI — visual, beginner-friendly

```bash
.venv/bin/localflow ui-serve --port 8501
```

| Page | Purpose |
|---|---|
| 🌀 Home | Hero + 3 featured packs (Research / Data Report / Project Handoff). Click a card to jump to Create Pack pre-populated. |
| 📦 Create Pack | Browse the recipe catalog; describe / suggest / run any pack. Phase 18 goal interpreter at the top. |
| 🗂️ Workspace | Scans the active workspace; shows file counts + previous runs against it. |
| 📋 Runs | Every task LocalFlow has executed on this machine. Open one to see verify status, trace event count, rollback availability. |
| ⚙️ Settings | 6 tabs — Forbidden paths (kernel-enforced), Naming style, Planner preference, Semantic + Repair, **🛰 Workspace backend** (Phase 34), Audit log. |
| 🧭 Plan | Type a goal, pick a planner (rule / llm). When `ANTHROPIC_API_KEY` is unset, defaults to rule + shows a friendly fallback hint. |
| ⚡ Execute | Preview → review → approve → execute → check. |
| ↩️ Rollback | Replay the rollback manifest with hash-drift detection. |

The sidebar shows the active workspace, the active Workspace backend
(local / docker / ssh), and a Memory summary.

### 6.3 Embedded — `localflow_kernel` as a library

For downstream tools that want the harness without LocalFlow's CLI
/ UI / skills / recipes:

```python
from pathlib import Path
from localflow_kernel import (
    Action, ActionPlan, ActionType, RiskLevel,
    Executor, RunStore,
    LocalWorkspace,
)

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

The kernel package's AST boundary lint
(`tests/test_kernel_boundary.py`) guarantees `localflow_kernel.*`
never imports application-layer modules. Read
[`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md) for the embed
recipe.

---

## 7. Feature catalog

### A. Planning — deterministic OR LLM-backed

Two planners ship:

- **`rule`** (default for most skills, ~300 ms): pure-Python
  heuristics. Deterministic, no API key needed.
- **`llm`** (`ANTHROPIC_API_KEY` required, ~20 s): the goal +
  workspace snapshot + skill manifest go to Claude; the model emits
  a tool-use payload that gets Pydantic-validated. Cached, immutable
  system prompt; adaptive thinking.

Both produce the same `ActionPlan` shape. The CLI defaults to `rule`;
the UI Plan page lets you pick per goal (and falls back to `rule`
when no key is set — Phase 34).

### B. Safety — three layered gates

1. **`policy_guard.resolve_inside`** — the only authority on path
   traversal. Every Workspace write calls it before touching disk.
   `..` / absolute paths / drive letters / `~` are all rejected.
2. **`policy_guard.evaluate_action`** — per-action verdict using
   the task's `forbidden_actions`, `forbidden_paths`, and (for
   `FETCH`) `fetch_allowed_domains` allowlist.
3. **`ConfirmationPolicy`** — runtime gate (NEVER / ALWAYS /
   ON_HIGH_RISK / ON_WRITE). Default `ON_HIGH_RISK`: the user is
   prompted only for HIGH-risk actions.

Combined, an action that wants to mutate disk must satisfy: policy
allows it → confirmation passes → and the executor verifies parent
directories / source files exist before the write hits storage.

### C. Execution — the Workspace facade

All kernel writes go through a `Workspace` Protocol implementation.
Switch backends without changing the kernel:

```bash
localflow execute --task-id T --workspace local          # default
localflow execute --task-id T --workspace docker:python:3.12-slim
localflow execute --task-id T --workspace ssh:user@host
localflow execute --task-id T --workspace ssh:user@host:22:/srv/wkspc
```

See §8 for the backend comparison table.

### D. Verification — structural + semantic

After execute, the independent verifier (`app/harness/verifier.py`)
runs 6 structural checks: every planned MKDIR target exists, every
MOVE source is gone, every COPY source is preserved, every INDEX
target is non-empty, no file outside the workspace was touched, the
rollback manifest hashes match.

Optional **Phase 13 semantic verifier** (`enable_semantic_verifier`
memory pref) runs 7 LLM-as-judge graders: did the output address the
goal, is the summary grounded in the source, does the chart match the
data, etc. Failure can trigger Phase 13's auto-repair loop.

### E. Rollback — manifest-replay with drift detection

Every successful action writes one or more `RollbackEntry`s in
order. `localflow rollback`:

1. Reads the manifest in reverse.
2. For each entry, recomputes the current sha-256 of the file/dir
   it's about to restore.
3. If it matches the manifest's recorded post-action hash, the
   inverse operation runs.
4. If it doesn't (someone edited the file outside LocalFlow), the
   entry is flagged as **drift** and skipped with a clear message
   (the user can re-run with `--force` if they accept the drift).

`PYTHON_COMPUTE` scratch dirs are also rolled back via
`DELETE_SCRATCH_DIR` entries — always appended, even on action
failure.

### F. Trace — append-only `trace.jsonl`

Every kernel decision emits a `TraceEvent`. Phase 25's
`ActionTraceEvent` extends action-level rows with:

- `thought` — the LLM's chain-of-thought (when planner=llm)
- `reasoning` — the LLM's natural-language justification
- `tool_call_raw` — the model's raw tool-use input
- `observation` — what actually happened: size_bytes,
  sha256_after, parent_created, error
- `critic_result` — the semantic verifier's verdict (when enabled)

Inspect via `localflow trace show <task_id> --show-thought
--show-observation` or in the UI Runs page.

### G. React loop — mid-execute LLM decisions (Phase 26 / v0.24.0)

Opt-in via `--react` or recipe `enable_react_mode: true`. After each
action's observation, the LLM is consulted and may decide:

- **CONTINUE** — run the next planned action as-is
- **REPLACE** — substitute a different Action (e.g. saw output is
  garbage, propose a corrected script)
- **INSERT** — run an extra Action first, then the original
- **SKIP** — drop the planned action
- **ABORT** — end the run, hand back to verify

Three fail-safes: drift budget (default 3 deviations from the
approved plan), LLM timeout / parse error → fall back to batch,
policy_guard rejection of the LLM-proposed action → FAILED record
but loop continues.

### H. Sandboxed PYTHON_COMPUTE (Phase 23 / v0.23.0)

The LLM can author a Python script. Restrictions:

- Runs in a fresh scratch workspace (`<home>/scratch/<task>/<action>/`)
- Subprocess confinement (separate Python process, no parent env
  inheritance)
- 300-second wall-clock timeout (configurable down, not up)
- Env scrub: proxy + AI provider keys are stripped
- Unix-only `RLIMIT_AS` memory cap (best-effort on macOS, no-op on
  Windows — explicitly documented)
- Outputs declared as `ArtifactSpec` get moved into the workspace
  on success; **anything** else stays in scratch and is rolled back

Recipe authors opt in with `RecipeSpec.allow_compute_action: true`.
The default rejects `python_compute` in any allowed_actions list.

This is **isolation, not security sandbox** — a determined attacker
who controls the script can read host files via `/etc/passwd` etc.
LocalFlow's compute action defends against accidental workspace
mutation + casual leakage, not against adversarial code.

### I. Memory preferences — persistent UX state

`~/.localflow/memory/prefs.json` (schema v5 as of Phase 34):

| Field | Default | Meaning |
|---|---|---|
| `forbidden_paths` | `[]` | Workspace-relative paths the kernel refuses to touch |
| `naming_style` | `original` | folder_organizer's filename transform |
| `prefer_llm_planner` | `false` | UI autodetect routes to LLM planner |
| `enable_semantic_verifier` | `false` | Run Phase 13 graders + auto-repair |
| `max_auto_repairs` | `2` | Cap on repair cycles |
| `fetch_allowed_domains` | `[]` | Hostname allowlist for FETCH actions |
| `workspace_backend_spec` | `"local"` | Default Workspace backend for UI |

Every mutation is audited to `audit.jsonl`. Tail with `localflow
memory audit` or browse the Settings → Audit log tab.

### J. Recipes & Packs — composition over new primitives

A **Recipe** is a YAML / Pydantic spec describing a multi-stage
workflow that compiles to a `TaskGraph`. A **Pack** is a recipe
shipped with example data, an eval task, and a README.

Shipped packs:

- `research_pack` — **the flagship's foundation**: turn a folder of
  research material (PDFs, notes) into a knowledge pack with per-PDF
  summaries, a synthesised review, and a **sources ledger** that
  tracks every claim's provenance. Phase 36 extends this with the
  claim-level **grounding gate** (verify-as-gate): each claim in the
  review must trace to a source fragment, or it's flagged for human
  review and the artifact is gated. See
  [`docs/PHASE_35_PLAN.md`](docs/PHASE_35_PLAN.md) §5.
- `data_report_pack` — turn CSV / Excel data into a deliverable
  report.
- `project_handoff_pack` — turn a mid-project workspace (code, notes,
  data, images, logs) into a deliverable hand-off doc.

Each pack ships with `examples/<pack>/seed.py` that plants the seed
for a 1-command demo. See [`docs/PACK_BUILDER.md`](docs/PACK_BUILDER.md).

### K. MCP — LocalFlow as both client & server

- **Server**: `localflow mcp-serve` exposes `plan`, `execute`,
  `verify`, `rollback`, `taskgraph_run`, `verify_semantic`,
  `repair_run` over stdio.
- **Client**: `localflow mcp-clients add fs 'mcp-filesystem ...'`
  registers external MCP servers; their tools join the Phase 4.2
  Tool Registry.

See [`docs/MCP.md`](docs/MCP.md).

---

## 8. Workspace backends

| Backend | Per-op latency | Isolation | Persistence | Best for |
|---|---|---|---|---|
| **`local`** (default) | ~10 μs | none | persistent | dev loops, single-machine workflows |
| **`docker:<image>`** | ~100-300 ms (or ~5-20 ms with `use_agent_server`) | container (full) | wiped on close | risky / experimental plans, reproducible image |
| **`ssh:<host>[:<port>][:<root>]`** | ~100-300 ms + network RTT (or ~10-50 ms with agent-server) | network | persistent (user-managed) | dedicated remote workers, lab VMs |
| **`AgentServerWorkspace`** (programmatic) | ~1-5 ms localhost / ~10-50 ms LAN | depends on transport | depends on transport | embedded use; the perf upgrade for Docker / Remote |

Use the CLI flag or the UI Settings → 🛰 Workspace backend tab to
switch. Each backend ships with its own user manual:

- [`docs/WORKSPACE.md`](docs/WORKSPACE.md) — LocalWorkspace + Protocol contract
- [`docs/DOCKER_WORKSPACE.md`](docs/DOCKER_WORKSPACE.md) — Docker backend + agent-server mode
- [`docs/REMOTE_WORKSPACE.md`](docs/REMOTE_WORKSPACE.md) — SSH backend + tunnel mode
- [`docs/AGENT_SERVER.md`](docs/AGENT_SERVER.md) — the HTTP daemon's protocol

---

## 9. Configuration & persistence

### 9.1 The `~/.localflow/` tree

```
~/.localflow/
├── memory/
│   ├── prefs.json        # MemoryPreferences (schema v5)
│   └── audit.jsonl       # Every mutation, append-only
├── runs/
│   └── <task_id>/
│       ├── task.json
│       ├── workspace_snapshot.json
│       ├── plan.json
│       ├── dry_run.md
│       ├── execution_log.jsonl
│       ├── trace.jsonl       # ActionTraceEvent stream
│       ├── rollback_manifest.json
│       ├── verify_report.json
│       ├── final_report.md
│       └── ...
└── scratch/
    └── <task_id>/<action_id>/    # PYTHON_COMPUTE workspaces
        ├── inputs/
        ├── outputs/
        ├── script.py
        ├── stdout.log
        └── stderr.log
```

### 9.2 Environment variables

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enable LLM planner / semantic verifier |
| `LOCALFLOW_LLM_MODEL` | Override default `claude-opus-4-7` |
| `LOCALFLOW_ANTHROPIC_TIMEOUT` | Per-call timeout (seconds, default 180) |
| `LOCALFLOW_HOME` | Override `~/.localflow/` location |
| `LOCALFLOW_REQUIRE_SIGNED_SKILLS` | Phase 16 — refuse unsigned external skills |
| `AGENT_SERVER_PORT/TOKEN/WORKSPACE/HOST` | Override agent-server defaults |

### 9.3 Choosing a Workspace backend

CLI: `--workspace local | docker:<image> | ssh:<host>[:<port>][:<root>]`

UI: Settings → 🛰 Workspace backend tab. The choice persists into
`memory.workspace_backend_spec` and shows in the sidebar badge.

> **Phase 34.5 note**: as of v0.32.0, the UI persists the chosen
> backend and shows it in the sidebar, but the Plan / Execute pages
> still wire `LocalWorkspace` at runtime. Pipe-through is the next
> deferred slice (per `docs/PHASE_34_DESIGN.md` §6).

### 9.4 Forbidden paths & domain allowlists

```bash
# Refuse to touch a path
localflow memory forbid private/secrets

# Allow a domain for FETCH actions (default = empty list = no fetches allowed)
localflow memory allow-domain raw.githubusercontent.com
```

Both are read by the kernel at policy-check time. The kernel can
**only** read these — never write — making memory the user's
"contract with LocalFlow" that survives across runs.

---

## 10. Important caveats (honesty discipline)

LocalFlow ships with strong defaults but doesn't pretend to be
something it isn't. Read this section before deploying.

### 10.1 Isolation ≠ security sandbox

`PYTHON_COMPUTE` runs in a subprocess with cwd confinement, env scrub,
and `RLIMIT_AS` (on Unix). A **determined attacker who controls the
script** can still read host files, hit `/etc/passwd`, etc. The
guarantee is "your workspace stays clean if the LLM hallucinates a
bad script"; the guarantee is **not** "you can run code from
strangers safely".

### 10.2 DockerWorkspace = container isolation, not network isolation

By default the container has network access. A `python:3.12-slim`
image has `pip` + libc loaders. If you need network isolation, run
the container with `--network=none` (not exposed via CLI yet — drop
into the Python API).

### 10.3 SSH RemoteWorkspace requires passwordless auth

`BatchMode=yes` is enforced. If your remote needs a password, the
ssh process hangs silently. Set up key-based auth + accept the host
key into `~/.ssh/known_hosts` BEFORE pointing LocalFlow at the
remote. The remote workspace directory is **not** removed on
`close()` — it's a user-managed directory.

### 10.4 FETCH actions never autoplay

`ActionType.FETCH` exists (Phase 16) but is gated by:

1. `fetch_allowed_domains` memory pref (empty by default = no
   fetches allowed at all)
2. `requires_approval=true` on the action (always)
3. HTTPS-only (HTTP scheme is rejected by policy_guard)

You explicitly opt in per-host via `localflow memory allow-domain
<host>`.

### 10.5 LLM costs

When you use `--planner llm` or `enable_semantic_verifier`, every
plan / verify call goes to Anthropic. Plan ~$0.01-0.05 depending
on workspace size + model. The semantic verifier hits the API per
grader (7 graders per stage by default). Set
`LOCALFLOW_ANTHROPIC_TIMEOUT` if you want a hard cap.

### 10.6 What cannot be undone

The rollback manifest covers:

- ✓ MKDIR / MOVE / COPY / INDEX / FETCH / PYTHON_COMPUTE
- ✓ OVERWRITE (via pre-action backup)
- ✗ **Deletions** — but the kernel rejects DELETE actions by default
  via `forbidden_actions=["delete", "overwrite", "shell"]`. The
  default safe path is "rename to a quarantine folder, never delete";
  see the agent meta-skill for the pattern.

### 10.7 §10.7 kernel-touch ledger

The project tracks every kernel edit. As of v0.34.0: **4 deliberate
exceptions across 43 deliveries, 39 zero-kernel-touch (90.7%)**. If
you submit a PR that touches `app/harness/*` or
`localflow_kernel/*`, expect to defend it against the same bar —
see `docs/PHASES.md` for the precedent.

---

## 11. Troubleshooting

### "ANTHROPIC_API_KEY not set"

You're trying to use `--planner llm` or the semantic verifier
without a key. Fix: export the key in your shell, OR switch to
`--planner rule` (works without LLM). The UI Plan page now defaults
to rule when no key is detected.

### "ssh probe to '<host>' failed: Permission denied (publickey)"

The remote isn't configured for passwordless auth from your user.
Run `ssh-copy-id user@host`, then verify with
`ssh -o BatchMode=yes user@host true` (must exit 0).

### "Docker CLI / daemon not reachable"

Either Docker isn't installed (install Docker Desktop or Docker
Engine), or the daemon is in Windows containers mode (LocalFlow
ships Linux images; switch the daemon back via the Docker tray
icon → Switch to Linux containers).

### Trace shows `policy.check` rows but no `action.start`

Policy_guard rejected the plan. Inspect the trace:
`localflow trace show <task_id> --event-type policy.check`. The
`payload.detail` field carries the reason (`path_forbidden`,
`fetch_domain_not_allowed`, etc.).

### Rollback says "drift detected on <file>"

Someone (or another process) edited the file after LocalFlow
recorded its post-action hash. Either:

1. Accept the drift: re-run with `--force` (manifest entry is
   skipped, but rollback continues).
2. Restore manually: the original content is in
   `.localflow/runs/<task_id>/backups/`.

### UI Plan page button spins forever

You probably have an old `prefs.json` and a stale LLM planner pref.
Try `localflow memory set prefer_llm_planner false`, then refresh.
The Phase 34 fallback should now show a blue info block if there's
no key.

### "DockerWorkspace agent-server start failed: ..."

The bundle handshake didn't complete (image missing `python3`, port
conflict, or `pydantic` not installed in the image). LocalFlow logs
the warning + falls back to `docker exec` per op automatically. Use
a `python:3.12-slim` (or any `pip install pydantic`'d image) for
the agent-server speedup.

---

## 12. Project status

### Phase ledger (current)

| Wave | Phases | Highlights |
|---|---|---|
| **Foundation** | 1–8 | core schemas, harness kernel, CLI, UI, skills |
| **Trace + Eval** | 9–13 | TraceEvent, TaskGraph, plan refinement, semantic verifier |
| **Composition** | 14–22 | Workspace Pack Builder, MCP, Recipes / Packs, goal interpreter |
| **Sandbox + Trace v2** | 23–25 | PYTHON_COMPUTE, ActionTraceEvent + repair feedback |
| **Loop + Approval** | 26–27 | React loop, ConfirmationPolicy 4-tier |
| **Backends** | 28–33 | Workspace abstraction → Local, Docker, Remote, AgentServer |
| **Distribution + UI parity** | 30, 34 | `localflow_kernel` package + boundary lint, UI catches up to CLI |

Full per-phase changelog in [`docs/PHASES.md`](docs/PHASES.md).

### Testing & quality gates

- **1093 tests passing** (CI on macOS / Linux / Windows × Python
  3.11 / 3.12 / 3.13).
- Pre-push hook mirrors CI: `ruff check` + `ruff format --check` +
  `pytest --tb=no`. Activate via `git config core.hooksPath
  .githooks`.
- Kernel boundary lint (`tests/test_kernel_boundary.py`): walks
  every module reachable from `localflow_kernel.*` + every
  underlying `app.*` implementation, asserts none import from
  `app.{skills,recipes,cli,ui,eval,memory,primitives,templates,mcp}`
  or from any of the 5 forbidden harness orchestrators.

---

## 13. Documentation map

### Strategic / direction

- [`docs/PROJECT_DIRECTION.md`](docs/PROJECT_DIRECTION.md) — harness-first project direction, the locked Route B decision
- [`docs/PHASES.md`](docs/PHASES.md) — full per-phase changelog + §10.7 ledger (4 deliberate kernel exceptions / 43 deliveries / 39 zero-kernel-touch)
- [`docs/research/OPENHANDS_HARNESS_STUDY.md`](docs/research/OPENHANDS_HARNESS_STUDY.md) — the 26 KB source-evidence study that motivated v0.24+

### Per-phase design / user-facing

- [`docs/PHASE_23_PLAN.md`](docs/PHASE_23_PLAN.md) · [`docs/COMPUTE_ACTION.md`](docs/COMPUTE_ACTION.md) — Sandboxed ComputeAction (isolation, not security sandbox)
- [`docs/PHASE_25_PLAN.md`](docs/PHASE_25_PLAN.md) — ActionTraceEvent refactor
- [`docs/PHASE_26_DESIGN.md`](docs/PHASE_26_DESIGN.md) · [`docs/REACT_LOOP.md`](docs/REACT_LOOP.md) — react loop
- [`docs/PHASE_27_DESIGN.md`](docs/PHASE_27_DESIGN.md) · [`docs/CONFIRMATION_POLICY.md`](docs/CONFIRMATION_POLICY.md) — ConfirmationPolicy
- [`docs/PHASE_28_DESIGN.md`](docs/PHASE_28_DESIGN.md) · [`docs/WORKSPACE.md`](docs/WORKSPACE.md) — Workspace abstraction
- [`docs/DOCKER_WORKSPACE.md`](docs/DOCKER_WORKSPACE.md) — Phase 29 + Phase 33 agent-server mode
- [`docs/PHASE_30_DESIGN.md`](docs/PHASE_30_DESIGN.md) · [`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md) — `localflow_kernel` package
- [`docs/PHASE_31_DESIGN.md`](docs/PHASE_31_DESIGN.md) · [`docs/REMOTE_WORKSPACE.md`](docs/REMOTE_WORKSPACE.md) — RemoteWorkspace (SSH) + agent-server mode
- [`docs/PHASE_32_DESIGN.md`](docs/PHASE_32_DESIGN.md) · [`docs/AGENT_SERVER.md`](docs/AGENT_SERVER.md) — HTTP agent-server
- [`docs/PHASE_33_DESIGN.md`](docs/PHASE_33_DESIGN.md) — Docker/Remote agent-server integration
- [`docs/PHASE_34_DESIGN.md`](docs/PHASE_34_DESIGN.md) · [`docs/E2E_TEST_PLAN.md`](docs/E2E_TEST_PLAN.md) — UI parity + E2E test report

### Architecture / extension

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 5-layer breakdown + 8 iron rules + extension guide
- [`docs/RECIPES.md`](docs/RECIPES.md) — author a recipe / pack
- [`docs/PACK_BUILDER.md`](docs/PACK_BUILDER.md) — pack lifecycle (5 stages end-to-end)
- [`docs/TASKGRAPH.md`](docs/TASKGRAPH.md) — drive a multi-stage graph by hand (YAML)
- [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) — goal interpreter + typed primitives
- [`docs/MCP.md`](docs/MCP.md) — drive LocalFlow as an MCP server

### Operations

- [`docs/UI.md`](docs/UI.md) · [`docs/UI_zh.md`](docs/UI_zh.md) — Streamlit UI walkthrough
- [`docs/SECURITY.md`](docs/SECURITY.md) — security model (isolation, not security sandbox)
- [`docs/EVAL.md`](docs/EVAL.md) — eval task authoring
- [`docs/SEMANTIC_VERIFIER.md`](docs/SEMANTIC_VERIFIER.md) — Phase 13 LLM-as-judge graders
- [`docs/REFINE.md`](docs/REFINE.md) — plan refinement loop
- [`docs/demo_walkthrough.md`](docs/demo_walkthrough.md) — end-to-end demo script

---

## 14. Development & contributing

### 14.1 Local setup

```bash
git clone https://github.com/zhangyi-nb1/localflow.git
cd localflow
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
git config core.hooksPath .githooks   # activate pre-push hook
```

### 14.2 Test conventions

- Unit tests in `tests/test_*.py`, integration tests share the
  `tests/` namespace
- `pytest --tb=no` runs the full suite in ~70 s
- Backend-dependent tests use `_skip_no_docker` / `_skip_no_ssh`
  markers; CI handles the matrix
- New tests live next to the module they cover (e.g.
  `tests/test_workspace_remote.py` next to
  `app/tools/remote_workspace.py`)

### 14.3 Code style

- `ruff check app/ tests/ localflow_kernel/`
- `ruff format --check app/ tests/ localflow_kernel/ examples/`
- Both checks live in the pre-push hook + CI step 5/6/7

### 14.4 Kernel boundary discipline

The kernel package is fenced off — `tests/test_kernel_boundary.py`
fails CI if you import application-layer code from
`localflow_kernel.*` or from the underlying `app.harness.*` pure
modules. If you genuinely need a kernel touch (a new ActionType, a
new policy field), the §10.7 ledger expects you to:

1. Open an issue stating the case
2. Write a design doc under `docs/PHASE_*_DESIGN.md`
3. Wire the change as a deliberate exception with the ledger row
   in `docs/PHASES.md`

To date, four deliberate exceptions have been admitted:

| Phase | Exception | Justification |
|---|---|---|
| 5 | `forbidden_paths` | universal safety primitive — kernel must enforce |
| 16 | `ActionType.FETCH` | WebCollect needs HTTPS GET as a typed primitive |
| 23 | `ActionType.PYTHON_COMPUTE` | LLM-authored code needs a sandboxed exec primitive |
| 26 | react loop kwarg threading | mid-execute LLM decisions need executor hooks |

The ratio (4/41) is the project's identity contract.

### 14.5 Pull requests

- Branch off `main`, push, open a PR.
- Pre-push hook must pass locally (mirror of CI).
- For kernel changes, ledger row + design doc are required.
- Tag the maintainers in the PR description for the §10.7 review.

---

## 15. License

MIT. See [`LICENSE`](LICENSE).

---

> Made with care under the constraint that the user — not the model —
> is always the source of intent. If you find a place where the
> harness over-trusts the model, please file an issue. Honesty
> discipline (CLAUDE.md rule F) is what makes this project worth
> shipping.
