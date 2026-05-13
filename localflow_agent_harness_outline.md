# LocalFlow Agent：Personal Automation Agent Harness 项目大纲

> 项目定位：面向个人数字工作区的安全自动化 Agent Harness。  
> 核心不是“做一个会整理文件的 Agent”，而是设计一套让 Agent 能在真实本地环境中**安全、可控、可恢复、可验证、可扩展**地完成长阶段个人自动化任务的执行支撑系统。

---

## 1. 项目背景

### 1.1 问题来源

随着 LLM Agent 从对话助手走向任务执行，普通用户、学生、IT 从业者和 AI 爱好者开始希望 Agent 能真正处理个人数字工作区中的事务，例如：

- 整理混乱的下载文件夹；
- 批量分类课程资料、论文 PDF、图片、压缩包、代码项目；
- 为本地资料生成索引和摘要；
- 从 CSV/Excel 数据生成图表和报告；
- 对重复性文件任务进行自动化处理；
- 将散乱资料逐步沉淀为结构化知识库。

这些任务不是简单问答，而是涉及真实环境操作、文件系统读写、多步骤计划、状态维护、失败恢复和结果校验。

### 1.2 一般 Agent 的不足

普通 Agent 通常遵循：

```text
用户请求 → 模型推理 → 调用工具 → 返回结果
```

这种模式在低风险任务中可用，但在个人自动化任务中存在明显问题：

| 问题 | 说明 |
|---|---|
| 执行不可控 | 模型可能直接执行文件移动、覆盖、删除等高风险操作 |
| 缺少预演 | 用户看不到执行前计划，不知道将发生哪些文件变更 |
| 状态脆弱 | 长任务中断后难以恢复，只能重新开始 |
| 难以回滚 | 文件移动或重命名后缺少可恢复记录 |
| 缺少验证 | 模型说“完成了”不等于任务真的完成 |
| 缺少审计 | 失败后难以定位是哪一步出错 |
| 扩展混乱 | 继续加工具会导致 prompt 和 tool 调用逻辑越来越不可维护 |

因此，该项目的核心不是提升模型本身能力，而是为模型外部构建一套执行控制系统。

### 1.3 Harness Engineering 的项目意义

Harness Engineering 可以理解为：

```text
模型负责智能：理解、规划、推理、决策。
Harness 负责工程控制：上下文、边界、行动、状态、验证、恢复。
```

本项目希望将 Agent 从“单轮推理工具”升级为“可持续运行的个人自动化执行系统”。

---

## 2. 项目描述

### 2.1 项目名称

暂定名：

```text
LocalFlow Agent
```

完整名称：

```text
LocalFlow Agent：Personal Automation Agent Harness
```

### 2.2 一句话介绍

LocalFlow Agent 是一个面向个人数字工作区的安全自动化 Agent Harness，支持文件整理、文档索引、批量处理、数据分析和报告生成；它通过 dry-run、权限控制、审批、checkpoint、rollback、执行日志和结果校验，使 Agent 能稳定、安全地完成长阶段个人自动化任务。

### 2.3 项目边界

本项目初期不做“万能个人助理”，而是聚焦本地个人数字资料处理。

优先支持对象：

```text
文件夹
PDF
Markdown / txt
Word / Excel / CSV
图片
压缩包
代码文件
个人笔记
本地脚本输出
```

优先支持任务：

```text
分类
重命名
移动
复制
去重候选标记
摘要
索引
批处理
数据分析
报告生成
```

初期不优先支持：

```text
真实支付
自动发邮件
操作真实社交账号
自动删除文件
修改真实云盘
任意 shell 执行
高权限系统操作
```

### 2.4 初期 MVP 场景

MVP 聚焦一个核心任务：

```text
受控的本地文件夹整理 Agent
```

用户输入示例：

```text
帮我整理这个 Downloads 文件夹。
把课程资料、论文、图片、压缩包、代码项目分开；
PDF 尽量提取标题重命名；
重复文件只标记，不要删除；
执行前先给我 dry-run 方案。
```

系统执行流程：

```text
扫描文件夹
→ 识别文件类型和元数据
→ 生成结构化整理计划
→ 进行风险检查
→ 输出 dry-run 预演
→ 用户确认
→ 执行移动/复制/重命名
→ 生成 rollback manifest
→ 验证结果
→ 输出执行报告
```

---

## 3. 可参考开源项目与设计启发

本项目不是简单复刻某个开源项目，而是从多个高星/高关注项目中抽取设计思想。LocalFlow Agent 的核心定位是：

```text
面向个人数字工作区的安全自动化 Agent Harness。
```

因此，参考项目主要分为五类：

```text
1. Agent Harness / 长任务执行框架参考
2. 本地执行与工具安全参考
3. 工作流与 Skill 扩展参考
4. 垂直 Skill 能力参考
5. 不建议作为主线但可吸收思想的项目
```

### 3.1 核心架构参考：Agent Harness 与长任务执行

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| DeepAgents | https://github.com/langchain-ai/deepagents | LangChain 生态下的 Agent Harness，强调 planning、filesystem、subagents、context management、long-term memory，适合复杂多步骤任务。 | LocalFlow 可以借鉴其“模型外部需要完整执行壳”的思想：Agent 负责规划，Harness 负责上下文、状态、工具边界、任务分解和执行控制。 |
| DeerFlow | https://github.com/bytedance/deer-flow | 字节开源的 long-horizon SuperAgent harness，面向 research、coding、creation 等长任务，包含 sandbox、memory、tools、skills、subagents、message gateway 等。 | 借鉴其长阶段任务处理思路：LocalFlow 后续也可以演进为由多个 Skill 协作的个人自动化任务执行框架。 |
| Magentic-UI | https://github.com/microsoft/magentic-ui | 微软开源的人机协同 Agent 原型，面向复杂 Web 和 coding 任务，强调执行前展示计划、用户可指导动作、敏感操作需要审批。 | 直接启发 LocalFlow 的 dry-run、plan preview、human approval、sensitive action gate 和透明执行机制。 |

**对 LocalFlow 的结论**：高质量 Agent 不应只是“模型 + 工具调用”，而应该有一套外部 Harness 实现任务规划、上下文管理、工具边界、状态持久化、人机协同、安全审批、执行日志、失败恢复。LocalFlow 的 Harness Kernel 应该优先实现这些能力，而不是一开始堆很多 Skill。

### 3.2 本地执行与工具安全参考

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| Open Interpreter | https://github.com/openinterpreter/open-interpreter | 一个自然语言操作本地计算机的开源项目，支持让 LLM 在本地运行 Python、JavaScript、Shell 等代码，并处理文件、浏览器、数据等任务。 | 证明“自然语言 → 本地电脑操作”的需求真实存在。但 LocalFlow 不能做成通用 OS Agent，而应更强调安全执行、dry-run、权限边界、rollback 和结果验证。 |
| MCP Filesystem Server | https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem | MCP 官方参考实现之一，为 AI 应用提供标准化文件系统操作，包括读写文件、创建/列出/删除目录、移动文件、搜索文件、获取元数据，并支持目录访问控制。 | 启发 LocalFlow 的 Tool Layer：文件操作必须工具化、结构化、受 workspace root 限制，禁止模型直接操作系统。 |
| Composio | https://github.com/ComposioHQ/composio | 面向 AI Agent 的工具集成平台，提供大量 toolkits、tool search、context management、authentication 和 sandboxed workbench。 | 后续可以借鉴其 Tool Registry / Permission / Sandbox 思路，把 LocalFlow 的工具和 Skill 做成可注册、可授权、可扩展的能力包。 |
| OpenHands Software Agent SDK | https://github.com/OpenHands/software-agent-sdk | OpenHands 提供的 Software Agent SDK，支持构建处理代码任务的 Agent，可使用本地或 Docker/Kubernetes 临时工作区。 | 不作为 LocalFlow MVP 主线，但可借鉴其 workspace isolation、ephemeral workspace、agent-computer interface 等安全执行思想。 |

**对 LocalFlow 的结论**：本地执行能力必须采用“受控工具”模式，而不是开放任意 Shell。初期原则：① 不开放任意 shell；② 所有路径限制在 workspace root 内；③ 模型只能输出结构化 ActionPlan；④ Harness 负责校验和执行；⑤ 所有写操作必须 dry-run + approval；⑥ 所有 move/copy/rename 必须有 rollback manifest。

### 3.3 工作流、Connector 与 Skill 扩展参考

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| n8n | https://github.com/n8n-io/n8n | 高关注工作流自动化平台，支持 native AI capabilities、可视化构建、custom code、自托管和大量 integrations。 | 借鉴 workflow、connector、recipe、run history 等思想。但 LocalFlow 不应一开始做低代码平台，而应先做本地个人自动化 Harness。 |
| Activepieces | https://github.com/activepieces/activepieces | 开源 AI automation / workflow automation 项目，强调 type-safe pieces framework，并可让 Pieces 自动成为 MCP servers。 | 启发 LocalFlow 的 SkillManifest 和 Skill Pack 机制：每个 Skill 应该是独立、可声明、可测试、可接入的能力包。 |
| Flowise | https://github.com/FlowiseAI/Flowise | 可视化构建 AI Agents / workflow 的低代码平台。 | 仅作为后续可视化编排参考，不建议作为主线。LocalFlow 初期应保持 Python CLI / API 优先，避免过早做复杂前端。 |
| Langflow | https://github.com/langflow-ai/langflow | 用于构建和部署 AI-powered agents and workflows 的平台，提供可视化编排、API、MCP server 等能力。 | 后续如果 LocalFlow 做成可视化工作流或 MCP 工具服务，可以参考其 workflow-as-tool 和部署思路。 |

**对 LocalFlow 的结论**：可以从单一 Agent 演进为 `Harness Kernel + Skill Registry + Tool Registry + Recipe System + Run History`。但 MVP 不做平台化，初期只需保证“新增 Skill 不修改 Harness Kernel”。

### 3.4 垂直 Skill 能力参考

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| TaskWeaver | https://github.com/microsoft/TaskWeaver | Microsoft 开源的 code-first data analytics agent framework，面向数据分析任务，能将用户请求转成代码片段并协调插件函数执行。 | 后续 DataOps Skill 可借鉴其模式：CSV/Excel → schema 检查 → Python 代码生成 → 执行 → 图表 → 报告 → 结果校验。 |
| browser-use | https://github.com/browser-use/browser-use | 面向浏览器自动化的 AI Agent 项目，目标是让网站能被 AI agents 使用。 | 后续 WebCollect Skill 可借鉴其浏览器操作、网页内容提取、网页任务 trace 等能力。但 LocalFlow MVP 不做真实网页提交。 |
| Open Deep Research | https://github.com/langchain-ai/open_deep_research | LangChain 开源的 deep research agent，支持多模型、多搜索工具和 MCP servers。 | 后续 Research/WebCollect Skill 可借鉴其 research planner、source tracking、citation collection 和 report synthesis 思路。 |
| Mem0 | https://github.com/mem0ai/mem0 | 面向 AI agents 的 universal memory layer，用于记住用户偏好、适应个人需求，并支持长期个性化交互。 | 后续 Memory Skill 可用于记住用户命名风格、常用目录结构、禁用路径、报告模板、常用任务偏好。 |
| mini-swe-agent | https://github.com/SWE-agent/mini-swe-agent | 轻量级软件工程 Agent，可用于解决 GitHub issues 或命令行任务。 | 不作为 LocalFlow 主线，但可借鉴其简洁命令执行、任务日志、代码任务 sandbox 和失败反馈机制。 |

**对 LocalFlow 的结论**：后续 Skill Pack 可以按以下方向逐步扩展：

```text
FileOps Skill       → 参考 MCP Filesystem、Open Interpreter
DocumentOps Skill   → 参考 Open Deep Research 的资料处理与报告生成
DataOps Skill       → 参考 TaskWeaver
WebCollect Skill    → 参考 browser-use、Open Deep Research
Memory Skill        → 参考 Mem0
Automation Recipe   → 参考 n8n、Activepieces
```

### 3.5 不建议作为 MVP 主线的项目类型

| 类型 | 代表项目 | 不作为主线的原因 |
|---|---|---|
| 低代码 Agent Builder | Flowise、Langflow | 会把重点转移到前端和平台化，稀释 Python + Harness Engineering 核心。 |
| 多用户工作流平台 | n8n、Activepieces | 工程量大，容易陷入用户系统、权限、多租户、部署等平台问题。 |
| 通用 OS Agent | Open Interpreter | 能力范围过大，安全风险高，LocalFlow 初期应限制在 workspace 内的个人数字资料处理。 |
| 浏览器全自动 Agent | browser-use、Magentic-UI | 演示强，但真实网页不稳定；可以作为后续 WebCollect Skill，而不是 MVP。 |
| 代码修复 Agent | OpenHands、mini-swe-agent | 工程含量高，但偏 coding agent，容易偏离 LocalFlow 的个人数字工作区自动化主线。 |

### 3.6 LocalFlow 的差异化定位

综合以上项目，LocalFlow 不应被定义为：又一个 Open Interpreter / n8n / Flowise / 浏览器 Agent / 个人助手。准确定位是：

```text
面向个人数字工作区的安全自动化 Agent Harness。
```

| 对比对象 | LocalFlow 的差异 |
|---|---|
| Open Interpreter | Open Interpreter 偏通用电脑操作；LocalFlow 偏安全、可控、可回滚的个人资料自动化。 |
| DeepAgents | DeepAgents 是通用 Agent Harness；LocalFlow 是基于 Harness 思想的具体个人自动化应用。 |
| MCP Filesystem Server | MCP Filesystem 是文件工具层；LocalFlow 是完整执行控制层。 |
| n8n / Activepieces | 它们是工作流平台；LocalFlow 是 Agent 驱动的本地任务执行 Harness。 |
| TaskWeaver | TaskWeaver 偏数据分析；LocalFlow 覆盖文件、文档、数据、资料索引等个人数字工作区任务。 |
| Mem0 | Mem0 是记忆层；LocalFlow 后续可把记忆作为个性化 Skill。 |
| browser-use | browser-use 偏网页操作；LocalFlow 初期聚焦本地 workspace，后续再扩展 WebCollect。 |

### 3.7 对项目设计的直接约束

参考上述项目后，LocalFlow 初期必须坚持以下设计约束：

```text
1.  不做万能个人助理。
2.  不做多用户平台。
3.  不开放任意 shell。
4.  不自动删除文件。
5.  不直接执行模型自然语言指令。
6.  所有副作用操作必须结构化为 Action。
7.  所有写操作必须 dry-run。
8.  所有高风险操作必须 approval。
9.  所有 move/copy/rename 必须有 rollback manifest。
10. Verifier 必须独立判断任务结果。
11. 新 Skill 必须遵守 inspect → plan → validate → dry-run → execute → verify → report 生命周期。
12. 新 Skill 不允许绕过 Harness Kernel。
```

### 3.8 推荐的参考优先级

| 优先级 | 项目 | 用途 |
|---|---|---|
| 第一优先级 | DeepAgents、Magentic-UI、MCP Filesystem Server、Open Interpreter | 确定 LocalFlow 的核心架构（Agent Harness、Plan Preview、Dry-run、Approval、Workspace Boundary、Structured Tool Calls、Rollback、Verifier） |
| 第二优先级 | TaskWeaver、Mem0、n8n、Activepieces | 后续 Skill 与个性化扩展（DataOps、Memory、Workflow Recipe、Skill Registry） |
| 第三优先级 | browser-use、Open Deep Research、Composio、Langflow、Flowise、OpenHands、mini-swe-agent | 长期扩展（WebCollect、Research Agent、Tool Ecosystem、Visual Workflow、Coding Agent） |

---

## 4. Harness Engineering 核心要点

结合项目架构，可以把 Harness 分成五层。

### 4.1 Context Injection：上下文注入层

作用：让模型在每一步决策前明确知道当前任务、环境、边界和状态。

注入内容包括：

```text
用户目标
workspace root
文件扫描摘要
历史执行状态
可用工具
可用 Skill
禁止操作
风险策略
checkpoint 信息
上一步执行结果
```

示例上下文：

```json
{
  "workspace_root": "./examples/downloads",
  "allowed_actions": ["mkdir", "copy", "move", "rename", "summarize", "index"],
  "forbidden_actions": ["delete", "overwrite", "outside_workspace"],
  "risk_policy": "all write actions require dry_run and approval",
  "task_status": "planning"
}
```

核心价值：降低模型越界操作、遗漏状态、误解任务边界的概率。

---

### 4.2 Control Layer：控制层

作用：强制 Agent 按固定协议运行，而不是自由调用工具。

初期固定执行协议：

```text
Inspect → Plan → Risk Check → Dry-run → Approve → Checkpoint → Execute → Verify → Report
```

控制层要保证：

- 模型不能跳过 dry-run；
- 写操作必须经过审批；
- 高风险动作必须被拦截或降级；
- 失败后进入 recovery，而不是继续盲目执行；
- 每一步状态都进入 ledger。

---

### 4.3 Action Layer：行动层

作用：连接真实环境，但所有动作必须结构化、可校验、可审计。

模型不能直接执行 shell 或自然语言命令，只能输出 typed actions。

示例：

```json
{
  "action_id": "a-001",
  "action_type": "move",
  "source_path": "raw/paper1.pdf",
  "target_path": "papers/agent_memory_survey.pdf",
  "reason": "PDF title indicates this file is an AI agent memory survey paper.",
  "risk_level": "medium",
  "reversible": true,
  "requires_approval": true
}
```

Harness 负责校验并执行这些 action。

---

### 4.4 Persist Layer：持久化层

作用：让长任务可暂停、恢复、回滚、复现和审计。

每次运行保存：

```text
task.json
workspace_snapshot.json
plan.json
dry_run.md
actions.json
rollback_manifest.json
execution_log.jsonl
verify_report.json
final_report.md
```

推荐运行目录：

```text
.localflow/runs/2026-05-12-001/
  task.json
  workspace_snapshot.json
  plan.json
  dry_run.md
  actions.json
  rollback_manifest.json
  execution_log.jsonl
  verify_report.json
  final_report.md
```

---

### 4.5 Observe & Verify：观察与验证层

作用：独立判断任务是否真的完成，而不是让模型自称完成。

验证内容包括：

```text
目标目录是否存在
目标文件是否存在
文件是否丢失
是否访问 workspace 外路径
是否发生未授权覆盖
dry-run action 与真实执行 action 是否一致
rollback manifest 是否完整
index.md 是否覆盖目标文件
失败 action 是否被记录
```

核心原则：

```text
完成状态由 Verifier 判定，而不是由模型判定。
```

---

## 5. 相比一般 Agent 的优势

| 能力 | 一般 Agent | LocalFlow Harness Agent |
|---|---|---|
| 执行安全 | 依赖 prompt 约束 | 由 Policy Guard、路径边界、审批机制强制约束 |
| 写操作控制 | 可能直接执行 | 必须 dry-run + approval |
| 状态维护 | 多依赖上下文窗口 | task ledger + checkpoint 持久化 |
| 失败恢复 | 常需要重跑 | 可从 checkpoint 恢复 |
| 回滚能力 | 通常没有 | rollback manifest 支持回滚 |
| 结果验证 | 模型自述完成 | Verifier 程序化检查 |
| 可观测性 | 日志弱 | action log + report + audit trace |
| 可扩展性 | 堆 prompt 和 tools | Skill 生命周期统一接入 |
| 适合真实文件操作 | 风险较高 | 风险可控 |

这里的“性能强”主要体现为任务级性能，而不是模型智力本身：

```text
更低误操作率
更高任务完成可验证性
更强长任务稳定性
更强失败恢复能力
更高可审计性
更清晰的扩展路径
```

---

## 6. 总体架构设计

### 6.1 架构图

```text
User Request
    ↓
Interface Layer
CLI / API / Web UI
    ↓
Agent Core
Intent Parser / Planner / Repair Reasoner
    ↓
Harness Kernel
Context Manager
Control Loop
Policy Guard
Action Validator
Dry-run Engine
Approval Gate
Checkpoint Manager
Safe Executor
Rollback Manager
Verifier
Audit Logger
    ↓
Tool Layer
File Scanner
File Operator
PDF Reader
Text Summarizer
Index Generator
CSV Analyzer
Image Processor
    ↓
Persistence Layer
Task Ledger
Workspace Snapshot
Action Log
Rollback Manifest
Verification Report
    ↓
Final Report / Rollback / Resume
```

### 6.2 模块职责

| 模块 | 职责 |
|---|---|
| Agent Core | 负责理解用户目标、生成计划、解释原因、必要时修复计划 |
| Harness Kernel | 项目核心，负责控制流程、安全边界、审批、执行、恢复、验证 |
| Tool Layer | 提供受控工具，不允许模型直接操作系统 |
| Skill Layer | 将不同任务能力打包为可插拔模块 |
| Persistence Layer | 保存任务、计划、状态、日志、回滚信息 |
| Interface Layer | CLI、API 或后续 Web UI |

---

## 7. 初期核心工程规则

### 7.1 模型不能直接执行副作用操作

模型只能输出：

```text
TaskSpec
ActionPlan
Action
RepairSuggestion
```

真正执行由 Harness 完成。

---

### 7.2 所有 Action 必须结构化

禁止模型输出模糊自然语言执行指令。

允许：

```json
{
  "action_type": "rename",
  "source_path": "raw/a.pdf",
  "target_path": "papers/a-renamed.pdf"
}
```

不允许：

```text
把那些论文文件重命名一下。
```

---

### 7.3 写操作必须 dry-run

以下 action 必须先预演：

```text
mkdir
copy
move
rename
convert
compress
write_report
```

dry-run 阶段不得改变文件系统。

---

### 7.4 默认禁用 delete

MVP 阶段不允许自动删除文件。

重复文件只生成：

```text
duplicates_report.md
```

而不是直接删除。

---

### 7.5 所有路径必须限制在 workspace 内

必须阻止：

```text
../outside
/home/user/other_dir
C:\Users\Other
绝对路径越界
符号链接逃逸
```

---

### 7.6 默认不覆盖已有文件

如果 target path 已存在，默认策略：

```text
自动生成安全新文件名
或进入 review
或要求显式确认
```

---

### 7.7 所有写操作必须可追踪

每个执行动作必须写入：

```text
action_id
start_time
end_time
source_path
target_path
status
error
rollback_action
file_hash_before
file_hash_after
```

---

### 7.8 Verifier 独立于模型

Verifier 应尽量用程序规则检查结果，不让模型自评任务完成。

---

## 8. 核心数据结构设计

以下为初期建议的 Pydantic Schema。

### 8.1 TaskSpec

```python
class TaskSpec(BaseModel):
    task_id: str
    user_goal: str
    workspace_root: str
    constraints: list[str]
    allowed_actions: list[str]
    forbidden_actions: list[str]
    created_at: datetime
```

### 8.2 WorkspaceSnapshot

```python
class FileMeta(BaseModel):
    path: str
    file_type: str
    size_bytes: int
    modified_at: datetime
    sha256: str | None
    text_preview: str | None = None

class WorkspaceSnapshot(BaseModel):
    snapshot_id: str
    task_id: str
    root: str
    files: list[FileMeta]
    total_files: int
    total_size_bytes: int
    created_at: datetime
```

### 8.3 Action

```python
class Action(BaseModel):
    action_id: str
    action_type: Literal[
        "mkdir",
        "copy",
        "move",
        "rename",
        "summarize",
        "index",
        "convert",
        "analyze"
    ]
    source_path: str | None
    target_path: str | None
    reason: str
    risk_level: Literal["low", "medium", "high"]
    reversible: bool
    requires_approval: bool
    confidence: float | None = None
```

### 8.4 ActionPlan

```python
class ActionPlan(BaseModel):
    plan_id: str
    task_id: str
    summary: str
    actions: list[Action]
    expected_outputs: list[str]
    risk_summary: str
    created_at: datetime
```

### 8.5 RiskAssessment

```python
class RiskAssessment(BaseModel):
    plan_id: str
    passed: bool
    blocked_actions: list[str]
    warnings: list[str]
    risk_level: Literal["low", "medium", "high", "blocked"]
    reason: str
```

### 8.6 ExecutionRecord

```python
class ExecutionRecord(BaseModel):
    run_id: str
    action_id: str
    status: Literal["pending", "running", "success", "failed", "skipped"]
    started_at: datetime | None
    ended_at: datetime | None
    error: str | None
    rollback_action: dict | None
```

### 8.7 RollbackManifest

```python
class RollbackManifest(BaseModel):
    run_id: str
    task_id: str
    actions: list[dict]
    file_hashes_before: dict[str, str]
    created_dirs: list[str]
    generated_files: list[str]
    created_at: datetime
```

### 8.8 VerificationResult

```python
class VerificationResult(BaseModel):
    task_id: str
    run_id: str
    passed: bool
    checks: list[dict]
    failed_checks: list[dict]
    summary: str
    created_at: datetime
```

### 8.9 SkillManifest

```python
class SkillManifest(BaseModel):
    name: str
    description: str
    version: str
    capabilities: list[str]
    allowed_actions: list[str]
    requires_approval: list[str]
    supports_dry_run: bool
    supports_rollback: bool
    supports_verify: bool
```

---

## 9. 初期实现细节

### 9.1 技术栈建议

```text
Python 3.11+
Typer / Rich          CLI 交互与 dry-run 展示
Pydantic              Action 与 Plan schema
SQLite / JSONL        任务状态与执行日志
pathlib / shutil      文件系统操作
hashlib               文件 hash
pypdf                 PDF 文本提取
python-docx           Word 文档解析，可选
pandas / duckdb       DataOps 扩展，可选
Pillow                图片元数据与压缩扩展，可选
LiteLLM               多模型适配，可选
LangGraph / DeepAgents 后续 Agent orchestration 参考，可选
```

MVP 阶段优先 CLI，不急于做复杂 Web UI。

### 9.2 推荐目录结构

```text
localflow-agent/
  app/
    cli.py
    main.py

    agent/
      planner.py
      prompts.py
      repair.py

    harness/
      context.py
      control_loop.py
      policy_guard.py
      action_validator.py
      dry_run.py
      approval.py
      checkpoint.py
      executor.py
      rollback.py
      verifier.py
      audit.py

    tools/
      file_scan.py
      file_ops.py
      pdf_ops.py
      text_ops.py
      index_ops.py
      hash_ops.py

    skills/
      folder_organizer/
        skill.yaml
        planner.py
        validator.py
        reporter.py

      pdf_indexer/
        skill.yaml
        planner.py
        validator.py
        reporter.py

      data_reporter/
        skill.yaml
        planner.py
        validator.py
        reporter.py

    schemas/
      task.py
      workspace.py
      action.py
      plan.py
      execution.py
      rollback.py
      verification.py
      skill.py

    storage/
      db.py
      run_store.py
      jsonl_logger.py

  examples/
    messy_downloads/
    course_materials/
    papers/

  tests/
    test_policy_guard.py
    test_dry_run.py
    test_rollback.py
    test_verifier.py

  README.md
  pyproject.toml
```

### 9.3 MVP 命令设计

```bash
localflow inspect ./examples/messy_downloads

localflow plan ./examples/messy_downloads \
  --goal "整理课程资料、论文、图片和压缩包，不删除任何文件"

localflow dry-run --task-id <task_id>

localflow execute --task-id <task_id>

localflow verify --task-id <task_id>

localflow rollback --run-id <run_id>
```

### 9.4 MVP 功能清单

必须实现：

```text
1. 指定 workspace root
2. 扫描文件树与文件元数据
3. 文件类型识别
4. 基于规则或 LLM 生成 ActionPlan
5. Pydantic schema 校验
6. Policy Guard 路径与风险检查
7. dry-run 预览
8. 用户确认后执行
9. 生成 execution_log.jsonl
10. 生成 rollback_manifest.json
11. 程序化 verify
12. 支持 rollback
13. 生成 final_report.md
```

暂不实现：

```text
1. 自动删除文件
2. 真实邮件操作
3. 真实云盘同步
4. 任意 shell 执行
5. 多用户权限系统
6. 复杂前端
```

---

## 10. 检验标准与指标设计

本项目必须用工程指标证明 Harness 不是摆设。

### 10.1 安全性指标

| 指标 | 目标 |
|---|---:|
| workspace 外路径访问次数 | 0 |
| 未经审批写操作次数 | 0 |
| 默认 delete 执行次数 | 0 |
| 写操作 dry-run 覆盖率 | 100% |
| move/rename/copy rollback 记录覆盖率 | 100% |
| 非法路径拦截率 | 100% |

### 10.2 可恢复性指标

测试方法：执行到 30% 或 50% 时强制中断，重启任务。

合格标准：

```text
已完成 action 不重复执行
未完成 action 可继续执行
最终状态正确
日志连续
任务状态从 interrupted 恢复到 completed
```

### 10.3 回滚指标

测试方法：执行完整理任务后调用 rollback。

合格标准：

```text
文件树恢复到执行前状态
文件 hash 与执行前一致
新增空目录被清理
生成文件按 manifest 删除或还原
rollback 日志完整
```

MVP 阶段目标：常规 move/rename/copy 的 rollback 成功率接近 100%。

### 10.4 计划合法性指标

构造非法计划，包括：

```text
访问 workspace 外目录
删除文件
覆盖文件
非法路径
缺少 target_path
重复 action_id
不可逆动作未标高风险
```

合格标准：

```text
Policy Guard 和 Action Validator 能全部拦截。
```

### 10.5 任务完成指标

针对文件整理任务，检查：

```text
目标目录是否创建
目标文件是否存在
源文件是否按计划处理
未处理文件是否有说明
失败 action 是否有记录
index.md 是否覆盖目标文件
低置信度文件是否进入 review 区
```

不要求初期分类 100% 准确，但要求：

```text
所有动作可解释
不确定文件不进行高风险处理
所有执行结果可追踪
```

### 10.6 可观测性指标

每次运行必须能回答：

```text
模型为什么做这个计划？
系统执行了哪些动作？
哪些动作被跳过？
哪里失败了？
失败后怎么处理？
最终生成了什么？
是否可以回滚？
```

需要产出：

```text
plan.json
dry_run.md
execution_log.jsonl
verify_report.json
final_report.md
rollback_manifest.json
```

### 10.7 可扩展性指标

新增一个 Skill 时，不应该修改 Harness Kernel。

例如加入 `pdf_indexer`，只允许新增或修改：

```text
skills/pdf_indexer/skill.yaml
skills/pdf_indexer/planner.py
skills/pdf_indexer/validator.py
skills/pdf_indexer/reporter.py
tools/pdf_ops.py
```

不应该修改：

```text
control_loop.py
policy_guard.py
approval.py
rollback.py
audit.py
```

---

## 11. Baseline 对照设计

为了证明项目不是普通 Agent，可以设计一个对照实验。

### 11.1 Baseline：普通 Agent

```text
用户请求 → LLM 生成脚本或命令 → 执行 → 返回结果
```

典型问题：

```text
无 dry-run
无审批
无 rollback
无路径边界
无独立 verifier
失败后难恢复
```

### 11.2 LocalFlow：Harness Agent

```text
用户请求
→ inspect
→ plan
→ validate
→ dry-run
→ approve
→ checkpoint
→ execute
→ verify
→ report / rollback
```

### 11.3 对比指标

| 指标 | 普通 Agent | LocalFlow Harness Agent |
|---|---|---|
| dry-run | 无 | 有 |
| 审批机制 | 弱 | 强制 |
| 路径边界 | 弱 | 强制 workspace root |
| 回滚 | 通常无 | manifest 支持 |
| 中断恢复 | 通常无 | checkpoint 支持 |
| 结果验证 | 模型自述 | 程序化 verifier |
| 执行日志 | 弱 | JSONL + report |
| 真实文件操作风险 | 高 | 可控 |

---

## 12. Skill 扩展机制

### 12.1 Skill 的定义

Skill 是一个可插拔任务能力包。每个 Skill 必须遵守统一生命周期：

```text
inspect → plan → validate → dry-run → execute → verify → report
```

### 12.2 Skill Manifest 示例

```yaml
name: folder_organizer
version: 0.1.0
description: Organize local folders with dry-run and rollback support.

capabilities:
  - scan_files
  - classify_files
  - propose_moves
  - propose_renames
  - generate_index

allowed_actions:
  - mkdir
  - copy
  - move
  - rename
  - index

requires_approval:
  - mkdir
  - move
  - rename

supports_dry_run: true
supports_rollback: true
supports_verify: true
```

### 12.3 后续 Skill Pack 规划

#### V1：FileOps Skill Pack

```text
文件扫描
文件分类
批量重命名
批量移动
重复文件候选检测
目录索引生成
回滚
```

#### V2：DocumentOps Skill Pack

```text
PDF 标题提取
PDF 摘要
论文/资料分类
Markdown 索引
Word 文档摘要
批量文档重命名
```

#### V3：DataOps Skill Pack

```text
CSV/Excel 读取
字段识别
数据清洗建议
图表生成
异常检测
Markdown/HTML 报告生成
```

#### V4：ImageOps Skill Pack

```text
图片尺寸识别
图片压缩
EXIF 信息提取
按日期/尺寸分类
缩略图生成
```

#### V5：WebCollect Skill Pack

```text
网页资料抓取
链接整理
网页内容摘要
网页转 Markdown
来源索引
```

#### V6：Memory Skill Pack

```text
记住用户命名偏好
记住常用目录结构
记住不允许访问的目录
记住报告模板
记住用户常用任务
```

---

## 13. 新功能加入原则

后续任何新功能都必须满足以下原则。

### 13.1 安全优先

新 Skill 不能绕过：

```text
workspace root
policy guard
dry-run
approval
execution log
rollback / compensation
verifier
```

### 13.2 先只读，后写入

新 Skill 初期优先做只读能力，例如扫描、摘要、索引。稳定后再开放写操作。

### 13.3 每个写操作必须有补偿策略

如果无法真正 rollback，至少要有 compensation strategy，例如：

```text
生成 backup
写入 output 目录而不是覆盖原文件
保留原始文件映射
要求高风险审批
```

### 13.4 不确定时进入 review

LLM 低置信度判断不得直接执行高风险操作。

策略：

```text
low confidence → review_dir / review_report.md
```

### 13.5 Skill 不应侵入 Harness Kernel

Skill 只能通过标准接口接入：

```text
SkillManifest
Tool Schema
Action Schema
Validator
Reporter
```

不能直接修改主控制循环。

### 13.6 新 Skill 必须提供测试集

每个 Skill 至少提供：

```text
正常样例
非法 action 样例
dry-run 样例
rollback 样例
verify 样例
```

### 13.7 Skill 与参考项目映射

LocalFlow 的 Skill 扩展可以按照 §3 中的参考项目逐步演进：

| Skill Pack | 主要参考项目 | 加入原则 |
|---|---|---|
| FileOps Skill | MCP Filesystem Server、Open Interpreter | 只允许 workspace 内文件操作；默认不删除；写操作必须 dry-run + rollback。 |
| DocumentOps Skill | Open Deep Research、DeepAgents | 先做只读摘要、索引、分类；再做重命名和归档。 |
| DataOps Skill | TaskWeaver | 代码执行必须 sandbox；图表和报告必须由 Verifier 检查。 |
| WebCollect Skill | browser-use、Magentic-UI | 初期只做网页资料收集和摘要；不做自动提交、支付、登录后敏感操作。 |
| Memory Skill | Mem0 | 只记住用户明确允许的偏好，例如命名风格、目录结构、报告模板、禁用目录。 |
| Workflow Recipe Skill | n8n、Activepieces | 后续把高频任务沉淀为 recipe，但 recipe 仍必须经过 Harness Kernel。 |
| Tool Registry | Composio、Activepieces | 后续支持工具注册、权限声明和可用范围控制。 |
| Visual Workflow UI | Flowise、Langflow | 非 MVP 功能，只有在 CLI/API 稳定后才考虑。 |

新增 Skill 的判断标准：

```text
1. 是否有明确用户场景。
2. 是否需要长阶段执行。
3. 是否存在副作用或失败风险。
4. 是否能通过 dry-run、approval、rollback、verify 受控。
5. 是否能在不修改 Harness Kernel 的前提下接入。
6. 是否能提供测试样例，包括正常样例、非法 action、dry-run、rollback、verify。
```

---

## 14. 路线图

### Phase 0：无 LLM 的 Harness 骨架

目标：证明 Harness 可用。

实现：

```text
按扩展名规则分类
生成 action plan
dry-run
approval
execute
rollback
verify
```

重点产出：

```text
Action Schema
Policy Guard
Dry-run Engine
Executor
Rollback Manager
Verifier
Audit Logger
```

### Phase 1：LLM Planner

目标：让 LLM 根据自然语言生成结构化计划。

实现：

```text
用户自然语言目标
workspace summary
LLM 输出 ActionPlan
Pydantic 校验
Policy Guard 检查
dry-run
execute
```

### Phase 2：Document Intelligence

目标：让 Agent 能读文档内容辅助分类。

实现：

```text
PDF 文本提取
标题识别
Markdown 摘要
资料主题分类
index.md 生成
```

### Phase 3：DataOps

目标：支持 CSV/Excel 数据分析和报告生成。

实现：

```text
读取数据
schema 检查
生成分析计划
执行 Python 代码
生成图表
验证输出
生成报告
```

### Phase 4：Skill 插件化

目标：让项目从单功能工具变成可扩展框架。

实现：

```text
SkillManifest
Skill Registry
Tool Registry
统一生命周期
统一测试模板
```

### Phase 5：Memory 与个性化

目标：让 Agent 能记住用户偏好。

实现：

```text
命名风格记忆
目录结构偏好
禁用目录记忆
报告模板记忆
常用任务记忆
```

### Phase 6：WebCollect / MCP 扩展

目标：连接外部工具和网页资料。

实现：

```text
网页资料收集
MCP 工具接入
浏览器只读操作
外部服务 connector
```

---

## 15. 风险与规避策略

### 15.1 文件误操作风险

规避：

```text
默认禁止 delete
写操作先 dry-run
执行前生成 checkpoint
workspace root 限制
move/rename/copy 均写 rollback manifest
高风险操作强制审批
```

### 15.2 LLM 分类错误风险

规避：

```text
低置信度进入 review
不覆盖原文件
保留原始文件名映射
生成分类原因
允许用户修改 dry-run plan
```

### 15.3 范围膨胀风险

规避：

```text
MVP 只做 folder organizer
不做邮件
不做云盘
不做真实网页提交
不做任意 shell
不做多用户协作
```

### 15.4 变成普通脚本工具的风险

规避：

必须体现：

```text
自然语言任务理解
多步骤计划
结构化 action
dry-run
approval
checkpoint
rollback
audit
verifier
skill 扩展
```

### 15.5 变成危险 OS Agent 的风险

规避：

```text
不开放任意 shell
只开放受控工具
所有路径约束在 workspace 内
所有副作用操作可审计
```

---

## 16. 初期项目交付物

### 16.1 代码交付

```text
CLI 工具
Harness Kernel
Folder Organizer Skill
File Scanner
Dry-run Engine
Rollback Manager
Verifier
测试用例
```

### 16.2 文档交付

```text
README.md
architecture.md
safety_policy.md
skill_development_guide.md
demo_report.md
```

### 16.3 Demo 交付

建议准备 3 个 demo：

#### Demo 1：下载文件夹整理

展示：

```text
scan
plan
dry-run
approval
execute
rollback
```

#### Demo 2：课程资料整理

展示：

```text
PDF/Markdown 文档理解
分类
index.md 生成
```

#### Demo 3：个人数据报告

展示：

```text
CSV 分析
图表生成
Markdown 报告
```

---

## 17. 项目成功标准

MVP 成功标准：

```text
1. 能对指定 workspace 进行完整 inspect。
2. 能生成结构化 ActionPlan。
3. 能进行 dry-run 且不修改文件。
4. 能拦截非法路径、delete、overwrite 等高风险行为。
5. 用户确认后能执行 move/rename/copy/mkdir。
6. 每个写操作都有 execution log 和 rollback action。
7. 能通过 rollback 恢复常规文件变更。
8. Verifier 能独立判断主要输出是否完成。
9. final_report.md 能说明任务计划、执行动作、失败项和输出结果。
10. 新增一个 Skill 不需要修改 Harness Kernel。
```

如果这 10 条成立，项目雏形就是合格的 Harness Engineering 项目，而不是普通 Agent Demo。

---

## 18. 推荐项目摘要

```text
LocalFlow Agent 是一个面向个人数字工作区的 Agent Execution Harness。
它以 LLM Agent 作为规划与决策核心，以 Harness Kernel 作为执行控制层，
通过上下文注入、结构化行动、权限边界、dry-run 审批、状态持久化、
checkpoint 恢复、rollback 和结果验证，使 Agent 能安全地完成本地文件整理、
资料索引、批量处理和数据报告等长阶段个人自动化任务。
```

项目核心原则：

```text
一般 Agent 解决“能不能做”；
LocalFlow 解决“如何安全、稳定、可恢复、可验证地做完”。
```
