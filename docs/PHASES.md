# LocalFlow — Phase-by-Phase Changelog

Source of truth for what shipped, when, and what touched the kernel.
Each phase has been delivered in a focused single-sitting session and
verified with both unit tests and real-data implementation. The §10.7
ledger (kernel-touch count) is tracked explicitly — a project rule is
"adding a new Skill / Tool / Memory category should NOT require kernel
modifications". Phases 1-4.3 + 6.1 = zero kernel touches. Phase 5 is the
documented exception (~25 lines for `forbidden_paths`, a universal
safety primitive that *must* live kernel-side to be plug-in robust).

---

## Phase 0 — Harness skeleton (no LLM)

**Goal**: prove the harness is usable before plugging in an LLM. Build
the inspect → plan → dry-run → approve → execute → verify → rollback
control loop with a rule-based folder organizer.

**Shipped**:
- Pydantic schemas for `TaskSpec` / `ActionPlan` / `Action` / `RollbackManifest` / `VerificationResult` / `WorkspaceSnapshot`
- `policy_guard` (action-type allow-list, path containment)
- `Executor` with per-action defense-in-depth re-check, JsonlLogger audit
- `Verifier` (rules-only, independent of any LLM)
- `Rollback` with backup restore
- CLI: `inspect / plan / dry-run / execute / verify / rollback / status`
- `folder_organizer` skill (rule planner, classifies by extension)

**Files**: entire `app/harness/`, `app/schemas/`, `app/storage/`, `app/tools/file_*`, `app/skills/folder_organizer/`

**Tests added**: ~53 (foundation)

**Kernel touch**: this *is* the kernel — baseline.

---

## Phase 1 — LLM planner

**Goal**: let an LLM emit a typed `ActionPlan` via strict tool call.
Critical: model must NEVER directly write to disk — only emits the
plan; harness performs IO.

**Shipped**:
- `app/agent/`: `OpenAIClient`, `AnthropicClient`, strict tool-call
  schema, `tool_result` repair loop on Pydantic validation failure
- SSE streaming output with Rich Live in CLI (long planning calls
  stream tokens in real time)
- Default provider is OpenAI-compatible; configure via
  `LOCALFLOW_LLM_PROVIDER` / `LOCALFLOW_LLM_MODEL` /
  `LOCALFLOW_LLM_BASE_URL` in `.env` (see `.env.example`)

**Files**: `app/agent/`, `app/cli.py` (new `--planner llm` path)

**Tests added**: ~10

**Kernel touch**: NO — agent layer sits *above* the harness, only
produces `ActionPlan` to feed in.

---

## Phase 2.1 + 2.2 — Content awareness

**Goal**: let the planner see the *contents* of files, not just names.

**Shipped**:
- `app/tools/pdf_ops.py`: pypdf-based text preview (graceful return
  `None` on encoded / scanned / broken PDFs)
- `app/tools/text_ops.py`: first ~2000 chars of text/code/structured/
  tabular files, with NUL-byte binary detection
- `file_scan.scan_workspace(compute_preview=True)` injects previews
  into `FileMeta.text_preview`
- `agent/prompts.py` renders previews into the LLM system prompt;
  prompts updated to encourage semantic rename / classification

**Files**: `app/tools/pdf_ops.py`, `app/tools/text_ops.py`,
`app/tools/file_scan.py` (extended), `app/agent/prompts.py` (extended)

**Tests added**: 14

**Kernel touch**: NO — new module under `app/tools/`, used by
existing scan flow.

---

## Phase 2.3 — Skill ABC + plug-in pattern + `pdf_indexer`

**Goal**: turn LocalFlow from "single-purpose tool" into a Skill
plug-in framework. Each task feature becomes a `Skill` subclass.

**Shipped**:
- `Skill` ABC with `manifest / plan / plan_with_llm / validate /
  report` lifecycle ([app/skills/_base.py](../app/skills/_base.py))
- `SkillRegistry` — process-wide skill catalog
- `pdf_indexer` skill: scan PDFs → extract titles → synthesize a
  single `pdf_index.md` with provenance metadata (Open Deep
  Research-style)
- `folder_organizer` retrofitted as a `Skill` subclass
- CLI fully switched to registry dispatch (`--skill <name>`)

**Files**: `app/skills/_base.py`, `app/skills/folder_organizer/skill.py`,
`app/skills/pdf_indexer/`

**Tests added**: 20

**Kernel touch**: NO — Skill ABC lives in `app/skills/`, the kernel
only knows about the typed return shapes.

---

## Phase 3.1–3.3 — DataOps (`data_reporter` + `data_analyzer`)

**Goal**: read CSV/XLSX, produce report + analysis, render charts.
Without ever giving the LLM raw `exec()` capability.

**Shipped**:
- **3.1**: `data_reporter` skill emits `data_report.md` with per-table
  schema + numeric stats + sample rows (rule-based, no LLM)
- **3.1c**: `RESTORE_FROM_BACKUP` rollback op for "overwrite original
  with backup on undo"
- **3.2**: matplotlib chart generation (`chart_ops.histogram_png` /
  `bar_png`), binary action payload via `metadata.binary_content_b64`,
  `_record_implicit_parents` tracks `mkdir(parents=True)` for rollback
- **3.3a**: typed `AnalysisSpec` Pydantic schema (filter → groupby →
  sort → limit → chart). `data_analysis.execute_analysis` is a pure
  function: spec in, `AnalysisResult` out, NO eval/exec
- **3.3b**: LLM planner outputs `AnalysisSpec` (not pandas code) via
  strict tool call, harness's engine runs it. **Iron rule ⑤ kept** —
  the model still doesn't write code.

**Files**: `app/tools/data_ops.py`, `app/tools/chart_ops.py`,
`app/tools/data_analysis.py`, `app/schemas/analysis.py`,
`app/skills/data_reporter/`, `app/skills/data_analyzer/`,
`app/agent/analysis_prompts.py`, `app/agent/analysis_planner.py`

**Tests added**: ~60 (cumulative across 3.1 → 3.3b)

**Kernel touch**: NO — added one helper `_record_implicit_parents` to
the executor, **internal** to the existing `_do_index` path; no API
change and no new safety primitives.

---

## Phase 4.1 — Filesystem skill discovery

**Goal**: make LocalFlow a *real* plug-in framework. Drop a skill
folder anywhere LocalFlow looks, it loads at startup.

**Shipped**:
- `app/skills/_loader.py`: `discover_and_register_external(registry,
  dirs)` uses `importlib.util.spec_from_file_location` with hashed
  module namespace per skill (avoids `app.skills.*` collisions). Each
  load attempt becomes a `LoadFinding` entry — failures don't block
  other skills.
- Search paths (priority): `$LOCALFLOW_SKILLS_DIR` (multi-path) →
  `<cwd>/.localflow/skills/` → `~/.localflow/skills/`
- Name collision resolution: built-ins register first; external
  collisions error out, logged in audit
- `localflow skills` CLI command: registered table + search-paths +
  full load audit
- [examples/external_skill_example/](../examples/external_skill_example/):
  `workspace_stats` plug-in + multi-flavor install README

**Files**: `app/skills/_loader.py`, `app/cli.py` (new command),
`examples/external_skill_example/`

**Tests added**: 11

**Kernel touch**: NO — loader sits in `app/skills/`, kernel unaware.

**Outline §10.7 attestation**: project becomes "a framework", not "a
tool" — users write a 50-line `Skill` subclass without touching source.

---

## Phase 4.2 — Tool Registry

**Goal**: inventory the shared callable helpers (file_scan, pdf_ops,
data_ops, ...) skills are allowed to use, validate `required_tools`
declarations at register time. Composio-style.

**Shipped**:
- `app/tools/_registry.py`: `ToolSpec` (frozen dataclass: name,
  callable_ref, module, category, description, side_effects=False),
  `ToolRegistry` (register / get / has / list), lazy default factory
- 15 tools registered: 11 read / 2 transform / 2 render. `file_ops.*`
  (mutating IO) **intentionally excluded** — kernel-only, never
  Skills' to call directly
- `SkillManifest.required_tools: list[str] = []` (new field). When
  `SkillRegistry.register(skill, tool_registry=...)` is called, every
  declared name must resolve, else `SkillError`
- `localflow tools` CLI command + new "Tools" column in
  `localflow skills`

**Files**: `app/tools/_registry.py`, `app/schemas/skill.py` (extend),
`app/skills/_base.py` (validation hook), each built-in's `skill.py`
(declares deps)

**Tests added**: 24

**Kernel touch**: NO — `app/skills/_base.py` and `app/schemas/skill.py`
are framework, not kernel. Outline §10.7 still holds.

---

## Phase 4.3 — Unified Skill Contract Test Template

**Goal**: every skill (built-in or external) plug-able into a single
8-stage lifecycle test. "Does my skill work with LocalFlow?" gets a
one-call answer.

**Shipped**:
- `app/skills/_contract.py`: `run_skill_contract(skill, *,
  workspace_seeder, workspace_root, run_store, ...)` → `ContractReport`
  with `StageResult[]`. 8 stages: `manifest_valid` →
  `plan_empty_workspace` → `plan_happy_path` → `validate_accepts_own_plan`
  → `validate_rejects_garbage` → `execute_and_verify` →
  `rollback_restores` → `report_non_empty`. Each stage in its own
  try/except so a failure surfaces all downstream skips with reasons.
- All 4 built-ins parametrized through the contract; `folder_organizer`
  finally gets dedicated E2E coverage (previously only tested
  indirectly).
- External skill example: [examples/external_skill_example/test_contract.py](../examples/external_skill_example/test_contract.py)

**Files**: `app/skills/_contract.py`, `tests/test_skill_contracts.py`,
`tests/test_skill_registry.py` (extended)

**Tests added**: 10

**Kernel touch**: NO. Contract treats `Executor` / `Verifier` /
`Rollback` as black boxes.

---

## Phase 5 — Memory & personalization MVP

**Goal**: persistent user preferences. Outline §14 lists 5 categories;
this MVP ships 2 + lays the framework.

**Shipped**:
- `app/memory/`: `MemoryPreferences` (Pydantic schema with
  `forbidden_paths: list[str]` + `naming_style: NamingStyle` +
  `schema_version: int`), `MemoryStore` (atomic write, JSONL audit
  log), `apply_naming_style(name, style)` (4 styles: original /
  snake_case / kebab-case / lower)
- `TaskSpec` schema gets `forbidden_paths: list[str] = []` +
  `preferences: dict[str, Any] = {}` (skill-consumable bag)
- **KERNEL TOUCH** (the only one): `policy_guard.evaluate_action` /
  `assess_plan` / `_check_path_fields` gain `forbidden_paths` keyword
  parameter; `Executor.__init__` adds the same. ~25 lines total,
  fully backwards-compatible (defaults to empty tuple).
- `folder_organizer.planner` reads
  `task.preferences.get("naming_style", "original")` and transforms
  filenames at line 89
- CLI: `localflow memory list / forbid / unforbid / set / unset /
  audit` sub-app; plan command prints "Applied preferences from
  memory: ..." header when non-default

**Files**: `app/memory/`, `app/schemas/task.py`,
`app/harness/policy_guard.py` + `executor.py` + `control_loop.py`,
`app/skills/folder_organizer/planner.py`, `app/cli.py`

**Tests added**: 60

**Kernel touch**: **YES** — the documented exception. `forbidden_paths`
MUST be kernel-side; otherwise a forgetful Skill author could silently
bypass a user's "never touch X" rule. Doing it skill-side would
defeat the whole "plug-in safety" claim Phase 4 was built around.

**Outline §10.7 ledger**: 11 consecutive zero-kernel phases broken
deliberately. Phase 6.1 resumes the streak.

---

## Phase 6.1 — LocalFlow as MCP server

**Goal**: expose existing CLI surface to external MCP clients (Claude
Code, Claude Desktop, ...) over stdio JSON-RPC. **Zero new behavior** —
just wrap.

**Shipped**:
- `mcp = ["mcp>=1.6,<2.0"]` optional dep
- `app/mcp/`: `_serialize.py` (Pydantic/dataclass/Path/datetime/enum
  → JSON-safe), `tools.py` (15 `ToolDef` + handlers), `server.py`
  (`run_mcp_server()` boots stdio_server + Server.run; exceptions
  become `{"error": ...}` payloads, never break protocol)
- `localflow mcp-serve` CLI command (lazy SDK probe, graceful error
  when uninstalled)
- 15 MCP tools: read-only 7, state-changing 4 (create_plan / dry_run /
  execute_plan [requires `approved: true`] / rollback_run), memory
  mutations 4
- `[docs/MCP.md](MCP.md)`: setup + 15-tool reference + Claude Code
  config snippet

**Files**: `app/mcp/`, `app/cli.py` (one new command),
`pyproject.toml` (one new optional dep), `docs/MCP.md`

**Tests added**: 24

**Kernel touch**: NO — fully additive. **§10.7 streak resumes.**

**Real-world verification**: end-to-end JSON-RPC roundtrip via
`mcp.client.stdio.stdio_client` confirmed. `.mcp.json` wired into
Claude Code, `list_skills` / `create_plan` / `dry_run` / `execute_plan`
(with `approved=true`) / `rollback_run` all driven by Claude.
**Critical safety property held**: Phase 5's `forbidden_paths` blocked
execution through the MCP path identically to the CLI path —
`secrets/creds.txt` never touched.

---

## Phase 8.0 — Streamlit UI MVP (v0.7.0)

**Goal**: replace 5-step CLI commands with a clickable browser UI.
Outline L466/L512/L800/L1315 deferred UI until "CLI/API stable" —
v0.6.3 satisfied that condition, so v0.7.0 ships the UI.

**Shipped**:
- `app/ui/` package: `_sandbox.py`, `_layout.py`, `main.py`, 4
  pages (Plan / Execute / Rollback / Memory)
- `localflow ui-serve` CLI command — defaults to `127.0.0.1:8501`,
  graceful failure when streamlit dep missing
- Soft sandbox: workspace dropdown only shows subdirs of `./sandbox/`
  by default; `?unsafe=1` URL flag lifts with prominent banner
- Approval ceremony in browser: dry-run renders, checkbox required,
  Execute button only enabled after checkbox tick
- Rollback page visualizes Phase 7.1 drift detection — yellow rows
  for entries where the file changed since execute, safe-vs-force
  buttons

**Files**: `app/ui/` (new package), `app/cli.py` (one new command),
`pyproject.toml` (one new optional dep + version bump)

**Tests added**: 17 (`tests/test_ui_sandbox.py`) — sandbox boundary
parsing, query-param truthy values, eligible-workspace listing.
Streamlit UI itself smoke-tested via subprocess + HTTP 200 probe.

**Kernel touch**: NO — `app/ui/` is a brand-new driver layer that
reuses `control_loop.*` / `MemoryStore` / `Rollback` as black boxes.
**11th** zero-kernel phase (Phase 5 remains the lone exception).

**Reuses Phase 7.1**: the UI's Execute page mints approval tokens
the same way the MCP server does (`mint_token`), so both drivers
have symmetric "dry-run → approve → execute" ceremony. The
Rollback page uses `Rollback.preview()` to show drift inline.

---

## Phase 8.0.1–8.0.4 — UI hardening (v0.7.1–v0.7.4)

Bug-fix releases on top of the Phase 8.0 MVP:

- **v0.7.1**: disable Streamlit's first-run email prompt (was
  silently waiting for input on a hidden stdin)
- **v0.7.2**: add "🔍 Continue to Execute →" + "↺ Continue to
  Rollback →" cross-page navigation buttons after a successful
  plan / execute
- **v0.7.3**: fix `RollbackPreview.entry_count` AttributeError
  (regression test in `test_rollback.py`)
- **v0.7.4**: in the Rollback page, split `outcome.failed` into
  cascaded-from-conflict vs real failures — `delete_created_dir`
  ops that fail because the dir is non-empty due to a user-kept
  conflict are surfaced as PARTIAL with a blue info note, not
  red FAILED.

**Kernel touch**: NO. All four releases ship UI-side changes only.

---

## Phase 8.1 — UI UX overhaul (v0.8.0)

**Goal**: address three real-user pain points exposed by v0.7.0–v0.7.4
testing — Custom path was unreachable, the Skill/Planner dropdowns
exposed internals users didn't want to see, and the UI was English-only.

**Shipped**:
- `app/ui/_i18n.py` — flat `_DICT` (~120 keys), `t(key, **kwargs)`
  lookup with two-level fallback (requested lang → English →
  `!!key!!` sentinel), `render_language_toggle()` sidebar widget
  bound to `st.session_state["ui_lang"]`. Streamlit-free at import
  time so the dict is unit-testable.
- `app/ui/_autodetect.py` — `autodetect_skill()` +
  `autodetect_planner()`. Combines goal keywords (bilingual lists)
  with the workspace's file-type distribution and the skill's
  `supports_llm()` flag to pick both layers. Returns a reason string
  so the UI can explain its choice.
- `app/ui/_layout.py` — sidebar rewritten around a workspace-source
  radio (Sandbox subdir vs Custom path). Active-workspace badge
  always at the top. Custom-path input lives directly under the
  radio (no expander) with live validation; the option is hidden
  entirely without `?unsafe=1` (Streamlit can't disable a single
  radio choice). Language toggle at the very top.
- `app/ui/pages/1_Plan.py` — goal-only form, auto-detect badge + reason
  line, `▶ Override (advanced)` collapsed expander with the
  classic Skill + Planner widgets for power users. Auto-detect runs
  on every keystroke (cheap workspace scan cached per workspace
  in session_state).
- Every UI string (~120 unique keys) routed through `t()` —
  language toggle now switches the entire app instantly.

**Files**: `app/ui/_i18n.py` (NEW), `app/ui/_autodetect.py` (NEW),
`app/ui/_layout.py` (rewritten), `app/ui/main.py` (i18n migration),
`app/ui/pages/1_Plan.py` (rewritten), `app/ui/pages/2_Execute.py`
(i18n migration), `app/ui/pages/3_Rollback.py` (i18n migration),
`app/ui/pages/4_Memory.py` (i18n migration). `pyproject.toml`
version 0.7.4 → 0.8.0.

**Tests added**: 36 (`test_ui_i18n.py` 16 + `test_ui_autodetect.py`
20) — covers every autodetect branch, every i18n contract clause,
every critical key. Pure Python, zero Streamlit dep. Total suite
283 → 318.

**Kernel touch**: NO. `app/harness/`, `app/schemas/`, `app/tools/`,
`app/skills/`, `app/memory/`, `app/mcp/` all untouched. **12th**
zero-kernel-touch phase. (Phase 5 remains the lone exception.)

**Design note on the Override expander**: the user's original ask
was to hide Skill + Planner entirely. I pushed back: when the
heuristic guesses wrong, users with no override would be stuck.
A collapsed expander gives the dead-simple flow (95% case) without
trapping power users. The decision is surfaced in the auto-detect
reason line so users know exactly what's being chosen.

---

## Phase 23 — Sandboxed ComputeAction Engine + Recipe escape hatch (v0.23.0)

**Trigger**: through Phase 22 the harness's intelligence ceiling was
capped by its eight typed actions (`MKDIR / MOVE / RENAME / COPY /
INDEX / SUMMARIZE / CONVERT / FETCH`). Any task that needed to
*transform* file content — clean a messy CSV, derive a statistic, plot
a chart from raw data — had to be hard-coded as a new skill. That
does not scale. The Phase 23 deliverable is the third deliberate
§10.7 kernel exception (after Phase 5 `forbidden_paths` and Phase 16
`FETCH`): a `PYTHON_COMPUTE` action that lets the planner emit a
hand-written Python script, run it in an *isolated scratch
workspace* outside the user's data, and surface declared artefacts to
a follow-up pack stage. The honesty discipline — pinned in
`docs/COMPUTE_ACTION.md` — is that this is **isolation, not security
sandbox**: it stops accidental workspace mutation and casual leakage,
not a determined attacker. Hosts that want hard isolation run
LocalFlow inside Docker or a firewall-segregated account.

**Goal**: ship ComputeAction as a full kernel primitive — schema +
runtime + policy guard + verifier + rollback + trace + dry-run +
TaskGraph + Recipe — without sacrificing the iron-rule guarantees that
the workspace stays untouched until a separate pack stage.

**Three sub-phases shipped**:

- **Phase 23.0 — Skeleton + end-to-end demo.** New schemas
  (`app/schemas/compute.py`): `ComputeAction` (the typed payload —
  `script`, `script_summary`, `inputs`, `expected_outputs`,
  `sandbox_policy`, `requires_approval=True` by default),
  `ComputeInputRef`, `ArtifactSpec`, `SandboxPolicy`, `ComputeOutcome`,
  `ComputeOutcomeStatus`. New `ActionType.PYTHON_COMPUTE` enum value.
  New `RollbackOpType.DELETE_SCRATCH_DIR` rollback op. Runtime:
  `app/tools/scratch.py::ScratchWorkspace` (per-action layout under
  `<home>/scratch/<task>/<action>/` with `inputs/` + `outputs/` +
  `script.py` + `stdout.log` + `stderr.log`) and
  `app/harness/sandbox.py::SandboxRuntime` (subprocess + cwd
  confinement + 300s timeout cap + env scrub for proxy + AI provider
  keys; Unix-only `RLIMIT_AS` memory cap; Job Objects deferred). The
  executor dispatches `PYTHON_COMPUTE` through `_do_compute` which
  ALWAYS appends a `DELETE_SCRATCH_DIR` rollback entry (even on
  failure) so scratch never leaks. End-to-end demo:
  `examples/compute_action_pack/workspace/sales_dirty.csv` (50-row CSV
  with case inconsistency / mixed currency / duplicates / mixed date
  formats / outliers) + `tests/test_compute_demo_end_to_end.py` —
  three integration tests covering the cleaning script flow, full
  rollback restoring the workspace bit-for-bit, and the 4-event trace
  (`COMPUTE_ACTION_START` / `COMPUTE_ACTION_END` /
  `COMPUTE_OUTPUT_VERIFIED` / `SANDBOX_TIMEOUT` on timeout).

- **Phase 23.1 — Approval UX + trace + verifier integration.**
  Policy guard learns `PYTHON_COMPUTE` (`app/harness/policy_guard.py`):
  every declared input path resolves through `resolve_inside` and is
  matched against `forbidden_paths`; invalid `ComputeAction` metadata
  is rejected. The structural verifier gained
  `compute_outcomes_ok` — scans the manifest's `DELETE_SCRATCH_DIR`
  entries and fails the check if any compute action's
  `outcome.status != "ok"`, with the failure classified under the
  existing `FailureType.MISSING_OUTPUT` bucket so eval graders pick it
  up. CLI dry-run (`app/harness/dry_run.py`) renders the
  `script_summary` in the actions table's reason column (with
  `scratch/outputs/` as the target since outputs land outside the
  workspace) and adds a dedicated `## Compute scripts` section below
  the table with the full Python source rendered as a `python` fenced
  code block, capped at 4 KiB for malformed plans (full source lives
  in scratch `script.py` at execute time).

- **Phase 23.2 — TaskGraph integration.** `app/harness/taskgraph_runner.py`
  threads optional `scratch_workspace` + `sandbox_runtime` kwargs
  through `run_taskgraph` and forwards them to every stage's
  `Executor`. When omitted the runner constructs defaults rooted at
  `<home>/scratch/` so a recipe that uses a single compute stage works
  out of the box. The aggregated rollback manifest gains the
  `DELETE_SCRATCH_DIR` entry with stage-prefixed action_ids so
  `localflow rollback --run-id` wipes scratch alongside any other
  stage's workspace edits. New `registry` kwarg makes it easy to
  inject test-only stub skills without polluting the process-wide
  default.

**Phase 24 — Capability-first Recipe escape hatch (same delivery)**:
new `RecipeSpec.allow_compute_action: bool = False` field
(`app/schemas/recipe.py`). When False (default), the schema validator
refuses any stage that lists `python_compute` in `allowed_actions`,
AND `compile_to_taskgraph()` auto-appends `python_compute` to the
graph-level `forbidden_actions` so an LLM-planned agent stage cannot
hallucinate a ComputeAction even if it tries — belt-and-braces. When
True, the recipe AUTHOR has explicitly opted into the third §10.7
exception; the compile step then leaves `python_compute` out of the
forbidden list. This is the audit-trail anchor — grepping
`allow_compute_action: true` lists every recipe in the org that
touches the compute path.

**Files**:
- `app/schemas/compute.py` (new) — typed ComputeAction payload + outcome
- `app/schemas/action.py` — `PYTHON_COMPUTE` ActionType
- `app/schemas/rollback.py` — `DELETE_SCRATCH_DIR` RollbackOpType
- `app/schemas/trace.py` — `COMPUTE_ACTION_START/END/OUTPUT_VERIFIED/SANDBOX_TIMEOUT`
- `app/schemas/recipe.py` — `allow_compute_action` field + validator
- `app/tools/scratch.py` (new) — `ScratchWorkspace` layout helper
- `app/harness/sandbox.py` (new) — `SandboxRuntime`
- `app/harness/executor.py` — `_do_compute` dispatch + scratch_workspace/sandbox_runtime kwargs
- `app/harness/rollback.py` — `DELETE_SCRATCH_DIR` branch + scratch_workspace kwarg
- `app/harness/policy_guard.py` — `PYTHON_COMPUTE` block
- `app/harness/verifier.py` — `compute_outcomes_ok` check
- `app/harness/dry_run.py` — compute summary + scripts section
- `app/harness/taskgraph_runner.py` — scratch+sandbox+registry kwargs
- `docs/COMPUTE_ACTION.md` (new) — honesty discipline + 10 design principles
- `examples/compute_action_pack/` (new) — sales_dirty.csv fixture + README

**Tests added**: ~25 new (executor compute dispatch + scratch +
sandbox + policy guard + verifier + dry-run + TaskGraph compute stage
+ recipe opt-in + end-to-end demo). Full suite expected to pass with
no regressions.

**Kernel touch**: **YES (3rd exception)**. The kernel itself now
knows how to dispatch one new action type (`PYTHON_COMPUTE`), undo one
new rollback op (`DELETE_SCRATCH_DIR`), enforce one new policy
(input-only path check on ComputeActions), and verify one new outcome
shape (`ComputeOutcome.status`). This is the third deliberate §10.7
exception in 30 delivered phases — the surface area added is bounded
by the ten design principles in `docs/COMPUTE_ACTION.md` (output-to-
scratch, no source delete, declared-inputs-only, declared-outputs-
only, approval-mandatory, wall-clock-capped, env-scrub, network-best-
effort, always-rollback-cleans, scratch-outside-workspace).

**§10.7 ledger row 29 + 30** added below.

**Discovered after release (2026-05-24 UI smoke)**: the kernel-side
plumbing is complete and verified by 25 new tests, but **no production
code path emits a `PYTHON_COMPUTE` action**. Grep across `app/` for
`ActionType.PYTHON_COMPUTE` returns only the kernel layers
(schema / executor / policy_guard / dry_run); inside `app/skills/` no
skill manifest declares `python_compute` in `allowed_actions`, and the
agent skill's LLM tool schema (`app/agent/prompts.py`) does not expose
`python_compute` in its `action_type` enum. End-to-end a user-driven
goal cannot produce a ComputeAction in v0.23.0 — the cleaning-CSV demo
in `examples/compute_action_pack/` only works when wired up by hand in
the integration test. The fix is the **Phase 26 LLM-loop** decision
already locked in `docs/PROJECT_DIRECTION.md`: once the executor stage
becomes step-by-step with the LLM choosing each action against the
full `ActionType` enum (not a per-skill manifest subset), the
`PYTHON_COMPUTE` path becomes naturally reachable. Intermediate
patching (Phase 23.3 / 23.4) would be ~2-3 hours of work that the
Phase 26 refactor would discard. Decision recorded
[2026-05-24]: ship v0.23.0 as the kernel exception of record, fix
end-to-end reachability in Phase 26.

**Status update [2026-05-24, v0.24.0]: ✅ FIXED in Phase 26.** The
react loop landed in v0.24.0 (`docs/PHASE_26_DESIGN.md`,
`docs/REACT_LOOP.md`). When a recipe sets both
`enable_react_mode: true` and `allow_compute_action: true`, the
`react_loop`'s LLM tool schema (`app/agent/react_prompts.py`) exposes
`python_compute` in its action_type enum; an LLM mid-loop decision
may REPLACE or INSERT a `PYTHON_COMPUTE` action when prior
observations reveal typed primitives are insufficient (e.g. the
sales_dirty.csv test fixture). End-to-end the user-driven goal can
now reach the kernel exception they paid for in v0.23.0.
`examples/compute_action_pack/README.md` documents the opt-in.

---

## Phase 22 — UI productisation + bilingual substrate (v0.22.0)

**Trigger**: the v0.21 product-arc ledger called out two outstanding
gaps before LocalFlow could be handed to a non-developer:
(1) **terminology** — the UI still surfaced `Skill` / `Approval Token` /
`Verifier` / `Dry-run` to end users, every one of which forced a
"what is that?" tutorial; (2) **language** — the LLM-synthesised
README / SOURCES / per-stage reports were always English even when the
user typed a Chinese goal. v0.22 closes both gaps in one release
without touching the kernel.

**Goal**: ship a pack-buyer experience — landing page, soft
terminology, native-language deliverables — on top of the v0.17-v0.21
substrate. No new harness primitive, no new skill, no new schema for
the kernel. Pure productisation.

**Five lanes shipped**:

- **Lane B2 — Locale plumbing.** New
  `app/agent/locale_prompts.py::locale_instruction(locale)` returns a
  one-paragraph language directive (e.g. *"Write all narrative
  prose, headings, and bullet points in Simplified Chinese ..."*)
  appended to every LLM system prompt that synthesises user-facing
  text (agent / data_analyzer / pdf_indexer reporters). New
  `TaskGraph.locale: Literal["zh-CN", "en-US"]` schema field; new
  `--locale {zh-CN,en-US}` flag on `localflow taskgraph run` and
  `localflow pack run` (CLI rejects anything else with a friendly
  error). Existing graphs with no `locale:` field default to the
  English path, so v0.21 YAMLs run byte-identically.

- **Lane D — Bilingual deliverable templates.** Six new Jinja2
  templates under `app/templates/reports/` (one per reporting skill:
  `agent.md.j2` / `folder_organizer.md.j2` / `pdf_indexer.md.j2` /
  `data_reporter.md.j2` / `data_analyzer.md.j2` /
  `workspace_visualizer.md.j2`). Each template carries a `{% if
  locale == "zh-CN" %} ... {% else %} ... {% endif %}` block so
  headings + section labels + boilerplate prose match the requested
  language. Each skill's `reporter.py` was rewired to render the
  template instead of building the markdown by string concatenation
  — English text is the default branch, so missing translations fall
  back gracefully. +15 tests in
  `tests/test_bilingual_reports.py` (parametrised over both locales
  × every reporter).

- **Lane A-copy — terminology polish.** Public-facing UI strings in
  `app/ui/_i18n.py` softened across the board: Skill → Capability /
  能力; Approval Token → Approve / 确认授权; Verifier → Check /
  校验; Dry-run → Preview / 预览. Power-user override labels on the
  Plan page (behind the "(advanced)" expander) keep their technical
  clarifier so MCP / CLI users can still tell what they're picking.
  `pack.cards.stage_line` dropped the raw `skill · planner` suffix —
  the per-stage card now reads `1. ✓ **Organize files by type**`
  with no jargon.

- **Lane A-home — product landing page.** `app/ui/main.py` rewritten
  from "intro paragraph + manual lifecycle table" to a real product
  landing surface:
    - Hero with one-line value prop.
    - 3 featured pack cards (research_pack / data_report_pack /
      project_handoff_pack) rendered as `st.container(border=True)`
      with title + 1-line description + "Try this pack" primary CTA.
    - CTAs use session-state handoff (`_home_pack_select` key) +
      `st.switch_page("pages/0_Pack.py")` to land on the Pack page
      with the matching recipe card auto-expanded.
    - Manual lifecycle table demoted to "Or take manual control"
      section near the bottom for power users.
  CTAs are disabled until a workspace is picked.

- **Lane C-nav — partial sidebar restructure.** Two new pages:
    - `app/ui/pages/1_Workspace.py` — active workspace browser
      (file count + total size + run count + per-file table capped
      at 200 entries).
    - `app/ui/pages/3_Runs.py` — index of every past task
      (workspace filter / per-run table with status + rollback
      availability / "Open in Rollback" button using session-state
      handoff / inline final_report.md preview).
  `4_Memory.py` renamed to `4_Settings.py` (page title bumped to
  `⚙ Settings`). Pack page title bumped from `Pack` to
  `Create Pack`. Plan / Execute / Rollback pages pushed to `5_*` /
  `6_*` / `7_*` prefixes so the natural sidebar order reads Home →
  Workspace → Create Pack → Runs → Settings → (advanced).
  Every cross-page `st.switch_page("pages/N_*.py")` call updated to
  match the new prefixes.

**What's NOT in v0.22 (deferred)**:
- **Full `st.navigation` collapse** — the original C-nav scope
  wanted the advanced 5/6/7 pages hidden from the default sidebar
  via `st.navigation` + `st.Page`. Deferred because Streamlit's
  `set_page_config` may only be called once per script run, which
  forces touching every page's `configure_page` to make it
  idempotent — that's a refactor that needs a running browser to
  verify, out of scope for this session. The partial C-nav already
  delivers the new IA via natural prefix ordering.
- DataOps deepening (multi-table joins / anomaly detection /
  conclusion grounding) — Phase 23.
- StageRunStore backup-path bug surfaced in Phase 19 testing —
  Phase 24 engineering cleanup.

**Live verification**:
- Full test suite: 658 → **681 passed**; +23 new tests (15
  bilingual reports + 8 covering locale flag / template fallback /
  page registration / handoff session-state contract). 0
  regressions.
- `localflow taskgraph run examples/research_pack/workspace_pack.yaml
  --locale zh-CN --yes` against a fresh workspace produces a Chinese
  README + SOURCES + per-stage reports; the same command with
  `--locale en-US` (or omitted) produces English. Stage outputs
  (file moves, charts, pdf_index titles) are language-neutral.
- Streamlit UI smoke (manual): the new home renders the 3 pack
  cards, clicking "Try this pack" navigates to the Pack page with
  the right recipe pre-selected. New Workspace + Runs pages
  populate against an existing `.localflow/runs/` directory.

**Kernel touch**: NO. New module `app/agent/locale_prompts.py` lives
above the harness. New `TaskGraph.locale` field is read by skill
reporters, not by `app/harness/*`. Jinja templates are rendered by
skill code, not by the executor. UI pages are entirely outside the
kernel. **28th** zero-kernel-touch phase.

**§10.7 ledger row 28** added below.

---

## Phase 21 — Recipe Auto-Repair Loop (v0.21.0)

**Trigger**: productisation guide §2.5 "repair 主要依赖 failed verdict
的 hint" — Phase 19 deliverable verifiers already attach a typed
``suggested_hint`` to every failure, but those hints were display-only.
v0.21 closes the loop: when a recipe verifier fails, the hint flows
into ``skill.plan_with_llm(user_hint=...)`` for the targeted stage,
that stage is rolled back + replayed, and verifiers re-run.

**Goal**: take the hand-off from Phase 19's structured-hint contract
all the way to a re-planned, re-executed deliverable — without
touching the harness kernel.

**Shipped**:
- `app/schemas/taskgraph.py` — new ``TaskGraph.stage_hints: dict[str,
  str]`` field (stage_id → hint). The runner reads it when planning
  a stage with planner='llm' and threads it through as ``user_hint``.
- `app/schemas/recipe.py` — new ``RecipeSpec.repair_target_map: dict
  [str, str]`` (verifier_name → stage_id) + ``resolve_repair_target``
  helper that defaults to the recipe's last LLM stage when no
  explicit mapping is set. ABORT-promotion preserved from Phase 17.
- `app/harness/taskgraph_runner.py` (`_run_one_stage`) — when
  ``stage.planner == "llm"`` AND ``graph.stage_hints[stage_id]`` is
  populated, passes ``user_hint=...`` to ``skill.plan_with_llm``.
  Skills that don't accept the kwarg (rule-planned stages) ignore it.
  +6 lines, no kernel-behaviour change.
- `app/harness/recipe_repair.py` (NEW) — ``run_recipe_repair`` is the
  orchestration entrypoint:
    1. Picks the first non-skipped fail verdict with a hint.
    2. Resolves its target stage via ``recipe.resolve_repair_target``.
    3. ``model_copy``s the TaskGraph with ``stage_hints[target] = hint``.
    4. Calls ``replay_from_stage`` (Phase 15 primitive — rolls back
       affected entries + replays).
    5. Re-builds a ``RecipeVerifierContext`` from the post-replay
       workspace + manifest and re-runs every verifier the recipe
       declared.
    6. Repeats up to ``repair_policy.max_rounds`` (≤ 3). Halts on:
       *passed* / *exhausted* / *no_repairable_failures* /
       *no_target_stage* / *replay_error*.
  Returns a ``RecipeRepairResult`` with per-attempt history that the
  CLI renders as a Rich table.
- `app/cli.py` — ``pack run`` now triggers the loop when (a) every
  stage PASSED, (b) ≥ 1 recipe verifier FAILED, (c)
  ``recipe.repair_policy.enabled`` is true. Persists
  ``<run_dir>/recipe_repair.json`` AND rewrites
  ``recipe_verification.json`` to the post-repair verdict.
- All 3 flagship recipes now ship with ``repair_policy.enabled=true``
  + ``max_rounds=2``. ``research_pack`` + ``project_handoff_pack``
  add ``repair_target_map`` entries routing coverage / review-queue
  failures back to the organizer (instead of the synth default).
- `tests/test_recipe_repair.py` (12) — schema helpers, halt
  conditions, happy path, exhaustion, replay-error capture,
  stage_hint wiring, TaskGraph backward compat.

**Live verification**:
- All 3 flagship recipes still compile + load (`localflow pack list`).
- The flagship recipes now expose ``repair_policy.enabled=true`` to
  the user via ``pack describe`` so the auto-repair behaviour is
  transparent.
- Full test suite: 646 → **658 passed**; +12 new tests, 0 regressions.

**Kernel touch**: NO. ``app/harness/recipe_repair.py`` is a new
orchestration module; ``app/harness/taskgraph_runner.py`` gains 6
lines wiring stage_hints into the existing ``plan_with_llm`` call —
the hint is passed by value, no kernel state changes. **27th**
zero-kernel-touch phase.

**What's NOT in v0.21 (deferred)**:
- DataOps deepening (multi-table joins, anomaly detection,
  conclusion grounding) — bundled with Phase 22 originally; split
  to its own Phase 22 alongside WebCollect deepening + Trace
  dashboard.
- Recipe repair targeting at the per-verdict level (right now the
  loop picks the first repairable verdict per round; if multiple
  verifiers want different stages repaired, only one runs per
  round). Phase 22 will add parallel + bulk-repair semantics.
- StageRunStore backup-path bug (a-009 ValueError on dirty
  workspace) — Phase 23 cleanup.

---

## Phase 20 — Flagship packs formalised + product-led README + Phase 19 bug fixes (v0.20.0)

**Trigger**: productisation guide §12 Phase A ("产品主线重包装") +
§8 ("旗舰场景") — the project's positioning was still
engineering-led ("safe execution harness") rather than product-led
("local-first workspace delivery agent"). Phase 17 introduced the
Recipe layer but only the research_pack flagship had a runnable
example workspace; data_report_pack + project_handoff_pack were
catalog-only. Phase 19 also surfaced 3 real product-quality bugs
that hand-waved verifier failures in the user's live testing.

**Goal**: ship the three flagship packs with full demo material;
rewrite the README narrative from feature-list to deliverable-pack
product positioning; fix the 3 real bugs Phase 19 verifiers caught
in user testing.

**Shipped**:

  Bug fixes (driven by Phase 19's live-testing feedback):
  - `route_low_confidence_to_review` now auto-propagates as a
    workspace preference when a recipe declares `review_queue_
    verifier`. Fixes the Phase 19 finding where `untitled.dat` was
    force-classified into `misc/` instead of `review/`.
    User overrides still win (CLI / memory pref).
    File: `app/schemas/recipe.py:compile_to_taskgraph`.
  - `TaskSpec.expected_outputs` is a new field; the TaskGraph runner
    populates it from `stage.expected_outputs` for every stage; the
    agent meta-skill's user prompt now includes a "REQUIRED
    deliverables" section. Fixes the Phase 19 finding where the
    LLM synthesised README but skipped SOURCES.md.
    Files: `app/schemas/task.py`, `app/harness/taskgraph_runner.py`,
    `app/agent/prompts.py`.
  - `chart_data_consistency_verifier` now scopes ONLY to
    `analysis_charts/`. Workspace-overview charts (file_counts.png
    in `images/`) are metadata-driven, not row-driven — comparing
    them to an unrelated CSV produced false-positive failures in
    Phase 19 user testing. Falls back to `analysis_report.md` when
    no per-chart caption exists.
    File: `app/eval/recipe_verifiers/semantic.py`.

  Three flagship pack demos:
  - `examples/data_report_pack/seed.py` + workspace + README —
    3 CSVs (revenue / users / errors) + 1 XLSX + a seed README.
    Designed for the 3-stage data_analyzer → workspace_visualizer →
    agent synth recipe.
  - `examples/project_handoff_pack/seed.py` + workspace + README —
    4 .py files + .csv + 2 .md notes + .env.example +
    pyproject snippet + a tiny .png. Designed for folder_organizer →
    workspace_visualizer → agent synth recipe.
  - `evals/workspace_pack/task_011_data_report_pack.yaml` (new)
  - `evals/workspace_pack/task_012_project_handoff_pack.yaml` (new)
  - `.gitignore` extended to ignore the new workspace dirs.

  Documentation:
  - `README.md` top section rewritten: deliverable-pack hero +
    three flagship Pack table + Goal Interpreter quickstart + a
    "what's in a deliverable pack" tree. Engineering description
    of the harness moved to "Why a harness, not a script" later
    in the doc.
  - `What's shipped` section gains 4 new entries at the top
    (Pack system / Goal Interp / Primitives / Verifiers) covering
    Phase 17-19 product layers.
  - Roadmap reflowed: Phase 21 = auto-repair wiring + DataOps;
    Phase 22 = WebCollect + Trace dashboard; Phase 23 = engineering
    debt + StageRunStore backup-path bug fix.

  Tests:
  - `tests/test_recipe_schema.py` — 3 new tests for the auto-
    propagation behaviour: review_queue_verifier wires the
    preference, recipes without it don't, user overrides win.
  - `tests/test_eval_runner.py` — task discovery expectation bumped
    9 → 11.
  - `tests/test_recipe_verifiers_semantic.py` — 2 chart-consistency
    tests rewritten to match the new analysis_charts/ scope +
    analysis_report.md fallback; 1 new test pinning the
    workspace-overview exclusion behaviour.

**Live verification**:
- `python examples/data_report_pack/seed.py` → 5 files plant cleanly.
- `python examples/project_handoff_pack/seed.py` → 10 files plant
  cleanly.
- `localflow pack describe data_report_pack` / `project_handoff_pack`
  shows the 3-stage layouts.
- Full test suite: 642 → **646 passed**; +4 tests, 0 regressions.

**Kernel touch**: NO. The bug fixes are all application-layer:
recipe compilation, task spec field, LLM prompt content, verifier
scope filter. **26th** zero-kernel-touch phase. `app/harness/*`
unchanged (Phase 5 + Phase 16 remain the only exceptions).

**What's NOT in v0.20 (deferred to Phase 21)**:
- Auto-repair loop wiring: Phase 19 verifier `suggested_hint`s
  exist on every fail verdict but aren't yet consumed by Phase 13's
  repair loop. Phase 21 will glue them.
- DataOps deepening (multi-table joins, anomaly detection,
  conclusion grounding) — report §12 Phase F.
- StageRunStore backup-path bug surfaced in user testing (a-009
  `index notes/index.md` ValueError on dirty workspace) — Phase 23
  cleanup.

---

## Phase 19 — Deliverable Verifier expansion (v0.19.0)

**Trigger**: productisation guide §3.3 ("structural verifiers pass
while semantic quality fails") + §10 (the explicit list of 7
verifiers the project should prioritise: Coverage, SourceLedger,
SummaryGrounding, ChartDataConsistency, ReviewQueue,
DeliverableCompleteness, TopicCoherence). v0.14 had `every_input_
accounted_for` + `analysis_result_nonempty` as starter semantic
graders; v0.19 ships the full 7 at the **recipe** (not eval-task)
level so every `pack run` gets a deliverable verdict.

**Goal**: after a pack's stages finish, run every grader the recipe
declared in its `verifiers:` field. Persist the bundle as
`recipe_verification.json`. Surface verifier failures as exit code 3
(distinguishable from "pipeline crashed" exit code 1) so CI separates
broken pipelines from broken quality.

**Shipped**:
- `app/eval/recipe_verifiers/` — new package, separate registry from
  `app/eval/graders/` so the recipe-level abstraction can evolve
  independently. Three modules:
    * `_schema.py` — `RecipeVerifierContext` + `RecipeVerifierVerdict`
      + `RecipeVerification` envelope (all Pydantic v2).
    * `_registry.py` — `@register` + `run_all` helpers. Catches
      unknown verifier names AND verifier exceptions and surfaces
      both as failed verdicts (typo / bug doesn't abort verification).
    * `structural.py` (4 verifiers) + `semantic.py` (3 verifiers).
- **4 structural verifiers** (no LLM):
    * `coverage_verifier` — every input file moved OR cited in `*.md`
      (closes the gap §10 #1 named).
    * `source_ledger_verifier` — backticked paths in `SOURCES.md`
      resolve to real files (§10 #2).
    * `review_queue_verifier` — unclassifiable extensions land in
      `review/` (§10 #5).
    * `deliverable_completeness_verifier` — every
      `recipe.expected_outputs` path exists on disk (§10 #6).
- **3 semantic verifiers** (LLM-as-judge via `app.agent.judge`):
    * `summary_grounding_verifier` — README claims trace to real
      workspace files (§10 #3).
    * `chart_data_consistency_verifier` — chart caption stats match
      the source CSV preview (§10 #4).
    * `topic_coherence_verifier` — first non-trivial category /
      topics/<sub> directory is semantically coherent (§10 #7).
- `app/cli.py` — `pack run` now executes recipe verifiers after the
  TaskGraph finishes. Writes `<run_dir>/recipe_verification.json`,
  renders a verdict table, displays `suggested_hint` for each
  failure. **Exit code 3** = pipeline ran cleanly but ≥ 1 verifier
  failed (vs 1 for pipeline crashes).
- All three flagship recipes (`recipes/*.yaml`) updated to list the
  new verifier names in their `verifiers:` field.
- `tests/test_recipe_verifiers_structural.py` (16) — registry shape,
  per-verifier happy / fail / skip paths, run_all error handling,
  RecipeVerification aggregation.
- `tests/test_recipe_verifiers_semantic.py` (13) — no-LLM skip,
  judge mock for happy + fail paths, summary-md vs `<stem>_summary.md`
  caption detection, topics/<sub>/ layout handling.
- `docs/VERIFIERS.md` — full reference: 7-verifier table, integration
  with `pack run`, graceful-degradation behaviour, extension guide.

**Live verification** (research_pack vs the v0.14 seeded workspace):
- All 5 stages PASSED in ~13 s (same as Phase 14).
- 7 verifiers ran: 3 PASS, 3 FAIL, 1 SKIPPED. The failures caught
  REAL issues the pipeline silently shipped pre-v0.19:
    * `review_queue_verifier`: `untitled.dat` ended up in `misc/`
      instead of `review/`.
    * `chart_data_consistency_verifier`: the workspace-overview
      chart's caption doesn't actually relate to the experiment
      CSV (they're different datasets).
    * `topic_coherence_verifier`: `images/index.md` claims 2 files
      but the directory has 3.
  Each failure has a `suggested_hint` ready for Phase 20+ auto-repair.
- Full test suite: 608 → **637 passed**; +29 new tests, 0 regressions.

**Kernel touch**: NO. New package `app/eval/recipe_verifiers/` is
application-layer; `app/cli.py` adds verifier orchestration AFTER the
runner returns. **26th** zero-kernel-touch phase. `app/harness/*`
unchanged.

**What's NOT in v0.19 (deferred)**:
- Auto-repair loop consuming the `suggested_hint` field — Phase 20
  will wire it into recipe-level stage repair (Phase 13's loop
  consumes per-stage hints today; recipe-level needs a wider
  context-aware re-plan).
- Vision-LLM image grader for chart_data_consistency — currently
  uses the text caption + CSV preview path. Phase 22+ when vision
  primitives land.
- TopicCoherence over a sample of topic dirs (not just the first) —
  trivial extension, deferred until empirically useful.

---

## Phase 18 — Capability Primitives + LLM Goal Interpreter (v0.18.0)

**Trigger**: productisation guide §4.3 ("flip from skill-first to
recipe-first") + §6.2 ("Delivery Planner asks clarifying questions
when goal is vague") + §7 (the canonical "User Goal → Goal
Interpreter → TaskGraph Planner" framework diagram). v0.17 shipped
the Recipe layer; v0.18 ships the layer that sits ABOVE it (Goal
Interpreter) and the layer BELOW it (Capability Primitives), closing
two of the three boxes between "user goal" and "executing skill".

**Goal**: when a user's goal doesn't clearly map onto one recipe, ask
clarifying questions instead of crashing or silently picking the
wrong pack. Plus give the LLM Interpreter (and Phase 19 verifiers) a
stable typed primitive surface to talk about capabilities at —
without naming a skill.

**Shipped**:
- `app/primitives/_schemas.py` — typed I/O models: `ContentRef`,
  `Content`, `Classification`, `ContentKind` enum (7 coarse buckets:
  document / note / table / image / code / structured / binary).
- `app/primitives/extract_content.py` — primitive #1, dispatches over
  ContentKind to `pdf_ops` / `text_ops` / `data_ops`. Best-effort,
  never raises; `error="binary"` for images, `"missing"` for absent
  files, `"unreadable"` when the backend returns None.
- `app/primitives/classify_content.py` — primitive #2, curated
  extension → label table (paper / data / note / code / structured
  / image / binary). Confidence = 1.0 on extension hit, 0.5 on
  ContentKind fallback, 0.2 on binary.
- `app/primitives/catalog.py` — 10-entry `PrimitiveEntry` registry.
  Only 2 entries are implemented wrappers; the other 8 are
  catalog-only with `backed_by` pointers to the tool / skill that
  provides the behaviour today. The doc (`docs/CAPABILITIES.md`)
  explains the "earn its wrapper" rule that keeps abstraction
  minimal.
- `app/agent/goal_interpreter.py` — `GoalInterpreter` class.
  Decision tree:
    1. Router scores ≥ `CONFIDENT_SCORE_THRESHOLD` (=6) with margin
       ≥ `CONFIDENT_MARGIN` (=2) → commit deterministically,
       source='router', no LLM call.
    2. Ambiguous + no LLM client → degrade to router top with
       low-confidence rationale, source='router'.
    3. Ambiguous + LLM client → call LLM with strict tool schema
       (`recipe_name` enum-constrained to loaded names);
       `decision=pick` returns a recipe, `decision=clarify` returns
       1–3 short questions in the user's language. Source='llm'.
    4. LLM call fails / returns unknown recipe → defense-in-depth
       fallback to router top with explanation in `rationale`.
  Three layers of safety net: schema enum-constraint, payload
  validation, post-hoc unknown-name check.
- CLI: `localflow goal "..." --workspace <ws> [--no-llm] [--run]`.
  Goal text in any language; presents clarifying questions
  interactively at the prompt (max 2 rounds); on confident pick,
  prints the suggested pack + the full router ranking. With
  `--run`, chains directly into `pack run`.
- UI: Pack page's "Suggest" block (Phase 17) replaced with a
  Phase 18 "🎯 Interpret a goal" block that walks the full
  GoalInterpreter loop. Session-state machinery handles the
  clarifying-question rerun cycle.
- `docs/CAPABILITIES.md` — the full primitive taxonomy + layering
  diagram. Documents the "earn its wrapper" abstraction discipline.
- `tests/test_primitives.py` (17) — schema enum coverage, path
  normalisation, every implemented primitive's happy + error paths,
  catalog completeness invariants.
- `tests/test_goal_interpreter.py` (9) — all three decision paths
  (router-confident, router-fallback, LLM-driven pick + clarify),
  LLM failure degradation, unknown-recipe defense-in-depth, audit
  trail, clarifying-round answer propagation, public threshold
  constants.
- `tests/test_goal_cli.py` (3) — command registration, missing
  workspace exit code, end-to-end `--no-llm` confident pick.

**Live verification**:
- `localflow goal "整理研究资料" --workspace examples/research_pack/workspace --no-llm`
  returns `Suggested pack: research_pack` with source='router',
  score +9, margin +6 → router-confident path, no LLM call.
- `localflow goal "做点东西" --workspace examples/research_pack/workspace --no-llm`
  degrades to router fallback with low-confidence rationale (no
  crash, no clarification round in router-only mode).
- The `--no-llm` flag forces the deterministic path even when a key
  is set — useful for CI / dry runs.
- Full test suite: 578 → **608 passed**; +30 new tests, 0
  regressions.

**Kernel touch**: NO. `app/primitives/` is a new application module;
`app/agent/goal_interpreter.py` lives alongside the existing planner.
**25th** zero-kernel-touch phase. `app/harness/*` unchanged.

**What's NOT in v0.18 (deferred to Phase 19)**:
- Wrapping the other 8 primitive entries as typed functions —
  deferred until a verifier or recipe needs them at the function
  level (the catalog points at the existing backend in the
  meantime).
- Deliverable verifier expansion (CoverageVerifier,
  SourceLedgerVerifier, SummaryGroundingVerifier, …) — Phase 19's
  job; v0.18 just establishes the primitive I/O contracts those
  verifiers will consume.
- Vision-LM backed `extract_content` for images — Phase 22+ when
  the WebCollect deepening also lands.

---

## Phase 17 — Recipe / Pack System (v0.17.0)

**Trigger**: the productisation guide
(`localflow_productization_development_guide.md`) diagnosed
LocalFlow's biggest product-shape gap (§3.1 "current features exist
as modules, not as user outcomes") and prescribed the fix (§4.3
flip from skill-first to recipe-first; §5 reposition as
"Local-first Workspace Delivery Agent"; §12 Phase B "Recipe / Pack
System"). v0.17 ships that layer.

**Goal**: give users a product-level entry point — they pick a
deliverable pack (Research Pack / Data Report Pack / Project
Handoff Pack), never a skill name. Each pack compiles to a
TaskGraph the v0.11 runner already drives. Zero kernel changes.

**Shipped**:
- `app/schemas/recipe.py` — `RecipeSpec`, `RecipeStage`,
  `InputExpectation`, `RepairPolicy`. Field order matches §12
  Phase B verbatim: name / description / input_expectation / stages
  / expected_outputs / verifiers / repair_policy.
  `RecipeSpec.compile_to_taskgraph()` is the bridge that emits a
  v0.11 `TaskGraph`; `repair_policy.enabled=true` promotes every
  ABORT stage to REPAIR with `max_retries=max_rounds` (SKIP /
  CONTINUE stages preserved).
- `app/recipes/registry.py` — `RecipeRegistry` with lazy load,
  cached scan, error list (broken YAMLs surface in a separate
  warning section instead of silently disappearing).
  `LOCALFLOW_RECIPES_DIR` env var override.
- `app/recipes/router.py` — deterministic, no-LLM scorer over
  (user_goal, workspace_snapshot). Rule:
  `score = (keyword hits × 2) + (file-kind matches, cap 5)
         - (10 if min_files violated) - (5 if require_any violated)`.
  Phase 18 will add an LLM clarifying path on top.
- `recipes/research_pack.yaml` — research workspace (PDFs + data
  + notes + images) → 5-stage pack with per-category indexes,
  PDF index, analysis report, overview chart, README, SOURCES.
- `recipes/data_report_pack.yaml` — tabular-only workspace →
  3-stage pack (data_analyzer + workspace_visualizer + agent
  synth).
- `recipes/project_handoff_pack.yaml` — code project →
  3-stage pack (folder_organizer + workspace_visualizer + agent
  synth).
- CLI: `localflow pack list / describe / suggest / run`. The `run`
  subcommand is functionally identical to `taskgraph run` against
  the compiled graph — same approval ceremony, same single
  aggregated rollback.
- `app/ui/pages/0_Pack.py` — Streamlit page with filename prefix
  `0_` so it lands FIRST in the sidebar (ahead of Plan / Execute /
  Rollback / Memory). Three sub-flows: Browse, Suggest, Run.
- `tests/test_recipe_schema.py` (8) — schema validation,
  duplicate-stage rejection, compile round-trip, repair promotion
  semantics.
- `tests/test_recipe_registry.py` (8) — directory loading,
  duplicate-name rejection, missing-dir handling, reload, the
  three shipped flagships always compile cleanly.
- `tests/test_recipe_router.py` (8) — keyword scoring, file-kind
  cap, min_files penalty, require_any penalty, tie-breaking,
  end-to-end routing for the three flagships.
- `tests/test_pack_cli.py` (7) — `pack list/describe/suggest/run`
  exit codes + error messages.
- `docs/RECIPES.md` — full schema reference + CLI surface + UI
  surface + extension guide.

**Live verification**:
- `localflow pack list` shows all 3 flagships with stage/output
  counts.
- `localflow pack suggest examples/research_pack/workspace --goal
  "整理我的研究资料"` ranks research_pack first with score +9 and
  exposes the keyword + file-kind reasons.
- `localflow pack describe research_pack` prints the spec, stage
  list, expected deliverables, verifiers, and repair policy.
- Full test suite: 542 → **578 passed**; +36 new tests.

**Kernel touch**: NO. Pure application layer — Recipe compiles
DOWN to v0.11 TaskGraph; the runner / executor / verifier /
rollback paths are untouched. **24th** zero-kernel-touch phase.

**§10.7 ledger position**: 24 of 27 phases so far have been
zero-kernel-touch. Phase 5 (forbidden_paths primitive) and Phase
16 (ActionType.FETCH) remain the only documented kernel exceptions.

**What's NOT in v0.17 (deferred to Phase 18)**:
- LLM-powered Goal Interpreter (clarifying questions when no
  recipe scores high enough).
- Capability primitives layer (`app/primitives/*`) — pulling
  `extract_content` / `classify_content` / etc. out of skills so
  recipes can compose at the function level, not just the skill
  level.
- Recipe-level verifier wiring — `verifiers:` is recorded as
  metadata in v0.17 and consumed in Phase 19's Deliverable
  Verifier expansion.

---

## Phase 14 — Workspace Pack Builder (v0.14.0)

**Trigger**: v0.10-v0.13 each shipped a substrate piece (TaskGraph,
Plan Refinement, Data-Aware Routing, Auto-Repair). Each was a
capability in isolation. The experiment report's `Section 8` calls
out **Workspace Pack Builder** as the canonical strong demo proving
those layers actually stack into a real-world pipeline. v0.14
delivers exactly that.

**Goal**: turn a messy research workspace (PDFs + CSVs + images +
notes) into a deliverable knowledge pack via one command. Compose
every existing skill into a 5-stage TaskGraph; bundle the example
workspace + a runnable YAML graph + an eval task that benchmarks
the pipeline under v0.13's `--compare-repair` mode.

**Shipped**:
- `examples/research_pack/seed.py` — script planting a 10-file
  messy workspace (3 PDFs with real %PDF headers so pypdf can
  extract titles, 1 CSV with 30 rows of synthetic experiment data,
  1 XLSX with model scores, 2 PNGs, 2 notes, 1 unknown-type stub).
- `examples/research_pack/workspace_pack.yaml` — the canonical
  5-stage TaskGraph (folder_organizer → pdf_indexer → data_analyzer
  → workspace_visualizer → agent). Stages 1-4 rule-planned;
  stage 5 LLM-planned with `failure_policy: skip` so CI without an
  API key still produces stages 1-4 outputs.
- `examples/research_pack/README.md` — quickstart.
- `app/eval/graders/structural.py` — new `every_input_accounted_for`
  grader (Phase 14 coverage check: each seeded file must be either
  moved to a target dir OR cited by basename in a generated `.md`
  report).
- `evals/workspace_pack/task_010_workspace_pack.yaml` — eval task
  with 6 graders + `must_pass` set tuned so the task passes in CI
  even when stage 5 skips.
- `tests/test_coverage_grader.py` — 4 tests pinning the grader's
  two coverage branches + empty-seed + missing-file edge case.
- `tests/test_pack_builder_demo.py` — 4 tests including a real
  end-to-end run of stages 1-4 against the seeded workspace.
- `docs/PACK_BUILDER.md` — full walkthrough.

**Live verification** (run during development):
- `python examples/research_pack/seed.py` plants 10 files.
- `localflow taskgraph run examples/research_pack/workspace_pack.yaml --yes`
  with an LLM key configured: ALL 5 stages PASSED, total ~19 s
  (16 s of which is stage 5's LLM call). The produced workspace
  contained the full pack: README.md, pdf_index.md,
  analysis_report.md, analysis_charts/ (with pie charts —
  v0.12.0's Phase 12 heuristic kicked in for the model column),
  per-category dirs with index.md files, duplicates_report.md,
  file_counts_summary.md, sources ledger.

**Kernel touch**: NO. Pure composition + 1 grader + YAML + docs.
**23rd** zero-kernel-touch phase. `app/harness/*` unchanged.

---

## Phase 13 — Semantic Verifier + Auto-Repair Loop (v0.13.0)

**Trigger**: v0.12 closed the *user-driven* correction loop (manual
`localflow revise --hint`). The natural follow-up is the *automatic*
counterpart — give the harness an LLM-as-judge layer that runs
*after* execute + structural verify, and a retry-with-repair cycle
that fires when a semantic grader rejects the run.

The three motivating failure modes (all observed in eval traces):

- `data_analyzer` produces `analysis_report.md` whose every analysis
  ended in `EMPTY_RESULT` (column reference miss) — structural OK,
  semantically empty.
- `folder_organizer` writes `papers/index.md` containing generic
  boilerplate that doesn't mention the actual files it claims to
  index.
- `agent` produces a chart whose X-axis labels don't match any
  category in the source data.

**Shipped**:
- `app/schemas/semantic.py` — `SemanticVerdict` + `SemanticVerificationResult`
- `app/agent/judge.py` — thin LLM-as-judge wrapper (typed
  `{verdict, reason, suggested_hint}` schema; graceful no-op when no
  API key)
- `app/eval/graders/semantic.py` — 3 starter graders, registered via
  the same `@register` decorator as structural ones
- `app/harness/semantic_verifier.py` — runtime verifier (separate
  from the structural `Verifier`; §10.7 boundary preserved)
- `app/harness/repair_loop.py` — orchestrates rollback → revise →
  re-execute → re-verify, bounded by `max_auto_repairs`
- `app/harness/control_loop.py` — new `run_with_auto_repair` composite
  (additive — existing `run_*` functions unchanged)
- `app/harness/taskgraph_runner.py` — `failure_policy: repair`
  dispatch inside `_run_one_stage` (existing dispatch at the runner
  body unchanged)
- `app/memory/_schema.py` + `_store.py` — schema_version 2 → 3 +
  `_migrate()` helper + new fields `enable_semantic_verifier` +
  `max_auto_repairs` + corresponding mutators
- `app/storage/run_store.py` — `semantic_verify.json` +
  `repairs.jsonl` artifacts
- `app/schemas/trace.py` — finally consumes the reserved
  `REPAIR_TRIGGERED` + `SEMANTIC_MISMATCH` enum values
- `app/cli.py` — `localflow verify-semantic`, `localflow repair`,
  `localflow execute --no-auto-repair`, memory toggles for the two
  new prefs
- `app/ui/pages/2_Execute.py` — semantic verdict panel after the
  structural badge (only renders when verifier ran)
- `app/ui/pages/4_Memory.py` — new "🔁 Semantic + Repair" tab with
  toggle + slider
- `app/ui/_i18n.py` — EN + 中文 keys for the new surfaces
- `app/eval/runner.py` — `enable_auto_repair` + `max_auto_repairs`
  kwargs; eval runner can be driven through `run_with_auto_repair`
- `app/cli.py eval run` — `--enable-repair` + `--compare-repair` +
  `--max-auto-repairs` flags; comparison mode renders a side-by-side
  markdown table
- 5 new test modules, 30 new tests (465 → 495 total)
- `docs/SEMANTIC_VERIFIER.md`

**Kernel touch**: NO. `app/harness/{executor,verifier,rollback}.py`
remain byte-identical. `control_loop.py` gains one composite +
`taskgraph_runner.py` gains one branch — both additive. `trace.py`
adds one helper (`emit_repair_triggered`). **22nd** zero-kernel-touch
phase.

**§10.7 invariant**: `git diff app/harness/executor.py
app/harness/verifier.py app/harness/rollback.py` = empty.

---

## Phase 11 — Plan Refinement Loop + Data-Aware Routing (v0.12.0)

**Trigger**: a real-world v0.11 UI run exposed two coupled failures —
the agent meta-skill received an Excel file with NO content preview
(only filename + hash) and so produced a meta-description of the
workspace + a file-type bar chart instead of analyzing the data. The
report literally confessed `"未能直接读取表格单元格内容"`. And the
harness had no in-loop fix-up surface — the only recovery options
were "rollback + retype goal" or "live with the wrong plan".

**Goal**: turn the bug into a demonstration of the harness's
correction capability. Two tracks:

**Track A — Data-aware routing + Excel preview + pie/line**:
- `app/ui/_autodetect.py`: when the goal mentions analysis verbs
  (分析/解读/统计/analyze/interpret/aggregate/...) AND the workspace
  contains a tabular file, route to `data_analyzer` instead of the
  agent meta-skill. `data_analyzer` reads cells via pandas.
- `app/tools/data_ops.py` adds `extract_tabular_preview()` —
  `file_scan` now extracts the first ~10 rows of every .xlsx/.csv
  as a markdown table into `FileMeta.text_preview`. The LLM sees
  real data when reasoning about a spreadsheet.
- `app/tools/chart_ops.py` gains `pie_png` + `line_png`. The
  `ChartRequest.kind` literal extends to include `pie`.
  `data_analyzer/planner.py` picks `pie` for ≤6-category groupby
  results and `line` for datetime + numeric pairs.

**Track B — Plan refinement loop**:
- `Skill.revise(task, snapshot, prior_plan, hint)` default in
  `app/skills/_base.py` — delegates to `plan_with_llm` with
  `prior_plan_actions` + `user_hint` threaded through. The agent
  planner synthesizes a "your previous plan was X; the user said Y;
  please re-plan" user message before the first LLM turn — reuses the
  same single-call codepath as a fresh plan, no new state machine.
- `RunStore` gains `plans/plan_v<n>.json` versioning + `revisions.jsonl`
  audit log. `plan.json` always mirrors the latest version so
  executor / verifier / rollback are oblivious.
- `control_loop.run_revise()` is the orchestration entry point —
  caps at `MAX_REVISIONS = 5`, validates the revised plan, emits one
  `TraceEventType.PLAN_REVISED` event.
- `localflow revise --task-id <id> --hint "..."` CLI surface.
- UI: Plan page renders a refine expander below the plan summary
  with EN + 中文 i18n. Click → text-area → `重新规划` button →
  Streamlit re-render with the new plan. Iterate up to 5 times.

**Files**: NEW `app/harness/control_loop.run_revise` + 5 test modules
+ `docs/REFINE.md`. EXTENDED `app/agent/{planner,analysis_planner}.py`,
`app/skills/_base.py`, `app/skills/data_analyzer/planner.py`,
`app/storage/run_store.py`, `app/schemas/{trace,analysis}.py`,
`app/tools/{chart_ops,data_ops,file_scan}.py`, `app/ui/_autodetect.py`,
`app/ui/pages/1_Plan.py`, `app/ui/_i18n.py`, `app/cli.py`.

**Tests**: 35 new (430 → 465).

**Kernel touch**: NO. `control_loop.run_revise` is a new function
that sits next to existing `run_*` orchestrators; it calls into
existing kernel modules but does not mutate their behaviour. The
executor / verifier / rollback never see "plan versions" because they
read `plan.json` which mirrors the latest. **21st** zero-kernel-touch
phase.

---

## Phase 10 — TaskGraph / Multi-Stage Execution (v0.11.0)

**Goal**: give compound goals a deterministic alternative to the v0.9
`agent` meta-skill. The user's original "整理然后画图" goal can now be
solved two ways:

- **agent** (v0.9): one LLM call, one ActionPlan — flexible but
  every step is at the model's mercy.
- **TaskGraph** (v0.11): a static YAML graph of skill invocations —
  zero LLM cost, byte-deterministic, per-stage failure policy.

Both paths coexist; users pick the right tool. The eval suite from
v0.10 will quantify which one wins on which workload.

**Shipped**:

- **TaskGraph schemas** ([app/schemas/taskgraph.py](localflow/app/schemas/taskgraph.py))
  — `StageSpec` / `TaskGraph` / `StageResult` / `TaskGraphResult` +
  `StageFailurePolicy` (abort / continue / skip) +
  `StageStatus` (passed / failed / skipped / aborted). Phase 12's
  REPAIR policy + `max_retries` semantics are reserved (schema field
  exists, runner ignores them for now).
- **RunStore extension** ([app/storage/run_store.py](localflow/app/storage/run_store.py))
  — `stage_dir(stage_id)` + `stages_root` + `taskgraph_path` /
  `taskgraph_result_path`. One-line addition per property, no
  rewrites of existing path helpers.
- **TraceLogger.stage()** ([app/harness/trace.py](localflow/app/harness/trace.py))
  — `with trace.stage("s1_organize"):` decorates every event emitted
  inside the block with `stage_id="s1_organize"`. Implemented via
  `contextvars.ContextVar` — single-threaded today, future-proof for
  parallel stages. Existing emission sites unchanged.
- **TaskGraphRunner** ([app/harness/taskgraph_runner.py](localflow/app/harness/taskgraph_runner.py))
  — walks stages sequentially through the standard
  `control_loop.run_*` pipeline. Per-stage `StageRunStore` (a thin
  RunStore subclass) redirects each stage's artifacts under
  `<run_dir>/stages/<stage_id>/`. One graph-level `trace.jsonl` +
  one aggregated `rollback_manifest.json` at the top.
  action_ids are stage-prefixed (`s1.a-001` etc.) to keep them
  unique across the merged manifest.
- **EvalTask.stages** ([app/eval/schema.py](localflow/app/eval/schema.py))
  — optional field. When set, `run_eval()` dispatches to the
  TaskGraph path; when absent, single-skill behaviour from v0.10
  unchanged. The multi-stage runner synthesises an aggregated
  ActionPlan from every stage's plan.json so existing graders
  (which read `ctx.plan.actions`) see the union of all stages'
  actions.
- **CLI** ([app/cli.py](localflow/app/cli.py)) —
  `localflow taskgraph describe <yaml>` (preview) +
  `localflow taskgraph run <yaml> [--workspace ...] [--yes]`
  (single approval ceremony on the graph spec; per-stage plans
  generated just-in-time; rollback via existing
  `localflow rollback --run-id <id>`).
- **`task_007_organize_then_chart`** ([evals/workspace_pack/](localflow/evals/workspace_pack/task_007_organize_then_chart.yaml))
  — first multi-stage starter task. Stage 1: folder_organizer.
  Stage 2: workspace_visualizer. The v0.9-original compound goal,
  solved deterministically. Eval suite now 7/7.

**Files**: 1 new schema, 1 new runner, 1 new CLI subcommand group,
1 new starter task YAML, 1 new doc (`docs/TASKGRAPH.md`),
4 new test files (`test_taskgraph_schema.py` +
`test_taskgraph_runner.py` + `test_eval_multi_stage.py` +
extensions to `test_trace_schema.py` + `test_cli_trace.py`).
`pyproject.toml` 0.10.1 → 0.11.0.

**Tests**: 402 → 430 (+28: 11 schema + 9 runner + 3 multi-stage eval
+ 3 trace stage-ctx + 2 CLI taskgraph). Lint + format clean.

**§10.7**: NO kernel-BEHAVIOUR changes. The TaskGraphRunner is a new
file SIBLING to control_loop.py — it composes the existing
`control_loop.run_*` functions, does not modify them. `TraceLogger`
gains the `stage()` context manager (additive — events without a
contextual stage still emit `stage_id=None` exactly as before). All
6 v0.10.1 eval tasks (no `stages` field) continue to pass via the
single-skill path unchanged. **20th** consecutive zero-kernel-behaviour
phase.

**Behaviour change visible to users**: `<run_dir>/` now optionally
contains a `stages/` subdirectory + `taskgraph.json` +
`taskgraph_result.json` when the run was driven by a TaskGraph.
Existing single-skill runs are byte-identical to v0.10.1.

**Worked example** — the user's v0.9 complaint goal handled both
ways:

```powershell
# Path 1 — LLM (existing v0.9):
localflow plan ./workspace --goal "organize then chart" --skill agent --planner llm

# Path 2 — static composition (new v0.11):
localflow taskgraph run my_graph.yaml --yes
#   where my_graph.yaml = folder_organizer → workspace_visualizer
```

Both produce a valid run_dir + trace + rollback manifest. The user
picks based on whether the goal is novel (agent) or repeatable
(TaskGraph).

---

## Phase 9.1 — Trace coverage for CLI + MCP, expanded eval suite (v0.10.1)

**Goal**: close the two gaps the v0.10.0 verification guide called out
honestly — regular CLI commands and MCP tools didn't emit trace
(only `localflow eval run` did) — and grow the starter eval suite
from 3 → 6 tasks so the failure-mode coverage isn't a token sample.

**Shipped**:

- **CLI trace wiring** ([app/cli.py](localflow/app/cli.py)) — `plan`,
  `dry-run`, `execute`, `rollback` commands construct a TraceLogger
  per run and thread it through every `control_loop.run_*` call +
  the Executor / Rollback constructors + the LLM streaming planner
  path. Effect: every `localflow plan/execute` now produces a
  trace.jsonl alongside the existing artifacts.
- **MCP trace wiring** ([app/mcp/tools.py](localflow/app/mcp/tools.py))
  — `create_plan`, `dry_run`, `execute_plan`, `rollback_run` handlers
  construct a TraceLogger per call. MCP clients get the same
  observability surface CLI users get.
- **3 new eval tasks** ([evals/workspace_pack/](localflow/evals/workspace_pack/)):
  - `task_004_forbidden_action_blocked` — regression pin that
    folder_organizer never emits delete/overwrite/shell actions
    even when the plan asks for them implicitly
  - `task_005_empty_workspace` — edge case: zero-action plan walks
    the full lifecycle cleanly (rollback no-op, every grader
    trivially passes)
  - `task_006_duplicate_files_reported` — two byte-identical files
    → both moved + duplicates_report.md emitted, no delete ever
    attempted (pins the "report duplicates, never delete" rule)
- **5 new tests**: 3 task-coverage tests + 2 CLI trace tests
  (`test_cli_plan_emits_trace_jsonl` /
  `test_cli_execute_emits_action_and_verifier_events`).

**Files**: `app/cli.py`, `app/mcp/tools.py`,
3 new YAMLs under `evals/workspace_pack/`,
`tests/test_cli_trace.py` (new), `tests/test_eval_runner.py` (updated
to expect 6 tasks). `pyproject.toml` 0.10.0 → 0.10.1.

**Tests**: 397 → 402. Lint + format clean.

**§10.7**: NO kernel-behaviour changes. The Phase 9 additive-only
TraceLogger-kwarg pattern is exactly the surface I extended here
— still 19th consecutive zero-kernel-behaviour phase. The
`test_executor_with_trace_none_writes_no_trace_file` invariant test
still passes (library callers that don't pass trace still see no
file).

**Behaviour change visible to users**: regular CLI runs now create a
`trace.jsonl` in `<run_dir>/`. This isn't breaking — the file is
observation-only, ignored by everything except the eval graders.
Users who want the v0.10.0 behaviour (no trace.jsonl on normal runs)
can delete the file post-execute or in CI cleanup hooks.

---

## Phase 9 — Trace + Eval Harness (v0.10.0)

**Goal**: address the user's experiment-report diagnosis that
LocalFlow had reached the Control + Safety + Persistence layers of
the 5-layer Harness model but stalled at structural Verification with
no Improvement Harness at all. The report explicitly recommended
building Trace + Eval **before** anything else — Phases 10–12
(TaskGraph, Workspace Pack Builder, Semantic Verifier + Repair Loop)
all need measurable graders to justify their existence. v0.10.0 lands
that foundation.

**Shipped**:

- **TraceEvent schema** ([app/schemas/trace.py](localflow/app/schemas/trace.py))
  — closed enum of 13 kernel event types + 14 failure types. The
  `FailureType` enum is pinned including the Phase 12 placeholder
  values (`semantic_mismatch`, `summary_not_grounded`, etc.) so the
  histogram code doesn't need a schema bump when Phase 12 starts
  emitting them.
- **TraceLogger** ([app/harness/trace.py](localflow/app/harness/trace.py))
  — sister to AuditLogger; writes `trace.jsonl` via the same atomic
  `JsonlLogger` primitive; reads back into typed `TraceEvent`
  objects; groups by failure_type for histograms.
- **Emission wired at 7 sites** (additive only — kernel behaviour
  unchanged when `trace=None`):
  - `app/agent/planner.py` — LLM_CALL_START / END + LLM_REPAIR + token usage
  - `app/harness/control_loop.py` — POLICY_CHECK + DRY_RUN_RENDERED
  - `app/harness/executor.py` — ACTION_START / END + per-action duration
  - `app/harness/verifier.py` — VERIFIER_CHECK per check with failure_type
  - `app/harness/rollback.py` — ROLLBACK_ENTRY per replayed op, drift surfaced
  - `app/mcp/approval.py` — TOKEN_MINTED / CONSUMED / REJECTED
- **Eval package** ([app/eval/](localflow/app/eval/)):
  - `schema.py` — `EvalTask`, `GraderContext`, `EvalResult`,
    `GraderVerdict`, `WorkspaceFile`
  - `runner.py` — runs one task end-to-end in an isolated workspace
    + isolated RunStore; runs every grader; runs rollback if
    `rollback_restores` grader is present; aggregates failure
    histogram from trace
  - `report.py` — markdown report with batch summary + failure
    histogram + per-task verdicts
  - `graders/structural.py` — 4 starter graders:
    `safety_no_forbidden_path`, `expected_outputs_present`,
    `all_files_accounted_for`, `rollback_restores`
  - Semantic graders deferred to Phase 12 (need LLM-as-judge).
- **CLI**: `localflow eval list <target>` + `localflow eval run
  <target>` — exit code = failed task count.
- **3 starter eval tasks** ([evals/workspace_pack/](localflow/evals/workspace_pack/)):
  - `task_001_basic_organize` — folder_organizer + rule, exercises
    Safety + Operational correctness end-to-end
  - `task_002_compound_chart` — agent skill rule fallback (LLM-free
    so CI stays deterministic)
  - `task_003_forbidden_path_blocked` — pins the policy_guard's
    forbidden_paths behaviour, including `must_pass` filtering so
    the task passes even when one grader sensibly reports a missing
    target (the target's move was correctly blocked).
- **docs/EVAL.md** — task YAML format, grader API, trace schema
  walkthrough, failure taxonomy table, custom-grader cookbook.
- **Hygiene**: clarifying comments in `app/agent/prompts.py` +
  `app/skills/agent/llm_planner.py` explaining why ruff format
  leaves their long lines alone (literal LLM prose, not a
  formatting bug). The report's §13.1 concern about "raw GitHub
  unreadable" was overstated — these lines are intentional, but
  the comments make that obvious to future reviewers.

**Files**: 6 new files in `app/eval/`, `app/schemas/trace.py`,
`app/harness/trace.py`, `evals/workspace_pack/` (3 YAMLs),
`docs/EVAL.md`, plus 4 new test files. Plus additive trace-emission
in 6 existing files. `pyproject.toml` 0.9.1 → 0.10.0.

**Tests**: 368 → 397 (+29: 8 schema + 6 emission + 10 graders + 5
runner). Lint + format clean.

**Live end-to-end check** (3 starter tasks against a fresh sandbox):
all 3 pass with the expected grader verdicts, the report markdown
populates with the path_forbidden histogram from task_003, and every
run's `trace.jsonl` is well-formed.

**§10.7**: zero kernel **behaviour** changes. Every trace-emission
hook accepts an optional `TraceLogger` kwarg defaulting to `None`,
and the kernel produces byte-identical artifacts whether trace is
attached or not (pinned by `test_executor_with_trace_none_writes_no_trace_file`).
The kernel kwarg additions are observation surfaces, not behaviour
surfaces — this is the 18th consecutive zero-kernel-behaviour phase.
(Phase 5 remains the lone behaviour-changing exception.)

**What v0.10.0 does NOT claim**: agent semantic correctness is
unchanged from v0.9.1. This phase builds the **instrumentation** to
measure it. After this release we have:

- A way to say "task_002 passed 4/4 graders, took 78 ms, 0 trace failures"
- A trace stream showing which kernel layer caught each LLM mistake
- A failure-type histogram across the eval suite
- A grader registry external code can extend

This is the foundation Phases 10–12 will measure their work against.
Without it, every later "improvement" would be unfalsifiable.

---

## Phase 8.3.1 — project hygiene + opt-in external skills (v0.9.1)

**Goal**: address review feedback on the v0.9.0 ship — stale doc
sections, eagerly-loaded external skills (security risk), README
still CLI-only after the UI became the primary surface, and the
long-deferred dependency split that the v0.9.0 comment claimed was
out of scope.

**Shipped**:

- **README rewrite** — version refs synced (`Tests: 357 → 359`,
  Architecture box updated to mention `agent` + `workspace_visualizer`,
  `mcp/` description bumped from 15 → 18 tools, sample wheel filename
  `0.6.3 → 0.9.0`, layout's `tests/` line `259 → 359`). Quickstart
  split into two sections: **WebUI Quickstart** (recommended for
  demos, uses `localflow ui-serve`) and **CLI Quickstart** (developer
  path, shows every stage including `dry-run` and `verify`
  explicitly).
- **UI.md cleanup** — removed the v0.8.x "Auto-detect heuristic"
  paragraph that still claimed five-way skill routing, removed the
  Override-panel troubleshooting rows. Replaced with a clean "Single
  skill, no override" section that matches the v0.9.0 code reality.
- **External skill opt-in** ([app/skills/_loader.py](localflow/app/skills/_loader.py)) —
  v0.7.x shipped external skill auto-loading with a stderr warning;
  in practice users ignored the warning. v0.9.1 flips the default:
  `LOCALFLOW_ENABLE_EXTERNAL_SKILLS=1` is now required to load
  anything from `~/.localflow/skills/`. The legacy
  `LOCALFLOW_DISABLE_EXTERNAL_SKILLS=1` kill switch still wins when
  both are set (back-compat with CI scripts). Audit table shows
  "skipped: external skills opt-in required" for missed loads.
- **Dependency split** — pandas / openpyxl / matplotlib / pypdf moved
  out of the base install into `[data]` and `[pdf]` extras
  ([pyproject.toml](localflow/pyproject.toml)). Survey confirmed every
  heavy import in v0.9.0 is already inside a function body, so the
  SkillRegistry registers all skills with zero heavy imports — the
  comment in pyproject claiming "eagerly imports at module-load time"
  was stale by Phase 6.1. chart_ops now raises a friendly
  ImportError pointing at `[data]` when matplotlib is missing.
- **Agent meta-skill integration tests** ([tests/test_agent_integration.py](localflow/tests/test_agent_integration.py)) —
  7 new tests covering the harness's defensive layers against the
  agent's expanded action surface: compound plan executes + verifies
  + rolls back cleanly; path-traversal in target_path blocked by
  policy_guard; duplicate action_ids rejected by plan validator;
  forbidden_paths from memory blocked at risk-check; chart_request
  with zero-value still renders; validator catches a PNG action that
  skipped post-processing; rollback removes both generated chart PNGs
  and index.md files.

**Files modified**: `README.md`, `docs/UI.md`, `docs/SECURITY.md`,
`docs/PHASES.md`, `app/skills/_loader.py`, `app/tools/chart_ops.py`,
`pyproject.toml`, `tests/test_skill_loader.py`. New:
`tests/test_agent_integration.py`.

**Tests**: 359 → 368 (+9: 7 integration tests in
`test_agent_integration.py`; +2 in `test_skill_loader.py` for the
opt-in default and the DISABLE-wins-over-ENABLE precedence).
Lint + format clean.

**§10.7**: NO kernel touches. Doc + loader + dependency refactor only.
**17th** zero-kernel-touch phase.

---

## Phase 8.3 — agent meta-skill (v0.9.0)

**Goal**: close the v0.8.2 design gap where compound goals like
"organize then chart then summarize" still routed to a single
specialist skill that could only do one third of the task. Real-user
feedback: the multi-skill picker + override panel was rated 蠢 ("dumb")
and the user wanted "agent 自行决定 + harness 纠错保证质量".

**Shipped**:

- **New `agent` meta-skill** ([app/skills/agent/](localflow/app/skills/agent/)) —
  the v0.9.0 default. LLM-driven. Allowed actions: every category
  (mkdir / move / rename / copy / index). Required tool:
  `chart_ops.bar_png`. Produces a SINGLE ActionPlan covering every
  step of a compound goal in one cycle.
- **Custom system prompt** ([app/skills/agent/llm_planner.py](localflow/app/skills/agent/llm_planner.py)) —
  extends the folder-organizer prompt with a `chart_request`
  convention: the LLM emits INDEX actions with target_path ending in
  `.png` and metadata.chart_request = `{kind, title, xlabel, counts}`.
  Python renders the PNG via `chart_ops.bar_png` after the LLM call.
  LLM never produces base64 bytes itself, keeping plan.json small +
  human-readable.
- **`render_chart_actions` post-processor**: walks the LLM's plan,
  renders every chart_request to PNG bytes, substitutes
  `binary_content_b64`. Malformed specs degrade to markdown error
  placeholders instead of crashing the plan.
- **Refactor `app/agent/planner.py`**: `plan_with_llm` now accepts a
  `system_prompt` kwarg (default = legacy folder_organizer prompt for
  back-compat). The agent skill passes its own. **Pluggable prompts
  are the only way a new skill can use the existing LLM repair-loop
  infrastructure without forking it.**
- **UI simplification** ([app/ui/_autodetect.py](localflow/app/ui/_autodetect.py)
  + [app/ui/pages/1_Plan.py](localflow/app/ui/pages/1_Plan.py)) —
  auto-detect always returns `agent` + `llm` (or `rule` for empty
  goal). Override expander removed. Capability-gap warning removed
  (no gaps when one skill handles everything). The Plan page is
  now: goal text area + one-line "agent will plan end-to-end" +
  Create plan button.

**Files** (new): `app/skills/agent/` (6 files), `tests/test_agent_skill.py`.
**Files** (modified): `app/agent/planner.py`, `app/skills/__init__.py`,
`app/ui/_autodetect.py`, `app/ui/pages/1_Plan.py`, `app/ui/_i18n.py`,
`tests/test_ui_autodetect.py`, `pyproject.toml` (0.8.2 → 0.9.0).

**Tests**: 359 → 357 net (the v0.8.2 autodetect tests were rewritten
from 27 to 19 to match the new always-agent contract — minus 8;
`test_agent_skill.py` adds 15 — plus 15; minus 9 from removing the
v0.8.2 compound + capability-gap cases that the agent now handles
internally. Lint + format clean.

**§10.7**: NO kernel touches. The `system_prompt` parameter is added
to the agent planner (which lives in `app/agent/`, NOT
`app/harness/`). The harness still sees a generic ActionPlan it knows
how to dry-run + execute + verify + rollback. **16th** zero-kernel-touch
phase.

**Specialist skills stay in the registry**: folder_organizer /
pdf_indexer / data_reporter / data_analyzer / workspace_visualizer
remain available via CLI (`--skill <name>`) and MCP (`create_plan`).
Only the UI defaults to agent — power users keep their precision tools.

**Worked example** — the user's compound goal:

> 将文件按种类整理，然后在各自文件夹下总结文件的信息，最后把各文件夹的文件数以柱状图绘制然后放在图象文件夹下

v0.8.2 routed to `data_reporter + rule` and produced a markdown file
pretending to be a chart. v0.9.0 routes to `agent + llm`; the LLM
decomposes the goal into mkdir + move (organize part) + per-folder
index.md (summarize part) + INDEX action with chart_request (chart
part). The Python post-processor renders the real PNG. ONE plan,
ONE approval, ONE execute.

---

## Phase 8.2 — workspace_visualizer + smart planner upgrades (v0.8.2)

**Goal**: close the v0.8.1 gap where user testing exposed three real
limits — auto-detect routing compound goals to the wrong skill, no
skill drawing real PNG charts of file metadata, and no way to make
LLM the default planner without bypassing the heuristic.

**Shipped**:

- **New skill** `workspace_visualizer` ([app/skills/workspace_visualizer/](localflow/app/skills/workspace_visualizer/)):
  counts files by parent folder (when ≥60% of files live in subdirs)
  or by file_type otherwise, then renders a real PNG bar chart via
  `chart_ops.bar_png` + base64-encoded INDEX action. Mirrors the
  binary-write mechanism `data_analyzer` / `data_reporter` already
  use for column charts. Rule-only — counts and matplotlib have
  nothing for an LLM to add.
- **Compound-goal detection** in [app/ui/_autodetect.py](localflow/app/ui/_autodetect.py):
  goals with 然后/再/最后/then/finally/etc. (or three+ distinct
  action verbs) auto-upgrade the planner to LLM. Rule planners can
  only emit one action category — multi-step goals silently lose
  the rest unless an LLM synthesizes the plan.
- **Capability-gap warning** on the Plan page: when the auto-detected
  skill can't satisfy every part of the goal (e.g. user asks for
  organize+chart and only folder_organizer was picked), surface a
  yellow warning with a suggested second-step skill. The warning
  doesn't block — the user can still run the plan and pick up the
  missing part as a second task.
- **`prefer_llm_planner` memory pref** ([app/memory/_schema.py](localflow/app/memory/_schema.py)):
  new boolean field, defaults to False. When ON, every LLM-capable
  skill uses LLM regardless of goal text. New CLI command
  (`localflow memory set prefer_llm_planner true|false`), new MCP
  tools (`memory_set_prefer_llm_planner`,
  `memory_unset_prefer_llm_planner`), new Memory-page tab
  ("🤖 Planner preference"). Schema bumped 1 → 2.
- **Smarter routing heuristic**: a goal with `organize` keyword in a
  mixed workspace (more non-tabular than tabular files) now routes
  to `folder_organizer` even with a stray xlsx — the user is
  organizing their workspace, not analyzing the spreadsheet.

**Files**: 5 new files in `app/skills/workspace_visualizer/`,
extended `app/ui/_autodetect.py` + `_i18n.py`, modified
`app/memory/_schema.py` + `_store.py`, `app/cli.py`, `app/mcp/tools.py`,
`app/ui/pages/1_Plan.py` + `4_Memory.py` + `_layout.py`,
`app/skills/__init__.py`.

**Tests added**: 40 (`test_workspace_visualizer.py` 14 +
`test_ui_autodetect.py` extensions 17 + `test_memory_store.py`
extensions 6 + `test_cli_memory.py` extensions 5 + `test_mcp_tools.py`
update 2). Total 319 → 359.

**Kernel touch**: NO. The schema_version bump is memory-side, not
kernel-side. `app/harness/*`, `app/schemas/*`, `app/tools/*`
untouched. **15th** zero-kernel-touch phase.

**The full bilingual decision tree** the user wrote that exposed this:

> 将文件按种类整理，然后在各自文件夹下总结文件的信息，最后把各文件夹的文件数以柱状图绘制然后放在图象文件夹下

Before v0.8.2: routed to `data_reporter + rule` because of one
incidental xlsx + "总结" keyword → output was a markdown mermaid
block, not a real chart. After v0.8.2: routes to `folder_organizer +
llm` (organize keyword in mixed workspace + compound 然后 marker),
with a yellow gap warning nudging the user to run
`workspace_visualizer` as a second task for the real PNG chart.

---

## Phase 8.1.1 — Sticky unsafe mode (v0.8.1)

**Bug**: in v0.8.0, after the user opted into unsafe path mode with
``?unsafe=1`` and picked a custom workspace outside `./sandbox/`, the
first page navigation (e.g. clicking "Plan" in the sidebar) silently
reverted the workspace to a sandbox subdir. Custom path was no longer
visible in the Source radio either.

**Root cause**: Streamlit's multi-page navigation **strips URL query
parameters**. ``render_unsafe_banner`` and ``render_sandbox_sidebar``
both read ``?unsafe=1`` fresh from ``st.query_params`` on every
render, so a page change → unsafe=False → Custom-path radio hidden →
sandbox dropdown picker runs → ``SESSION_WORKSPACE_KEY`` overwritten.

**Fix**: new ``_resolve_unsafe()`` helper in ``app/ui/_layout.py``
latches ``unsafe=True`` to ``st.session_state["unsafe_mode_enabled"]``
on first detection. Once a session opts in, the bit sticks through
every page render until the tab is closed. A fresh tab without
``?unsafe=1`` still starts in safe mode — the latch is per-session,
not global.

**Test**: ``test_resolve_unsafe_latches_to_session_state`` in
``tests/test_ui_sandbox.py`` mocks ``st.query_params`` +
``st.session_state`` and verifies all three cases (first-visit latch,
post-nav persistence, fresh-session reset). Total 318 → 319.

**Kernel touch**: NO.

---

## Outline §10.7 final ledger

| Phase | Kernel-touch | Notes |
|-------|--------------|-------|
| 0 | — | the kernel itself |
| 1 | NO | agent layer above |
| 2.1 + 2.2 | NO | new modules under `app/tools/` |
| 2.3 | NO | Skill ABC + registry under `app/skills/` |
| 3.1–3.3 | NO | `_record_implicit_parents` is internal to existing executor path, no API change |
| 4.1 | NO | filesystem skill loader |
| 4.2 | NO | Tool Registry |
| 4.3 | NO | contract test template |
| **5** | **YES (~25 lines)** | `forbidden_paths` — universal safety primitive, kernel-only by design |
| 6.1 | NO | new package `app/mcp/`, 1 CLI command |
| 7.1 (v0.6.3) | NO | doc/format/UX hardening + rollback hash-guard in `app/harness/rollback.py` — extends existing class only, no new kernel primitives |
| **8.0 (v0.7.0)** | NO | new package `app/ui/`, 1 CLI command (`ui-serve`) |
| 8.0.1–8.0.4 (v0.7.1–v0.7.4) | NO | UI bug-fix sequence |
| **8.1 (v0.8.0)** | NO | UI UX overhaul: i18n + autodetect + sidebar rewrite |
| 8.1.1 (v0.8.1) | NO | Sticky unsafe mode (Streamlit page-nav drops query params) |
| **8.2 (v0.8.2)** | NO | workspace_visualizer skill + compound-goal detection + capability-gap warning + prefer_llm_planner memory pref |
| **8.3 (v0.9.0)** | NO | agent meta-skill (LLM-driven, one-shot compound execution) + pluggable system prompts + UI collapse to single skill |
| 8.3.1 (v0.9.1) | NO | Project hygiene — README split, UI.md cleanup, external skill opt-in default, [data]/[pdf] dep extras, agent integration tests |
| **9 (v0.10.0)** | NO (additive only) | Trace + Eval Harness: TraceEvent schema + TraceLogger + emission at 7 kernel sites + `app/eval/` package with 4 structural graders + `localflow eval run/list` CLI + 3 starter eval tasks + docs/EVAL.md |
| 9.1 (v0.10.1) | NO | CLI + MCP commands now wire trace; starter eval suite grew 3 → 6 tasks |
| **10 (v0.11.0)** | NO (additive only) | TaskGraph — multi-stage execution: schemas + runner + `TraceLogger.stage()` ctx + `localflow taskgraph` CLI + `EvalTask.stages` + 1 starter multi-stage eval task |
| **11 (v0.12.0)** | NO (additive only) | Plan Refinement Loop + Data-Aware Routing — `Skill.revise` default + `control_loop.run_revise` + `RunStore` plan versioning + `TraceEventType.PLAN_REVISED` + `localflow revise` CLI + UI refine expander; Excel preview in scanner; pie + line chart kinds; `autodetect_skill` routes analysis goals to `data_analyzer` |
| **13 (v0.13.0)** | NO (additive only) | Semantic Verifier + Auto-Repair Loop — `SemanticVerifier` (new module next to existing structural Verifier) + 3 LLM-as-judge graders (`output_addresses_goal` / `summary_grounded` / `analysis_result_nonempty`) + `run_repair_loop` + `control_loop.run_with_auto_repair` composite + `localflow verify-semantic` / `repair` CLI + `--no-auto-repair` flag + UI Execute-page verdict panel + Memory toggles + `StageFailurePolicy.REPAIR` wired up (uses Phase 10's reserved `max_retries`) + eval `--compare-repair` mode; emits `REPAIR_TRIGGERED` + `SEMANTIC_MISMATCH` (both reserved since Phase 9) |
| **14 (v0.14.0)** | NO | Workspace Pack Builder strong demo — 5-stage `examples/research_pack/workspace_pack.yaml` composing folder_organizer + pdf_indexer + data_analyzer + workspace_visualizer + agent; `examples/research_pack/seed.py` plants the messy seed; 1 new structural grader (`every_input_accounted_for`); 1 new eval task (`task_010_workspace_pack`); 8 new tests (495 → 503); `docs/PACK_BUILDER.md` walkthrough. No new harness primitives — pure composition demo proving v0.10-v0.13 substrate stacks. |
| 14.1 (v0.14.1) | NO | Workspace Pack polish — typed `SourceLedger` + `SourceEntry` schema + `localflow ledger` CLI; folder_organizer's `route_low_confidence_to_review` task pref routes `other`-classified files to `review/` with `review/unresolved_files.md`; new `topic_clusterer` skill (LLM-driven semantic topic grouping into `topics/<topic>/` dirs — distinct from folder_organizer's extension-based categories). +15 tests (503 → 518). |
| **15 (v0.15.0)** | NO | Integration / exposure layer — vision-based `chart_accurate` grader (Anthropic-style multimodal call; graceful skip on no LLM / unsupported provider); MCP `taskgraph_run` + `verify_semantic` + `repair_run` tools so external MCP clients can drive v0.10 TaskGraph + v0.13 semantic verifier + auto-repair; `filter_manifest_to_stage` helper + `localflow rollback --stage <id>` for per-stage rollback; `replay_from_stage` + `localflow taskgraph replay --from-stage` for cross-stage repair; `StageSpec.cross_stage_repair_target` field (declarative hook, currently consumed by the CLI helper). +8 tests (518 → 526). |
| **16 (v0.16.0)** | **YES** (2nd exception) | Ecosystem layer — (1) HMAC-SHA256 skill manifest signing with `LOCALFLOW_REQUIRE_SIGNED_SKILLS` gate + `localflow skills-sig sign/verify` CLI; (2) per-skill LLM tool schema capability scoping — `build_action_plan_tool_schema(allowed_action_types=...)` restricts the `action_type` enum to the calling task's `allowed_actions`, defense-in-depth so the LLM literally can't propose a forbidden action type; (3) **WebCollect skill + new `ActionType.FETCH`** (2nd deliberate §10.7 exception after Phase 5): executor learns HTTPS GET, policy_guard enforces `fetch_allowed_domains` allowlist, `localflow memory allow-domain/disallow-domain` CLI, memory schema v3 → v4; (4) MCP client — `app/mcp/client.py` async stdio probe wrapper + `app/mcp/catalog.py` JSON-backed catalog + `localflow mcp-clients list/add/remove/probe` CLI for registering external MCP servers (filesystem / fetch / search etc.). +15 tests (526 → 541). |
| 16.1 (v0.16.1) | NO | UX + intelligence polish from real-user testing — (1) **UI nav bug fix**: "Continue to Execute" + "Continue to Rollback" buttons now use session-state flag + top-of-render `st.switch_page` so clicks always navigate (the old code had the if-block bypassed when subsequent reruns skipped the post-plan branch); (2) **autodetect display removed** from Plan page (every non-empty goal showed "llm mode" — useless info; routing logic stays internal); (3) **agent system prompt** gained explicit rules for content-driven rename (`重命名` / Chinese filenames from PDF content) + vague data-analysis goal handling; (4) **partial-plan fallback** — when `LLMPlanner.plan` exhausts `max_attempts`, instead of raising `PlannerFailure` it synthesises a degraded ActionPlan from the last attempt (per-action policy_guard salvage) + a `_diagnose()` summary explaining which constraints kept tripping; the user sees the partial in dry-run and decides; (5) **data_analyzer LLM stronger reasoning**: system prompt rewritten with explicit "vague-goal mental checklist" + a self-eval retry that re-calls the LLM with a "your spec produced empty/error — try simpler" hint when the first spec returns no rows. +2 tests (541 → 542). |
| **17 (v0.17.0)** | NO | Recipe / Pack System — `app/recipes/` registry + deterministic router; flagship `recipes/*.yaml` (research / data_report / project_handoff); `localflow pack list/describe/suggest/run` CLI; new `📦 Pack` UI page. Recipes compile to v0.11 TaskGraphs. +36 tests (542 → 578). |
| **18 (v0.18.0)** | NO | Goal Interpreter (`localflow goal "..."` w/ LLM clarifying loop, 3 safety nets) + Capability Primitives (`app/primitives/` typed `extract_content` / `classify_content` + 10-entry catalog); UI Pack page Suggest block upgraded to full interpreter loop. +30 tests (578 → 608). |
| **19 (v0.19.0)** | NO | Deliverable Verifier expansion — 7 recipe-level verifiers (`coverage` / `source_ledger` / `review_queue` / `deliverable_completeness` structural + `summary_grounding` / `chart_data_consistency` / `topic_coherence` LLM-as-judge). New `app/eval/recipe_verifiers/` registry; `pack run` writes `recipe_verification.json` + exits 3 on quality fail (vs 1 for crash). Each failure carries `suggested_hint`. +29 tests (608 → 637). |
| **20 (v0.20.0)** | NO | Flagship packs formalised + product-led README + Phase 19 bug fixes — three deliverable packs each ship with `examples/<pack>/seed.py` + workspace + README + eval task; `route_low_confidence_to_review` auto-propagates when recipe declares `review_queue_verifier`; agent meta-skill now receives `task.expected_outputs` (generates README + SOURCES, not just README); `chart_data_consistency_verifier` scoped to `analysis_charts/` only. +4 tests (642 → 646, after Phase 19 absorbed +5 unrelated). |
| **21 (v0.21.0)** | NO | Recipe Auto-Repair Loop — `app/harness/recipe_repair.py` orchestrates verifier-hint → `plan_with_llm(user_hint=...)` → `replay_from_stage` → re-verify, capped at `repair_policy.max_rounds`; new `TaskGraph.stage_hints` + `RecipeSpec.repair_target_map` schema fields; 6-line `_run_one_stage` edit threads stage_hints into `plan_with_llm`. All 3 flagship recipes ship with `repair_policy.enabled=true, max_rounds=2`. +12 tests (646 → 658). |
| **22 (v0.22.0)** | NO | UI productisation + bilingual substrate — (1) **Lane B2** locale plumbing: `--locale {zh-CN,en-US}` flag on `taskgraph run` + `pack run`, new `app/agent/locale_prompts.py::locale_instruction()` injected into LLM system prompts, `TaskGraph.locale` schema field; (2) **Lane D** 6 bilingual Jinja templates under `app/templates/reports/*.j2` (agent / folder_organizer / pdf_indexer / data_reporter / data_analyzer / workspace_visualizer), each reporter rewired to render the template; (3) **Lane A-copy** UI terminology softened (Skill → 能力 / Capability; Approval Token → 确认授权 / Approve; Verifier → 校验 / Check; Dry-run → 预览 / Preview); (4) **Lane A-home** product landing page (hero + 3 featured pack cards + state-handoff to Pack page); (5) **Lane C-nav** new `🗂️ Workspace` + `📊 Runs` pages, `Memory` → `⚙ Settings` rename, Pack title → `Create Pack`, Plan/Execute/Rollback pushed to `5_*` / `6_*` / `7_*` prefixes. Full `st.navigation` collapse deferred. +23 tests (658 → 681). |
| **23 (v0.23.0)** | **YES** (3rd exception) | **Sandboxed ComputeAction Engine** — third deliberate §10.7 kernel exception after Phase 5 (`forbidden_paths`) and Phase 16 (`FETCH`). (1) **Schemas**: new `app/schemas/compute.py` (`ComputeAction` typed payload + `ComputeInputRef` + `ArtifactSpec` + `SandboxPolicy` + `ComputeOutcome` + `ComputeOutcomeStatus`); new `ActionType.PYTHON_COMPUTE`; new `RollbackOpType.DELETE_SCRATCH_DIR`; 4 new `TraceEventType` members. (2) **Runtime**: `app/tools/scratch.py::ScratchWorkspace` (per-action layout `<home>/scratch/<task>/<action>/{inputs,outputs,script.py,stdout.log,stderr.log}`) + `app/harness/sandbox.py::SandboxRuntime` (subprocess + cwd confinement + 300s timeout cap + env scrub for proxy + AI provider keys; Unix-only `RLIMIT_AS` memory cap). (3) **Kernel dispatch**: `Executor._do_compute` ALWAYS appends `DELETE_SCRATCH_DIR` rollback entry (even on failure); `Rollback._apply` learns the inverse; policy_guard input-only path check; verifier `compute_outcomes_ok` check classified under `FailureType.MISSING_OUTPUT`; CLI dry-run renders `script_summary` + dedicated `## Compute scripts` section. (4) **TaskGraph integration**: `run_taskgraph` accepts `scratch_workspace` + `sandbox_runtime` + `registry` kwargs with sensible defaults; stage-prefixed `DELETE_SCRATCH_DIR` entries land in the aggregated manifest. (5) **End-to-end demo**: `examples/compute_action_pack/workspace/sales_dirty.csv` (50-row deliberately messy CSV) + `tests/test_compute_demo_end_to_end.py` proves the cleaning script flow + bit-for-bit rollback. **Honesty discipline** pinned in `docs/COMPUTE_ACTION.md`: isolation, **not** security sandbox — prevents accidental workspace mutation and casual leakage, not a determined attacker. +25 tests (681 → ~706). |
| **24 (v0.23.0)** | NO | **Recipe capability-first escape hatch** — companion to Phase 23. New `RecipeSpec.allow_compute_action: bool = False` field. When False (default), schema validator refuses any stage declaring `python_compute` in `allowed_actions`, AND `compile_to_taskgraph()` auto-appends `python_compute` to graph-level `forbidden_actions` (belt-and-braces against LLM hallucination). When True, the recipe AUTHOR has opted in — grepping `allow_compute_action: true` lists the entire surface area. Pure schema change; no kernel touch. +6 tests in `test_recipe_schema.py`. |
| **25 (v0.23.x → v0.24-prep)** | NO | **ActionTraceEvent + trace consolidation** (six slices). 25.0 typed `ActionTraceEvent` schema (extends `TraceEvent` with `thought` / `reasoning` / `tool_call_raw` / `observation` / `critic_result` / `schema_version`; `extra='forbid'`). 25.1 executor emits the rich shape — every ACTION_END row now carries the LLM provenance + observation dict. 25.2 `localflow trace show / summary` CLI. 25.3 semantic_verifier emits per-verdict `VERIFIER_CHECK` rows (matching the structural side; eval histograms no longer half-blind). 25.4 `.githooks/pre-push` mirroring CI (terminates "local-pass, CI-red"). 25.5 `RunStore.execution_log_view()` + `audit_view()` read-side filter views over trace.jsonl (physical collapse deferred). 25.6 `repair_loop` reads failed-action observations from trace.jsonl and folds them into the LLM hint. All zero-kernel-touch. +101 tests (681 → 782). |
| **26 (v0.24.0)** | **YES** (4th exception) | **Execute-stage React Loop** — Route B (stage-spine + step-by-step LLM inside execute). 26.0 typed `LoopDecision` (CONTINUE / REPLACE / INSERT / SKIP / ABORT) + `ReactConfig` (`enabled=False` default; `max_drift=3`, `llm_timeout_sec=30`) + 3 new `TraceEventType` (`LOOP_DECISION_REQUESTED` / `DECIDED` / `APPLIED`). 26.1 `run_react_loop` + `react_prompts` + `executor.execute(react_mode=...)` dispatch — every dispatched action still passes through `policy_guard.evaluate_action` and `_run_one`, the loop adds orchestration not new dispatch paths. Three failsafes: drift exhausted (forces CONTINUE), LLM call fails (`fallback_to_batch=True`), policy_guard rejects an LLM-proposed action (FAILED record, loop continues). 26.2 `--react` + `--react-max-drift` CLI flags, `RecipeSpec.enable_react_mode` opt-in, `docs/REACT_LOOP.md` user manual. 26.3 closes the v0.23.0 PYTHON_COMPUTE reachability gap — when `allow_compute_action: true` AND `allow_new_action_types: true`, the loop's tool schema exposes `python_compute` so the LLM can REPLACE/INSERT a compute step the planner never proposed. +55 tests (782 → 837). |
| **27 (v0.25.0)** | NO | **ConfirmationPolicy — 4-tier per-action approval**. 27.0 typed `ConfirmationPolicy` (`NEVER` / `ALWAYS` / `ON_HIGH_RISK` / `ON_WRITE`) + `policy_requires_confirmation` pure helper + `ask_action_approval` interactive prompt. 27.1 executor consults the policy + caller-supplied `action_approver` callback before `_run_one`; rejected action → FAILED record + `POLICY_CHECK` trace row; fail-closed when policy gates an action but no approver wired. 27.2 react loop honours the same gate so LLM-proposed REPLACE/INSERT actions are eligible for the same approval flow. CLI `--confirm-policy {never,always,on_high_risk,on_write}`, Recipe `confirmation_policy` field. Zero kernel touch (approval is application-layer, same tier as dry_run). +25 tests (837 → 862). |
| **28 (v0.26.0)** | NO | **Workspace abstraction — local + injection seam**. 28.0 typed `Workspace` Protocol (`runtime_checkable`) covering reads (exists / stat / sha256 / list_dir / read_text+bytes) and writes (mkdir / move / copy / rename / write_text+bytes / safe_target_rel); `LocalWorkspace` in-process implementation delegating to `app.tools.file_ops` + calling `policy_guard.resolve_inside` before every disk touch. 28.1 `Executor.__init__` accepts optional `workspace=` kwarg (defaults to LocalWorkspace pointed at workspace_root → zero behaviour change for v0.25.x callers); `_do_mkdir` / `_do_move` / `_do_copy` migrated through `self.workspace`. 28.2 completed the migration — `_do_index` (text + binary writes), `_do_fetch` (HTTPS download payload) routed through Workspace; the only remaining direct fs touch in the executor is the OVERWRITE-backup shutil.move to `run_store.backups_dir`, which lives outside the user workspace (not a Workspace concern). `_do_compute` still writes to scratch via `SandboxRuntime` (separate Workspace-like isolation). 28.3 user-facing `docs/WORKSPACE.md` + this ledger row. The seam unblocks Phase 29 DockerWorkspace + the candidate RemoteWorkspace as drop-in implementations. Zero kernel touch. +32 tests (862 → 899; 27 LocalWorkspace contract + 5 executor injection). |
| **29 (v0.27.0)** | NO | **DockerWorkspace — container-isolated backend**. 29.0 typed `DockerWorkspace` + `DockerUnavailable` / `DockerWorkspaceError` exceptions + host-side `_validate_rel_path` mirror of policy_guard's defence + container lifecycle (`docker pull` → `docker run -d --workdir /workspace <image> sh -c "mkdir -p /workspace && sleep infinity"` → `docker exec` for each op → `docker rm -f` on close). Default image: `python:3.12-slim` (~50 MB; coreutils + sh enough for every Workspace method). Two-layer test suite: 18 path-defence + ctor tests run without Docker; 23 container-actual contract tests skip when daemon unreachable, run on CI macOS / Linux / Windows. 29.1 Executor injection demo + 4 integration tests proving the abstraction is genuinely drop-in (same plan + same ExecutionOutcome shape; only the fs mutations land in the container). 29.2 `parse_workspace_spec(spec)` factory + `localflow execute --workspace docker:<image>` CLI flag with try/finally lifecycle so a crashed exec never leaves an orphaned container; `docs/DOCKER_WORKSPACE.md` user manual. Zero kernel touch — DockerWorkspace lives in `app/tools/` and never imports `app/harness/`. Honesty discipline: ~100-300ms per-op latency documented; HTTP agent-server upgrade deferred to a 29.x when the latency actually bites. +47 tests (899 → 923 passed + 27 skipped when no Docker; 41 in test_workspace_docker + 4 in test_executor_docker_workspace + 6 new factory-spec tests in test_workspace_local). |
| **30 (v0.28.0)** | NO | **`localflow_kernel` — distributable kernel package**. 30.0 boundary `grep` audit + `docs/PHASE_30_DESIGN.md`: identified pure-kernel modules (all of `app/schemas/*`; `app/harness/{action_validator,approval,audit,checkpoint,context,dry_run,executor,policy_guard,react_loop,rollback,sandbox,trace,verifier}`; `app/tools/{workspace,docker_workspace,scratch,file_ops,hash_ops}`; `app/storage/{run_store,jsonl_logger}`) vs application-layer (`app/harness/{control_loop,repair_loop,semantic_verifier,recipe_repair,taskgraph_runner}` + everything in `app/{skills,recipes,cli,ui,mcp,eval,memory,primitives,templates,agent}`). 30.1 created `localflow_kernel/` top-level package — facade with submodules `schemas / harness / workspace / storage / llm / react_prompts` re-exporting from the pure-kernel modules + top-level shortcut re-exports for the most-used names; `LLMClient` Protocol + `LLMClientError` + `StructuredResponse` physically MOVED from `app/agent/client.py` to `localflow_kernel/llm.py` (back-compat re-export at `app.agent.client`); `react_prompts` (stdlib-only) MOVED from `app/agent/react_prompts.py` to `localflow_kernel/react_prompts.py` (back-compat re-export); `app/harness/react_loop.py` switched to canonical `localflow_kernel.{llm,react_prompts}` imports; `app/harness/{executor,control_loop}.py` TYPE_CHECKING blocks switched to `localflow_kernel.llm`. PEP 561 `py.typed` marker shipped. 30.2 boundary lint — `tests/test_kernel_boundary.py` AST-walks every kernel-reachable module and asserts zero `from app.{skills,recipes,cli,ui,eval,memory,primitives,templates,mcp}` imports + zero references to the 5 forbidden harness orchestrators; `tests/test_kernel_package.py` end-to-end plan→execute→verify using ONLY `localflow_kernel.*` imports proving the facade is a working harness; `docs/KERNEL_PACKAGE.md` user manual with embed-in-your-own-orchestrator example. Zero kernel touch — `app.harness.*` implementations unchanged, only their import paths shifted to the canonical kernel package; the §10.7 ledger guarantees apply identically. +12 tests (923 → 935; 8 kernel-package e2e + 4 boundary lint). |
| **31 (v0.29.0)** | NO | **RemoteWorkspace — SSH-backed Workspace backend**. 31.0 `docs/PHASE_31_DESIGN.md` weighed SSH vs HTTP-agent-server vs gRPC for the third Workspace implementation; chose SSH for Phase 31 (isomorphic to DockerWorkspace's `docker exec` pattern, zero new deps, ~300 LOC). 31.1 `app/tools/remote_workspace.py` — `RemoteWorkspace` dataclass + `RemoteWorkspaceError`/`RemoteUnavailable` exceptions + host-side `_validate_rel_path` mirror of policy_guard's defence + lifecycle (`start()` probes `ssh -V` then `ssh ... -- mkdir -p <root>` doubles as connectivity test; `close()` is a no-op because the remote dir is user-managed). Default SSH options: `BatchMode=yes` (refuses password prompts so the harness never hangs silently) + `ConnectTimeout=10` + `ServerAliveInterval=30`. Every Workspace method shells one command: `ssh <opts> <host> -- <cmd>`. `parse_workspace_spec` learned `ssh:<host>[:<port>][:<root>]` grammar — right-to-left parse extracts trailing `/<path>` as remote root, trailing integer as port, the rest as ssh-resolvable host (so `~/.ssh/config` aliases work). Two-layer test suite: **44 mock-subprocess unit tests** (all CI matrix legs) exercising path defence + ctor + lifecycle + every method's ssh argv shape; **2 ssh-actual contract tests** with `_skip_no_ssh` marker that probes `ssh -o BatchMode=yes localhost true` (skip on CI workers without local sshd key auth). 31.2 `docs/REMOTE_WORKSPACE.md` user manual + this ledger row + README ASCII art demoted "Remote planned" → "Local + Docker + Remote shipped" (closing CLAUDE.md rule F's "honesty discipline" obligation). Zero kernel touch — `app/tools/` only; the Phase 30.2 kernel boundary lint stays green. +44 tests (935 → 979). |
| **32 (v0.30.0)** | NO | **HTTP agent-server — long-lived Workspace backend**. 32.0 `docs/PHASE_32_DESIGN.md`: chose stdlib `http.server` + `urllib.request` over FastAPI / gRPC to keep zero non-stdlib deps; single-tenant + shared-secret bearer-token auth + base64-wrapped binary payloads; protocol surface = Workspace Protocol 1:1. 32.1 `app/tools/agent_server/{protocol,server}.py` — Pydantic request/response models with `extra='forbid'`; `validate_rel_path` mirrors docker/remote workspace defence; `AgentServer` dataclass with `ThreadingHTTPServer` + `secrets.token_hex(32)` per-process token + `secrets.compare_digest` constant-time check; `python -m app.tools.agent_server.server` entrypoint prints `AGENT_SERVER_PORT/TOKEN/WORKSPACE` for supervised mode. 32.2 `app/tools/agent_server/client.py` — `urllib`-based client returning Pydantic models, wraps non-2xx in `AgentServerError(status, body)`; `app/tools/agent_server_workspace.py` — `AgentServerWorkspace` implements Workspace Protocol by delegating every op to `AgentServerClient`. Three-layer test suite: **34 protocol unit tests** (path defence + Pydantic JSON round-trip + extra-forbid + endpoint table); **15 endpoint e2e tests** (real `ThreadingHTTPServer` on ephemeral port + real client + on-disk assertions, including auth + error mapping); **15 AgentServerWorkspace contract tests** including a full `Executor.execute` roundtrip through the server (proves the abstraction is drop-in for Local/Docker/Remote siblings). 32.3 `docs/AGENT_SERVER.md` user manual + this ledger row + README pointer. Zero kernel touch — `app/tools/` only; kernel boundary lint stays green. +64 tests (979 → 1043). What Phase 32 deliberately defers: Docker/Remote integration (Phase 33 — agent-server inside container + ssh-tunnelled), keep-alive HTTP, TLS / multi-tenancy. |
| **33 (v0.31.0)** | NO | **DockerWorkspace + RemoteWorkspace integration with agent-server**. Wires Phase 32's HTTP daemon into both backends so the perf upgrade is reachable in a real run. 33.0 `docs/PHASE_33_DESIGN.md` weighed three distribution strategies (baked image / `docker cp` / `python -c stdin` bundle) and locked in `sh -c "python3 -c '<bundle>'"` — one round-trip, no files written, works on any image / sshd with python3. 33.1 `app/tools/agent_server/bundle.py` — `build_bundle()` assembles a standalone ~26 KB Python source string by inlining WorkspaceStat + sha256_file + protocol.py + server.py + a fresh `_main()` (the inlined source is stripped of `from app.*` imports + the in-module main block so it runs free of LocalFlow's package layout); `@lru_cache` + `bundle_sha256()` for pinning. DockerWorkspace learns `use_agent_server=True` opt-in: picks a free host port, adds `-p 127.0.0.1:<host>:8765` to docker run, spawns the bundle via `docker exec -i <id> python3 -c <bundle>` with env vars for port/token/workspace/host, reads the three-line `AGENT_SERVER_*` handshake, then routes every Workspace op through an `AgentServerClient`. **Three-tier fallback**: handshake timeout / non-zero token / Python ImportError → warning to stderr + `_agent_client = None` → all Workspace methods fall through to the original docker exec path. 33.2 RemoteWorkspace gets the same opt-in: `ssh -L <local>:127.0.0.1:8765 <host> -- env ... python3 -` streams the bundle via stdin (stays under shell argv limits regardless of sshd config), reads the handshake, opens a tunnelled `AgentServerClient`. close() terminates the ssh process which collapses the tunnel + reaps the remote agent. 13 new bundle tests (assembly + subprocess handshake + token pinning); both Docker + Remote integration paths reuse the existing test suites (no regressions, +13 tests 1043 → 1056). 33.3 docs updated — `docs/DOCKER_WORKSPACE.md` + `docs/REMOTE_WORKSPACE.md` both gain "Phase 33 — agent-server mode" sections; this ledger row + README v0.31.0; CHANGELOG.md entry. Zero kernel touch — `app/tools/` only. Per-op latency under agent-server mode: ~5-20 ms (LAN RTT + JSON parse), vs ~100-300 ms under exec-per-op mode. Honesty discipline: opt-in by default; fallback path tested + documented; no marketing claims about "10x faster" until a measured benchmark lands. |
| **34 (v0.32.0)** | NO | **UI parity with v0.31 CLI surface + CLI papercut fixes** — closes all four findings from the Phase 33 E2E test report (`docs/E2E_TEST_PLAN.md`). 34.0 root `--version` callback sourcing from `localflow_kernel.__version__` (F-1); `trace show` / `trace summary` accept `task_id` as either positional or `--task-id`, resolved via shared `_resolve_task_id` helper that detects + rejects conflict (F-2). 34.1 Plan page learns a `planner` radio (rule / llm) defaulting to the autodetect choice; when `ANTHROPIC_API_KEY` is unset, forces `rule` + shows a blue info block instead of stalling the LLM loader indefinitely (F-4). 34.2 Settings gains a 6th tab "🛰 Workspace backend" with three radios (local / docker / ssh) + conditional inputs (image / host + port + remote root) + live spec preview + validated save through `parse_workspace_spec`; new memory pref `workspace_backend_spec` (schema v4 → v5 migration backfills "local"); sidebar `render_workspace_backend_badge()` shows the active spec on every page (F-3). 6 new MemoryStore unit tests; 3 test fixtures updated to assert schema_version == 5. 34.3 local-observable verification via headed Playwright (visible Chromium, 600ms slow-mo for human-watch): 13 screenshots saved to `docs/test_artifacts/v0.32.0-phase34/` covering tab open / docker select / save / ssh select / host fill / save / sidebar badge update / Plan no-key fallback / reset to local. 34.4 `docs/PHASE_34_DESIGN.md` + this ledger row + CHANGELOG v0.32.0 + README + CLAUDE.md §5 update. Zero kernel touch — `app/cli.py` + `app/ui/*` + `app/memory/*` only; Phase 30.2 kernel boundary lint stays green. +6 tests (1056 → 1062). Phase 34.5 (UI executor wire-up to consume the persisted backend pref) deliberately deferred until benchmark + downstream evidence lands. |

| **35 (v0.33.0)** | NO | **方向收敛 + 止损 — flagship = verifiable LLM-artifact pipeline / verify-as-gate**. A direction-refinement phase (almost zero code) that locks the demo layer onto a concrete, evidence-backed flagship: **literature review with provenance verification** — every claim in a synthesised review must trace to a source fragment, or it's flagged for human review and the artifact is gated (ship-or-rollback). New differentiation #7: **verify-as-gate** (verification as an execution gate + rollback + human approval at key nodes — the market gap vs post-hoc observability dashboards). 35.1 `docs/PROJECT_DIRECTION.md` updated (Tracking Goal → verify-as-gate flagship; Roadmap Bias → Phase 35-37; differentiation list +1); `CLAUDE.md` §5 registers Phase 35-37 + the "做减法" boundaries. 35.2 **UI Workspace-backend decorative-gap honest fix**: the Phase 34.2 backend selector persisted `workspace_backend_spec` but Plan/Execute never consumed it (always LocalWorkspace) — the "saved but ignored" smell. Decision: rather than fake-drive containers inside a fragile Streamlit rerun lifecycle (the flagship is local-only), be honest — new pure `app/ui/_workspace_backend.py::describe_ui_backend()` returns `executes_locally` / `cli_command` / `message`; Execute page shows an `st.info` notice + the exact `localflow execute --workspace <spec>` command for docker/ssh; Settings tab reframed from "fake driver" to "validated spec-builder + CLI bridge". 8 new pure-function tests. 35.3 README.md + README.zh-CN.md TL;DR + research_pack narrative refront the flagship; "organise messy folder" demoted to an explicit *starter example*. 35.4 `docs/PHASE_35_PLAN.md` archived with §11 execution status. Zero kernel touch — `app/ui/*` + docs only; Phase 30.2 kernel boundary lint stays green. +8 tests (1062 → 1070). Locally verified via Playwright (`docs/test_artifacts/v0.33.0/`, 2 screenshots: Settings CLI bridge + Execute honest notice). Phase 36 (flagship vertical: grounding grader → execute gate) is the next step. |

| **36 (v0.34.0)** | NO | **Flagship vertical — verifiable literature review (claim-level grounding gate)**. Makes PROJECT_DIRECTION §7 differentiation #7 (verify-as-gate) concrete + demoable + measurable. 36.0 `docs/PHASE_36_DESIGN.md` — source-grounded reuse map (recipe-verifier registry + `judge()` helper + recipe-repair gate + exit-code-3 all reused; nothing reinvented) + acceptance criteria + §10.7 zero-touch argument. 36.3 (core) `app/eval/grounding/` — `schema.py` (Claim / SourceFragment / ClaimVerdict / GroundingPolicy / GroundingGateResult / ClaimGroundingResult, all `extra='forbid'`, NOT re-exported through `localflow_kernel`) + `engine.py`: deterministic `split_claims` (skips heading/code/table/blockquote/HR + narrow self-referential framing filter), `load_source_fragments`, `ClaimJudge` Protocol with **two implementations** — `LexicalClaimJudge` (deterministic salient-term overlap; single letters kept only when uppercase entity labels; the no-key fallback + CI/eval baseline) and `LLMClaimJudge` (per-claim LLM-as-judge via `app.agent.judge`, the production path), `evaluate_grounding` (gate + planner hint), `ground_review` (orchestration). `app/eval/recipe_verifiers/grounding.py::claim_grounding_verifier` plugs the engine into the recipe gate — no key → lexical, key → LLM; failed verdict → `recipe_verification.json` fail + `pack run` exit 3 + (repair_policy) replay of the synthesise stage. 36.5 evidence bundle — writes `claim_grounding.json` (machine) + `review_queue.md` (human-review queue of ungrounded claims) into the workspace (verification reports, not plan actions; kernel + rollback untouched). 36.1/36.2/36.4 `recipes/literature_review_pack.yaml` (composition of folder_organizer + agent, not a new primitive; auto-discovered by `pack list`/`describe`) + `repair_target_map: {claim_grounding_verifier: s2_synthesize}`; synthesise degrades to SKIPPED without an LLM key → gate skips (honest degrade). 36.6 `examples/literature_review_pack/seed.py --check` — plants 2 deliberate hallucinations (Method C / unnamed transformer); the deterministic lexical gate flags exactly those 2 with **zero false positives**, gate FAILs (3/5 grounded). 36.7 `tests/test_grounding_eval.py` — measures **hallucination recall = 1.0 + grounded false-positive rate = 0.0** against by-construction ground truth (the reproducible number Phase 37 will publish). Zero kernel touch — all in `app/eval/` + `recipes/` + `examples/` + tests; `tests/test_kernel_boundary.py` stays green; no new `ActionType`. Honesty (rule F): the lexical judge is documented as a crude reproducible baseline, not semantic understanding — production grounding uses the LLM judge; both emit the same verdict shape + gate. +23 tests (1070 → 1093; 15 engine + 6 verifier + 2 eval). |

| **37 (v0.35.0)** | NO | **Failure-mode ablation benchmark + public numbers**. Turns the six harness failure modes (`docs/research/FEISHU_HARNESS_ENGINEERING_SUMMARY.md` §11) into a reproducible, deterministic **ablation** (each guard ON vs OFF on a by-construction injected failure) — the empirical backing for README §3's "why a harness" claims. `docs/PHASE_37_DESIGN.md` locks the methodology + honesty caveats (ablation ≠ competitor comparison; deterministic injection ≠ wild-field rate). `app/eval/failure_modes/` — `schema.py` (`FailureModeReport`) + `benchmark.py` (6 scenarios + `run_benchmark` + `render_markdown_table`) + `__main__.py` (`python -m app.eval.failure_modes`). **Four runtime ablations** calling the existing guards as libraries: goal_drift (react loop drift budget via a SKIP-stub LLM — guard-on max_drift=1 abandons 1/3 of the approved plan, guard-off 3/3), false_completion (grounding gate — guard-on FAILs on the planted hallucination, guard-off ships), tool_runaway (policy_guard — guard-on blocks a `..` workspace-escape unconditionally via resolve_inside, guard-off executes), quality_entropy (deliverable_completeness_verifier — guard-on FAILs the missing promised output, guard-off ships incomplete). **Honesty, on the table (rule F)**: context_rot is a declared **gap** (no handoff/checkpoint/resume → fails in BOTH modes), and harness_self is a **process control** (kernel-boundary lint + §10.7 ledger, not a per-task number) — the renderer's headline is "4/4 runtime modes", and a test asserts the table never claims 6/6. Result: guard made the difference on 4/4 runtime modes. Zero kernel touch — `app/eval/` only, uses the kernel as a library; `test_kernel_boundary` green. +9 tests (1106 → 1115). |

| **R1–R7 (v0.36.0–v0.39.0)** | **YES — R7 only** | **Harness-optimization campaign** (full per-round detail: `docs/HARNESS_OPTIMIZATION_LOG.md`). R1 §10.7 ledger-drift fix; R2 README five-layer harness map; R3 grounding-gate ON/OFF ablation (recall 6/6, false-pos 0/12); R4 react loop first real-provider run (provider-aware `--react` + OpenAI strict-mode schema sanitizer — both app-layer); R5 trace digest → repair planner hint; R6/Phase 38 stage-level checkpoint/resume/handoff (`context_rot` gap→mitigated, benchmark 4/4→5/5). **R7 = §10.7 deliberate exception #5**: react loop Reflexion no-progress stall-detector — must abort *inside* `app/harness/react_loop.py` in real time (a sibling module can't stop the loop mid-iteration); user-approved 2026-06-22. R1–R6 zero kernel touch. |

**Score**: 5 deliberate exceptions across 45 deliveries (rows 17–21 backfilled retroactively in v0.22; rows 23 + 26 + R7 carry the §10.7 ledger entries). 40/45 zero-kernel-touch (88.9%).

---

## Deferred (groundwork laid)

- **Phase 5.x** — remaining 3 memory categories from outline §14:
  directory structure pref / report template / common task recipes.
  Schema already has `schema_version` for migration; pattern is
  proven; adding each is a new field + one consumer site.
- **Phase 6.2** — MCP **client** (reverse direction): LocalFlow calls
  external MCP servers (community filesystem, fetch, search, ...) and
  registers their tools into Phase 4.2 Tool Registry. Builds on the
  `mcp>=1.6` SDK Phase 6.1 already pulled in.
- **Phase 6.3** — WebCollect skill (HTTPS GET → workspace markdown).
  Needs new `ActionType.FETCH` (second deliberate kernel touch),
  domain allow-list in memory, robots.txt check, content-type
  handling.
- **Phase 6.x** — browser only-read (browser-use-style), external
  service connector.

Each phase's full design rationale, real-data validation notes, and
lessons-learned were captured during development. The most important
ones are inlined above; the master design doc is
[localflow_agent_harness_outline.md](../localflow_agent_harness_outline.md).
