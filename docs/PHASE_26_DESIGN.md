# Phase 26 — 阶段内 React Loop（Route B 真正落地）

**起草日期**：2026-05-24
**前置条件**：Phase 25.0–25.6 全部落地（`ActionTraceEvent` schema + 执行链路 + `observation` 喂回 LLM）
**§10.7 影响**：是 — 新增执行模式开关，executor 内部多一种循环结构
**预计周期**：3–5 天的 deep work + 一周 prompt 迭代
**版本目标**：`v0.24.0`

---

## 0. TL;DR

LocalFlow 现在是 **plan-once-execute-batch** —— LLM 一次产出 N 个 action，executor 顺序跑完。**Phase 26 改造 execute 阶段**：每个 action 跑完后，把 `ActionTraceEvent.observation` 反馈给 LLM，LLM 决定下一步（在已批 plan 范围内 `±N` 步漂移）。

**这不是抛弃阶段式架构**——`plan / dry-run / approval / verify / rollback` 五段骨架保留。**只在 execute 段内部**接入 react loop。

完成 Phase 26 = 答出"OpenHands 的智能感"从哪里来，**同时**保留 LocalFlow 不可替代的差异化（dry-run / rollback / 独立 verifier）。

---

## 1. 命题

> **在保留阶段式骨架的前提下，让 execute 阶段从顺序跑批，升级为"每步看 observation、由 LLM 决定下一步"的 react loop，能解锁 Phase 23 跑不动的所有 corner-case 任务，且不破坏 8 条铁律。**

Phase 26 成功 = 命题成立 = 至少 3 个原来跑不动的 demo 任务跑通 + Phase 23 ComputeAction reachability gap 自然消失 + 全部既有 802 测试通过。

## 2. 与 Phase 25 的关系

Phase 25 把数据通路修好了——Phase 26 是消费者：

| Phase 25 提供 | Phase 26 消费 |
|---|---|
| `ActionTraceEvent.observation` 字段 | 每步喂给 LLM 作为下一步决策的输入 |
| `ActionTraceEvent.thought / reasoning / tool_call_raw` | LLM history reconstruction（让模型"知道自己上一步说了什么"）|
| `_format_failed_action_context` helper | react loop 的失败反馈格式化 |
| `RunStore.read_trace_events` / view 方法 | resume / replay 场景下从 trace 重建 LLM history |
| `localflow trace show` CLI | 调试 react-mode 的可视化通道 |

**Phase 25 是必要前置；缺一个 Phase 26 都得自己再造一次轮子。**

---

## 3. 架构变更

### 3.1 不变的部分（坚守）

| 部分 | 保留原因 |
|---|---|
| Plan / Dry-run / Approval / Verify / Rollback 五段骨架 | LocalFlow 五大坚守差异化 |
| `RollbackManifest` + hash-drift | 用户文件不一定在 git 里 |
| 独立 Verifier（结构 + 语义两层） | "任务式 agent" vs "对话式 agent" 的根本区别 |
| 程序化 `PolicyGuard.resolve_inside` | 不被 prompt injection 误导 |
| 8 条铁律 | 项目身份 |

### 3.2 变的部分（execute 阶段内部）

```
v0.23.x (现状):
  Plan ──> dry-run ──> approval ──> execute (顺序跑 N 个 action) ──> verify ──> rollback
                                       └────── batch ──────┘

v0.24.0 (Phase 26):
  Plan ──> dry-run ──> approval ──> execute ──> verify ──> rollback
                                       │
                                       ├── react_mode=False (默认):
                                       │    顺序跑 N 个 action  ← v0.23 行为，零变化
                                       │
                                       └── react_mode=True:
                                           for action in plan.actions:
                                             run(action) → observation
                                             ┌──────────────────────┐
                                             │ ask LLM:             │
                                             │   thought + obs +    │
                                             │   remaining_plan     │
                                             │   → next_decision    │
                                             └──────────────────────┘
                                             apply decision:
                                               CONTINUE    → run next action as planned
                                               REPLACE(X)  → swap next action with X (in ±N drift budget)
                                               INSERT(X)   → run X before next action
                                               SKIP        → skip next action
                                               ABORT       → stop, hand back to verify
```

### 3.3 关键不变量（必须始终成立）

写代码时逐条对照，PR 评审时逐条 check：

| # | 不变量 | 实施位置 |
|---|---|---|
| 1 | react loop 内 LLM 决策也走 `policy_guard.evaluate_action` 后才能 dispatch | executor 内部 |
| 2 | LLM 在 react loop 中**不能新增** plan 外的 ActionType（除非用户 approval 时开启 escape hatch） | recipe 层 + executor 校验 |
| 3 | 每步 LLM 决策也写 trace（新 event types：`LOOP_DECISION_*`） | trace schema |
| 4 | rollback manifest 仍然每个写操作一条 entry，不分 react / batch | executor `_dispatch` |
| 5 | `react_mode` 是 opt-in，默认 False，老 plan 行为零变化 | API 默认值 + 测试 |
| 6 | 漂移有上限（默认 ±3 步，可配 max_drift） | `ReactConfig` |
| 7 | LLM 在 react loop 中**不能**触发新的 dry-run / approval —— 那是阶段边界 | 物理隔离：react 调用不重入 `control_loop` |
| 8 | 任意一步 LLM 失败 / 超时 / 越界，自动 fallback 到 batch 模式继续跑剩余 action | executor 防御逻辑 |

### 3.4 §10.7 ledger 记账

新增第 4 行 deliberate exception（如果 Phase 26 落地）：

```
| 26 (v0.24.0) | YES (4th exception) | execute-stage react_mode — Executor learns
  to consult an LLM mid-batch between actions and apply ±drift_budget
  decisions. Defaults to off (react_mode=False = v0.23 behaviour);
  opt-in via Recipe.enable_react_mode or `localflow execute --react`.
  Decisions still go through policy_guard before dispatch, still
  produce manifest entries, still hit the same Verifier. 8 iron rules
  intact. |
```

**这是 §10.7 第 4 个 deliberate exception**，第一次在 v0.23 后扩展。重要——确认是真的需要新加一行 ledger，而不是退化到"全面 LLM-loop"破坏五大差异化。

### 3.5 文件清单

**新增**：
- `app/schemas/react.py` — `ReactConfig` / `LoopDecision` / `LoopDecisionType` 类型
- `app/harness/react_loop.py` — react loop 实现，依赖 executor 的低层 dispatch
- `app/agent/react_prompts.py` — react step 的 system prompt + tool schema
- `docs/REACT_LOOP.md` — 用户文档（什么时候开、什么时候不开、漂移上限语义）
- `tests/test_react_loop.py` / `test_react_decisions.py` / `test_react_drift_budget.py`
- `evals/workspace_pack/task_011_react_csv_clean.yaml`（demo eval task）

**修改**（kernel 边界附近，需登记 §10.7）：
- `app/schemas/action.py` — 无需改 ActionType（react loop 用既有 action types）
- `app/schemas/trace.py` — 加 `LOOP_DECISION_REQUESTED / DECIDED / APPLIED` 事件类型
- `app/schemas/recipe.py` — `RecipeSpec.enable_react_mode: bool = False`
- `app/harness/executor.py` — `execute()` 加 `react_mode` kwarg，在 `react_mode=True` 时 dispatch 到 `react_loop.run()`，否则走老 batch path

**修改**（kernel 之外）：
- `app/cli.py` — `localflow execute --react` flag
- `app/ui/pages/6_Execute.py` — 一个 checkbox "Allow LLM to adapt mid-execution"（默认未勾选）
- `app/harness/control_loop.py` — `run_execute` 接 `react_mode` 参数透传
- `app/eval/recipe_verifiers/_schema.py` — Recipe.enable_react_mode 字段

---

## 4. 实施切片

### Phase 26.0 — Schema + LoopDecision 契约（目标：1 天）

**目标**：钉死 react loop 的类型契约 + LLM 工具 schema，**不动 executor**。

**交付清单**：
1. `app/schemas/react.py` 新建：
   - `LoopDecisionType` 枚举（`CONTINUE / REPLACE / INSERT / SKIP / ABORT`）
   - `LoopDecision` Pydantic 模型（decision_type + 可选 replacement_action + 可选 reason）
   - `ReactConfig` Pydantic 模型（`max_drift: int = 3`, `max_loops_per_action: int = 1`, `llm_timeout_sec: int = 30`）
2. `app/schemas/trace.py` 加 3 个新事件类型：
   - `LOOP_DECISION_REQUESTED`（mid-execute LLM 询问开始）
   - `LOOP_DECISION_DECIDED`（LLM 返回决策）
   - `LOOP_DECISION_APPLIED`（决策落地为下一个 action）
3. `tests/test_react_schema.py` —— 6-8 个测试钉契约
4. **不动**任何 kernel / executor 文件

**验收**：802 → ~810 passed，schema 完整可序列化。

### Phase 26.1 — React Loop 核心（目标：2-3 天）

**目标**：跑通最小闭环，**用一个原 plan-once 跑不动的 demo 证明 react 能解锁**。

**交付清单**：
1. `app/harness/react_loop.py` 新建：
   - `run_react_loop(plan, executor, llm_client, config, trace)` 入口
   - 循环骨架：for action in plan.actions → execute → ask LLM → apply decision
   - drift 计数（每应用一次 REPLACE/INSERT/SKIP 算一次漂移）
   - drift 超限 → 自动降级到 batch 模式跑剩余
2. `app/agent/react_prompts.py`：
   - system prompt（针对单步决策，强调"你已经看到上一步 observation"）
   - tool schema：`submit_loop_decision`（forced tool call，包含 5 种 decision type）
3. `app/harness/executor.py` `execute()` 加 `react_mode=False` 参数，True 时 dispatch 到 `react_loop.run_react_loop`
4. demo eval task：怪 CSV 清洗 + 后续 chart 渲染——v0.23 plan-once 因 CSV 怪而失败，react 能在第一步 observation 看到失败后 INSERT 一个 PYTHON_COMPUTE 清洗 action
5. 单元测试 + 集成测试 ~12 个

**验收**：demo task 跑通，全套测试通过，trace.jsonl 有完整 LOOP_DECISION_* 事件。

### Phase 26.2 — Drift Budget + Failsafes（目标：1-2 天）

**目标**：把 26.1 的最小闭环硬化到产品级。

**交付清单**：
1. `ReactConfig.max_drift` 强制：超 budget 后 LLM 决策返回 IGNORED
2. LLM 超时 / 异常 / 越界（unknown action_type）→ 自动 fallback 到下一个原 plan action
3. Recipe `enable_react_mode` 字段 + 校验（默认 False，开启需配 `react_config`）
4. `localflow execute --react` CLI flag
5. UI checkbox "Allow LLM to adapt mid-execution"（默认未勾选 + tooltip 警告：会延长执行时间 / 不可预测）
6. 文档：`docs/REACT_LOOP.md` 用户视角讲清楚何时该开
7. 失败演练：把 LLM client 替换为故意返回烂决策的 stub，验证 fallback 正常

**验收**：所有"绕过 react"路径都仍能跑通 v0.23 行为；react 路径有完备的 fallback 链。

### Phase 26.3 — Pack / Recipe 集成 + Phase 23 gap 修复（目标：1 天）

**目标**：兑现 Phase 23 留下的"ComputeAction 端到端不可达"承诺。

**交付清单**：
1. 改 `examples/compute_action_pack/` 的 Recipe 加 `enable_react_mode: true` + `allow_compute_action: true`
2. demo：用户跑 `localflow pack run compute_action_pack` 时，agent skill 在 react loop 中**真的会**emit PYTHON_COMPUTE
3. 在 `docs/PHASES.md` 把 Phase 23 "Discovered after release" 段标记为 ✅ 已修复
4. eval task：用户怪 CSV → react loop 自动决定跑 ComputeAction 清洗 → 全套 verifier 通过
5. 一段 demo 录制 / GIF（可延后）

**验收**：从前端用户视角，"清洗这份脏 CSV"的目标能端到端跑出预期输出。Phase 23 gap 关闭。

---

## 5. 风险与缓解

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| 1 | LLM 在 react 步中产生 plan 外的危险动作（如 delete / overwrite） | 高 | policy_guard 仍是必经路径；LoopDecision 走完整 evaluate_action 后才能 dispatch |
| 2 | drift budget 被滥用变成"无限重 plan" | 高 | `max_drift` 硬上限默认 3；超出自动降级 batch；trace 留证 |
| 3 | react step 调用 LLM 失败 / 慢 / 错乱 | 中 | 30s timeout + fallback 到下一个原 action；测试覆盖三种异常 |
| 4 | 用户在 react 模式开始后才发现成本飙升 | 中 | UI / CLI 默认 OFF；开启时 tooltip 警告；trace 输出预估 token 消耗 |
| 5 | 现有 802 测试因 react_mode 默认行为变化而炸 | 中 | `react_mode=False` 是默认值；所有老测试无新参数 → 走 batch 路径 |
| 6 | Phase 23 ComputeAction reachability 期望落空（agent skill 还是不发 ComputeAction） | 中高 | 26.3 单独写 eval task 钉死期望；不通过 Phase 26 不发版 |
| 7 | §10.7 ledger 第 4 个例外引发"项目破戒"质疑 | 低 | 文档诚实说明：与 Phase 23 同性质，是 deliberate addition，五大差异化全保 |
| 8 | UI 复杂度（同步 progress 显示 react 决策）超预算 | 低 | UI 简化方案：先只显示"react 模式启用中…"，详细决策走 trace CLI |

---

## 6. 实验度量

Phase 26 完成后，回答这些问题（每个都要有 trace / eval / 测试证据）：

1. **能力**：3 个 demo 任务跑通了吗？trace.jsonl 里有完整 LOOP_DECISION_* 事件？
2. **隔离**：drift 超 budget 测试用例正确触发降级？
3. **回归**：v0.23 全套 802+ 测试是否仍通过（含 react_mode=False 默认路径）？
4. **诚信**：UI / CLI / 文档有没有暗示"react 模式更智能"？只能说"更灵活"。
5. **§10.7**：是否如实记账为第 4 个 deliberate exception？
6. **Phase 23 gap**：ComputeAction reachability 是否从 trace 上证明已修复？
7. **fallback**：LLM 异常路径的 3 个测试用例（timeout / malformed / policy-blocked）是否都覆盖？
8. **成本**：跑 react 模式 vs 跑 batch 模式的 token / 时间消耗增量是否在文档中量化？

任一答案为否，Phase 26 视为未完成。

---

## 7. 不在 Phase 26 做的事（明确划线）

- ❌ 完全替换阶段式架构为全 LLM-loop（那是 Route A，已被锁定排除）
- ❌ 改 dry-run / approval / verify / rollback 的阶段边界（不动）
- ❌ ConfirmationPolicy 多档审批（Phase 27 候选）
- ❌ Workspace 抽象升级到 Docker（Phase 27 候选）
- ❌ Harness 内核拆独立 PyPI 包（Phase 28+ 候选）
- ❌ Multi-agent / sub-agent delegation（远期，可能 Phase 30+）
- ❌ 把 react 模式做成默认（保守起见永远 opt-in，至少到 v1.0）

---

## 8. 版本规划

- Phase 26.0–26.2 合并为 `v0.24.0` release
- Phase 26.3 + demo GIF + README 更新 = `v0.24.1`
- Phase 27（Workspace 抽象、ConfirmationPolicy）= `v0.25.0`

§10.7 ledger 在 `v0.24.0` 发布后会变成：

```
33+ phases shipped, 27/33 zero-kernel-touch, 4 deliberate exceptions:
  Phase 5  — forbidden_paths (Memory & personalization)
  Phase 16 — ActionType.FETCH (WebCollect)
  Phase 23 — ActionType.PYTHON_COMPUTE (Sandboxed ComputeAction Engine)
  Phase 26 — execute-stage react_mode (Route B: stage spine + step-by-step)
```

---

## 9. 立即执行的第一步（待 Phase 25 收尾完成后启动）

**Phase 26.0 第一个 PR**：纯 schema PR，零行为改动。

1. `app/schemas/react.py` 新建：`LoopDecisionType` / `LoopDecision` / `ReactConfig`
2. `app/schemas/trace.py` 扩 3 个 `LOOP_DECISION_*` event types
3. `tests/test_react_schema.py` 新建 ~6-8 测试
4. **不动** executor / cli / ui / recipe schema

通过这一步可以：
- 锁定 LoopDecision 契约（之后所有 phase 都用这套类型）
- §10.7 第 4 个例外的"主动登记"动作完成（PR 描述里写明）
- 后续 26.1 实现有稳定 schema 锚点

确认这份蓝图无误后，开始 26.0。

---

## 10. 调研依据

本计划基于以下证据，不靠直觉：

- `docs/research/OPENHANDS_HARNESS_STUDY.md` §A3（OpenHands LLM-loop 控制循环）+ §C4（Orphaned-Action 修复模式）
- `docs/PHASES.md` Phase 23 "Discovered after release" 段（ComputeAction 不可达根因）
- v0.23.0 UI smoke test（实地证据：plan-once 模式下 agent skill 选不到 PYTHON_COMPUTE）
- `tests/test_action_trace_event_emission.py`（Phase 25.1 已建立的 observation 通路）
- `tests/test_repair_loop_observation.py`（Phase 25.6 已建立的 trace→hint 喂回机制）

Phase 26 是这些数据通路的自然消费者，而不是"再造一次"。
