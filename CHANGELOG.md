# Changelog

This is the user-facing release log. For the full per-phase audit trail
(every kernel touch, every test count, every honesty caveat), see
[`docs/PHASES.md`](docs/PHASES.md).

The §10.7 ledger is the project's identity contract. As of v0.30.0 it
reads **4 deliberate kernel exceptions across 39 deliveries, 35
zero-kernel-touch**. This file does not duplicate that ledger — it
summarises what each release means for downstream consumers.

The project follows informal [SemVer](https://semver.org/) at the
0.x level: minor bumps may break consumer-visible surfaces; patch
bumps never do.

---

## [Unreleased]

Phase 33 candidate — DockerWorkspace + RemoteWorkspace integration with
the agent-server shipped in v0.30.0. Performance target: ~10× per-op
throughput on hot paths.

---

## [0.30.0] — 2026-05-28

**Phase 32 — HTTP agent-server + AgentServerWorkspace**

The fourth Workspace Protocol backend. A long-lived HTTP daemon
answers Workspace ops over a single connection, dropping per-op
latency from `docker exec` / `ssh` levels (~100-300 ms) toward
network RTT (~1-5 ms localhost / 10-50 ms LAN).

Phase 32 ships the building blocks (protocol + server + client +
`AgentServerWorkspace` adapter); wiring them into the existing
Docker / Remote backends is Phase 33 candidate.

- New: `app.tools.agent_server` package — stdlib `http.server`-backed
  `AgentServer`, urllib-backed `AgentServerClient`, Pydantic wire models
- New: `app.tools.agent_server_workspace.AgentServerWorkspace`
- New: `python -m app.tools.agent_server.server` entrypoint for
  supervised deployments
- Auth: 256-bit shared-secret bearer token, `secrets.compare_digest`
  constant-time check
- Zero new third-party deps; zero kernel touches
- +64 tests (979 → 1043); 5-platform CI green

Docs: [`docs/PHASE_32_DESIGN.md`](docs/PHASE_32_DESIGN.md),
[`docs/AGENT_SERVER.md`](docs/AGENT_SERVER.md)

---

## [0.29.0] — 2026-05-27

**Phase 31 — RemoteWorkspace via SSH**

The third Workspace Protocol backend, isomorphic to DockerWorkspace's
`docker exec` pattern: every op shells out one `ssh <host> -- <cmd>`.
Closes the Phase 28 "Phase 30 candidate is RemoteWorkspace" comment
and the README's "Remote planned" footnote.

- New: `app.tools.remote_workspace.RemoteWorkspace`
- New: `ssh:<host>[:<port>][:<root>]` workspace spec grammar
- Hard rules: no password auth ever (`BatchMode=yes`); no
  `StrictHostKeyChecking=no`; remote dir is user-managed
- Test layers: 44 mock-subprocess unit (all CI matrix legs) + 2
  ssh-actual integration (skipif `ssh -o BatchMode=yes localhost true`
  unreachable)
- Zero kernel touches
- +44 tests (935 → 979)

Docs: [`docs/PHASE_31_DESIGN.md`](docs/PHASE_31_DESIGN.md),
[`docs/REMOTE_WORKSPACE.md`](docs/REMOTE_WORKSPACE.md)

---

## [0.28.0] — 2026-05-27

**Phase 30 — `localflow_kernel` distributable package**

The harness kernel becomes a first-class importable surface. Downstream
consumers can embed plan/dry-run/approval/execute/verify/rollback
without pulling in LocalFlow's CLI, UI, skills, recipes, eval graders,
or MCP server.

- New: `localflow_kernel/` top-level package (facade + submodules
  `schemas / harness / workspace / storage / llm / react_prompts`)
- Physical move: `LLMClient` Protocol + `react_prompts` relocated to
  the kernel package; back-compat re-exports at `app.agent.client` +
  `app.agent.react_prompts`
- AST-static **kernel boundary lint** (`tests/test_kernel_boundary.py`)
  asserts no kernel module imports application-layer packages
- PEP 561 `py.typed` marker — kernel ships typed for downstream
  consumers
- `pyproject.toml` wheel now ships both `app` and `localflow_kernel`
  packages
- Zero kernel touches (only import paths shifted)
- +12 tests (923 → 935)

Docs: [`docs/PHASE_30_DESIGN.md`](docs/PHASE_30_DESIGN.md),
[`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md)

---

## [0.27.0] — 2026-05-26

**Phase 29 — DockerWorkspace container-isolated backend**

The second Workspace Protocol backend. Plans execute inside a
container; the user's host filesystem is untouched.

- New: `app.tools.docker_workspace.DockerWorkspace` (default image
  `python:3.12-slim`)
- New: `docker:<image>` workspace spec; CLI `--workspace docker:...`
- Lifecycle: `docker pull` → `docker run -d` → `docker exec` per op
  → `docker rm -f` on close
- Two-layer test suite: 18 path-defence + ctor (no Docker) + 23
  container-actual (skipif daemon unreachable / Windows containers
  mode)
- Honesty: ~100-300 ms per-op latency documented; HTTP agent-server
  upgrade deferred to Phase 32 (shipped 2026-05-28)
- Zero kernel touches
- +47 tests

Docs: [`docs/DOCKER_WORKSPACE.md`](docs/DOCKER_WORKSPACE.md)

---

## [0.26.0] — 2026-05-25

**Phase 28 — Workspace abstraction**

The first cut of the `Workspace` Protocol — every kernel write goes
through the facade so the underlying filesystem becomes pluggable.

- New: `app.tools.workspace.Workspace` `runtime_checkable` Protocol
- New: `LocalWorkspace` in-process implementation (delegates to
  `app.tools.file_ops` + `policy_guard.resolve_inside`)
- `Executor.__init__` accepts optional `workspace=` kwarg (default =
  LocalWorkspace pointed at workspace_root → zero behaviour change
  for v0.25.x callers)
- Migrations: `_do_mkdir` / `_do_move` / `_do_copy` / `_do_index` /
  `_do_fetch` routed through `self.workspace`
- Unblocks: DockerWorkspace (v0.27.0), RemoteWorkspace (v0.29.0),
  AgentServerWorkspace (v0.30.0)
- Zero kernel touches
- +32 tests

Docs: [`docs/WORKSPACE.md`](docs/WORKSPACE.md)

---

## [0.25.0] — 2026-05-24

**Phase 27 — ConfirmationPolicy 4-tier per-action approval**

Plan-level "approve all" is no longer the only granularity. Each
action can require its own confirmation per the configured policy.

- New: `ConfirmationPolicy` enum — `NEVER` / `ALWAYS` /
  `ON_HIGH_RISK` / `ON_WRITE`
- New: `policy_requires_confirmation` pure helper +
  `ask_action_approval` interactive prompt
- Executor consults policy + caller-supplied `action_approver`
  callback before `_run_one`
- React loop honours the same gate so LLM-proposed REPLACE / INSERT
  actions are eligible for the same approval flow
- CLI: `--confirm-policy {never,always,on_high_risk,on_write}`
- Recipe: new `confirmation_policy` field
- Zero kernel touches
- +25 tests

Docs: [`docs/CONFIRMATION_POLICY.md`](docs/CONFIRMATION_POLICY.md)

---

## [0.24.0] — 2026-05-24

**Phase 26 — Execute-stage React Loop (4th kernel exception)**

Route B: keep the plan/dry-run/approval/verify/rollback spine; let
the LLM make per-action decisions inside the execute stage.

- New `ActionType` mechanics — the LLM can decide between five
  shapes: `CONTINUE` / `REPLACE` / `INSERT` / `SKIP` / `ABORT`
- Drift budget bounded (`ReactConfig.max_drift=3` by default)
- Three failsafes: drift exhausted forces `CONTINUE`; LLM call fails
  → `fallback_to_batch=True`; policy_guard rejects LLM-proposed
  action → FAILED record, loop continues
- New trace events: `LOOP_DECISION_REQUESTED` / `LOOP_DECIDED` /
  `LOOP_DECISION_APPLIED`
- CLI: `--react` + `--react-max-drift`
- Recipe: `enable_react_mode` opt-in
- **Kernel exception**: react_mode kwarg threaded through executor +
  `app.harness.react_loop.py` is a new kernel module
- +55 tests

Docs: [`docs/REACT_LOOP.md`](docs/REACT_LOOP.md)

---

## [0.23.0] — 2026-05-24

**Phase 23 — Sandboxed ComputeAction (3rd kernel exception)**

New `ActionType.PYTHON_COMPUTE` — the LLM can author a Python script
and the harness runs it under a scratch workspace with subprocess
confinement.

- New schemas: `ComputeAction` typed payload + `ComputeInputRef` +
  `ArtifactSpec` + `SandboxPolicy` + `ComputeOutcome`
- New runtime: `app.tools.scratch.ScratchWorkspace` (per-action layout)
  + `app.harness.sandbox.SandboxRuntime` (subprocess + cwd confinement
  + 300s timeout + env scrub + Unix-only `RLIMIT_AS` memory cap)
- New rollback: `RollbackOpType.DELETE_SCRATCH_DIR` — ALWAYS appended
  even on failure
- New trace events (4 members)
- Recipe escape hatch: `RecipeSpec.allow_compute_action = False` by
  default; explicit opt-in required
- **Kernel exception**: new `ActionType` enum member + executor
  `_do_compute` + policy_guard input-only path check + verifier
  `compute_outcomes_ok` check
- **Honesty discipline**: "isolation, not security sandbox" — prevents
  accidental workspace mutation + casual leakage, not a determined
  attacker
- +25 tests

Docs: [`docs/COMPUTE_ACTION.md`](docs/COMPUTE_ACTION.md)

---

## Pre-0.23 history

Phases 1–22 shipped 2024-Q3 → 2026-Q1 and built the LocalFlow
substrate: schemas + harness kernel + UI + skills + recipes + eval +
MCP + memory + bilingual templates. See [`docs/PHASES.md`](docs/PHASES.md)
rows 5 / 8.x / 9 / 10 / 11 / 13 / 14 / 15 / 16 / 17 / 18 / 19 / 20 / 21 / 22.

Notable kernel exceptions before Phase 23:
- **Phase 5** — `forbidden_paths` (1st §10.7 exception, universal
  safety primitive)
- **Phase 16** — `ActionType.FETCH` + WebCollect skill (2nd §10.7
  exception)

For the older user-facing tag history (v0.6.x → v0.22.x) see
`git tag -l` and the PHASES.md ledger.
