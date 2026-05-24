# Phase 27 — ConfirmationPolicy + per-action approval granularity

**起草日期**：2026-05-24
**前置条件**：Phase 26 (v0.24.0) 已发布
**§10.7 影响**：否（应用层 + 审批 UX 重构，不动 kernel 行为）
**预计周期**：1-2 天
**版本目标**：`v0.25.0`

---

## 0. TL;DR

LocalFlow 当前审批是 **plan 级 0/1**：
- `auto_approve=True` → 整个 plan 全自动跑
- 否则 → 一次 yes/no 决定整个 plan

OpenHands 调研（[docs/research/OPENHANDS_HARNESS_STUDY.md](research/OPENHANDS_HARNESS_STUDY.md) §A5 + §C2）给出更细致的对模型：**ConfirmationPolicy** 把"风险评估"和"批准策略"解耦——
LLM / 规则定 risk，policy 决定 risk 要不要 confirm。

Phase 27 引入：
- `ConfirmationPolicy` Pydantic 类型（4 档：NEVER / ALWAYS / ON_HIGH_RISK / ON_WRITE）
- 现有 `ask_approval` 改为 policy-aware（plan 级仍存在，但增加 per-action hook）
- React loop 也用同一策略（mid-loop 高风险动作可触发新一轮 confirm）

**这不是 §10.7 例外**——审批是 application 层（同 dry_run 一样），不破 8 条铁律。

---

## 1. 命题

> 把审批策略从 0/1 升级到 4 档，让用户能"自动跑无风险动作，仅在 high-risk 处停下来 confirm"，从而把"长 plan 无脑 yes 全跑" 与 "每步问一遍很烦"两个极端都解决。

Phase 27 成功 = 4 档 ConfirmationPolicy 落地 + CLI / Recipe 都能配置 + 测试覆盖 + zero kernel touch。

---

## 2. 设计

### 2.1 ConfirmationPolicyType 枚举

| 值 | 行为 |
|---|---|
| `NEVER` | 永不询问（等同当前 `--yes` / `auto_approve=True`） |
| `ALWAYS` | 每个 action 都问一次（OpenHands 的 AlwaysConfirm） |
| `ON_HIGH_RISK` | 只对 `RiskLevel.HIGH` 的 action 询问 |
| `ON_WRITE` | 对任何 `is_write()` 动作询问（mkdir / move / copy / rename / 并入 PYTHON_COMPUTE） |

### 2.2 ConfirmationPolicy Pydantic 模型

```python
class ConfirmationPolicy(BaseModel):
    policy_type: ConfirmationPolicyType
    # 给 ON_HIGH_RISK 用 — 默认 HIGH，可降到 MEDIUM 让审批更频繁
    risk_threshold: RiskLevel = RiskLevel.HIGH
    # 始终自动通过的低风险动作（index/summarize) — 默认 True
    auto_approve_index: bool = True
    # 是否允许 "Approve all remaining" 快捷键 — 默认 True
    allow_approve_rest: bool = True
```

### 2.3 兼容性

- `auto_approve=True` 等价于 `ConfirmationPolicy(NEVER)`
- 老 `ask_approval(plan_level, ...)` 保留，包装到 policy 内部
- 默认值 = `NEVER`（v0.24.x 行为零变化）

### 2.4 集成点

| 模块 | 改动 |
|---|---|
| `app/schemas/approval.py` (new) | `ConfirmationPolicy` + `ConfirmationPolicyType` |
| `app/harness/approval.py` | 加 `decide_action_approval(action, policy) -> ApprovalDecision` |
| `app/harness/executor.py` | 在 `_run_one` 前 query policy，需要时打 hook 给 caller |
| `app/harness/react_loop.py` | 同样 query policy 决定是否让 LLM mid-loop |
| `app/cli.py` | `--confirm-policy {never,always,on_high_risk,on_write}` flag |
| `app/schemas/recipe.py` | `RecipeSpec.confirmation_policy: ConfirmationPolicy` |
| `docs/CONFIRMATION_POLICY.md` (new) | 用户文档 |

### 2.5 React loop 互动

React loop 已经在每个 action 前调 LLM。若 policy 要求 `ALWAYS` confirm，那么 plan 阶段已经过审批，loop 中如果 LLM 选 REPLACE/INSERT 引入新动作，该新动作的 risk 也要走 policy（如果是 HIGH 且 policy=ON_HIGH_RISK，应该 pause asking user）。

**Phase 27.0 不做这一步**——先让 plan 级 policy 工作，react-loop per-step confirm 留给 Phase 27.1。

---

## 3. 切片

### Phase 27.0 — schema + plan-level wiring

- `app/schemas/approval.py` 新建（`ConfirmationPolicy` / 枚举）
- `app/harness/approval.py` 加 `evaluate_policy(plan, policy) -> list[ApprovalDecision]`
- `app/cli.py` 加 `--confirm-policy` flag（plan 级先用，per-action 后续）
- Recipe `confirmation_policy` 字段
- tests: schema、policy decision、CLI flag

### Phase 27.1（候选，可与 27.0 同 commit）— per-action wiring

- Executor 内每个 action 前 query policy
- Confirm pause hook：CLI 暂停 + 提示用户，UI 弹窗（先 CLI）
- Tests: 各 policy × 各 RiskLevel 排列

### Phase 27.2（候选）— React loop 互动

- Loop 内 REPLACE/INSERT 引入的 action 走 policy
- 测试：mid-loop 高风险 INSERT 触发 confirm

---

## 4. 不在 Phase 27 做

- ❌ Workspace 抽象（Docker / Remote）—— 留给 Phase 28
- ❌ Multi-agent / sub-agent delegation
- ❌ ApprovalToken 重做（已经够用）
- ❌ UI 弹窗（Streamlit 这一轮够用，pause hook 暂只 CLI）

---

## 5. 立即执行

Phase 27.0 第一个 PR：
1. `app/schemas/approval.py` 新建 ConfirmationPolicy 类型
2. `app/harness/approval.py` 加 policy-aware helpers（不动现有 `ask_approval`）
3. `app/cli.py` 加 `--confirm-policy` flag（先做 NEVER + ALWAYS 两档）
4. `app/schemas/recipe.py` 加 `confirmation_policy` 字段
5. tests/test_confirmation_policy.py 新建 ~10 测试

完成后即可发 `v0.25.0`。
