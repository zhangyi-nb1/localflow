# Phase 30 — Harness kernel as a standalone package

**Status**: design locked 2026-05-26, ready to ship in three slices
**Predecessor**: Phase 29 (`DockerWorkspace`) shipped 2026-05-26
**Tracking goal alignment**: §5 Project Direction "harness kernel → distributable artefact"

---

## 1. Why now

After Phases 25–29 the kernel surface has stabilised:

- `app.schemas.*` — every wire/persistence shape is `extra="forbid"` and version-stable
- `app.harness.executor` + `policy_guard` + `verifier` + `rollback` — the
  plan/dry-run/approval/execute/verify/rollback spine
- `app.harness.react_loop` — step-by-step LLM-mediated decisions (Phase 26)
- `app.harness.approval` — `ConfirmationPolicy` 4-tier (Phase 27)
- `app.tools.workspace` + `app.tools.docker_workspace` — pluggable filesystem (Phase 28+29)

What's missing is the **package boundary**: an outside consumer (CLI script,
ops tool, downstream library) wanting to embed *just the harness* still has
to depend on the whole `app/` tree, which drags in skills, recipes, UI,
eval graders, etc.

The §10.7 ledger has been earning this boundary for months. Time to make
it physical.

---

## 2. Boundary analysis (evidence from `grep`)

### 2.1 Pure kernel (zero application-layer leaks)

```text
app/schemas/                          (all modules)
app/harness/action_validator.py
app/harness/approval.py
app/harness/audit.py
app/harness/checkpoint.py
app/harness/context.py
app/harness/dry_run.py
app/harness/executor.py
app/harness/policy_guard.py
app/harness/rollback.py
app/harness/sandbox.py
app/harness/trace.py
app/harness/verifier.py
app/tools/file_ops.py
app/tools/hash_ops.py
app/tools/scratch.py
app/tools/workspace.py
app/tools/docker_workspace.py
app/storage/jsonl_logger.py
app/storage/run_store.py
```

Each module's `from app.*` imports stay inside this set. Verified by grep
on 2026-05-26.

### 2.2 Kernel-adjacent (one removable leak)

```text
app/harness/react_loop.py
```

Imports `LLMClient`, `LLMClientError` from `app.agent.client` (the Protocol
+ exception are kernel-suitable; the concrete `AnthropicClient` /
`FakeLLMClient` are application-layer) and prompt templates from
`app.agent.react_prompts`. Phase 30.1 moves the Protocol + exception into
the kernel package; prompts stay application-layer, accessed via the
existing `loop_prompt`-style parameters that callers already pass in.

### 2.3 Application layer (intentionally outside the kernel)

```text
app/harness/control_loop.py     # ties skill registry + planner + executor
app/harness/repair_loop.py      # planner repair flow
app/harness/semantic_verifier.py # uses app.eval graders
app/harness/recipe_repair.py    # uses app.eval recipe_verifiers
app/harness/taskgraph_runner.py # uses app.skills
app/agent/*                     # concrete LLM clients (anthropic SDK)
app/skills/*
app/recipes/*
app/cli.py, app/ui/*, app/mcp/*
app/eval/*, app/memory/*, app/primitives/*, app/templates/*
```

These either reference the kernel **or** consume application-layer
fixtures (`Skill`, `MemoryStore`, evaluators). They are downstream of the
kernel and should depend on `localflow_kernel.*`, never the reverse.

---

## 3. Strategy: facade with one targeted move (Option C)

The most disruptive option (physically relocating every kernel module
under `localflow_kernel/`) would touch hundreds of import sites across
tests and tooling. The least disruptive option (pure facade re-exporting
from `app.*`) doesn't actually validate the boundary because the kernel
package still drags in whatever `app.*` drags in.

**Option C — facade + targeted move:**

1. Create `localflow_kernel/` top-level package whose `__init__.py` is the
   stable public API.
2. Re-export the pure-kernel modules from §2.1 via thin submodules
   (`localflow_kernel.schemas`, `localflow_kernel.harness`, etc.) — these
   are documented import points for downstream consumers.
3. **Physically move** the `LLMClient` Protocol, `LLMClientError`, and
   `StructuredResponse` dataclass from `app/agent/client.py` to a new
   `localflow_kernel/llm.py`. Leave a back-compat re-export at
   `app/agent/client.py` so the existing `AnthropicClient` / `FakeLLMClient`
   keep working unchanged.
4. Update `app/harness/react_loop.py` to import from `localflow_kernel.llm`
   (the new canonical location).
5. Add `tests/test_kernel_package.py` — a real end-to-end plan/execute/
   verify run that imports ONLY from `localflow_kernel.*`, proving the
   facade gives a working harness without touching `app.*`.
6. Add `tests/test_kernel_boundary.py` — a static check that walks every
   submodule reachable through `localflow_kernel.*` and asserts none of
   them transitively pull in `app.skills`, `app.recipes`, `app.cli`,
   `app.ui`, `app.eval`, `app.memory`, `app.primitives`, `app.templates`,
   or `app.mcp`. This is the long-term invariant that protects the
   boundary against accidental regressions.

This is enough to give external consumers a single import root
(`localflow_kernel`) without forcing a destructive move across the
codebase. The physical move from `app/` → `localflow_kernel/` can happen
in a later phase if a downstream consumer materialises — the boundary
test already proves it would be a no-op.

### 3.1 What §10.7 says

This phase **does not modify the kernel itself**. It adds a new top-level
package (`localflow_kernel/`) that re-exports existing kernel modules and
introduces one tiny new module (`localflow_kernel.llm`) backed by code
moved verbatim from `app.agent.client`. Both `app.agent.client` and
`app.harness.*` continue to expose identical surfaces.

`ActionType` enum is untouched. `ActionPlan` / `Action` / executor
dispatch table are untouched. No kernel exception added.

Ledger row: **"0 kernel touches"** — joins the 32 other zero-touch
deliveries.

---

## 4. Slice plan

### Phase 30.0 — boundary identification + design doc (this file)

- `grep` audit + analysis above
- design doc + slice plan + ledger row
- **no code changes**

### Phase 30.1 — `localflow_kernel/` facade + LLMClient move

Module layout:

```
localflow_kernel/
├── __init__.py          # version + public API re-exports
├── schemas.py           # re-exports app.schemas public API
├── harness.py           # re-exports app.harness pure-kernel modules
├── workspace.py         # re-exports app.tools.workspace + docker_workspace
├── storage.py           # re-exports app.storage.run_store + jsonl_logger
├── llm.py               # LLMClient Protocol + LLMClientError + StructuredResponse (MOVED here)
└── py.typed             # PEP 561 marker so downstream gets type hints
```

`app/agent/client.py` becomes a thin back-compat shim:

```python
from localflow_kernel.llm import (
    LLMClient,
    LLMClientError,
    StructuredResponse,
)
# concrete clients stay in app/agent/client.py:
class AnthropicClient: ...
class FakeLLMClient: ...
```

`app/harness/react_loop.py` switches its imports to
`from localflow_kernel.llm import LLMClient, LLMClientError`.

Smoke test: full `pytest -q` passes (~923 tests).

### Phase 30.2 — boundary test + user docs

Two new tests:

1. `tests/test_kernel_package.py` — programmatic plan → execute → verify
   round-trip importing only from `localflow_kernel.*`. Catches accidental
   removal of facade exports.
2. `tests/test_kernel_boundary.py` — module graph traversal asserting
   `localflow_kernel.*` does not transitively import any of the disallowed
   application-layer packages.

User-facing docs:

- `docs/KERNEL_PACKAGE.md` — what's in the kernel, what's not, how to
  import from `localflow_kernel`, future PyPI roadmap
- `README.md` snippet — short callout under "Architecture" pointing at
  the new package
- `docs/PHASES.md` — ledger row

### Phase 30 done = green CI + tag candidate v0.28.0

`v0.28.0` candidate cuts when:
- 30.0/30.1/30.2 all committed
- `pytest --tb=no -q` passes locally + CI (mac+linux+win × py 3.11/3.12/3.13)
- `ruff check` + `ruff format --check` clean
- README's "what's in v0.28" snippet says "kernel as standalone package"

---

## 5. Future phases this unlocks

Phase 30 is the prerequisite for several long-deferred directions:

- **Phase 31 (candidate)** — physically relocate kernel modules under
  `localflow_kernel/`, deprecate the back-compat re-exports. Once
  Phase 30.2's boundary test is green, this becomes a mechanical move.
- **Phase 32 (candidate)** — split `localflow_kernel` into its own
  PyPI-publishable distribution. The `py.typed` marker added in 30.1
  is the first signal that we're treating this as a real library.
- **Phase 33+ (speculative)** — let downstream agents (not just LocalFlow's
  CLI) embed the harness: a Rust runtime, a server, a notebook adapter.
  All of these become tractable once kernel is a stable, importable
  unit.

Per CLAUDE.md §C, none of these are committed to until they become the
next logical step backed by evidence.
