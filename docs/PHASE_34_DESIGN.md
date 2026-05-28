# Phase 34 — UI parity with v0.31.0 CLI surface + CLI papercut fixes

**Status**: shipped 2026-05-28
**Predecessor**: v0.31.0 (Phases 23 → 33) + the E2E test report
[`docs/E2E_TEST_PLAN.md`](E2E_TEST_PLAN.md)
**Audience**: anyone reviewing why v0.32.0 exists and what changed.

---

## 1. Why this phase

The Phase 33 E2E test report identified four user-visible gaps in
v0.31.0:

| ID | Severity | Gap |
|---|---|---|
| F-1 | low | `localflow --version` not recognised |
| F-2 | low | `localflow trace show <task_id>` rejected positional task_id |
| F-3 | **high** | UI had no Workspace backend selector — Phases 28-33 built four backends (Local / Docker / Remote / AgentServer) but UI users could only reach LocalWorkspace |
| F-4 | medium | Plan page silently called LLM planner with no `ANTHROPIC_API_KEY`, spinner stalled forever |

CLAUDE.md rule F (honesty discipline) says "ship 了但 UI 没暴露" is
oversell territory. Phase 34's job is to close all four gaps before
declaring v0.31 demo-ready.

---

## 2. Slice plan

| Slice | Scope | Files touched |
|---|---|---|
| 34.0 | F-1 + F-2 — root `--version` callback + dual-shape (positional + flag) `trace show` / `trace summary` | `app/cli.py` |
| 34.1 | F-4 — Plan page planner radio + no-key fallback | `app/ui/pages/5_Plan.py` |
| 34.2 | F-3 — Settings new "🛰 Workspace backend" tab + memory schema v5 + sidebar badge | `app/ui/pages/4_Settings.py`, `app/ui/_layout.py`, `app/memory/_schema.py`, `app/memory/_store.py`, 6 new unit tests |
| 34.3 | Local-observable headed-browser verification | `docs/test_artifacts/v0.32.0-phase34/` (13 screenshots) |
| 34.4 | Design doc + ledger + CHANGELOG + git tag (this slice) | `docs/PHASE_34_DESIGN.md`, `docs/PHASES.md`, `CHANGELOG.md`, `README.md`, `CLAUDE.md` |

---

## 3. Design notes

### 3.1 F-1 — `--version`

Stock Typer doesn't ship a `--version` flag. Added a root callback
that prints `localflow {localflow_kernel.__version__}` and exits. The
kernel package is the source of truth so the CLI mirrors whatever the
kernel says.

### 3.2 F-2 — positional `task_id`

Typer can't natively map a single parameter to BOTH a positional
argument AND a named option. Workaround: declare two parameters
(`task_id_pos` as `Argument`, `task_id_opt` as `Option`), then
resolve them via a tiny helper `_resolve_task_id`. Conflict (both
provided with different values) → exit 2 with a clear message.
Backwards-compatible: existing `--task-id` users see no change.

### 3.3 F-3 — UI Workspace backend selector (the big one)

#### 3.3.1 Persistence: memory pref + schema v4 → v5 migration

The CLI's `--workspace` flag is per-invocation; the UI needs a sticky
setting so users don't have to repick on every page load. Added a new
field to `MemoryPreferences`:

```python
workspace_backend_spec: str = "local"
```

with a v4 → v5 migration that backfills `"local"` for users whose
prefs.json was written by an older release. Validated via the
existing `parse_workspace_spec` before persistence, so malformed
specs never hit disk.

#### 3.3.2 UI: new tab in Settings

`app/ui/pages/4_Settings.py` learned a 6th tab "🛰 Workspace backend".
The tab parses the current spec into three radios — `local` /
`docker` / `ssh` — and shows the right conditional fields:

- **local**: just a code block showing `local`
- **docker**: text input for image (default `python:3.12-slim`)
- **ssh**: text input for host, number input for port (default 22),
  text input for remote root path (default `/tmp/localflow-ws`)

The composed spec is rendered live (so the user sees the wire format
that will be persisted), and Save is only enabled when there's a
pending change. Save calls `MemoryStore.set_workspace_backend_spec`,
which re-validates through `parse_workspace_spec` and surfaces
ValueError as a red banner.

#### 3.3.3 Sidebar: backend badge

Every page's sidebar (via `_layout.render_sandbox_sidebar`) now ends
with a small badge reading e.g. `🖥 local` / `🐳 docker:python:3.12-slim`
/ `🛰 ssh:bob@example.com:22:/srv/wkspc`, plus the help line
"Change in ⚙ Settings → 🛰 Workspace backend tab." This makes the
current backend visible from every page without forcing the user to
go back to Settings.

#### 3.3.4 Wire-up to Executor

**Deferred to Phase 34.5+**. Phase 34.2 persists the choice but the
Plan / Execute pages still wire LocalWorkspace by default. The next
slice will plumb `MemoryStore().load().workspace_backend_spec` into
`parse_workspace_spec` at executor wire-up time in each page. That's
a small change (one function per page) and is best done with the
benchmark numbers Phase 34 unblocks.

### 3.4 F-4 — Plan page planner toggle

Two changes in `5_Plan.py`:

1. Detect `os.environ.get("ANTHROPIC_API_KEY")` at render time.
2. If unset, force `planner = "rule"` and show a blue info block
   explaining the fallback.
3. If set, expose a radio (rule / llm) defaulting to the autodetect
   choice. Users can override.

This means new users without an API key see immediate, deterministic
plans (the `rule` planner runs in ~300ms); users with a key get the
choice both ways.

---

## 4. §10.7 ledger

**0 kernel touches.**

Phase 34 modifies:
- `app/cli.py` (CLI surface)
- `app/ui/*` (3 UI files)
- `app/memory/_schema.py` + `app/memory/_store.py` (application-layer
  preferences storage)
- `tests/test_memory_store.py` + `tests/test_mcp_tools.py` +
  `tests/test_cli_repair_and_semantic.py` (test fixtures updated to
  match new schema_version)

Zero changes to `app/harness/`, `app/schemas/`, `localflow_kernel/`,
or `app/tools/`. Phase 30.2 kernel boundary lint stays green.

Ledger row: **0 kernel touches**; ledger after Phase 34 reads **4
deliberate / 41 deliveries / 37 zero-kernel-touch**.

---

## 5. Verification

Per CLAUDE.md rule D (evidence-driven):

| Verification | Result |
|---|---|
| pytest --tb=no | 1062 passed, 29 skipped (was 1056; +6 for new setter tests) |
| ruff check + ruff format | both clean |
| Pre-push hook | mirrors CI; passes |
| Local-observable UI walkthrough | 13 screenshots in `docs/test_artifacts/v0.32.0-phase34/` |

The headed-browser Playwright script (`/tmp/lflow_phase34_verify_v2.py`)
drove the UI end-to-end while the human user watched:

1. Open Settings → "🛰 Workspace backend" tab → screenshot 01
2. Select docker → screenshot 02 → Save → screenshot 03 (current backend = docker:python:3.12-slim)
3. Switch to ssh → fill host bob@build-vm.example.com → screenshot 04-05
4. Save → screenshot 06 (current backend = ssh:bob@build-vm.example.com)
5. Open Home → screenshot 07 (sidebar shows ssh backend badge)
6. Open Plan → screenshot 08 (no-key info block visible)
7. Reset to local for clean state → screenshot 09

Every step matched expectations. Failures from the v1 verification
run (radio selector mismatch) were Playwright-script bugs, not LocalFlow
bugs — fixed in v2 by using `label`-level selectors.

---

## 6. What Phase 34 deliberately defers

Per CLAUDE.md rule C (don't lock the blueprint), the following stay
deferred behind evidence:

- **34.5 candidate** — Wire `MemoryStore.workspace_backend_spec` into
  each page's executor (currently the Plan / Execute pages still use
  LocalWorkspace by default; the Settings preference is persisted but
  not yet consumed at run time).
- **34.6 candidate** — Execute page React mode + ConfirmationPolicy
  toggles. The E2E report flagged these as "CLI exposes ✅, UI exposes
  ❌" too; deferred until 34.5 lands.
- **35 candidate** — keep-alive HTTP client / Unix socket transport /
  TLS for the agent-server.
- **36 candidate** — physical relocation of `app/harness/*` →
  `localflow_kernel/`, PyPI split.

Each unlocks once a downstream consumer or benchmark makes it the
obvious next step.
