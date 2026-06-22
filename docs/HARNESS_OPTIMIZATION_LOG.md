# LocalFlow Harness Engineering — 优化与学习日志

> 本文档是 LocalFlow **harness-engineering 优化的活账本**，同时兼作大厂面试
> **八股学习材料**。设计目标：每一轮优化既改进 harness，又沉淀一条可背诵、
> 有出处的面试知识点，一次投入双重产出。
>
> 永久流程备忘存于 `~/.claude/projects/.../memory/harness-optimization-flow.md`
> 与 `harness-optimization-campaign.md`（新会话自动加载）。

---

## 工作流程（每轮必做四步）

每一轮（Round, `Rn`）都按这四步记录，缺一不可：

1. **措施（intent）** —— 这轮优化什么、属于哪个价值优先级梯队、为什么值得做。
2. **改动（changes）** —— 落到项目的**精确文件 + 行位置**，方便你 locate / 修改 / 学习。
3. **量化结果（metric）** —— 这轮的 before→after 指标。**诚实（规则 F）**：性能轮给
   性能/召回/误报数字；文档轮给覆盖/一致性指标，**不编造假性能数字**。
4. **知识点八股（KB 八股）** —— 对应的面试知识点，**标注 offerclaw 知识库出处**
   （格式 `llm_app_interview_NN_*.md §<小节> L<行号>`）。

> **知识库路径**：`/Users/zhangronglei/Desktop/XIANGMU/offerclaw/knowledge_base/learning_resources/`
> 核心 harness 章节：ch10 `_10_harness_engineering.md`、ch11 `_11_harness_core_workflow.md`、
> ch12 `_12_harness_scenarios.md`；相邻 ch04/06/09 = agent basics / planner / skills。

**纪律约束**：① 优先按"一轮一 commit"切片（规则 I），每轮 = 一个可定位 diff；
② KB 行号写进本文前先 `grep` 核对（生成它的 workflow 可能差几行）；
③ 触碰 kernel 的轮次（§10.7：`app/harness/*` + `ActionType` 枚举）实现前**必须**先经用户确认（规则 H）。

---

## 进度记分牌

| 轮 | 标题 | 命中层 / 失败模式 | 触碰 kernel | 工作量 | 状态 | 量化结果 |
|---|---|---|---|---|---|---|
| R1 | 修 §10.7 ledger 数字漂移 `4/43→4/44` | harness_self（诚信） | 否 | small | ✅ done | 文档矛盾 1→0；+1 回归测试守护 |
| R2 | README 五层框架自评表（EN+zh） | 全部 5 层（定位） | 否 | small | ✅ done | 5 层显式映射 0→5/5（双语一致） |
| R3 | Option 1：guard ON/OFF 真 LLM ablation | Observe&Verify / false_completion | 否 | medium | ⏳ todo | 目标：recall/误报数字进 README §3 |
| R4 | react loop 真实 trace（LOOP_DECISION/drift ABORT） | Control / goal_drift | 否 | medium | ⏳ todo | 目标：≥1 条真实 trace artifact |
| R5 | trace.jsonl → agent 可消费的 repair 输入 | Observe&Verify→Control | 否 | medium | ⏳ todo | 目标：闭环 trace→hint 注入 |
| R6 | Phase 38 stage-level checkpoint/resume/handoff | Persist / context_rot | 否（facade） | large | ⏳ todo | 目标：context_rot gap→mitigated(stage) |
| R7 | Reflexion 同动作死循环检测器 | Control | **是** ⚠️ | medium | 🔒 待确认 | 需 §10.7 登记 + 用户确认 |
| R8 | 删 CONVERT/ANALYZE 死枚举 | Action | **是** ⚠️ | small | 🔒 待确认 | 需 §10.7 登记 + 用户确认 |

图例：✅ done · ⏳ todo · 🔒 触碰 kernel，实现前需用户确认。

---

## §0 初始项目状态（baseline · v0.35.0 · 2026-06-22）

### 0.1 一句话定位

LocalFlow 是一个 **本地 Agent Execution Harness**：模型只产出 typed `ActionPlan`，
kernel 是唯一能碰磁盘的代码；生命周期 = **plan → dry-run → approval → execute →
verify → rollback**，第 7 条差异化 = **verify-as-gate**。flagship = 带出处核验的
可验证文献综述。flagship 弧（Phase 35→36→37）已闭合。

### 0.2 五层框架自评矩阵（对照 KB ch10 §五层 L44-48）

| 层 | 机制 | 成熟度 | 缺口 |
|---|---|---|---|
| Context Injection 信息注入 | content-aware planning（`file_scan` 注入预览）+ `GoalInterpreter` clarify | 🟡 partial | 无 compaction / token-budget / 渐进披露 |
| Control 执行控制 | 五段 `control_loop` + react loop（drift budget）+ `ConfirmationPolicy` 4-tier + auto-repair | 🟢 **强** | react loop 真实 LLM trace 未验证 |
| Action 行动执行 | typed `ActionType` + `FETCH` + 沙箱 `PYTHON_COMPUTE`；per-skill schema 收窄 | 🟡 partial | 词汇窄；CONVERT/ANALYZE 死枚举；无 Bash/Git/browser/DB |
| Persist 状态持久化 | `RollbackManifest` + sha-drift + `trace.jsonl` + `Workspace` 门面（per-run） | 🔴 **弱** | 无跨 session checkpoint/resume/handoff |
| Observe & Verify 观察验证 | 结构 `Verifier`（7 检查）+ `SemanticVerifier` + claim-grounding gate + 7 deliverable verifiers | 🟢 **强** | 唯一有真实 LLM 端到端验证的层 |

**净判断**：2 强（Control / Observe&Verify）+ 1 故意窄（Action）+ 2 诚实弱
（Context Injection token 管理 / Persist 跨 session）。

### 0.3 六大失败模式状态（对照 KB ch12 §大类表 L59-94；实现 `app/eval/failure_modes/`）

| # | 失败模式 | 状态 | guard |
|---|---|---|---|
| 1 | goal_drift（跑偏） | 🟢 mitigated | drift budget + approval |
| 2 | false_completion（虚标完成） | 🟢 mitigated（**真 LLM 证据**） | claim-grounding gate |
| 3 | context_rot（失忆/无法长期工作） | 🔴 **gap**（guard on/off 都 fail） | 无 |
| 4 | tool_runaway（env/工具失控） | 🟢 mitigated | policy_guard |
| 5 | quality_entropy（质量熵增） | 🟢 mitigated | deliverable verifier |
| 6 | harness_self（自身工程） | ⚪ process | §10.7 ledger + boundary lint |

### 0.4 测试基线

- 全套：**1161 collected / 1132 passed / 29 skipped（Docker/SSH 不可用）/ 0 failed**（exit 0）。
- §10.7 ledger（截至 v0.35.0）：**4 deliberate exceptions / 44 deliveries / 40 zero-touch（90.9%）**。

### 0.5 已知红线 / 纪律风险（baseline 时点）

- **未提交工作堆积**（规则 I）：REPORT.md 的 Fix A/B/C + 新 grounding 测试 + demo +
  docs + `pyproject`/`__init__` 版本号全部未 commit。本日志后续轮次的 diff 会与这批
  混在一起，**强烈建议尽快按切片 commit**，否则 per-round 定位会越来越糊。
- **"long-running" 招牌红线**（规则 F）：只有 R6（Phase 38）落地后才能挂；当前严禁宣称。

---

## R1 — 修复 §10.7 ledger 数字漂移（`4/43 → 4/44`）

**梯队**：🟢 第一梯队（立即做 · 零成本 · 保护信誉）。

### 措施

诚信账本（§10.7 ledger）是 LocalFlow 的工程身份。失败模式 benchmark 的
`harness_self` 行（项目的"诚信行"）硬编码的比例 `4/43` 与 README、PHASES.md 的
`4/44` **自相矛盾**。一个把"诚实记账"当卖点的项目，其展品里藏着对不上的分母 ——
这是规则 E 禁止的漂移，也是面试官一旦 grep 到就会怀疑你**所有**数字的自残式伤口。

### 改动

| 文件 | 行 | 改动 |
|---|---|---|
| `app/eval/failure_modes/benchmark.py` | 308 | detail 字符串 `43 deliveries` → `44 deliveries` |
| `tests/test_failure_mode_benchmark.py` | +`test_harness_self_ledger_ratio_matches_docs` | 断言 detail 含 `4 deliberate exceptions / 44 deliveries` 且**不含** `43 deliveries`，钉死防漂移 |

### 量化结果

- 文档一致性缺陷：**1 → 0**（benchmark.py 现与 README:215 / PHASES.md:1908 一致）。
- 回归测试：failure_mode benchmark 测试 **9 → 10**，全过（`pytest tests/test_failure_mode_benchmark.py` = 10 passed）。
- 验证渲染：`python -m app.eval.failure_modes` 输出 harness_self 行实测显示 `4 deliberate exceptions / 44 deliveries`。
- 全局扫描：`grep -rn "43 deliveries" app/` = clean（无残留）。

### 知识点八股

> **面试问题**："你怎么保证你的项目宣称的指标是可信的？"

**答**：把"质量左移 + steering loop"用在自己身上——一次手动改、多次/可复发的就改成
机器强制的约束。这里我没止于改一个数字，而是**加了一条回归测试钉死它**，让任何未来
的 ledger 漂移在 CI 里直接 fail。这是 KB 说的 architecture-fitness / 行为 harness：
**测试通过 ≠ 需求正确，连"我的文档诚实"这件事都要有机器证据，不能靠自觉**。

- KB 出处：`llm_app_interview_11_harness_core_workflow.md §第二阶段 调控循环 steering loop`
  （L252「review 中发现一次问题可手动改，发现多次就应该改 harness」）+
  §三类调控目标 architecture fitness / behaviour harness（L260-266）。
- 项目内对应：规则 E（ledger 是工程身份）+ `tests/test_kernel_boundary.py`（同类 fitness 函数）。

---

## R2 — README 五层框架自评表（EN + zh-CN）

**梯队**：🟢 第一梯队（立即做 · 零成本 · 定位）。

### 措施

KB 把 harness 拆成 5 个标准层（Context Injection / Control / Action / Persist /
Observe & Verify）。LocalFlow 在 5 层都有机制，但公开 README **从未用这套层语言映射**
——读者容易把它当"文件整理器"。补一张一屏自评表，把每个机制对到层 + 诚实标成熟度
（🟢🟡🔴），直接服务规则 G（叙述 harness 生命周期）并给出面试开场白。

### 改动

| 文件 | 行 | 改动 |
|---|---|---|
| `README.md` | §2 内（L190 起，§3 前） | 新增 `### The five-layer harness map` 表 + "Honest self-assessment" 段，链到本日志 |
| `README.zh-CN.md` | §2 内（"LocalFlow 不是"后，§3 前） | 新增 `### Harness 五层框架自评` 表（中文镜像），链到本日志 |

### 量化结果

- 定位覆盖：canonical 5 层显式映射 **0 → 5/5**，双语（EN+zh）一致。
- 诚实标记：每层带 🟢/🟡/🔴 成熟度 + 2 强/1 窄/2 弱自评（规则 F：标 gap 不标愿景）。
- 不破坏：`ruff check app/ tests/` = All checks passed；`ruff format --check` = 289 files already formatted。
- **诚实声明**：本轮是文档轮，指标是"定位覆盖/一致性"，**没有性能数字**——
  把它包装成性能提升才是规则 F 禁止的 oversell。

### 知识点八股

> **面试问题**："介绍一下你这个 harness 的整体架构 / 它和普通 tool-call agent 的区别？"

**答**：用五层框架自评开场——"我的 harness 映射到 Context Injection / Control /
Action / Persist / Observe & Verify 五层；**Control 和 Observe & Verify 是我的承重强项，
Persist 跨 session 和 Context 的 token 管理是我诚实承认的弱项**。" 主动用框架自定位 +
点名自己的弱层，是面试官重点打分的成熟度信号，也是"这不是文件整理器"最强的防御。

- KB 出处：`llm_app_interview_10_harness_engineering.md §五层框架`（L44-48）；
  `llm_app_interview_12_harness_scenarios.md §状态丢失 + Harness 五层结构`（L355-395，自评矩阵）；
  `llm_app_interview_11_harness_core_workflow.md §三层关系一句话总结`（L131-139，Context/Harness/Infra 边界）。
- 项目内对应：规则 G（区分 harness 层 / 应用层，公开叙述要讲清生命周期）。

---

## 待办 backlog（R3–R8 · 优先级序）

> 详细价值/工作量/KB 出处见 `memory/harness-optimization-campaign.md` 与会话交叉分析。
> R3–R6 零 kernel；R7/R8 触碰 §10.7，**实现前需用户确认**。

- **R3（下一步主推）** Option 1 真 LLM ablation：`literature_review_pack` vs
  `literature_review_pack_nogate.yaml` 在 ~10-12 源真实语料上并排跑，recall/误报进
  README §3。KB：ch12 §过早标完成 L150-202 + §无法自证 L233-267。
- **R4** react loop 真实 trace：flagship 开 `react_mode` 抓含 `LOOP_DECISION_REPLACE` /
  drift `ABORT` 的 trace.jsonl，存 `docs/test_artifacts/`。KB：ch04 §ReAct L135-163。
- **R5** trace→agent 自纠输入：验证器 FAIL 时把相关 trace 行摘要成 `user_hint` 注入
  auto-repair / react REPLACE。KB：ch11 §可观测性被放大 L182。
- **R6** Phase 38 stage-level checkpoint/resume/handoff（progress file + feature-list
  状态机）→ 把 context_rot 翻成 mitigated(stage-level)，唯一能挂"long-running"的事。
  KB：ch12 §跨 window 接力 L297-329 + §仅靠 compaction 不够 L331-353。
- **R7** ⚠️ Reflexion 同动作死循环检测器（`react_loop.py`，触碰 kernel）。KB：ch04 §Reflexion L165-169。
- **R8** ⚠️ 删 CONVERT/ANALYZE 死枚举（`action.py` ActionType，触碰 kernel）。KB：ch10 §Action L46。
