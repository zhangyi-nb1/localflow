# LocalFlow Agent：可参考高星项目仓库信息补充指令

> 用途：将本文件内容复制给大模型，用于补充或替换 `LocalFlow Agent：Personal Automation Agent Harness 项目大纲` 中的「类似开源项目与启发」部分。  
> 注意：不要写具体 star 数。GitHub star 会随时间变化，建议统一表述为“高星/高关注开源项目”。

---

## 修改指令

请修改当前 Markdown 大纲文件 `/mnt/data/localflow_agent_harness_outline.md`。

修改要求：

1. 将原来的「## 3. 类似开源项目与启发」整节替换为下面的新版本。
2. 保留原有章节编号风格。
3. 不要写具体 star 数，只写“高星/高关注开源项目”，因为 star 数会随时间变化。
4. 这些项目不是要求 LocalFlow 复刻，而是作为架构、Harness Engineering、工具抽象、安全执行、工作流扩展、记忆层和后续 Skill 设计的参考。
5. 替换完成后，确保后文「Skill 扩展机制」「后续 Skill Pack 规划」「新功能加入原则」能和本节的参考项目形成呼应。

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

---

### 3.1 核心架构参考：Agent Harness 与长任务执行

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| DeepAgents | https://github.com/langchain-ai/deepagents | LangChain 生态下的 Agent Harness，强调 planning、filesystem、subagents、context management、long-term memory，适合复杂多步骤任务。 | LocalFlow 可以借鉴其“模型外部需要完整执行壳”的思想：Agent 负责规划，Harness 负责上下文、状态、工具边界、任务分解和执行控制。 |
| DeerFlow | https://github.com/bytedance/deer-flow | 字节开源的 long-horizon SuperAgent harness，面向 research、coding、creation 等长任务，包含 sandbox、memory、tools、skills、subagents、message gateway 等。 | 借鉴其长阶段任务处理思路：LocalFlow 后续也可以演进为由多个 Skill 协作的个人自动化任务执行框架。 |
| Magentic-UI | https://github.com/microsoft/magentic-ui | 微软开源的人机协同 Agent 原型，面向复杂 Web 和 coding 任务，强调执行前展示计划、用户可指导动作、敏感操作需要审批。 | 直接启发 LocalFlow 的 dry-run、plan preview、human approval、sensitive action gate 和透明执行机制。 |

#### 对 LocalFlow 的结论

这些项目说明：高质量 Agent 不应只是“模型 + 工具调用”，而应该有一套外部 Harness：

```text
任务规划
上下文管理
工具边界
状态持久化
人机协同
安全审批
执行日志
失败恢复
```

LocalFlow 的 Harness Kernel 应该优先实现这些能力，而不是一开始堆很多 Skill。

---

### 3.2 本地执行与工具安全参考

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| Open Interpreter | https://github.com/openinterpreter/open-interpreter | 一个自然语言操作本地计算机的开源项目，支持让 LLM 在本地运行 Python、JavaScript、Shell 等代码，并处理文件、浏览器、数据等任务。 | 证明“自然语言 → 本地电脑操作”的需求真实存在。但 LocalFlow 不能做成通用 OS Agent，而应更强调安全执行、dry-run、权限边界、rollback 和结果验证。 |
| MCP Filesystem Server | https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem | MCP 官方参考实现之一，为 AI 应用提供标准化文件系统操作，包括读写文件、创建/列出/删除目录、移动文件、搜索文件、获取元数据，并支持目录访问控制。 | 启发 LocalFlow 的 Tool Layer：文件操作必须工具化、结构化、受 workspace root 限制，禁止模型直接操作系统。 |
| Composio | https://github.com/ComposioHQ/composio | 面向 AI Agent 的工具集成平台，提供大量 toolkits、tool search、context management、authentication 和 sandboxed workbench。 | 后续可以借鉴其 Tool Registry / Permission / Sandbox 思路，把 LocalFlow 的工具和 Skill 做成可注册、可授权、可扩展的能力包。 |
| OpenHands Software Agent SDK | https://github.com/OpenHands/software-agent-sdk | OpenHands 提供的 Software Agent SDK，支持构建处理代码任务的 Agent，可使用本地或 Docker/Kubernetes 临时工作区。 | 不作为 LocalFlow MVP 主线，但可借鉴其 workspace isolation、ephemeral workspace、agent-computer interface 等安全执行思想。 |

#### 对 LocalFlow 的结论

LocalFlow 的本地执行能力必须采用“受控工具”模式，而不是开放任意 Shell。

初期原则：

```text
1. 不开放任意 shell。
2. 所有路径限制在 workspace root 内。
3. 模型只能输出结构化 ActionPlan。
4. Harness 负责校验和执行。
5. 所有写操作必须 dry-run + approval。
6. 所有 move/copy/rename 必须有 rollback manifest。
```

---

### 3.3 工作流、Connector 与 Skill 扩展参考

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| n8n | https://github.com/n8n-io/n8n | 高关注工作流自动化平台，支持 native AI capabilities、可视化构建、custom code、自托管和大量 integrations。 | 借鉴 workflow、connector、recipe、run history 等思想。但 LocalFlow 不应一开始做低代码平台，而应先做本地个人自动化 Harness。 |
| Activepieces | https://github.com/activepieces/activepieces | 开源 AI automation / workflow automation 项目，强调 type-safe pieces framework，并可让 Pieces 自动成为 MCP servers。 | 启发 LocalFlow 的 SkillManifest 和 Skill Pack 机制：每个 Skill 应该是独立、可声明、可测试、可接入的能力包。 |
| Flowise | https://github.com/FlowiseAI/Flowise | 可视化构建 AI Agents / workflow 的低代码平台。 | 仅作为后续可视化编排参考，不建议作为主线。LocalFlow 初期应保持 Python CLI / API 优先，避免过早做复杂前端。 |
| Langflow | https://github.com/langflow-ai/langflow | 用于构建和部署 AI-powered agents and workflows 的平台，提供可视化编排、API、MCP server 等能力。 | 后续如果 LocalFlow 做成可视化工作流或 MCP 工具服务，可以参考其 workflow-as-tool 和部署思路。 |

#### 对 LocalFlow 的结论

LocalFlow 后续可以从单一 Agent 演进为：

```text
LocalFlow Harness Kernel
+
Skill Registry
+
Tool Registry
+
Recipe System
+
Run History
```

但 MVP 不做平台化。初期只需要保证：

```text
新增 Skill 不修改 Harness Kernel。
```

---

### 3.4 垂直 Skill 能力参考

| 项目 | GitHub 仓库 | 简介 | 对 LocalFlow 的启发 |
|---|---|---|---|
| TaskWeaver | https://github.com/microsoft/TaskWeaver | Microsoft 开源的 code-first data analytics agent framework，面向数据分析任务，能将用户请求转成代码片段并协调插件函数执行。 | 后续 DataOps Skill 可借鉴其模式：CSV/Excel → schema 检查 → Python 代码生成 → 执行 → 图表 → 报告 → 结果校验。 |
| browser-use | https://github.com/browser-use/browser-use | 面向浏览器自动化的 AI Agent 项目，目标是让网站能被 AI agents 使用。 | 后续 WebCollect Skill 可借鉴其浏览器操作、网页内容提取、网页任务 trace 等能力。但 LocalFlow MVP 不做真实网页提交。 |
| Open Deep Research | https://github.com/langchain-ai/open_deep_research | LangChain 开源的 deep research agent，支持多模型、多搜索工具和 MCP servers。 | 后续 Research/WebCollect Skill 可借鉴其 research planner、source tracking、citation collection 和 report synthesis 思路。 |
| Mem0 | https://github.com/mem0ai/mem0 | 面向 AI agents 的 universal memory layer，用于记住用户偏好、适应个人需求，并支持长期个性化交互。 | 后续 Memory Skill 可用于记住用户命名风格、常用目录结构、禁用路径、报告模板、常用任务偏好。 |
| mini-swe-agent | https://github.com/SWE-agent/mini-swe-agent | 轻量级软件工程 Agent，可用于解决 GitHub issues 或命令行任务。 | 不作为 LocalFlow 主线，但可借鉴其简洁命令执行、任务日志、代码任务 sandbox 和失败反馈机制。 |

#### 对 LocalFlow 的结论

LocalFlow 的后续 Skill Pack 可以按以下方向逐步扩展：

```text
FileOps Skill       → 参考 MCP Filesystem、Open Interpreter
DocumentOps Skill   → 参考 Open Deep Research 的资料处理与报告生成
DataOps Skill       → 参考 TaskWeaver
WebCollect Skill    → 参考 browser-use、Open Deep Research
Memory Skill        → 参考 Mem0
Automation Recipe   → 参考 n8n、Activepieces
```

---

### 3.5 不建议作为 MVP 主线的项目类型

以下项目或方向可以参考，但不建议作为 LocalFlow 初期主线：

| 类型 | 代表项目 | 不作为主线的原因 |
|---|---|---|
| 低代码 Agent Builder | Flowise、Langflow | 会把重点转移到前端和平台化，稀释 Python + Harness Engineering 核心。 |
| 多用户工作流平台 | n8n、Activepieces | 工程量大，容易陷入用户系统、权限、多租户、部署等平台问题。 |
| 通用 OS Agent | Open Interpreter | 能力范围过大，安全风险高，LocalFlow 初期应限制在 workspace 内的个人数字资料处理。 |
| 浏览器全自动 Agent | browser-use、Magentic-UI | 演示强，但真实网页不稳定；可以作为后续 WebCollect Skill，而不是 MVP。 |
| 代码修复 Agent | OpenHands、mini-swe-agent | 工程含量高，但偏 coding agent，容易偏离 LocalFlow 的个人数字工作区自动化主线。 |

---

### 3.6 LocalFlow 的差异化定位

综合以上项目，LocalFlow 不应被定义为：

```text
又一个 Open Interpreter
又一个 n8n
又一个 Flowise
又一个浏览器 Agent
又一个个人助手
```

LocalFlow 的准确定位是：

```text
面向个人数字工作区的安全自动化 Agent Harness。
```

与参考项目的区别：

| 对比对象 | LocalFlow 的差异 |
|---|---|
| Open Interpreter | Open Interpreter 偏通用电脑操作；LocalFlow 偏安全、可控、可回滚的个人资料自动化。 |
| DeepAgents | DeepAgents 是通用 Agent Harness；LocalFlow 是基于 Harness 思想的具体个人自动化应用。 |
| MCP Filesystem Server | MCP Filesystem 是文件工具层；LocalFlow 是完整执行控制层。 |
| n8n / Activepieces | 它们是工作流平台；LocalFlow 是 Agent 驱动的本地任务执行 Harness。 |
| TaskWeaver | TaskWeaver 偏数据分析；LocalFlow 覆盖文件、文档、数据、资料索引等个人数字工作区任务。 |
| Mem0 | Mem0 是记忆层；LocalFlow 后续可把记忆作为个性化 Skill。 |
| browser-use | browser-use 偏网页操作；LocalFlow 初期聚焦本地 workspace，后续再扩展 WebCollect。 |

---

### 3.7 对项目设计的直接约束

参考上述项目后，LocalFlow 初期必须坚持以下设计约束：

```text
1. 不做万能个人助理。
2. 不做多用户平台。
3. 不开放任意 shell。
4. 不自动删除文件。
5. 不直接执行模型自然语言指令。
6. 所有副作用操作必须结构化为 Action。
7. 所有写操作必须 dry-run。
8. 所有高风险操作必须 approval。
9. 所有 move/copy/rename 必须有 rollback manifest。
10. Verifier 必须独立判断任务结果。
11. 新 Skill 必须遵守 inspect → plan → validate → dry-run → execute → verify → report 生命周期。
12. 新 Skill 不允许绕过 Harness Kernel。
```

---

### 3.8 推荐的参考优先级

如果开发时间有限，优先参考顺序如下：

```text
第一优先级：
DeepAgents
Magentic-UI
MCP Filesystem Server
Open Interpreter

第二优先级：
TaskWeaver
Mem0
n8n
Activepieces

第三优先级：
browser-use
Open Deep Research
Composio
Langflow
Flowise
OpenHands
mini-swe-agent
```

第一优先级用于确定 LocalFlow 的核心架构：

```text
Agent Harness
Plan Preview
Dry-run
Approval
Workspace Boundary
Structured Tool Calls
Rollback
Verifier
```

第二优先级用于后续 Skill 与个性化扩展：

```text
DataOps
Memory
Workflow Recipe
Skill Registry
```

第三优先级用于长期扩展：

```text
WebCollect
Research Agent
Tool Ecosystem
Visual Workflow
Coding Agent
```

---

## Skill 与参考项目映射

请在「## 12. Skill 扩展机制」或「## 13. 新功能加入原则」后补充以下小节。

### Skill 与参考项目映射

LocalFlow 的 Skill 扩展可以按照参考项目逐步演进：

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

补充完成。
