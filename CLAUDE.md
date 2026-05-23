# LocalFlow — Claude 工作偏好

本文件是用户在 LocalFlow 项目上对 Claude 的固定指令。每次新会话都会自动加载。
作者本人对项目当前状态不满意，明确写下这份偏好是为了让 Claude 协助把项目从
"功能有限的桌面整理 agent"推向"真正的本地 Agent Execution Harness"。

如与 `docs/PROJECT_DIRECTION.md` 冲突，以本文件为准；本文件未提及的，再去看那一份。

---

## 1. 用户当前的三个不满（必须时刻记得）

1. **定位不清**：LocalFlow 当前体感像"模板化桌面整理工具"，复杂任务一卡就要新写
   skill。用户认为这不智能，也不是 harness 应有的样子。
2. **灵感不够**：用户对 harness 的理解还不透彻，需要参考成熟、高 star 的 harness
   项目来学习真实可用的 harness 长什么样。
3. **经验不足**：用户希望边调研、边推进、边调整目标，**不要急于锁死最终蓝图**。

任何重大建议、计划、代码改动，都必须能回答：它在缓解上面 1–3 中的哪一个？

## 2. 行动规则（按优先级）

### 规则 A — Harness-first，不要再加窄 skill

- 在提议任何新功能、新 skill、新 pack 之前，先问："这是 harness 内核能力，
  还是又一个窄模板？" 答案是"窄模板"就**不要做**，除非它是为了演示某个 harness 能力。
- 每条建议要能映射到这六个维度之一才值得提：**安全 / 可控 / 可恢复 / 可验证 /
  任务成功率 / 基于 trace 的持续改进**。映射不上的就砍掉。
- "再加一个 skill 把这个 corner case 包起来"是反模式。优先方向是**让通用动作原语
  更强**（如 Phase 23 的 `ComputeAction`），而不是让 skill 菜单更长。

### 规则 B — 调研先于重设计

用户明确表示需要参考项目。在做战略决策前，先输出**对标证据**：

- 必看的 harness / 长程 agent 项目（按优先级）：
  - **OpenHands** (All-Hands-AI/OpenHands, ~40k★) — 事件流 + Action/Observation 模型 + sandbox runtime
  - **goose** (block/goose, ~14k★) — 本地、可扩展、checkpoint
  - **Aider** (Aider-AI/aider) — diff-based 编辑、repo map、可回滚的 git 集成
  - **SWE-agent** (princeton-nlp/SWE-agent) — ACI（agent-computer interface）抽象
  - **smolagents** / **Inspect AI** / **AutoGen** — 评测和编排参考
- 输出形式：**横向对比表**（planning 模型 / 动作词汇 / 工具边界 / sandbox 模型 /
  权限模型 / 持久化 / eval / 失败恢复），不要只复述 README。
- 仅靠"我读过几篇博客"不算调研证据。**用 WebFetch / 看源码** 拿到一手材料后再下结论。

### 规则 C — 阶段性目标，不锁死蓝图

- 用户已经说"可以不在当前给出最终意见，根据调研结果和进度不断调整"。
- 每完成一个 Phase 或一段调研，**主动**重新审视 `docs/PROJECT_DIRECTION.md` 里的
  "Tracking Goal"和"Current Roadmap Bias"。如有更新建议，明确给出 diff 而不是只说"应该改"。
- 不要一次写超过两个 Phase 的细节计划。下一个 Phase 落地后再写下下个。

### 规则 D — 证据驱动，不靠直觉

任何"应该做 X / 不应该做 Y"的论断，必须挂上以下证据之一：

- `trace.jsonl` 中实际发生的事件
- 独立 verifier 的输出
- eval 套件 (`evals/workspace_pack/`) 的 pass/fail
- rollback / repair 路径的运行记录
- 对标项目的源码引用（带文件路径 + 行号）

没有证据的"我觉得"在本项目不被接受。

### 规则 E — §10.7 ledger 是工程身份，不能稀释

- 当前 kernel 改动次数：22 个 phase shipped，2 个 deliberate exception
  （Phase 5 `forbidden_paths`、Phase 16 `ActionType.FETCH`），Phase 23 的
  `ActionType.PYTHON_COMPUTE` 会成为第 3 个。
- 所有 kernel 边界改动（`app/harness/*` + `app/schemas/action.py` 的 ActionType
  枚举）默认拒绝，除非有充分论证写入 `docs/ARCHITECTURE.md` §10.7。
- 用户对"诚实记账"很在意——任何 kernel 改动**必须主动登记**，不能藏。

### 规则 F — 诚信措辞

Phase 23 已经定下命名纪律，扩展到全项目：

- **不要**说 "security sandbox"——LocalFlow 的隔离是 best-effort，写成 "isolation"。
- **不要**说 "fully automatic"——harness 的本质就是要 approval。
- **不要**夸大智能水平。卡住的任务就老实说卡住，并定位到是 plan / execute /
  verify 哪一层失败。
- 用户对宣传性措辞很敏感，宁可少说也不要 oversell。

### 规则 G — 区分 harness 层和应用层

- **Harness Kernel** = 项目真正的价值，是 Claude 在本项目的主战场
- **Skill** = 稳定、可复用的高频能力
- **Pack / Recipe** = 应用层 demo，是脸面但不是核心
- 公开叙述（README / UI 文案 / 演示）必须把 harness 生命周期讲清楚：plan / risk /
  preview / approval / trace / verify / repair / rollback。不能让用户以为这是"文件整理器"。

### 规则 H — 提问与确认

- Auto Mode 下默认不打断用户问问题，按合理判断推进。
- **例外**：涉及"是否破坏 §10.7"、"是否新增 kernel exception"、"是否改动 8 条铁律"，
  必须先停下来跟用户确认。这三类不是工程细节，是项目身份问题。

### 规则 I — 当前未提交工作的纪律

当前 git 工作树里 Phase 23.0 + 23.1 + 部分 23.2 的代码全堆在一起（详见 README 状态）。
在用户没有显式说"全部 commit"之前：

- 优先建议**按 Phase 23.0 → 23.1 → 23.2 切片提 commit / PR**
- 任何新建议都要考虑：是否会把这堆未提交工作越堆越大？如果会，先帮用户清理。

## 3. 沟通风格

- **中文回复**为主，技术名词保留英文（harness / sandbox / verifier / rollback /
  trace / commit / PR 等不要硬译）。
- 回答要**直接给判断**，不要堆叠"可能 / 也许 / 取决于"。判断后面附理由。
- 对用户战略性问题（路线、定位、对标），用**结构化输出**（表格 / 列表 / 对比），
  不要写成大段散文。
- 对用户战术性问题（"这个 bug 怎么修"），先给最短答案，再附必要上下文。

## 4. 起手三件事（每个新会话默认先做）

如果用户没明确指令，开局先扫这三件事再回复：

1. `git status` + 最近 3 条 commit message — 知道项目当前处于哪个 phase
2. 看 `docs/PROJECT_DIRECTION.md` 的 Tracking Goal — 知道当前北极星
3. 看 `docs/PHASES.md` 的最新 phase 段落 / 最新的 PHASE_*_PLAN.md — 知道正在做什么

这三步加起来不超过 30 秒，但能避免"答了一堆但跟项目当前状态对不上"的尴尬。

---

> 这份偏好本身也是活文档。用户在调研 / 推进过程中改变想法时，主动建议更新本文件，
> 不要让它和真实意图慢慢偏离。

---

## 5. 已锁定的决策（2026-05-24，OpenHands 调研后）

调研报告：[docs/research/OPENHANDS_HARNESS_STUDY.md](docs/research/OPENHANDS_HARNESS_STUDY.md)

### 架构路线 = 路线 B（阶段式 + 阶段内 react loop）

- **保留**：plan / dry-run / approval / verify / rollback 五段式骨架
- **改造**：execute 阶段从 batch-顺序 改为 step-by-step LLM-loop（已批 plan 范围内 +/- N 步漂移）
- **不走** 全面 LLM-loop（会丢掉 LocalFlow 的差异化变成 OpenHands 的弱复制品）

### Phase 顺序（已锁定 Phase 24-25，更远的不锁）

1. 清理当前 Phase 23 未提交工作 → 发 v0.23.0
2. **Phase 24 = C1 ActionEvent 重构**（三流合一）
3. Phase 25 = C4 Orphaned-Action 反馈
4. Phase 26 = 阶段内 react loop 落地

后续 Phase 在前置 Phase 落地后再写细节计划。

### 提议代码改动时的硬约束

- 任何"加新 Action 类型"提议 = 先问"能不能让 LLM 用 PYTHON_COMPUTE / 现有动作做"
- 任何"加新 skill"提议 = 先问"能不能让 ActionEvent + react loop 自然处理"
- 任何"动 harness kernel"提议 = §10.7 ledger 登记 + 用户确认
- 任何"丢 dry-run / rollback / verifier"提议 = **直接拒绝**，这是项目身份

### Pre-push 防呆（v0.23.x 起强制）

`.githooks/pre-push` 在每次 `git push` 前跑三条检查（顺序 fail-fast）：
1. `ruff check app/ tests/` ← 镜像 CI step 5
2. `ruff format --check app/ tests/ examples/` ← 镜像 CI step 6
3. `pytest --tb=no -q` ← 镜像 CI step 7

新 clone 仓库时**必须**一次性激活：

```bash
git config core.hooksPath .githooks
```

紧急 push 跳过用 `git push --no-verify`（仅在确认 CI 修复 PR 时使用）。**任何"local 全过、CI 红"的情况都是 hook 没装或被绕过的信号**。
