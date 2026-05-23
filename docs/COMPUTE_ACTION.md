# ComputeAction — Isolation, not security sandbox

> **One paragraph honesty discipline:** `PYTHON_COMPUTE` runs untrusted
> model-authored Python in an *isolated scratch workspace*, not a security
> sandbox. The isolation primitives (cwd confinement, env scrub, wall-clock
> timeout, declared-output verification) prevent **accidental** workspace
> mutation and **casual** information leakage. They do **not** stop a
> determined attacker. Treat every ComputeAction as code you would run with
> your own privileges — because that is exactly what happens. If you need
> hard isolation, run LocalFlow inside Docker / a VM / a firewall-segregated
> account. Phase 23.0 does not ship that.

This document is the contract between the planner, the executor, the
verifier, the rollback engine, and the human approver.

---

## 1. Why this exists

Through Phase 22, LocalFlow's intelligence ceiling was capped by its eight
typed actions (MKDIR, MOVE, RENAME, COPY, INDEX, SUMMARIZE, CONVERT,
FETCH). Any task that needed to *transform* file content (clean a messy
CSV, plot a chart, derive a statistic) had to be hard-coded as a new skill.
That does not scale.

`ActionType.PYTHON_COMPUTE` is the third deliberate §10.7 kernel exception
(after Phase 5 `forbidden_paths` and Phase 16 `FETCH`). It lets the
planner propose a single Python script, which the executor runs inside a
per-action scratch directory and surfaces declared outputs to the next
stage. The user workspace stays bound by the eight iron rules.

---

## 2. Ten design principles

Every change to ComputeAction handling must preserve all ten:

1. **Outputs land in scratch, never in the workspace.** A pack stage
   (MOVE / COPY) is required to promote artefacts.
2. **No workspace mutation, ever.** ComputeAction is *not* in
   `WRITE_ACTIONS`. The workspace before-hash and after-hash must match
   bit-for-bit.
3. **Declared inputs only.** The script sees `inputs/<rel_path>` for
   every `ComputeInputRef` declared up-front. Nothing else from the
   workspace is reachable.
4. **Declared outputs only.** The verifier matches files in
   `outputs/` against `ArtifactSpec.relative_path`. Undeclared files
   are dropped, not promoted.
5. **Approval is mandatory.** `ComputeAction.requires_approval` defaults
   to True and the executor will not run an unapproved plan.
6. **Wall-clock timeout.** `SandboxPolicy.timeout_sec` caps at 300s.
   Hitting it raises `ComputeOutcomeStatus.SANDBOX_TIMEOUT` and emits a
   dedicated trace event.
7. **Env scrub.** Proxy variables (`HTTP_PROXY`, etc.) and known
   credential variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, AWS,
   Azure, Google, Gemini, HuggingFace) are stripped before spawn.
   `LOCALFLOW_COMPUTE_NETWORK=off` is injected as a *hint* (not an
   enforcement) to scripts.
8. **Network isolation is best-effort.** No iptables, no Job Objects,
   no namespaces. A script that opens `urllib.request.urlopen` will
   still reach the host network — env scrub only removes proxy config.
   If you need real isolation, use the host firewall.
9. **Rollback always cleans up.** Every ComputeAction emits a
   `DELETE_SCRATCH_DIR` rollback entry, even when the outcome is a
   failure. Rollback wipes `<home>/scratch/<task_id>/<action_id>/`.
10. **Scratch lives outside the workspace.** `<home>/scratch/...` is
    deliberately separate from `<workspace>/.localflow/` so a worker
    script that gets confused about its cwd cannot accidentally write
    into the user's data.

---

## 3. On-disk layout

For one action with `action_id=a-001` under task `t-abc`:

```
<localflow_home>/scratch/t-abc/a-001/
├── inputs/                # copied from workspace by ScratchWorkspace.copy_inputs
│   └── sub/dir/file.csv
├── outputs/               # ArtifactSpec target dir; script writes here
│   └── cleaned.csv
├── script.py              # the ComputeAction.script payload
├── stdout.log             # captured stdout (full, see truncation below)
└── stderr.log             # captured stderr
```

`scratch/` is created on-demand and survives until either the next run
of the same `(task_id, action_id)` or until rollback wipes it.

---

## 4. Sandbox primitives (what they really do)

### 4.1 cwd confinement
`subprocess.run(..., cwd=str(layout.root))`. The child process starts
with `cwd = <scratch>/<task>/<action>/`. The workspace is **not** on
`sys.path`; bare imports like `import secret_module` will not find
workspace files.

### 4.2 Timeout
`subprocess.run(..., timeout=policy.timeout_sec)`. On expiry the child
is killed (POSIX: `SIGKILL`; Windows: `TerminateProcess`) and
`ComputeOutcomeStatus.SANDBOX_TIMEOUT` is returned. Hard upper bound
300s; defaults to 30s.

### 4.3 Env scrub
The default denylist (see `app/harness/sandbox.py:_DEFAULT_ENV_DENYLIST`)
plus, when `network_isolation == "best_effort"`, anything ending in
`_API_KEY` or `_TOKEN`. Pass an extra list via
`SandboxRuntime(extra_env_denylist=(...))` for project-specific keys.

### 4.4 Memory cap (Unix only)
`resource.setrlimit(RLIMIT_AS, memory_mb * MB)` runs in a `preexec_fn`
on POSIX. Windows Job Objects are deferred to Phase 23.x — on Windows
the field is a documentation hint, not an enforcement.

### 4.5 Artifact verification
After exit, `_collect_artifacts` matches `ArtifactSpec.relative_path`
against scratch output files. Three failure modes:

| Outcome status        | When it fires                                      |
| --------------------- | -------------------------------------------------- |
| `OK`                  | every required artifact present and within caps    |
| `OUTPUT_MISSING`      | a required artifact is absent                      |
| `OUTPUT_OVER_SIZE`    | a produced file exceeds `max_size_bytes`           |

`required=False` artifacts may be absent without flipping the status.

### 4.6 Log truncation
`stdout` and `stderr` are written *in full* to `stdout.log` /
`stderr.log`. The in-memory `ComputeOutcome` carries only the last 8
KiB of each. Approval UIs / verifiers that need more context read the
log files directly.

---

## 5. Trace events

Every ComputeAction emits four trace events along its lifecycle:

| Event type                    | Status   | Payload highlights                       |
| ----------------------------- | -------- | ---------------------------------------- |
| `COMPUTE_ACTION_START`        | `ok`     | `script_summary`, input/output counts    |
| `SANDBOX_TIMEOUT` (on timeout)| `fail`   | `timeout_sec`                            |
| `COMPUTE_ACTION_END`          | `ok/fail`| `outcome_status`, `exit_code`, counts    |
| `COMPUTE_OUTPUT_VERIFIED`     | `ok/fail`| `produced` list, `missing` list          |

Eval graders sum these into per-failure-mode histograms via
`FailureType` attribution.

---

## 6. Rollback contract

Every successful or failed ComputeAction appends one
`RollbackOpType.DELETE_SCRATCH_DIR` entry to the manifest with metadata:

```json
{
  "task_id": "t-abc",
  "action_id": "a-001",
  "outcome": { "status": "ok", "exit_code": 0, ... }
}
```

`Rollback._apply` calls `ScratchWorkspace.cleanup_action(task_id,
action_id)`. The op is idempotent — a second cleanup on a missing dir
is a no-op.

`DELETE_SCRATCH_DIR` deliberately does *not* go through
`resolve_inside` because scratch lives outside the workspace. The
target is identified by `(task_id, action_id)` in metadata, and the
guard is `ScratchWorkspace.action_dir(...)` always resolving under its
own root.

---

## 7. What this is NOT

- Not a security sandbox. See §1.
- Not a long-running compute substrate. 300s wall-clock cap is hard.
- Not a multi-script primitive. One script per action; chain via
  TaskGraph stages.
- Not a workspace writer. Pack stage promotes artefacts.
- Not a network blocker. Env scrub is necessary but not sufficient.
- Not the Recipe escape hatch by default. `Recipe.allow_compute_action`
  defaults to False in Phase 24.

---

## 8. References

- Schemas: `app/schemas/compute.py`, `app/schemas/action.py`
- Runtime: `app/harness/sandbox.py`
- Scratch: `app/tools/scratch.py`
- Executor dispatch: `app/harness/executor.py:_do_compute`
- Rollback: `app/harness/rollback.py:_apply` (DELETE_SCRATCH_DIR branch)
- Policy: `app/harness/policy_guard.py` (PYTHON_COMPUTE block)
- Plan: `docs/PHASE_23_PLAN.md`
