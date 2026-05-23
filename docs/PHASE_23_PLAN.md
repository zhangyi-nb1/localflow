# Phase 23 — Sandboxed ComputeAction Engine (Isolation-first)

**实验指导报告 · 2026-05-23 · 战略版本 0.1**

---

## 0. TL;DR

LocalFlow 当前形态被"动作词汇过窄"卡住智能上限——8 个固定 skill 覆盖不了用户的低频定制需求，体感像"templated agent"。Phase 23 在保留全部 8 条铁律的前提下，**新增一个动作类型 `ActionType.PYTHON_COMPUTE`**：模型可以提出在隔离 scratch workspace 中跑的 Python 脚本，原 workspace 安全保证不变。

**这是 §10.7 ledger 上第 3 个 deliberate kernel exception**，前两个分别是 Phase 5 的 `forbidden_paths` 和 Phase 16 的 `ActionType.FETCH`。公开记账，不藏。

**核心命名锁定**：**Isolation-first**，不是 "受控"，更不是 "Sandboxed"。文档、commit message、issue 标题里出现 "security sandbox" 这种措辞 = 项目失败。

---

## 1. 背景与诊断

### 1.1 当前形态的瓶颈

经过 Phase 0–22 的演进，LocalFlow 已经搭出一套完整的 5 层架构（Drivers → Skills → Tool Registry → Kernel → Memory）+ 681 通过的测试 + 三个 flagship pack 的产品落地页。但用户实测时反复碰到一类卡壳：

- 任务超出 8 个内置 skill 能覆盖的组合时，agent 只能要求用户**新写一个 skill**
- "做 agent 的体感"变成了"边做任务边写代码扩展 skill"
- 智能上限不是模型不够强，而是**动作词汇不够通用**

### 1.2 同类项目的解法

参考 OpenHands (~40k★) 和 goose (~14k★)：它们的智能来自**少而通用的动作原语**（CmdRunAction / FileEditAction / BrowseAction 等），不是大而专的 skill 菜单。但它们也都同时配套了 sandbox + event stream + checkpoint，证明"通用动作"和"安全保证"可以共存。

### 1.3 LocalFlow 的差异化定位（不变）

不抛弃 LocalFlow 已有的差异化：
- **本地优先**（不强制 Docker、不强制云端）
- **8 条铁律**（结构化 plan / dry-run / approval / 路径边界 / 可追踪 / 可回滚 / 独立 verify / 模型不直接执行副作用）
- **§10.7 ledger**（kernel 改动诚实记账，零 kernel-touch 是常态而非例外）
- **Pack 体系作为产品脸面**

Phase 23 是在不动这些差异化的前提下，**给执行内核装一个新动作类型**。

---

## 2. 核心命题与设计原则

### 2.1 待验证的命题

> **在 isolation-first 的隔离策略下增加 ComputeAction，能解锁原 LocalFlow 跑不动的定制任务，同时保持原 workspace 的 8 条铁律不破。**

Phase 23 成功 = 命题成立 = 至少一个原来跑不动的 demo 任务能跑通且全部 verifier 通过。

### 2.2 ComputeAction 的 10 条设计原则

写代码时严格遵守，写代码评审时逐条对照：

| # | 原则 | 实施位置 |
|---|---|---|
| 1 | 输入必须是显式 `ContentRef` / `FileRef` | schema |
| 2 | 输出只能落到 `.localflow/scratch/<task_id>/<action_id>/` 或后续 stage 指定的 pack output | sandbox runtime |
| 3 | 默认无网络（best-effort，仅靠 env 清理） | sandbox runtime |
| 4 | 默认不能删除源 workspace 文件 | policy_guard + sandbox runtime |
| 5 | 默认不能访问 workspace 外路径 | sandbox runtime (cwd + 输入复制) |
| 6 | 必须有 timeout、内存上限、单文件大小上限 | SandboxPolicy |
| 7 | 全部执行日志进 trace | sandbox runtime |
| 8 | 产出的 artifact 仍要过 verifier 才被信任 | executor + verifier |
| 9 | 执行前必须 approval（摘要模式） | dry_run + approval |
| 10 | 不能直接成为最终写操作；写入用户 workspace 必须经过单独的 pack stage | recipe / taskgraph 层约定 |

### 2.3 "关网"的诚实承诺

- 默认不向 compute action 注入任何 API key / proxy env
- 启动子进程前清理 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等敏感变量
- 注入 `LOCALFLOW_COMPUTE_NETWORK=off` 给脚本作 hint
- **文档明确写**："network isolation is best-effort unless Docker / firewall mode is enabled"
- Windows 上的可靠断网需要防火墙规则或容器，**留给 Phase 23.x，不做承诺**

### 2.4 Skill 与 ComputeAction 的角色分工（Phase 24 蓝图）

**Recipe should be capability-first, with ComputeAction as an escape hatch.** 不要写成 "ComputeAction-first"，那会让 LocalFlow 失去原有的可预测性。

| 角色 | 定位 | 例子 |
|---|---|---|
| **Skill** | 稳定、可复用、高频、可确定 | scan / fetch / pdf extract / source ledger / safe move |
| **ComputeAction** | 低频、多变、用户特定、难以预先枚举 | 自定义数据清洗 / 特殊格式转换 / 定制图表 / 临时统计 |
| **Verifier** | 决定输出是否可信 | 两类产物都要过 |

**默认走 skill，skill 覆盖不了再走 ComputeAction。**

---

## 3. 架构变更

### 3.1 §10.7 ledger 记账

新增第 3 行 deliberate exception：

```
| 23 (v0.23.0) | YES (3rd exception) | ActionType.PYTHON_COMPUTE — 新增受控代码执行通路，executor 内 dispatch 到 SandboxRuntime；policy_guard 学会 ComputeAction 路径校验；isolation-first 策略文档化（best-effort network，非 security sandbox）。 |
```

### 3.2 新增 / 修改文件清单

**新增**：
- `app/schemas/compute.py` — `ComputeAction` / `SandboxPolicy` / `ComputeOutcome` / `ArtifactSpec` Pydantic 模型
- `app/harness/sandbox.py` — `SandboxRuntime` 类：subprocess + cwd 限制 + timeout + env 清理
- `app/tools/scratch.py` — `ScratchWorkspace` 管理（创建 / 输入复制 / 清理）
- `docs/COMPUTE_ACTION.md` — 用户视角的 ComputeAction 使用指南，**显眼写明 isolation 不是 security sandbox**
- `tests/test_compute_action.py` / `test_sandbox_runtime.py` / `test_scratch_workspace.py`

**修改**（kernel 边界附近，需登记 §10.7）：
- `app/schemas/action.py` — `ActionType` 新增 `PYTHON_COMPUTE`，写法照搬 Phase 16 的 `FETCH` 注释模式
- `app/harness/executor.py` — dispatch ComputeAction 到 SandboxRuntime；记 manifest（rollback 时清 scratch dir）
- `app/harness/policy_guard.py` — ComputeAction 的路径/权限校验
- `app/harness/verifier.py` — 接受 ComputeAction outcome 的 artifact 校验
- `app/schemas/trace.py` — 新事件 `COMPUTE_ACTION_START` / `COMPUTE_ACTION_END` / `SANDBOX_TIMEOUT` / `COMPUTE_OUTPUT_VERIFIED`

**修改**（kernel 之外）：
- `app/agent/prompts.py` — 系统提示加入 ComputeAction 的 schema 说明（仅在 agent meta-skill / 高级 skill 中开放）
- `app/skills/agent/llm_planner.py` — agent skill 学会在合适场景输出 ComputeAction
- `app/cli.py` — dry-run 渲染 ComputeAction 时显示脚本摘要 + 折叠详情
- `app/ui/pages/*` — 审批页加 ComputeAction 摘要区
- `app/eval/recipe_verifiers/` — 新 verifier 校验 scratch artifact 不污染原 workspace

---

## 4. 实施阶段切分

### Phase 23.0 — Skeleton + End-to-End Demo（目标：1 周）

**目标**：跑通最小闭环，**用一个原来 LocalFlow 跑不动的 demo 任务证明 ComputeAction 解锁了上限**。这是 Phase 23 成败的唯一硬指标。

**交付清单**：
1. `ComputeAction` schema + `ActionType.PYTHON_COMPUTE` 入册
2. `ScratchWorkspace` 管理：创建 `.localflow/scratch/<task_id>/<action_id>/`，复制 inputs，执行后保留 outputs
3. `SandboxRuntime`：subprocess + cwd = scratch + timeout + env scrub（删 PROXY/API key vars）
4. Executor dispatch + rollback manifest 记录（rollback 时 rm -rf scratch dir）
5. 一个 demo 任务：
   - 输入：一份格式怪异的 CSV（比如 BOM + 半角全角混用 + 不规则 delimiter）
   - 现有 8 skill 跑不通（data_analyzer 会报错或 EMPTY_RESULT）
   - ComputeAction 通过模型生成 Python 清洗后写出标准 CSV 到 scratch
   - 后续 stage 把清洗后 CSV 移到 workspace
6. 单元测试 ~15 个（schema / scratch / sandbox / executor dispatch / rollback）
7. **诚信文档**：`docs/COMPUTE_ACTION.md` 第一段就是 "Isolation, not security sandbox" 声明

**验收标准**：
- demo 任务从 plan → dry-run → approve → execute → verify → rollback 全链路通
- rollback 后 scratch dir 干净，workspace 完全恢复
- 任意 `os.environ` 探针脚本测不到 `OPENAI_API_KEY` 等敏感变量
- 任意 `open("/etc/passwd")` / `open("C:/Windows/...")` 脚本失败（被 cwd 限制 + 文件存在性自然防御）
- 超时脚本被杀掉，trace 里能查到 `SANDBOX_TIMEOUT` 事件

### Phase 23.1 — Approval UX + Trace + Verifier 集成（目标：3-5 天）

**目标**：把 23.0 的最小闭环升级到产品级体验。

**交付清单**：
1. CLI dry-run 渲染：脚本前 10 行 + 总行数 + 输入清单 + 预期输出清单 + `[Y]eview full script / [A]pprove / [N]o`
2. UI 审批页：折叠 expander 显示完整脚本，默认展开摘要
3. Trace 事件全套接入（4 个新事件）
4. Verifier 集成：声明的 verifier 在 ComputeAction outcome 上跑；失败则该 action 标记 FAILED 且 outputs 不进入下一 stage
5. 单元测试 ~10 个

**验收标准**：
- 用户在不读完整脚本的情况下能做出合理 approve / reject 决策
- 失败的 ComputeAction 不会把脏 artifact 暴露给后续 stage

### Phase 23.2 — Pack Stage 集成（目标：3-5 天）

**目标**：让 ComputeAction 在 Recipe / TaskGraph 体系里有一席之地，但按 Phase 24 蓝图—**escape hatch only**。

**交付清单**：
1. `TaskGraph` schema 支持 stage 声明为 ComputeAction-only 或 ComputeAction-fallback
2. `Recipe` 增加 `allow_compute_action: bool = False` 字段，默认关闭
3. agent meta-skill 学会判断"skill 不够用 → 生成 ComputeAction"，但有 quota 限制（每 recipe 最多 N 个 ComputeAction）
4. 选一个 flagship pack 加 ComputeAction escape hatch demo（推荐 `data_report_pack`：用户的怪格式数据用 ComputeAction 清洗后再走 data_analyzer skill）
5. 单元测试 + 集成测试

**验收标准**：
- Recipe 默认仍走纯 skill 路径，行为零变化
- 开启 `allow_compute_action` 的 Recipe 在 skill 不够用时优雅降级到 ComputeAction
- Phase 21 的 auto-repair loop 兼容 ComputeAction 失败的修复

### Phase 23.3 — Hardening（如有时间，1 周）

可选硬化：
- Windows Job Objects 实现内存上限
- 单文件大小限制（写超过即截断/失败）
- 简易网络断开（Windows 防火墙规则或 Linux netns，文档明确这是"opt-in 严格模式"）
- Pyodide 备选方案探索（用于不允许 subprocess 的环境）
- 语义 verifier 升级：对 ComputeAction 产出的 PNG / CSV / JSON 做内容级校验

---

## 5. 风险与缓解

| # | 风险 | 严重度 | 缓解策略 |
|---|---|---|---|
| 1 | Windows 上 isolation 实现比预期复杂 | 高 | 23.0 不做 Job Objects；docs 写清楚 best-effort；接受工程妥协 |
| 2 | 模型生成的 Python 脚本质量不稳定 | 中高 | 23.0 不优化生成质量，先把执行通路打通；Phase 21 auto-repair 兜底 |
| 3 | 用户不读脚本就 approve | 中 | 摘要模式做好；高 risk_level 强制展示更多上下文 |
| 4 | ComputeAction 滥用变成"任何任务都跑代码" | 中 | Phase 24 capability-first 原则 + Recipe quota 限制 |
| 5 | scratch dir 占满磁盘 | 低 | rollback / cleanup 路径覆盖；超大输出在 verifier 阶段拒绝 |
| 6 | trace 信息泄漏（脚本 stdout 里有敏感数据） | 中 | trace 默认截断；用户可关闭 stdout 记录 |
| 7 | §10.7 ledger 第 3 个例外引发"项目是否破戒"的疑虑 | 低 | 文档诚实说明：这是按设计的 deliberate exception，与 Phase 5/16 同性质 |

---

## 6. 实验度量

Phase 23 完成后，回答这些问题：

1. **能力**：demo 任务（怪 CSV 清洗）跑通了吗？rollback 干净吗？
2. **隔离强度**：sandbox 探针测试通过率 100%？（env 清理 / cwd 限制 / timeout / 不可访问 workspace 外路径）
3. **诚信**：所有用户接触的文档/UI/CLI 输出有没有出现 "security sandbox" 之类的误导措辞？
4. **回归**：Phase 0–22 全部 681 测试是否仍通过（含 §10.7 invariant 测试，扣除 PYTHON_COMPUTE 这个 deliberate 改动）？
5. **§10.7 ledger**：是否如实记账为第 3 个 exception？
6. **escape hatch 纪律**：Recipe 默认 `allow_compute_action=false`，行为零变化？

任一答案为否，Phase 23 视为未完成。

---

## 7. 版本规划与 §10.7 ledger 后续

- Phase 23.0–23.2 合并为 v0.23.0 release
- Phase 23.3（hardening）作为 v0.23.x 增量
- Phase 24 = "Recipe 体系按 capability-first 重整 + ComputeAction escape hatch 在 flagship pack 落地" = v0.24.0

§10.7 ledger 在 v0.23.0 发布后会变成：

```
28 phase shipped, 26 zero-kernel-touch, 3 deliberate exceptions:
  Phase 5  — forbidden_paths (Memory & personalization)
  Phase 16 — ActionType.FETCH (WebCollect)
  Phase 23 — ActionType.PYTHON_COMPUTE (Sandboxed ComputeAction Engine, Isolation-first)
```

这条 ledger 是 LocalFlow 的工程身份，必须诚实维护。

---

## 8. 立即执行的第一步（待用户确认后启动）

Phase 23.0 的第一个 PR 应该是**纯 schema 工作**——零行为改动，零 §10.7 触碰之外的 kernel 改动，但已经把 ComputeAction 的契约钉死：

1. `app/schemas/compute.py` 新建：`ComputeAction` / `SandboxPolicy` / `ArtifactSpec` / `ComputeOutcome`
2. `app/schemas/action.py` 扩 `ActionType.PYTHON_COMPUTE`，照搬 Phase 16 FETCH 的注释模式（明确标第 3 个 §10.7 exception）
3. `tests/test_compute_action_schema.py` 新建：~6 个测试，钉死字段必填 / 默认值 / 校验规则
4. **不动** executor / policy_guard / sandbox runtime（留给 Phase 23.0 第二步）

通过这一步可以：
- 让团队评审 ComputeAction 契约是否合理
- 让后续步骤有稳定的 schema 锚点
- 把 §10.7 第 3 个例外的"主动登记"动作完成

确认这份蓝图无误后，开始这一步。
