# Phase 25 — ActionEvent 重构（三流合一）

**起草日期**：2026-05-24
**前置条件**：Phase 23 + 24（Recipe capability-first escape hatch）发布完成 = v0.23.0 tagged
**预计周期**：1-2 周
**§10.7 影响**：是 — schema 加新事件类型 + executor 改 emit 策略

> **编号说明**：PHASES.md 已经把 Recipe escape hatch (`allow_compute_action`)
> 作为 v0.23.0 同发布下的 Phase 25（ledger 行 24，no kernel touch）。本文档
> 是真正的下一个 milestone — Phase 25 ActionEvent 重构。后续 Phase 26 = C4
> Orphaned-Action 反馈，Phase 27 = 阶段内 react loop 落地。

---

## 1. 命题

> 把 LocalFlow 现有的三条事件流（`trace.jsonl` / `execution_log.jsonl` / `audit.jsonl`）
> 合一为单一 **`ActionEvent`** 流，让 trace / LLM history / UI 可视化 / grader 评估
> 共用同一份对象。

Phase 25 成功 = 三流合一完成 + 所有现有测试 (681 + Phase 23 增量) 通过 +
trace.jsonl 单文件能驱动 UI / replay / grader。

## 2. 背景

OpenHands 调研（[docs/research/OPENHANDS_HARNESS_STUDY.md](research/OPENHANDS_HARNESS_STUDY.md)
§A1 + §C1）显示：harness 真正的可扩展性核心是 **一个事件对象 = thought + tool_call +
action + risk + observation + reasoning** 全在一起。LocalFlow 当前把这些信息散在三条流：

- `trace.jsonl` — 计划/执行/验证阶段事件，但不含 LLM thought
- `execution_log.jsonl` — kernel 内部 action 进度，重复 trace 信息
- `audit.jsonl` — 用户审批操作

后果：
- LLM 的 thought / reasoning 完全没存（无法 replay LLM 决策）
- 同一 action 在三处各记一份，互相对不齐（time skew / id mismatch）
- Phase 25 的 Orphaned-Action 反馈、Phase 26 的 react loop 都需要"一个事件能完整描述一步" —
  没 ActionEvent 做不了

## 3. 设计原则（写代码评审时逐条对照）

| # | 原则 | 实施位置 |
|---|---|---|
| 1 | ActionEvent 是 self-contained：单条事件可独立 deserialize 回完整状态 | `schemas/trace.py` |
| 2 | 兼容现有 trace.jsonl 消费者（UI / grader / eval） — 增量字段，不破坏字段 | schema migration |
| 3 | LLM thought / reasoning_content 必须可选保存 | `executor.py` emit 点 |
| 4 | 不引入新文件 — trace.jsonl 仍是单文件 jsonl，只是字段变富 | trace logger |
| 5 | execution_log.jsonl / audit.jsonl 改为 trace.jsonl 的 view（filter 而非 source） | logger 重构 |
| 6 | 旧 trace 文件能被新 reader 读（向后兼容） | `_load_trace.py` |

## 4. 实施切片

### Phase 25.0 — Schema + 单文件改造（目标：3-5 天）

**目标**：ActionEvent schema 落地 + executor emit 单一富事件。**不动 UI / grader / eval**。

**交付清单**：
1. `app/schemas/trace.py` 扩 `ActionTraceEvent`（继承现有 TraceEvent）：
   - 新增字段：`thought: str | None`、`reasoning: str | None`、`tool_call_raw: dict | None`、
     `observation: dict | None`、`critic_result: dict | None`
   - 字段全部 Optional，默认 None — 旧 trace 文件 parse 不破
2. `app/harness/trace.py` `JsonlLogger` 扩 `emit_action_event()` 方法
3. `app/harness/executor.py` `_run_one` 改 emit 单条 `ActionTraceEvent`，废止当前
   `ACTION_START` + `ACTION_END` 双事件 + 散落 payload
4. `app/agent/llm_planner.py` / `app/skills/agent/*` 把 LLM `thought` / `reasoning_content`
   传给 executor，让 ActionEvent 能完整记录
5. 新测试 ~12 个：schema / executor emit / 向后兼容 reader

**验收标准**：
- 全部老测试 + Phase 23 测试通过
- 跑一个 sandbox demo，验证 trace.jsonl 一条事件能 reconstruct LLM thought + action 全套
- 老 trace.jsonl 文件（从 v0.22 / v0.23 跑出来的）能被新 reader parse

### Phase 25.1 — execution_log / audit 变 view（目标：2-3 天）

**目标**：把另两条流改为 trace.jsonl 的 filter 视图，物理上不再独立写。

**交付清单**：
1. `app/storage/run_store.py` 加 `execution_log()` / `audit_log()` 方法 — 内部走
   trace.jsonl filter
2. CLI / UI 改读上述方法，不再读独立文件
3. 现有写 execution_log / audit 的地方改为写富 ActionTraceEvent（带 source/origin 字段
   区分 user-action / kernel-action）
4. Cleanup：删除 `app/harness/execution_log.py` / `audit.py`（如果存在独立模块）
5. 新测试 ~5 个：filter view 正确性

**验收标准**：
- `.localflow/runs/<run_id>/` 下只有 `trace.jsonl`，不再有 `execution_log.jsonl` /
  `audit.jsonl`
- 老 run 目录（有独立 log 文件）的回看功能仍工作（兼容模式）
- UI 的执行日志页 / 审批历史页表现与改造前一致

### Phase 25.2 — Grader / Eval 升级（目标：2-3 天，可选）

**目标**：让 grader 用 ActionEvent 富字段做更细的判定。

**交付清单**：
1. `app/eval/recipe_verifiers/` 加 LLM-thought-aware grader（例：判断 LLM 在 plan 时
   是否考虑了已知 corner case）
2. `evals/workspace_pack/` 加 2-3 个 task 验证 reasoning 质量
3. 文档更新：`docs/EVAL.md` 加 ActionEvent grader API

**验收标准**：
- 新 grader 跑通，能给出可解释的评分
- 不打破老 grader

## 5. 风险与缓解

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| 1 | LLM thought 含敏感数据写进 trace | 中 | 加 `--no-thought-trace` flag；默认开启但记录裁切 |
| 2 | ActionEvent 字段过多导致 trace.jsonl 膨胀 | 低 | reasoning_content 默认不存全文，存 hash + 摘要 |
| 3 | 向后兼容打破 v0.22 / v0.23 跑出的 trace | 中 | reader 加 schema_version 字段，老文件走兼容路径 |
| 4 | 三流合一后并发写 trace.jsonl 撕裂 | 低 | 当前单进程 CLI 不触发；Phase 27+ 加 flock |

## 6. 验收度量

Phase 25 完成后，回答这些问题：

1. **结构**：trace.jsonl 一条事件能不能独立 deserialize 回 "LLM 当时想做什么 + 做了什么 +
   结果是什么" 的完整状态？
2. **覆盖**：681 + Phase 23 增量测试是否全过？
3. **兼容**：v0.22 / v0.23 的老 trace 文件新 reader 能不能读？
4. **简化**：execution_log.jsonl / audit.jsonl 是否真的删了，还是只是没人写但代码还在？
5. **§10.7 ledger**：本 Phase 是不是诚实记账为"schema 加 ActionTraceEvent，executor emit
   策略改 batch→single"？

任一答案为否，Phase 25 视为未完成。

## 7. 不在 Phase 25 做的事（明确划线）

- ❌ 改 execute 阶段为 LLM-loop（这是 Phase 26 的事）
- ❌ 加 ConfirmationPolicy 多档审批（Phase 27 候选）
- ❌ Workspace 抽象升级（Phase 27 候选）
- ❌ 拆 harness 内核包（Phase 28+ 候选）
- ❌ goose / Aider / SWE-agent 调研（按需做，不是 Phase 25 阻塞项）
- ❌ Phase 23 ComputeAction 接线（见下方 v0.23.0 leftover 段——Phase 26 自然修复）

## 7a. v0.23.0 leftover（Phase 26 自然修复）

2026-05-24 UI smoke 发现：v0.23.0 的 `PYTHON_COMPUTE` 内核管线齐全（schema +
sandbox runtime + executor dispatch + policy_guard + verifier + rollback），但
**没有 production code 路径会构造 ComputeAction**：

- `app/skills/agent/skill.py` 的 `allowed_actions` 不含 `python_compute`
- `app/agent/prompts.py` 的 LLM tool schema enum 不含 `python_compute`
- 没有 skill 的 planner 会发 `ActionType.PYTHON_COMPUTE`

结果：v0.23.0 的 ComputeAction 只能由测试直接构造，端到端用户不可达。

**为什么 Phase 25 不补**：

Phase 26 改造 execute 阶段为 step-by-step LLM-loop。LLM 每步直接面对完整
`ActionType` 枚举（不是 skill manifest 的子集），自然能选 `PYTHON_COMPUTE`。
中间打 Phase 23.3 / 23.4 接线补丁 ≈ 2-3h 工作，且 Phase 26 落地后会被替换掉
——属于"白干"。

**Phase 26 完成后该补的事**（不属于 Phase 25 范畴，仅在此存档）：

1. 写一个真实 LLM-loop demo：用 examples/compute_action_pack/workspace 跑通
   plan → step1: ComputeAction 清洗 → step2: index 写报告
2. 把 docs/PHASES.md 的 Phase 23 "Discovered after release" 段标记为已修复

这是 PROJECT_DIRECTION.md 规则 D（证据驱动）的实例：Phase 23 跑出来证明了
"templated agent" 痛点的具体表现，强化了 Route B 的正确性，并把修复时机推到
最合理的位置。

## 8. 立即执行的第一步（待 Phase 23 切片完成后启动）

Phase 25.0 第一个 PR 应该是**纯 schema PR**：
1. `app/schemas/trace.py` 加 `ActionTraceEvent`，**不改 executor**
2. `tests/test_action_trace_event.py` 新建，钉死字段必填 / 默认值
3. Pass — 验证不破老测试 — 合并

通过这一步可以：
- 让团队评审 ActionEvent 契约
- 后续步骤有稳定 schema 锚点
