# Phase 37 — Failure-mode benchmark + public numbers

> 状态：**设计锁定 / §10.7 预审：零 kernel 触碰**　·　日期：2026-05-29　·　分支：`main`（v0.34.x-dev → v0.35.0）
>
> 落实 `PHASE_35_PLAN.md` §6 Phase 37：把"harness 真的减少失败"从口号变成**可复现的数字**。

---

## 1. 目标 + 方法学

把飞书六大失败模式（`docs/research/FEISHU_HARNESS_ENGINEERING_SUMMARY.md` §11）变成一个
**ablation benchmark**：对每个失败模式造一个**确定性、按构造注入失败**的任务，分别在
**guard-on（LocalFlow 的对应防线开）** 和 **guard-off（关掉那条防线）** 两种模式下跑，
测"失败是否被拦下"。

**为什么是 ablation 而不是"朴素 agent vs LocalFlow"**：一个手搓的"朴素 tool-call agent"
要么是 strawman（故意造得很差好让 LocalFlow 赢，违反 rule F），要么需要真 LLM + 非确定性
（CI 跑不了、数字不可复现）。Ablation 是最诚实的对照：guard-off 就是**字面意义上的
"LocalFlow 减掉那条防线"**——delta 精确度量"那条防线买到了什么"，且完全确定性、可复现、
CI 可跑、不需 key。

**诚信硬约束（rule F）**：benchmark 必须同时暴露 LocalFlow **不**覆盖的失败模式，
不能只报赢的。一个只赢不输的 benchmark 在面试官眼里是红旗。

---

## 2. 六大失败模式 × LocalFlow 防线 × 可测量性

| # | 失败模式 | LocalFlow 防线 | 本 benchmark 怎么测 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | 规划 / 目标偏移 | react loop **drift budget** | react loop 喂 3 个 SKIP 决策；guard-on（max_drift=1）只放 1 个偏移、其余强制 CONTINUE；guard-off（max_drift=∞）全放 | **mitigated**（运行时 ablation）|
| 2 | 虚假完成 | **grounding gate**（Phase 36）| 综述含 1 处植入幻觉；guard-on 跑 `ground_review` 闸门→拦下；guard-off→幻觉 ship | **mitigated**（运行时 ablation）|
| 3 | Context Rot / 状态丢失 | —（**无** handoff/checkpoint/resume）| 无运行时防线；guard-on == guard-off 都失败 | **GAP（诚实声明）** |
| 4 | 工具 / 环境失控 | **policy_guard** | 计划含 `delete` 动作 + 越界路径；guard-on（forbidden_actions 设）→ blocked；guard-off → 危险操作 ship。另注：`..` 路径穿越被 `resolve_inside` **无条件**拦 | **mitigated**（运行时 ablation）|
| 5 | 质量 / 熵增 | **deliverable verifier** | recipe 声明 `expected_outputs=[report.md]` 但 workspace 没有；guard-on 跑 `deliverable_completeness_verifier`→fail；guard-off→残缺产物 ship | **mitigated**（运行时 ablation）|
| 6 | Harness 自身问题 | **§10.7 ledger + AST 边界 lint** | 非 per-task 运行时数字——由 `test_kernel_boundary` 通过 + ledger 比例度量 | **PROCESS（过程控制）** |

**结论**：4 个模式有硬数字（1/2/4/5），1 个诚实标为 gap（3），1 个是过程控制（6）。

---

## 3. 数据结构

```python
@dataclass FailureModeReport:
    feishu_id: int           # 1..6
    mode: str                # "goal_drift" / "false_completion" / ...
    mitigation: str          # 哪条防线
    status: str              # "mitigated" | "gap" | "process"
    guarded_failed: bool     # 防线开时，失败是否仍然 ship
    unguarded_failed: bool   # 防线关时，失败是否 ship
    detail: str              # 度量细节（如 "capped deviations 1/3"）
```

- **mitigated**：`guarded_failed=False, unguarded_failed=True` → 防线起作用。
- **gap**：`guarded_failed=True, unguarded_failed=True` → 防线不存在，都失败（诚实）。
- **process**：布尔 N/A；detail 给过程控制证据。

`run_benchmark()` 返回 `list[FailureModeReport]`；`render_markdown_table()` 出 README 表。

---

## 4. 组件 + §10.7

```
app/eval/failure_modes/
├── __init__.py
├── schema.py        # FailureModeReport
├── benchmark.py     # 6 scenarios + run_benchmark + render_markdown_table
└── __main__.py      # python -m app.eval.failure_modes → 打印表
tests/test_failure_mode_benchmark.py
```

§10.7：纯 application-eval 层。benchmark **调用**现有防线（policy_guard / grounding /
verifier / react loop）作为**库**——这不是改 kernel，是用 kernel。app/eval 允许 import
app/harness（eval 在 harness 之上）；反向被 `test_kernel_boundary` 禁止，本 benchmark 不碰
那个方向。新模块不经 `localflow_kernel` 再导出。**零新 ActionType，零 kernel 文件改动。**

drift 场景需要 Executor + RunStore + StubLLMClient（有状态），在 scenario 函数内部用
tempfile 自包含构造（复用 `test_react_loop` 的 `_StubLLMClient` + max_drift 模式）。

---

## 5. 诚信 caveat（写进 README + 文档）

1. 这是 **ablation**（guard-on vs guard-off），不是"vs 某个真实竞品 agent"。它度量的是
   "LocalFlow 的每条防线买到了什么"，不是"LocalFlow 比 X 强"。
2. 数字是**确定性、按构造注入**的——证明"防线在该触发时确实触发"，不是"野外真实失败率"。
   野外率需要真 LLM + 大样本，超出本 phase。
3. **Context Rot 是真实 gap**——LocalFlow 当前不解决长任务状态丢失。benchmark 如实报。
4. grounding 行用确定性 lexical judge（Phase 36 基线）；生产 LLM judge 路径已单独验证
   （`docs/test_artifacts/v0.34.0/llm_path_smoke.txt`，recall 2/2）。

---

## 6. 切片

| 切片 | 内容 | 验收 |
| --- | --- | --- |
| 37.0 | 本设计文档 | 文件入库（本切片）|
| 37.1/37.2 | `app/eval/failure_modes/`（schema + 6 scenarios + runner + `__main__`）+ 单测 | `python -m app.eval.failure_modes` 出表 + 测试断言每模式预期 |
| 37.3 | README（EN+ZH）数字表（含 gap 行）+ PHASES ledger + CHANGELOG + commit + tag | CI 绿 |

**不在 37 做**：真 LLM 大样本失败率、长任务 handoff（gap 留作未来）、公开 blog（plan §6 标可选，跳过）。

---

## 7. 落地状态（执行追加，2026-05-29）

- **37.0** ✅ 本设计文档。
- **37.1/37.2** ✅ `app/eval/failure_modes/`（`schema.py` + `benchmark.py` 6 scenarios +
  `__main__.py`）。4 个运行时 ablation（drift budget via react loop + FakeLLMClient /
  grounding gate / policy_guard `..` 逃逸 / deliverable verifier）+ context_rot 诚实 gap +
  harness_self process control。`python -m app.eval.failure_modes` 出 markdown 表。
  9 单测（`tests/test_failure_mode_benchmark.py`），含"表里不能出现 6/6"的诚信断言。
- **37.3** ✅ README（EN+ZH）§3 加实测表（含 gap + process 行）；PHASES ledger 行；
  CHANGELOG v0.35.0；本 §7。

**实测结果**：guard 在 **4/4 个运行时失败模式**上起决定性作用（guard-off ships /
guard-on caught）；context_rot 如实标 gap（两模式都 ship）；harness_self 标 process control。

**§10.7**：零 kernel 触碰——`app/eval/failure_modes/` 调用现有防线作为库（用 kernel，不改
kernel）；`test_kernel_boundary` 绿。+9 测试（1106 → 1115）。

**诚信纪律（rule F）**：表里同时暴露 LocalFlow **不**覆盖的 Context Rot gap + 把 harness_self
标为过程控制而非伪造数字；明说这是 ablation 而非竞品对比、确定性注入而非野外失败率。
