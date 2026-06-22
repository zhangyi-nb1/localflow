# Phase 38 — Stage-level checkpoint / resume / handoff (design)

> Harness optimization log **R6**. Goal: give LocalFlow a **Persist layer** so a
> task survives across sessions, flipping the failure-mode benchmark's
> `context_rot` row from an honest **gap** to **mitigated (stage-level)** — the
> single thing that lets the project honestly say it does long-ish tasks
> (and *only* stage-level, never an unqualified "long-running" claim — rule F).
>
> Status: **IMPLEMENTED** (38.1 facade + 38.2 CLI + 38.4 benchmark flip; 38.3
> long-pack demo deferred). context_rot flipped gap→mitigated, benchmark
> headline 4/4→5/5, **zero kernel touch** (verified: git diff on primitives
> empty). Implementation details + metrics in
> [`docs/HARNESS_OPTIMIZATION_LOG.md`](HARNESS_OPTIMIZATION_LOG.md) §R6.
> This doc is the original design (scouted via 4 parallel readers).

---

## 1. Success criterion (the exact metric R6 must hit)

`app/eval/failure_modes/benchmark.py::_bench_context_rot` (L187-200) is today a
**hardcoded GAP stub** — no run, no failure injected; the "failure" is
definitionally the *absence* of cross-run continuation:

```python
status=STATUS_GAP, guarded_failed=True, unguarded_failed=True
```

Phase 38 rewrites it into a **real ON/OFF ablation** (same shape as
`_bench_goal_drift` / `_bench_quality_entropy`), run **offline / no-LLM**
(deterministic, like the other five modes):

| path | behaviour | flag |
|---|---|---|
| **Guard OFF** (no checkpoint) | a multi-stage run is interrupted; restart loses state / re-does or can't finish → wrong-or-incomplete artifact | `unguarded_failed = True` (ships) |
| **Guard ON** (checkpoint per stage + resume) | resume skips completed stages, continues, and the final artifact is **equivalent to an uninterrupted run** | `guarded_failed = False` (caught) |

**Pass condition** = `FailureModeReport.guard_helps` (schema.py:33 =
`guarded_failed is False AND unguarded_failed is True`) returns `True`, with
`status = STATUS_MITIGATED`. That is the literal inverse of today's
`test_context_rot_is_honest_gap`.

**The equivalence invariant** (DEMO_AND_LONGTASK_GUIDE §2.6): "interrupted +
resumed final artifact == single uninterrupted run." This is what makes
`guarded_failed=False` *defensible* rather than asserted.

### Honest boundary (rule F)

This is **between-stage** resume, **not** mid-stage and **not** multi-day. When
it lands, README §3 row 3 changes from "real gap" to **"mitigated
(stage-level) — between-stage resume, not mid-stage / not multi-day"**, never to
a bare "long-running" badge.

---

## 2. Zero-kernel-touch verdict + precedent

**Verdict: Phase 38 is zero-kernel-touch.** Precedent: `recipe_repair.py` (the
documented **28th** zero-kernel-touch phase) lives *inside* `app/harness/` yet
defines its own Pydantic state models, persists its own JSON, reads trace
read-only, and drives a multi-round state machine purely by **calling** existing
primitives (`replay_from_stage`, `run_all`). §10.7 counts *editing a kernel
primitive*, not *adding an orchestration module that composes them*.

### DO NOT EDIT (kernel primitives — editing these = a §10.7 exception)

- `app/harness/executor.py` — `Executor.execute` (action-level resume engine)
- `app/harness/control_loop.py` — `run_execute` / `run_with_auto_repair`
- `app/harness/taskgraph_runner.py` — the `run_taskgraph` **stage loop**
  (adding an "if stage done: skip" branch *inside* it would be a kernel touch)
- `app/harness/checkpoint.py` — `completed_action_ids` (action-level; read-only template)
- `app/schemas/action.py` — `ActionType` enum (the schema kernel boundary)

### REUSE (existing primitives — compose, don't modify)

- `taskgraph_runner.replay_from_stage` (L414-528) — the existing cross-stage
  resume/redo primitive; itself zero-kernel; rolls back from a stage + re-runs
  the slice via `run_taskgraph(persist_*=False)` + re-stitches upstream
  `StageResult`s. **The forward-slice variant Phase 38 may add is a NEW sibling
  facade, not an edit to this.**
- `RunStore.path(name)` (L111) — arbitrary artifact name, no allow-list →
  `progress.json` / `handoff.md` need no RunStore edit
- `RunStore.write_model/read_model/write_json/write_text` (L381-396) — generic IO
- `RunStore(task_id=...)` (L85-90) — **cross-session re-attach to an on-disk run dir** (already exists)
- `StageRunStore` (taskgraph_runner.py:61) — per-stage path scoping
- `rollback.filter_manifest_to_stage` (L49-65) — pure stage-slice of a manifest
- `taskgraph_result.json` + `StageResult.status` {PASSED/FAILED/SKIPPED/ABORTED}
  — readable source for deriving stage state
- `JsonlLogger` (jsonl_logger.py:21-44) — append-only, crash-tolerant journal
- existing verifiers' evidence bundles (`claim_grounding.json` etc.) — the
  evidence that gates a stage `in_progress → verified`

### NEW (all facade / schema / doc — none touch the kernel boundary)

1. `app/schemas/checkpoint.py` (or `progress.py`) — **NEW** Pydantic models:
   `StageProgress` (5-state enum) + `ProgressState` + `HandoffNote`. Adding new
   schema models is **not** a §10.7 touch (the schema boundary is *only*
   `ActionType`).
2. `app/harness/stage_progress.py` — **NEW** facade: write/read `progress.json`,
   derive stage states from `taskgraph_result.json` (+ verifier evidence),
   render `handoff.md`, and compute the pending slice → delegate to
   `replay_from_stage` / a forward-slice sibling. Composes only.
3. `app/cli.py` — **NEW** `localflow pack resume --run-id <id>` (mirrors the
   existing `cmd_taskgraph_replay` cross-session re-entry). Pure wiring.
4. `recipes/literature_review_pack_long.yaml` — **NEW** variant that splits
   per-source summary into ~14 stages so resume has something to skip.
5. `app/eval/failure_modes/benchmark.py` — rewrite `_bench_context_rot` into the
   real ablation (doc/eval layer).

> Storage convenience (a `progress_path` property on `RunStore`) is optional and
> **not** a kernel touch — `app/storage/` is outside the boundary set. Prefer
> going through `path("progress.json")` like `recipe_repair` does, to keep the
> diff minimal.

---

## 3. The artifacts (KB-prescribed; ch12 §跨window接力 / §状态丢失)

The KB (Anthropic "Effective harnesses for long-running agents", 轮班工程师接力)
prescribes **three** externalized-state artifacts written at every stage
boundary. Phase 38 produces all three:

### 3.1 `progress.json` — the progress file (ch12 L319-321)

Canonical fields (KB L319-327):
```
current_goal        : str
stages              : [StageProgress]      # the feature-list state machine
verification_results: per-stage evidence path + verdict
failed_attempts     : [str]                # anti-rework (KB stresses this)
next_step           : str                  # where the next session starts
notes               : str
graph_hash          : str                  # guard: resume only a matching graph
updated_at          : ts (passed in, never Date.now)
```

### 3.2 stage state machine (ch12 L122-130, L196-202)

Each stage = a feature-list entry with **5 states**:
`pending → in_progress → implemented → verified → blocked`.

**Verification-constrained** (the honesty rule, KB L200): a stage may only reach
`verified` **with evidence** — bind the transition to LocalFlow's existing
verifier exit-code / evidence bundle (`claim_grounding.json`, deliverable
verifier output). No "false verified" on resume. This reuses the Phase 35/36
verify-as-gate as the evidence producer — zero new primitive.

### 3.3 `handoff.md` — the handoff note (ch12 L327, L397-409)

Per-stage exit contract, 5 fields: **done / files-changed / verified /
unresolved / next-start-point**. The KB's "un-clean handoff" negative checklist
(L397-409) is the acceptance test: commit-before-exit, record completion %,
record verification results, state known issues, explicit next step.

### 3.4 clean baseline (ch12 L321, L391)

A snapshot per increment so the next session faces a clean, diffable,
rollback-able site. LocalFlow already has `RollbackManifest` + workspace git;
Phase 38 records the baseline ref in `progress.json` rather than adding a new
mechanism.

> **Compaction-is-not-enough** (KB L331-337): the claim "mitigated(stage-level)"
> is earned ONLY if a *fresh* session resumes from these files alone — never
> from a context summary. This is the rule-F guardrail on the claim.

---

## 4. Implementation slices (land one at a time; rule C)

| slice | content | kernel | test |
|---|---|---|---|
| **38.0** | this design doc | — | — |
| **38.1** | `app/schemas/*` progress models (5-state enum, `ProgressState`, `HandoffNote`) + `app/harness/stage_progress.py` write/read/derive/render | none | unit: state derivation from a synthetic `taskgraph_result.json`; verified-requires-evidence |
| **38.2** | resume facade (compute pending slice → `replay_from_stage` / forward-slice sibling) + `localflow pack resume --run-id` CLI | none | unit: resume skips completed stages; equivalence to uninterrupted run on a stub graph |
| **38.3** | `literature_review_pack_long.yaml` (~14 stages) | none | deterministic pack compiles + runs offline |
| **38.4** | rewrite `_bench_context_rot` into the real ablation + **invert** the 3 benchmark tests (`test_context_rot_*`, `4→5` in `test_exactly_four_runtime_mitigations`, `4/4→5/5` + drop `gap` / keep `6/6-not-in` in render test) + README §3 row3/prose + reconcile stale ledger ratio + PHASES.md ledger row | none | full suite green; benchmark headline 4/4 → **5/5** |

**Acceptance for "R6 done"**: `python -m app.eval.failure_modes` prints
context_rot as ✅ caught (ON) / ❌ ships (OFF) / mitigated, headline **5/5**;
a real interrupt→resume run produces an artifact equal to the uninterrupted run;
README/PHASES reconciled; **zero kernel touches** (verify
`tests/test_kernel_boundary.py` + `git diff` on `app/harness/{executor,control_loop,rollback}.py` empty).

---

## 5. KB 八股 (the interview point R6 sediments)

> **面试问题**:"长任务跨多个 context window 怎么不丢状态?"

**答**:模型不天生记得"停在哪、改了哪些文件、哪些验证过了"。靠 context 记 = truncation/
compaction/reset 一来就崩,新 agent 还可能基于**错误状态**行动(重修已解决的问题、覆盖正确改动)。
**compaction 不够**——它压缩上下文,但不告诉下一轮"现场是否干净、哪些 stage 验过、下一步从哪开始"。
正解是 **Persist 层**:把状态外部化成可读可恢复的工件——progress 文件 + **受验证约束的** feature-list
状态机(只有带证据才能进 verified)+ 干净的 git baseline。新 session 只靠这些文件就能接力。
LocalFlow 的 Phase 38 把这套接到现有 verify-as-gate(证据来源)+ replay_from_stage(切片复跑)上,
**零 kernel 触碰**——它是编排,不是新原语。诚实边界:这是 **stage 级**接力,不是 mid-stage、不是多天。

- KB 出处:`llm_app_interview_12_harness_scenarios.md §跨window接力`(L297-329)、`§仅靠compaction不够`
  (L331-353)、`§状态丢失`(L355-395,feature-list 状态机 L122-130/L196-202);
  `llm_app_interview_10_harness_engineering.md §Persist`(L334)。
- 项目内对应:六维里的**可恢复**;差异化"基于 trace 的持续改进";rule A(harness 能力,非窄 skill)。

---

## 6. §10.7 ledger note

Phase 38 is planned **zero-kernel-touch** (composition over primitives, precedent
`recipe_repair.py`). When 38.4 lands: add a `docs/PHASES.md` ledger row (a new
zero-touch delivery) and **reconcile the stale ledger ratio** wherever it appears
(the demo guide flagged `4/41`; current README says `4/44` — verify the actual
string at flip-time, don't assume). The "4 deliberate exceptions" count does
**not** change. If implementation ever forces a kernel-primitive edit, **stop and
get user confirmation** (rule H) — but the scout found no such need.
