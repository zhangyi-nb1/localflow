# LocalFlow Agent Harness Engineering 实验分析报告

> 项目地址：<https://github.com/zhangyi-nb1/localflow>  
> 报告目的：基于当前 LocalFlow 项目进度，分析其是否已经体现 Harness Engineering，解释为什么当前仍难明显感知“正确率提升”和“复杂任务稳定性”，并提出后续实验化演进路线。  
> 报告日期：2026-05-15

---

## 1. 摘要

LocalFlow 当前已经可以被定义为一个 **基于 Harness 架构的本地个人自动化 Agent**。它的核心不是“让模型本身不犯错”，而是通过外部执行支撑系统，把模型可能产生的错误限制在可预演、可拦截、可验证、可回滚的范围内。

当前项目已经具备较完整的 Harness 基础能力：结构化 ActionPlan、Policy Guard、dry-run、approval token、workspace containment、forbidden paths、rollback manifest、independent verifier、audit log、MCP 入口和 Streamlit WebUI。

但是，它目前主要体现的是 **安全执行 Harness**，而不是完整成熟的 **长任务智能 Agent Harness**。它已经证明 Agent 不会轻易乱改本地文件，也能在失败时记录、回滚和验证；但还没有充分证明 Agent 在复杂长任务中能够显著提高任务成功率、进行基于 trace 的失败分析、局部修复、语义验证和持续迭代。

因此，LocalFlow 当前的真实阶段应判断为：

```text
一个完成度较高的 Agent Execution Harness 原型；
但还不是成熟的 long-horizon autonomous agent。
```

下一步不应继续简单堆叠小功能，而应围绕以下方向升级：

```text
Trace/Eval Harness
→ TaskGraph 长任务编排
→ Workspace Pack Builder 强场景
→ Semantic Verifier
→ Repair Loop
```

做到这些后，LocalFlow 才能从“安全的桌面自动化 Agent”升级为“真正能体现 Harness Engineering 的长任务个人工作区 Agent”。

---

## 2. 当前项目程度评价

### 2.1 总体判断

当前 LocalFlow 已经具备比较完整的工程原型。它不是普通 LangChain / Agent Demo，而是一个围绕 LLM 执行行为构建外部控制系统的 Harness 项目。

仓库当前已有的主要能力包括：

```text
完整执行生命周期
agent meta-skill
多个 specialist skills
Tool Registry
Memory
MCP Server
Streamlit UI
测试体系
Release 与文档
```

当前项目可以分为三层价值：

```text
应用层：个人 workspace 自动化
框架层：Skill / Tool / Memory / MCP 扩展
核心层：Harness Kernel 控制副作用执行
```

### 2.2 当前评分

| 维度 | 当前评价 | 说明 |
|---|---:|---|
| Harness 架构完整度 | 8.5 / 10 | dry-run、approval token、policy guard、rollback、verifier、MCP/UI 均已具备 |
| 工程化程度 | 8 / 10 | release、CI、tests、docs、MCP、UI 已具备 |
| Agent 智能性 | 5.5 / 10 | meta-skill 能做 compound plan，但任务能力仍偏文件整理/简单资料处理 |
| 长任务复杂度 | 5 / 10 | 尚未体现几十步、多阶段、失败修复、重规划 |
| 正确率提升证据 | 4.5 / 10 | 有安全测试，但缺少复杂任务成功率 eval suite |
| 简历可展示性 | 7.5 / 10 | WebUI 提升明显，但还需要强 demo 和评估结果 |

### 2.3 当前阶段定义

更准确的项目定位是：

```text
LocalFlow 是一个面向个人 workspace 的 Agent Execution Harness 原型。
它通过工程约束、审批、回滚和验证机制降低 Agent 在本地副作用任务中的误操作风险。
```

还不应过度宣传为：

```text
成熟的长任务自主 Agent
生产级安全本地操作系统 Agent
完全避免错误的 Agent
```

---

## 3. Harness Engineering 的本质表现

### 3.1 Harness 不是让模型变聪明

Harness Engineering 的本质不是直接提升模型智力，也不是简单增加几个工具。它的核心是：

```text
模型负责智能：理解、推理、规划、决策。
Harness 负责工程控制：上下文、边界、执行、状态、验证、恢复、评估。
```

因此，Harness 不保证模型不产生错误判断。它真正解决的是：

```text
模型计划错了，能否被拦截？
执行失败了，能否被记录？
结果不完整，能否被 verifier 发现？
动作有副作用，能否先 dry-run？
任务中断，能否恢复？
执行错误，能否回滚？
失败模式，能否被 trace 分析并用于下一轮改进？
```

### 3.2 Harness 的五层能力

成熟的 Harness 至少包含五个层次：

| 层次 | 作用 | LocalFlow 当前状态 |
|---|---|---|
| Control Harness | 限制模型如何行动 | 已经较强 |
| Safety Harness | 防止危险副作用 | 已经较强 |
| Persistence Harness | 记录状态、支持恢复 | 已经具备 |
| Verification Harness | 判断结果是否达标 | 结构性验证强，语义验证弱 |
| Improvement Harness | 基于 trace/eval 迭代改进 | 当前较弱 |

LocalFlow 目前已经覆盖前三层，第四层只完成了结构性验证，第五层还没有真正建立。

### 3.3 Harness 的成熟迭代方法

Harness Engineering 不应是一次性设计，而应是持续闭环：

```text
真实任务运行
→ Trace 分析
→ 定向修改
→ 评估效果
→ 删除冗余
→ 继续迭代
```

这意味着系统不能只关注“有没有 dry-run / rollback”，还必须建立：

```text
真实复杂任务集
结构化 trace
失败类型归因
有针对性的 Harness 改造
改造前后任务成功率对比
冗余组件删除机制
```

当前 LocalFlow 还缺少这一完整闭环。

---

## 4. 当前项目已经体现的 Harness 能力

### 4.1 模型与执行解耦

LocalFlow 当前的重要设计是：

```text
LLM / Skill 负责生成结构化计划；
Harness Kernel 负责检查、预演、审批、执行、验证、回滚。
```

这区别于普通 Agent：

```text
用户请求 → 模型推理 → 直接调工具 → 直接改环境
```

LocalFlow 的流程更接近：

```text
用户目标
→ TaskSpec
→ ActionPlan
→ Policy Guard
→ Dry-run
→ Approval Token
→ Safe Execute
→ Verify
→ Rollback / Report
```

这是 Harness 架构成立的基础。

### 4.2 行动约束

当前项目已经限制模型不能直接执行副作用操作，只能输出结构化 Action。

已经具备的约束包括：

```text
模型不执行副作用，只输出 TaskSpec / ActionPlan / Action
路径必须在 workspace 内
delete 默认禁用
写操作必须 dry-run
默认不覆盖
verifier 独立于模型
```

这说明 LocalFlow 当前不是普通本地助手，而是一个受控 Agent 执行框架。

### 4.3 执行安全

当前 LocalFlow 已经体现以下安全机制：

```text
workspace containment
forbidden paths
approval token
rollback manifest
dangerous tool gating
MCP execute token
hash guard
```

这些机制主要解决的是 Safety Correctness 和 Operational Correctness。

### 4.4 多入口复用同一 Kernel

LocalFlow 当前已有 CLI、MCP Server 和 Streamlit UI 三类入口。关键点在于：这些入口不应各自实现执行逻辑，而应统一复用 Harness Kernel。

这使项目具备较强架构一致性：

```text
CLI 不是一套逻辑；
MCP 不是一套逻辑；
UI 不是一套逻辑；
三者都只是 driver，真正的副作用执行都经过同一个 Kernel。
```

### 4.5 插件化与扩展能力

LocalFlow 已有 Skill ABC、Skill Registry、Tool Registry、external skill loader、contract test 等设计。这说明它不是单功能工具，而具备框架化扩展雏形。

不过 external skill 目前仍属于 trusted Python code，并不是安全沙箱插件系统。这一点应在文档和面试中诚实说明。

---

## 5. 为什么当前没有明显感到“正确率变高”

### 5.1 Harness 主要提升任务级可靠性，不直接提升语义智能

你当前的真实感受是合理的：引入这些机制后，并没有明显感觉 Agent 的语义正确率大幅提升。

原因是当前机制主要提升：

```text
安全性
可控性
可恢复性
可审计性
```

而没有直接提升：

```text
语义判断正确率
任务规划质量
复杂任务完成率
分析深度
```

如果模型把一篇论文错分到另一个主题，只要路径合法、action 合法、文件存在，当前 Harness 很可能不会判断“语义分类错了”。

### 5.2 当前 Verifier 主要验证结构，不验证语义

当前 verifier 更偏向检查：

```text
文件是否存在
action 是否执行
rollback 是否完整
路径是否越界
chart 是否生成
manifest 是否一致
```

它还没有充分验证：

```text
摘要是否忠实于原文
分类是否符合主题
报告是否覆盖所有关键文件
图表是否回答用户问题
数据分析结论是否由数据支持
多阶段任务是否真正完成
```

所以当前 verifier 更像 execution verifier，而不是 semantic/task verifier。

### 5.3 当前任务复杂度太低

如果任务只是：

```text
按文件类型整理文件夹
生成一个 index.md
画一个文件数量柱状图
```

普通脚本或弱模型也能完成。因此 Harness 的优势不明显。

Harness 的价值在以下任务中才会显著放大：

```text
多阶段任务
多工具链
中间结果影响后续步骤
存在失败恢复
存在语义验证
存在用户偏好 / 禁区
存在真实文件误操作风险
```

当前 demo 还没有足够复杂到逼出 Harness 的不可替代性。

### 5.4 当前反思机制仍是局部 repair loop

当前项目中已有 planner repair：如果模型输出不满足 schema、validator 或 policy guard，就把错误反馈给模型重试。

但这只是：

```text
plan-level repair
```

而不是完整的：

```text
task-level reflection
```

成熟反思应当包括：

```text
执行后观察结果
发现目标未达成
分析失败原因
生成局部修复计划
再次 dry-run
再次执行
再次验证
```

当前还没有形成这种完整闭环。

---

## 6. “避免犯错”应拆成四种正确性

不要笼统说 LocalFlow 防止 Agent 犯错。应拆成四种正确性：

### 6.1 Safety Correctness：安全正确性

检查：

```text
是否越界？
是否误删？
是否覆盖？
是否触碰 forbidden path？
```

LocalFlow 当前做得较好。

### 6.2 Operational Correctness：执行正确性

检查：

```text
action 是否真的执行？
文件是否真的生成？
rollback 是否真的恢复？
日志是否完整？
```

LocalFlow 当前也较好。

### 6.3 Semantic Correctness：语义正确性

检查：

```text
分类是否合理？
摘要是否忠实？
数据结论是否正确？
报告是否回答用户问题？
```

LocalFlow 当前较弱。

### 6.4 Strategic Correctness：策略正确性

检查：

```text
任务是否被正确拆解？
顺序是否合理？
是否知道什么时候问用户？
失败后是否能重规划？
是否能避免重复做无效动作？
```

LocalFlow 当前较弱。

### 6.5 当前项目真实覆盖情况

| 正确性类型 | 当前覆盖程度 | 说明 |
|---|---:|---|
| Safety Correctness | 高 | workspace、forbidden path、delete 禁用、approval token 已较完整 |
| Operational Correctness | 高 | action log、rollback、verifier、artifact 已具备 |
| Semantic Correctness | 低到中 | 缺少 summary grounding、source coverage、claim verification |
| Strategic Correctness | 低到中 | 缺少 TaskGraph、阶段性 verifier、repair loop |

---

## 7. 当前最大问题：功能复杂度不足

你认为当前项目仍像“桌面文件夹整理工具”，这个判断有一定道理。

虽然当前项目已经加入 data analysis、PDF index、workspace visualizer、agent meta-skill、UI，但核心 demo 仍然容易被理解为：

```text
把本地文件整理一下，顺便生成摘要或图表。
```

这不足以体现成熟 Agent 的长任务能力。

真正能体现 Harness 的任务应该是：

```text
输入是混乱 workspace；
输出是一个可交付成果；
过程中需要多阶段、多工具、多验证、多恢复。
```

因此，下一阶段应把项目从：

```text
Personal Automation Agent
```

升级为：

```text
Workspace Delivery Agent Harness
```

也就是：

```text
不是帮用户整理文件，
而是帮用户把杂乱工作区转成一个可交付资料包、分析包或知识包。
```

---

## 8. 下一阶段主场景：Workspace Pack Builder

### 8.1 场景定位

建议新增一个主场景：

```text
Workspace Pack Builder
```

定义：

```text
把混乱资料、文档、数据和笔记整理成结构化交付物，
并通过 Harness 保证过程安全、可恢复、可验证。
```

这可以让 LocalFlow 摆脱“桌面整理工具”的观感。

### 8.2 强 Demo 设计

输入 workspace 示例：

```text
workspace/
  papers/
    paper_v3_final.pdf
    memory_agents.pdf
    rag_eval_survey.pdf

  notes/
    random_note1.md
    lecture_agent.txt

  data/
    experiment_results.csv
    model_scores.xlsx

  images/
    architecture.png

  misc/
    TODO.txt
    unknown.pdf
    broken.pdf
```

用户目标：

```text
请把这个 AI Agent 学习资料文件夹整理成一个可交付的学习资料包：
1. 按主题整理论文、笔记、数据和图片；
2. 每个主题生成 index.md；
3. 对实验数据生成统计图和简短分析；
4. 生成一个总 README，说明资料结构、关键结论、推荐阅读顺序；
5. 无法解析或低置信度文件放入 review 区；
6. 不删除任何文件，执行前先 dry-run，执行后可 rollback。
```

期望输出：

```text
output/
  README.md
  source_ledger.json
  topics/
    memory/
      index.md
      files...
    rag_eval/
      index.md
      files...
    agent_architecture/
      index.md
      files...
  charts/
    model_scores.png
  review/
    unresolved_files.md
  reports/
    summary.md
```

### 8.3 这个场景能体现的能力

它比简单 Downloads 整理更能体现：

```text
长任务
多阶段
语义理解
数据分析
报告生成
低置信度处理
验证
回滚
```

这才是适合展示 Harness Engineering 的强任务。

---

## 9. 后续需要补充的 Harness 能力

### 9.1 TaskGraph：从单计划升级为多阶段任务图

当前的单个 ActionPlan 不足以表达复杂长任务。

建议引入：

```text
TaskGraph / StagePlan
```

结构示意：

```text
TaskGraph
  ├── Stage 1: inspect_workspace
  ├── Stage 2: classify_sources
  ├── Stage 3: organize_files
  ├── Stage 4: summarize_topics
  ├── Stage 5: analyze_data
  ├── Stage 6: build_readme
  ├── Stage 7: verify_pack
  └── Stage 8: repair_if_needed
```

每个 Stage 应包含：

```text
expected_outputs
allowed_actions
verifier
failure_policy
max_retries
artifacts
```

这会让 LocalFlow 从“执行一个计划”升级为“推进一个长任务”。

### 9.2 TraceEvent：把日志升级成 trace

当前已有 execution logs，但仍需要专门 trace schema。

建议新增：

```python
class TraceEvent(BaseModel):
    event_id: str
    task_id: str
    stage_id: str | None
    event_type: str
    input_summary: str | None
    output_summary: str | None
    model_name: str | None
    prompt_hash: str | None
    tool_name: str | None
    action_id: str | None
    verifier_check: str | None
    status: str
    error_type: str | None
    duration_ms: int | None
    token_usage: dict | None
```

事件类型：

```text
model_call
plan_generated
policy_check
dry_run_rendered
approval_granted
action_executed
verifier_check
repair_generated
rollback_previewed
rollback_executed
```

目标不是单纯增加日志，而是让 trace 支持失败分析。

### 9.3 Failure Taxonomy：结构化失败分类

建议定义失败类型：

```text
schema_invalid
policy_blocked
path_forbidden
missing_output
semantic_mismatch
low_confidence_classification
unsupported_file
data_analysis_failed
chart_render_failed
summary_not_grounded
stale_plan
rollback_drift
user_ambiguity
```

每次 verifier fail 或 repair loop 触发，都应归类。

这样你才能证明：

```text
LocalFlow 不是主观调 prompt，
而是根据失败类型定向改 Harness。
```

### 9.4 Semantic Verifier：从结构验证升级到语义验证

对 Workspace Pack Builder，建议至少加入以下 verifier：

| Verifier | 检查内容 |
|---|---|
| Coverage Verifier | 所有输入文件是否被处理，或进入 review |
| Source Ledger Verifier | README / index 是否引用了实际文件来源 |
| Summary Grounding Verifier | 摘要中的关键 claim 是否能追溯到文件 preview 或 source ledger |
| Data Consistency Verifier | 图表数值是否和 CSV/XLSX 统计一致 |
| Low Confidence Verifier | 低置信度文件是否没有被强行高风险移动 |
| Deliverable Verifier | README、topic index、charts、review report 是否都生成 |
| Safety Verifier | 没有越界、delete、overwrite、forbidden path |

这一步是从“安全执行工具”升级为“可靠长任务 Agent”的关键。

### 9.5 Repair Loop：执行后局部修复

建议扩展为：

```text
Plan
→ Dry-run
→ Execute
→ Verify
→ 如果失败：
    Analyze failure
    Generate RepairPlan
    Dry-run repair
    Execute repair
    Verify again
→ Report
```

RepairPlan 只能针对失败项，不允许重做整个任务。

例如：

```text
verify 发现 charts/model_scores.png 缺失
→ repair 只生成 chart action
→ 不重新移动所有文件
```

这才是工程化的“反思”。

---

## 10. 需要新增 Eval Suite，而不是只依赖单元测试

当前 tests 能证明：

```text
代码逻辑没坏
安全策略有效
某些 action 能被拦截
```

但不能证明：

```text
Agent 在复杂任务上更成功
```

因此需要新增：

```text
evals/
  workspace_pack/
    task_001_basic_organize.yaml
    task_002_pdf_topic_summary.yaml
    task_003_data_chart_report.yaml
    task_004_compound_goal.yaml
    task_005_low_confidence_review.yaml
    task_006_forbidden_path.yaml
    task_007_prompt_injection_pdf.yaml
    task_008_rollback_after_drift.yaml
```

每个 eval 定义：

```yaml
goal: "..."
workspace_seed: "..."
expected_outputs:
  - README.md
  - topics/*/index.md
  - charts/*.png
graders:
  - safety_no_forbidden_path
  - all_files_accounted_for
  - source_ledger_complete
  - chart_matches_csv
  - summary_has_sources
  - rollback_restores
```

建议指标：

```text
safety_pass_rate
task_success_rate
semantic_coverage_score
repair_success_rate
rollback_success_rate
average_actions
user_intervention_rate
```

这样才能展示：

```text
加入 PlanCritic 后，compound task pass rate 从 X 提升到 Y；
加入 Semantic Verifier 后，错误报告漏检率下降；
加入 Repair Loop 后，首次失败任务中一定比例可自动修复。
```

这才符合 Harness Engineering 的实验化迭代方法。

---

## 11. 后续路线图

### Phase 9：Trace + Eval Harness

目标：从“功能展示”进入“可度量改进”。

新增：

```text
TraceEvent schema
trace.jsonl
failure taxonomy
eval task YAML
eval runner
eval report
```

验收标准：

```text
至少 20 个 workspace eval tasks
每个 task 有 expected outputs 和 graders
eval report 能输出 pass/fail、失败类型、trace 链接
```

### Phase 10：TaskGraph 长任务编排

目标：从单个 ActionPlan 升级到多阶段任务。

新增：

```text
TaskGraph
StagePlan
StageVerifier
StageResult
RepairPlan
```

流程：

```text
inspect
→ stage plan
→ per-stage dry-run
→ execute
→ verify
→ repair
→ next stage
```

验收标准：

```text
能完成 organize + summarize + analyze + package 的多阶段任务
每个阶段有独立 verifier
某阶段失败时只 repair 该阶段
```

### Phase 11：Workspace Pack Builder

目标：做一个真正能展示成熟 Agent 的主场景。

功能：

```text
按主题整理资料
生成 source ledger
生成 topic index
生成总 README
分析 CSV/XLSX
生成图表
低置信度 review
输出 final pack
```

验收标准：

```text
一个复杂 workspace demo 能稳定跑通
输出成果不是“整理后的文件夹”，而是“可交付资料包”
```

### Phase 12：Semantic Verifier + Repair Loop

目标：让 Harness 真正提升任务成功率。

新增：

```text
PlanCritic
SummaryGroundingVerifier
DataChartVerifier
CoverageVerifier
RepairPlanner
```

验收标准：

```text
eval suite 中记录 before/after：
无 repair loop 的成功率
有 repair loop 的成功率
失败类型分布变化
```

---

## 12. 项目成熟后的目标形态

当前 Agent：

```text
用户目标 → 生成 ActionPlan → Harness 执行
```

应升级为：

```text
用户目标
→ 任务理解
→ 生成 TaskGraph
→ 阶段性执行
→ 阶段性验证
→ 失败分析
→ 局部修复
→ 汇总交付物
→ 最终评估
```

也就是从：

```text
plan executor
```

升级为：

```text
goal-driven workspace operator
```

对比：

| 当前 | 升级后 |
|---|---|
| 生成一次 ActionPlan | 生成多阶段 TaskGraph |
| 文件整理为主 | 资料包/报告/分析交付为主 |
| verifier 检查文件存在 | verifier 检查内容覆盖、来源、数据一致性 |
| repair 主要修 schema/policy | repair 修失败阶段和缺失产物 |
| demo 是 messy downloads | demo 是完整 study/research/data workspace |
| 测试是单元/安全测试 | eval 是复杂任务成功率测试 |

---

## 13. 当前仍需注意的工程硬伤

### 13.1 源码格式问题

当前 raw GitHub 中一些核心 Python 文件仍显示为极少数超长行，例如 `control_loop.py`、`app/ui/main.py` 等。这会影响代码审查和项目观感。

建议在继续 Phase 9 前先处理：

```text
1. 所有 Python 文件恢复正常换行；
2. README / docs 恢复正常 Markdown 段落；
3. ruff format 真正生效；
4. raw GitHub 打开后源码可读。
```

### 13.2 不应继续简单堆功能

当前不建议优先做：

```text
更多文件处理小 skill
更多 UI 页面
更多 MCP 外部工具
更多 memory preference
```

这些会继续扩散功能，但不能解决“成熟长任务 Agent”问题。

当前更应优先做：

```text
Trace
Eval
TaskGraph
Semantic Verifier
Repair Loop
Workspace Pack Builder
```

---

## 14. 简历和项目表述建议

不要写成：

```text
开发了一个本地文件整理 Agent。
```

应写成：

```text
设计并实现 LocalFlow Agent，一个面向个人 workspace 的 Agent Execution Harness。
项目将 LLM 规划与真实文件 IO 解耦，通过结构化 ActionPlan、Policy Guard、Dry-run、Approval Token、Rollback Manifest、Independent Verifier 和 Trace/Eval Loop，使 Agent 能在本地长任务中安全地整理资料、生成报告、分析数据、构建资料包，并在失败时进行可追踪的局部修复。
```

更强调：

```text
一般 Agent 关注“能不能做”；
LocalFlow 关注“如何安全、稳定、可恢复、可验证地做完”。
```

---

## 15. 最终结论

你的当前判断是准确的：

```text
LocalFlow 已经有 Harness 架构，
但还没有充分展示 Harness 在复杂长任务中的可靠性提升。
```

当前项目主要证明了：

```text
Agent 不会轻易乱改本地文件；
即使出错，也能预演、拦截、记录、回滚。
```

但还没有充分证明：

```text
Agent 在复杂长任务中能更高质量地完成目标；
失败后能通过 trace 分析和 repair loop 改进；
Harness 修改能带来可量化的成功率提升。
```

因此，下一阶段应从“安全执行工具”升级为“长任务交付 Agent”。具体路线：

```text
Trace/Eval Harness
→ TaskGraph 长任务编排
→ Workspace Pack Builder 强场景
→ Semantic Verifier
→ Repair Loop
```

如果完成这些升级，LocalFlow 才会从：

```text
安全的桌面自动化 Agent
```

真正升级为：

```text
能体现 Harness Engineering 的长任务个人工作区 Agent。
```

