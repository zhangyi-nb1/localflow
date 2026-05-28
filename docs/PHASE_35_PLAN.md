# Phase 35 方向细化与任务规划：可验证 LLM 产物 Harness

> 状态：**已锁定 / §10.7 已审查（零 kernel 触碰）**　·　日期：2026-05-29　·　适用分支：`main`（v0.32.x-dev → v0.33.0）
>
> 本文记录一次**方向细化**决策与 Phase 35–37 的任务规划。它不替代 `docs/PROJECT_DIRECTION.md`，
> 而是在其 harness-first 基调下，把"演示层 / flagship 场景"收敛到一个具体、可演示、有证据支撑的方向，
> 并据此排出 Phase 35–37。对 `PROJECT_DIRECTION.md` 的建议修改见 §9；落地状态见 §11。

---

## TL;DR — 本次锁定了什么

- **基调不变**：LocalFlow 仍是 harness-first 的本地 Agent Execution Harness（沿用 Route B：阶段式 + 阶段内 react loop）。
- **演示层收敛**：把 flagship 场景锁定为 **「可验证的 LLM 产物流水线」**——让一个被 harness 约束的生成过程产出产物，再用**独立验证作为执行闸门（verify-as-gate）**决定 ship 还是 rollback。
- **Flagship demo**：**「带出处核验的文献综述」**——把一批论文 PDF 综述成笔记，综述里每一条论断必须可追溯到某篇源文档的具体片段，追溯不到的被标记并交人工复核。
- **驱动约束（写下来以免后续漂移）**：本项目首要用途是作为**大模型应用开发工程师简历中的 harness 作品**。目标函数 = 让 HR/面试官 5 分钟扫 repo 的第一印象 + 45 分钟深聊时能讲出的工程判断。因此规划优先级是"把已有的强 harness 能力 surface 出来、用一个可信场景演示、用 eval 数字证明"，而**不是继续铺广度**。
- **本阶段新增的差异化主张**：把"验证"从事后看板变成**执行中的闸门 + 可回滚**，这是当前市场（见 §4）尚未被成熟占据的空位。

---

## 1. 背景与目的

`PROJECT_DIRECTION.md` 已经把基调锁成 harness-first，并明确 Route B。但它存在两个需要处理的现实：

1. **战略文档的 roadmap 已过期**：其 `Current Roadmap Bias` 段停在「Phase 27+」，而项目已 ship 到 Phase 34（32 个 release、1062 测试通过）。Phase 34 之后"下一步是什么"没有被文档回答。
2. **定位仍偏应用工具气质**：对外叙事和 demo 仍以"按文件类型整理乱目录"为主，这在简历读者眼里像个人效率工具，而非 LLM 应用工程师该建的 agent 基础设施。

本文的目的：在不动基调的前提下，**给出一个具体的演示定位 + 任务级 roadmap**，让项目从"harness 内核已成熟、但没有一个能打的场景和数字"这个拐点走出去。

---

## 2. 锁定的定位决策

### 2.1 一句话定位

> LocalFlow 是本地 Agent Execution Harness；本阶段以**「可验证 LLM 产物流水线」**为旗舰演示——
> 一个被 harness 约束（typed plan · dry-run · approval · rollback）的生成过程产出产物，
> 再由**独立 verifier 作为执行闸门**判定产物是否可交付，不达标即回滚或转人工。

### 2.2 LLM 在系统里的角色（重要：它不是"执行任意任务"的 LLM）

LocalFlow 里的 LLM 被关在三个**有边界**的角色里，而非一个自由行动的 autonomous agent：

| 角色 | 职责 | 边界 |
| --- | --- | --- |
| Planner | 只输出 typed 的 `ActionPlan`（Pydantic 校验） | 拿不到 shell；产物先经 dry-run 预览 + 审批 |
| Compute 生成器（可选） | 写 Python 脚本在沙盒子进程里真正生成产物（解析 PDF、做摘要、出图） | scratch 隔离、timeout、env scrub；非声明产物留 scratch 并回滚 |
| Judge | 语义 verifier 用 LLM-as-judge 给产物打分 / 判 grounding | 判决进入闸门，关键节点保留 human approval |

含义：本方向**不要求造一个通用 agent**。生成那一环可以"还不够聪明"——但因为它被关在预览 / 审批 / 验证 / 回滚里，不成熟也变得可用、可控、可追溯。这正是"用工程系统弥补模型不完美"的 harness 论点本身，也是与"调个大模型写报告"的本质区别。

---

## 3. 现状体检：对照飞书五层框架

| Harness 层 | LocalFlow 现状 | 判断 |
| --- | --- | --- |
| Context Injection | skill manifest + memory + workspace snapshot 注入；planner 吃 goal+snapshot+manifest | **基础具备但静态**——无 compaction / 检索 / 上下文分层 |
| Control | plan→dry-run→approval→ConfirmationPolicy(4 档)→drift budget→react loop(5 决策) | **最强**，正面命中"目标偏移"失败模式 |
| Action | typed `ActionPlan`(6 类) + Workspace(4 后端) + MCP + 沙盒 compute | **扎实但动作词汇偏文件操作**；广度已可能超前需求 |
| Persist | `trace.jsonl`(ActionTraceEvent) + rollback manifest + run store | **单次 run 强，长任务接力空白** |
| Observe & Verify | 6 结构检查 + 7 语义 grader + critic + eval suite | **第二强**；但**缺公开 eval 数字**、缺显式 completion gate |

**结论**：缺的恰好是 B/C 该补的——可公开的 eval 证据，以及一个让这些机制发光的具体场景。本规划据此排序，并**故意不**优先补长任务持久化（见 §8）。

---

## 4. 为什么是这个空位（证据支撑）

### 4.1 问题真实、且 2026 年仍未解决

- **连专家人工复核都漏掉幻觉**：NeurIPS 2025 有约 1%（53 篇）被接收论文带着编造引用，且每篇经过 3–5 位专家评审仍未被发现；ICLR 2026 投稿查出 50+ 处幻觉。
- **法律域**：斯坦福 2024 研究发现 LLM 回答法律问题时至少 75% 的时候编造判例；即便最好的模型在事实类基准上仍有约 17% 幻觉率。
- **LLM 的错误是"完整且自信"的**：与 OCR 出错时的乱码不同，LLM 抽取出错时输出往往完整自信，问题常到审计或对客户动作后才暴露。
- **生成天然缺乏可追溯性**：文档智能厂商承认 LLM 抽取是概率性的、缺乏可审计性——这几乎反向定义了 LocalFlow 的 trace + verifier 该解决什么。
- **正在杀死真实项目**：Gartner 判断到 2027 年底 40%+ 的 agentic AI 项目会因可靠性问题与目标不清被取消。
- **RAG 没解决它**：给了正确语境，模型仍可能误读来源、无标注地合并冲突证据。

### 4.2 市场在往"验证"走，但工具尚不成熟（这就是空位）

当前验证工具基本是两种形态，都不是本方向要做的：

1. **事后看板**（LangSmith / Langfuse / Ragas / Deepchecks）：在产物生成之后打 faithfulness 分；**打完分要不要拦、怎么回滚仍靠人**。
2. **刚冒头或绑死垂直的"闸门"**：vLLM 的 HaluGate（2025-12 才发）、Unstract LLMChallenge（绑死发票）、Snowflake Cortex（锁在数仓）。

**白色空位**：把"验证当成决定 ship-or-rollback 的执行闸门 + 可恢复 + 通用本地 harness"——目前散落在监控看板、刚发布的检测组件、垂直产品里，没有一个整合体。LocalFlow 已有的语义 verifier、rollback manifest、approval 闸门，恰好就是这个象限要的三样东西。

---

## 5. Flagship 场景规格：带出处核验的文献综述

### 5.1 用户与痛苦时刻

- **用户**：学生 / 研究者 / 任何写综述、做开题、读文献的人。
- **痛苦时刻**：让 AI 把 20 篇论文综述成一段，得到流畅、带引用的文字——但不知道"某研究发现 X 提升 12%"是真有出处还是编的。
- **两难**：要么自己重读 20 篇（自动化失去意义），要么信它然后被编造的引用坑。

### 5.2 走一遍 harness 流程

1. `plan`：生成多阶段计划——逐篇 PDF → 结构化摘要 → 综合成综述 → 生成 sources ledger。
2. `dry-run`：预览将产出哪些 artifact（每动作一行）。
3. 生成：用 LLM（planner / compute 生成器）产出逐篇摘要与综述（角色受限，见 §2.2）。
4. **grounding gate（核心）**：语义 verifier 把综述拆成论断，逐条判定能否追溯到某篇源摘要/原文片段，输出 grounded / ungrounded + 来源标注。
5. **闸门判决**：grounded 比例达标 → 通过；超过阈值的论断无出处 → 标记产物未通过 + 触发 rollback / 转人工，并把 ungrounded 论断列成"待人工核验"清单。
6. `trace` + sources ledger：每条论断在 trace 里绑定来源片段（或"无来源"），最终 sources ledger 作为 evidence bundle。

### 5.3 "完成"的定义（验收标准）

- 产物 = 综述笔记 + sources ledger。
- 一条论断"通过"当且仅当它能追溯到至少一个源文档片段；否则标 ungrounded。
- 闸门指标：**幻觉（无出处论断）召回率**、**grounded 误报率**、整体 grounded 比例阈值。

---

## 6. 路线图：Phase 35 / 36 / 37（任务级）

### Phase 35 — 定位收敛 + 止损（几乎零代码，但是 5 分钟扫 repo 的命门）

| # | 任务 | 为什么 | 验收证据 | kernel |
| --- | --- | --- | --- | --- |
| 35.1 | 更新 `PROJECT_DIRECTION.md`（Tracking Goal + Roadmap Bias + 锁定演示层 + flagship），见 §9 的 diff | 战略文档已过期、定位需明确 | PR diff | 否 |
| 35.2 | 处理"装饰性"缺口：UI 的 Workspace backend 选择器要么接通到 executor，要么在 flagship 叙事下诚实降级 | repo 不能有读起来半成品的东西 | UI 行为一致 + 测试 | 否（facade） |
| 35.3 | 重写 README 开篇 + research_pack 叙述，从"整理乱文件夹"换成"可验证 LLM 产物 harness / 带出处核验的文献综述" | 首屏定位 | README 评审 | 否 |
| 35.4 | 把本文归档为 `docs/PHASE_35_PLAN.md` | 方向细化留痕 | 文件入库 | 否 |

### Phase 36 — Flagship 垂直落地：可验证文献综述（本规划的核心）

| # | 任务 | 为什么 | 验收证据 | kernel |
| --- | --- | --- | --- | --- |
| 36.0 | 定义场景与验收：grounded claim 判定标准、产物结构（综述 + sources ledger）、eval task 的 ground truth | 先有验收再实现 | `docs/PHASE_36_DESIGN.md` | 否 |
| 36.1 | 输入域接入：让 recipe 接受一批论文 PDF，生成逐篇结构化摘要（复用现有 PDF/INDEX 能力） | 复用而非新原语 | 示例运行 + trace | 否 |
| 36.2 | 生成环节：LLM 把逐篇摘要综合成综述（planner / compute），输出经 dry-run 预览 | 让生成被 harness 约束 | dry-run 预览产物 | 否 |
| 36.3 | **grounding grader → gate（最核心）**：扩展语义 verifier，新增 claim-level grounding grader；**接成执行闸门**——verdict 决定 ship / rollback，而非只打分 | 正面解"虚假完成"；体现"verify-as-gate" | verifier output + eval（召回/误报） | 否 |
| 36.4 | rollback-on-fail 串联：闸门不达标时触发 rollback / 标记未通过 + 生成"待人工核验"清单 | 完成必须有恢复路径 | rollback 证据 + 清单 | 否 |
| 36.5 | trace + sources ledger：每条论断绑定来源片段，产出 sources ledger 作为 evidence bundle | completion gate / evidence bundle | `trace.jsonl` + ledger 文件 | 否 |
| 36.6 | demo 脚本 + 录屏：1 命令重建一个**含 1–2 处刻意植入幻觉论断**的示例 → 跑流程 → 展示 gate 抓出幻觉 | 简历/面试的钩子 | seed.py + 录屏 | 否 |
| 36.7 | 测试 + eval：grounding grader 单测 + 该 pack 的 eval task（幻觉召回率 / grounded 误报率） | 用 eval 而非模型自评定质量 | eval pass/fail | 否 |

> **kernel 触碰预期：整个 Phase 36 预期零 kernel 触碰**（verifier/grader/recipe 都在 facade 层）。若 grounding 意外需要新 `ActionType`，须按 §10.7 走 issue + 设计文档 + ledger 行。

### Phase 37 — 失败模式 benchmark + 公开数字（待 36 落地后细化）

| # | 任务 | 为什么 | 验收证据 |
| --- | --- | --- | --- |
| 37.1 | 用六大失败模式造最小 benchmark（每类 2–3 个任务） | 把失败变成可测量工程事件 | benchmark 集入库 |
| 37.2 | 跑对照：朴素 tool-call agent baseline vs LocalFlow | 证明 harness 真减少失败 | eval 结果表 |
| 37.3 | 把数字写进 README 一张表 + write-up | 从 implementation owner 到 capability owner 的证据 | README 表 |
| 37.4 | （可选）把对照 benchmark 写成公开 post | portfolio 增益 | 文章链接 |

---

## 7. 必须坚守的差异化（不能丢）

沿用 `PROJECT_DIRECTION.md` 列出的 6 条，**本阶段新增第 7 条**：

7. **Verify-as-gate**：验证不是事后看板，而是决定 ship / rollback 的执行闸门 + 可回滚 + 关键节点 human approval。这是 §4 定位图里的市场空位。

---

## 8. 边界与暂不做的事（做减法）

以下**故意不在 Phase 35–37 做**：

- **长任务 handoff / checkpoint / resume**（Persist 层空白）：投入大、对 flagship 叙事非必需。
- **更多 Workspace 后端 / agent-server 深化**：广度已可能超前需求；不再铺。
- **路线 A（代码域变更 agent，新增 EDIT 动作 + test-verifier）**：天花板更高但投入大、且要动 kernel。作为 flagship 立住后的潜在扩展保留。
- 继续堆窄 skill：只有当某 skill 是为演示/沉淀 harness 能力时才加。

沿用 `PROJECT_DIRECTION.md` 的 Boundaries：不做通用 OS 控制 agent、不做低代码平台、不做通用助理、不把任意 shell 作为默认路径。

---

## 9. 对 `PROJECT_DIRECTION.md` 的修改（已应用，见 §11）

### 9.1 Tracking Goal（更直接）

把"limited template-style local agent → ... Agent Harness"改成明确点出 flagship =
verifiable LLM-artifact pipeline + verify-as-gate。

### 9.2 Current Roadmap Bias（更新到 Phase 34 之后）

新增 2026-05-29 更新块：Phase 1–34 已 ship；下一阶段把演示层收敛为 flagship
「带出处核验的文献综述」，Phase 35 = 定位收敛、Phase 36 = flagship 垂直落地、
Phase 37 = 失败模式 benchmark。

### 9.3 在差异化列表新增第 7 条 Verify-as-gate。

---

## 10. 证据来源

1. arXiv 2602.05930 — NeurIPS 2025 编造引用失败模式（53 篇接收论文经 3–5 评审未发现）。
2. Medium / Neria Sebastien《On AI Hallucinations》— 斯坦福 2024 法律幻觉 75%/120+ 案件。
3. Springer / AI Review — 幻觉与事实性评估综述。
4. Medium《Don't Use LLMs as OCR》— 99% 仍失败、抽取错误"完整自信"。
5. LandingAI — LLM 抽取概率性/缺可审计/无源头追溯。
6. altersquare.io — OCR 误差级联、单字符 38,000,000→88,000,000。
7. Unstract — LLMChallenge 双 LLM 抽取-挑战。
8. Towards AI — Snowflake Cortex LLM-as-a-Judge。
9. getmaxim.ai — Gartner 40% agentic 项目 2027 前取消；No Free Labels。
10. vLLM — HaluGate（验证即闸门，2025-12）。
11. keymakr.com — 2026 幻觉仍未消除；human-in-the-loop 仍需。
12. deepchecks — RAG 中最常见的是 unfaithfulness。

---

## 11. 落地状态（执行追加，2026-05-29）

> 本节由实际执行 Phase 35 的会话追加，记录每个子任务的真实落地与判断。

### 35.1 — PROJECT_DIRECTION.md ✅
- Tracking Goal 重写为 verifiable LLM-artifact pipeline + verify-as-gate（§9.1）。
- Current Roadmap Bias 追加 2026-05-29 块，Phase 1–34 ship / Phase 35-37 规划（§9.2）。
- 差异化列表新增第 7 条 Verify-as-gate（§9.3）。
- 同步更新 `CLAUDE.md` §5 锁定决策，登记 Phase 35-37 + flagship 收敛。

### 35.2 — UI Workspace backend 装饰性缺口 ✅（选"诚实降级"而非"假驱动"）
**决策**：Docker/SSH 在 Streamlit rerun 模型里有真实的容器生命周期脆弱性，且 flagship
（文献综述）是 local-only。与其在 UI 里"假装驱动容器"，不如**诚实降级**：
- 新增纯函数 `app/ui/_workspace_backend.py::describe_ui_backend(spec, task_id)` —— 返回
  `executes_locally` / `cli_command` / `message`，可在无 Streamlit 下单测。
- Execute 页面：非 local 后端时显示 `st.info` 横幅说明"UI 本地执行 + 给出确切 CLI 命令"，
  消除"选了 docker 却跑在 host"的半成品味道（规则 F 诚信）。
- Settings 后端 tab：从"假驱动选择器"改为"校验过的 spec builder + CLI bridge"，docker/ssh
  显示可复制的 `localflow execute --workspace <spec>` 命令。
- 保留四后端可见 + 可达（经 CLI），不丢 Phase 28-33 的工作，不引入脆弱的容器-in-Streamlit 生命周期。

### 35.3 — README 定位叙述 ✅
- README.md + README.zh-CN.md 的 TL;DR、§2、§7.J research_pack 叙述前置 verify-as-gate +
  flagship「带出处核验的文献综述」；"整理乱文件夹"降级为入门示例而非旗舰。

### 35.4 — 本文档归档 ✅（本文件）

### kernel 触碰：整个 Phase 35 零 kernel 触碰，§10.7 ledger 不变（4 deliberate / 41 deliveries）。

---

*本文遵循 §10.7 精神：任何 kernel 边界变更须经 issue + 设计文档 + ledger 行登记为 deliberate exception。Phase 35–37 预期不触碰 kernel。*
