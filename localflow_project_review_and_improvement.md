# LocalFlow 项目进度评审与改进建议

> 评审对象：`https://github.com/zhangyi-nb1/localflow`  
> 评审方式：基于公开仓库 README、docs 与部分源码的静态审查；未进行本地 clone、安装、运行测试。  
> 当前结论：项目方向成立，Harness Engineering 主线清晰，但当前已经进入“功能收口、可信展示、安全边界强化、Release Hardening”的阶段，不建议继续无节制扩展新功能。

---

## 1. 总体判断

当前 LocalFlow 已经不是普通的 Agent Demo，而是一个有明确工程主线的 **Agent Execution Harness** 项目。

项目核心思想可以概括为：

```text
The model proposes; the harness disposes.
```

也就是：

```text
模型负责理解、推理、规划和生成 ActionPlan；
Harness 负责安全边界、dry-run、approval、checkpoint、execute、rollback、verify 和 audit。
```

### 当前阶段判断

```text
项目原型已经超过 MVP，进入“需要收口、证明、打磨”的阶段。
```

你现在不缺功能。项目已经具备：

```text
folder_organizer
pdf_indexer
data_reporter
data_analyzer
memory
MCP server
external skill loader
tool registry
contract test
```

这些功能说明项目扩展性已经建立。但当前更需要证明的是：

```text
LocalFlow 的 Harness 架构是否足够安全、稳定、可审计、可展示、可维护。
```

### 阶段评分

| 维度 | 评分 | 说明 |
|---|---:|---|
| 项目想法 | 8.5 / 10 | 方向清楚，和普通 Agent 项目有明显差异 |
| 架构设计 | 8 / 10 | Harness Kernel、Skill、Tool Registry、Verifier 分层是正确的 |
| 工程完整度 | 7.5 / 10 | 功能丰富，但需要 Release 化和展示材料 |
| 简历展示力 | 7 / 10 | 目前缺少强 demo、before/after、安全测试矩阵 |
| 安全可信度 | 6.5 / 10 | MCP approval、external skill、memory mutation 等边界需要加强 |

---

## 2. 当前做得好的地方

### 2.1 项目定位明确

当前项目已经没有停留在“文件整理 Agent”层面，而是明确强调：

```text
LocalFlow 不是一个会整理文件的 Agent，
而是一个围绕 LLM 的安全执行 Harness。
```

这个定位是正确的，也是项目区别于普通 LangChain / Agent Demo 的关键。

---

### 2.2 Harness 架构已经成型

项目架构中已经体现出几个关键边界：

```text
1. Skill 只负责产生 ActionPlan。
2. Tool Registry 提供 read / transform / render helper。
3. mutating file_ops 不注册给 Skill。
4. Executor 是唯一允许做真实文件 IO 的模块。
5. Verifier 独立于模型。
```

这说明项目已经抓住了 Agent 工程的关键问题：

```text
LLM 不应该直接控制真实环境。
```

而应该由 Harness 负责：

```text
结构化动作
权限边界
执行前预演
用户审批
状态持久化
失败恢复
结果验证
审计追踪
```

---

### 2.3 Skill 插件化方向正确

你已经实现 Skill ABC、SkillRegistry、filesystem loader、contract test，并支持 external skills。

这说明项目不是单功能工具，而是可扩展框架。

简历和面试中可以强调：

```text
LocalFlow 通过 Skill 生命周期和契约测试，使新任务能力可以在不修改 Harness Kernel 的情况下接入。
```

这体现：

```text
可扩展架构
插件化设计
框架化思维
契约测试意识
```

---

### 2.4 Tool Registry 与 Harness Kernel 的边界设计较好

当前架构里，Tool Registry 不是执行真实副作用的地方，而是提供共享工具能力。

这个边界很重要：

```text
Tool Registry 是 helper surface；
Harness Kernel 才是 side-effect boundary。
```

可以在后续文档中重点强调：

```text
LocalFlow separates reasoning, planning, transformation, and side-effect execution.
```

---

### 2.5 MCP Server 有前瞻性

LocalFlow 已经可以作为 MCP Server 对外暴露，说明项目具备生态连接能力。

但 MCP Server 也是当前风险较高的部分，因为它会让外部 MCP Client 触发 LocalFlow 的能力。

因此 MCP Server 现在应定位为：

```text
低风险集成入口，而不是完全开放的远程执行入口。
```

---

## 3. 当前主要问题与改进建议

---

## 问题 1：功能扩展太快，主线容易被稀释

### 表现

当前项目已经具备多个 Skill、Memory、MCP Server、External Skill Loader、Tool Registry 等功能。

这说明工程推进快，但也带来一个问题：

```text
别人很难在 1 分钟内判断项目主成果是什么。
```

如果继续加 WebCollect、MCP Client、更多 Memory 类型，项目会变成：

```text
功能很多，但主线不够锐利。
```

### 改进建议

下一阶段不要继续扩新功能，应进入：

```text
Phase 7：Release Hardening / 项目收口
```

核心任务：

```text
v0.1.0 或 v0.6.1 Release 收口
README 打磨
Demo 固化
测试证明
安全边界说明
项目演示材料
```

项目主线应该收束为一句话：

```text
LocalFlow 是一个面向个人 workspace 的 Agent Execution Harness。
它用结构化 Action、Policy Guard、Dry-run、Approval、Rollback、Verifier
保证 Agent 能安全执行本地自动化任务。
```

---

## 问题 2：MCP Server 的 approval 设计存在安全争议

### 表现

当前 MCP Server 中，如果 `execute_plan` 只需要外部 MCP Client 传入：

```text
approved=true
```

就能触发执行，那么安全等级不够。

因为：

```text
CLI --yes 是本地用户主动执行；
MCP approved=true 是外部客户端传入参数。
```

两者安全等级不同。

潜在问题：

```text
dry-run 和 execute 之间没有强绑定。
approved=true 没有本地确认 token。
MCP client 理论上可以 create_plan → execute_plan。
```

### 改进建议

将：

```text
execute_plan(task_id, approved=true)
```

升级为：

```text
dry_run → approval_token → execute_plan(task_id, approval_token)
```

### approval_token 设计要求

approval token 应绑定以下信息：

```text
task_id
plan_hash
dry_run_hash
workspace_root
created_at
expires_at
```

执行时必须检查：

```text
1. token 是否存在。
2. token 是否过期。
3. plan_hash 是否一致。
4. dry_run_hash 是否一致。
5. workspace_root 是否一致。
6. 当前 plan 是否被修改。
```

如果 plan、dry-run 或 workspace 发生变化，token 失效。

### 推荐规则

MCP Server 默认只开放低风险工具：

```text
localflow_inspect
localflow_plan
localflow_dry_run
localflow_status
localflow_verify
localflow_list_runs
```

`execute` 和 `rollback` 如果开放，必须：

```text
requires_local_approval = true
approval_token_required = true
```

这是 P0 级别问题。

---

## 问题 3：MCP 暴露 Memory mutation 可能削弱安全边界

### 表现

如果 MCP Server 暴露以下工具：

```text
memory_forbid_path
memory_unforbid_path
memory_set_naming_style
memory_unset_naming_style
```

其中 `memory_unforbid_path` 是敏感操作。

风险是：

```text
用户曾经设置 private/secrets 不允许访问。
外部 MCP client 调用 memory_unforbid_path。
然后再执行计划。
```

这会削弱用户原本设置的安全边界。

### 改进建议

将 Memory mutation 分级。

低风险工具：

```text
read_memory_prefs
read_memory_audit
memory_set_naming_style
```

高风险工具：

```text
memory_forbid_path
memory_unforbid_path
memory_unset_naming_style
```

建议策略：

```text
1. memory_unforbid_path 默认不通过 MCP 暴露。
2. 如果暴露，必须本地确认。
3. 或要求环境变量显式开启：
   LOCALFLOW_MCP_ALLOW_MEMORY_MUTATION=true
4. 所有 memory mutation 必须写入 audit log。
```

---

## 问题 4：External Skill 插件机制存在安全边界问题

### 表现

当前 External Skill 是 Python 代码。即使 Skill 声明 required_tools，外部 skill.py 仍可能：

```python
import os
import shutil
import pathlib
```

然后直接绕过 Harness Kernel 做文件操作。

这意味着：

```text
External Skill Loader 现在是插件扩展机制，
但还不是安全插件沙箱。
```

### 改进建议

短期必须在 README / ARCHITECTURE 中明确声明：

```text
External skills are trusted Python code in current version.
Tool Registry validates declared dependencies but does not sandbox arbitrary imports.
```

也就是说：

```text
Skill contract test 只能证明生命周期兼容，不能证明插件安全。
```

### 短期防护

```text
1. 默认不自动加载 external skills。
2. 必须显式传入 --enable-external-skills。
3. 加载前输出来源路径和风险提示。
4. external skill 加载记录写入 audit log。
```

### 中长期防护

```text
1. 子进程隔离。
2. 静态扫描危险 import。
3. 限制 import。
4. 独立 Python subprocess。
5. 声明式 Skill Manifest。
6. WASM / sandbox runtime。
```

当前不要对外宣称 external skill 是安全沙箱。

---

## 问题 5：缺少强展示材料

### 表现

当前项目文档和测试较多，但缺少能快速证明项目价值的展示材料。

应补充：

```text
before/after 文件树
dry_run.md 示例
final_report.md 示例
rollback 前后对比
MCP 调用示例截图
demo GIF
```

面试官或项目查看者最关心的是：

```text
这个项目实际运行时长什么样？
它如何防止误操作？
它如何回滚？
它和普通 Agent 的差别在哪里？
```

### 改进建议

新增：

```text
docs/demo_walkthrough.md
```

内容结构：

```text
1. 初始 workspace 文件树
2. 用户输入 goal
3. plan.json 片段
4. dry_run.md 示例
5. execute 后文件树
6. verify_report.json 片段
7. rollback 后文件树恢复
8. final_report.md 输出
```

建议再补：

```text
assets/demo.gif
assets/before_after_tree.png
assets/dry_run_preview.png
```

---

## 问题 6：Git 历史和 Release 不利于展示工程演进

### 表现

如果仓库只有很少 commit，且没有 GitHub Release，就不利于展示项目逐步演进。

项目虽然文档中有多个 Phase，但 Git 历史不能体现这些 Phase 的开发过程。

### 改进建议

从现在开始按工程项目方式维护：

```text
1. 每个功能单独 issue。
2. 每个任务单独 commit。
3. 每个阶段打 tag。
4. 每个版本写 release notes。
5. README 只展示当前稳定能力，Phase 历史放到 docs/PHASES.md。
```

建议 release/tag：

```text
v0.6.1-release-hardening
v0.7.0-webcollect
v0.8.0-mcp-client
```

---

## 问题 7：核心源码可读性可能需要检查

### 表现

如果部分核心文件在 raw 视图中显示为极少行的大长行，例如：

```text
control_loop.py
action.py
policy_guard.py
```

那么可维护性会受到影响。

### 改进建议

立即执行格式化：

```bash
ruff format .
ruff check . --fix
```

并在 `pyproject.toml` 中加入：

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
```

核心代码必须适合 review。否则面试官打开源码会扣分。

---

## 问题 8：LLM Provider 文档不应依赖不可复现配置

### 表现

如果公开文档里出现私有 relay proxy、project memory、不可复现模型配置等内容，会影响开源项目的可复现性。

公开项目应避免：

```text
依赖 project memory
依赖私有 proxy
依赖不可复现的默认模型
```

### 改进建议

统一改成：

```text
Default provider: OpenAI-compatible API.
Set LOCALFLOW_LLM_PROVIDER and LOCALFLOW_LLM_MODEL in .env.
```

`.env.example` 中给出：

```env
LOCALFLOW_LLM_PROVIDER=openai-compatible
LOCALFLOW_LLM_MODEL=your-model-name
LOCALFLOW_LLM_BASE_URL=https://api.example.com/v1
LOCALFLOW_LLM_API_KEY=your-api-key
```

不要在公开文档中写无法复现的 relay proxy 细节。

---

## 问题 9：依赖管理可以更专业

### 表现

如果默认 dependencies 中包含大量可选功能依赖，例如：

```text
pypdf
pandas
openpyxl
matplotlib
anthropic
mcp
```

会导致默认安装偏重。

### 改进建议

拆分 optional dependencies。

示例：

```toml
[project.optional-dependencies]
llm = ["openai>=1.50", "anthropic>=0.92"]
docs = ["pypdf>=4.0", "python-docx>=1.1"]
data = ["pandas>=2.0", "openpyxl>=3.1", "matplotlib>=3.7"]
mcp = ["mcp>=1.6,<2.0"]
dev = ["pytest", "pytest-cov", "ruff", "mypy"]
```

默认依赖保持轻量：

```text
pydantic
typer
rich
pyyaml
python-dotenv
```

---

## 4. P0 优先级改进清单

P0 是必须优先处理的问题。

### P0-1：收口 Release

马上做：

```text
1. 创建 Git tag。
2. 创建 GitHub Release。
3. release notes 写清楚 shipped / known limitations。
4. 上传 demo artifact。
5. README 标明当前 release 状态。
```

---

### P0-2：修 MCP execute approval

将：

```text
approved=true
```

改为：

```text
approval_token
```

并绑定：

```text
task_id
plan_hash
dry_run_hash
workspace_root
expires_at
```

---

### P0-3：明确 external skills 是 trusted code

README / ARCHITECTURE 加：

```text
External skills are trusted Python code in the current version.
They are not sandboxed.
Tool Registry validates declared dependencies but does not prevent arbitrary imports.
```

---

### P0-4：补 demo walkthrough

新增：

```text
docs/demo_walkthrough.md
```

必须包含：

```text
before tree
user goal
plan.json
dry_run.md
after tree
verify_report.json
rollback result
final_report.md
```

---

### P0-5：格式化源码

执行：

```bash
ruff format .
ruff check . --fix
```

---

## 5. P1 改进清单

### P1-1：加 GitHub Actions

至少包含：

```yaml
pytest
ruff check
ruff format --check
build wheel
```

README 增加 badge。

---

### P1-2：加测试覆盖率

增加：

```text
coverage badge
coverage report
关键模块覆盖率说明
```

重点覆盖：

```text
policy_guard
executor
rollback
verifier
mcp tools
external skill loader
memory forbidden_paths
```

---

### P1-3：补安全测试矩阵

新增文档：

```text
docs/security_test_matrix.md
```

建议测试项：

```text
case_001: path traversal
case_002: symlink escape
case_003: forbidden_paths
case_004: overwrite
case_005: delete
case_006: MCP approved without token
case_007: rollback after partial failure
case_008: external skill import escape
```

每项说明：

```text
测试目的
输入样例
预期行为
当前状态
相关测试文件
```

---

### P1-4：README 降噪

README 建议只保留：

```text
What is LocalFlow
Why Harness
Quickstart
Demo
Architecture
Safety Model
Extensibility
Roadmap
```

Phase 细节移到：

```text
docs/PHASES.md
```

---

## 6. P2 改进清单

### P2-1：WebCollect 不急

WebCollect 可以作为后续版本：

```text
v0.7.0 或 v0.8.0
```

前提是：

```text
MCP execute 安全修完
Release 打完
Demo 走通
CI 有了
External Skill 风险写清楚
```

---

### P2-2：考虑轻量 Web UI

后续可以做轻量 UI：

```text
left: workspace tree
middle: dry-run action list
right: risk / approval / rollback
```

这对展示很有帮助，但不是当前优先级。

---

## 7. 简历表达建议

不要写成：

```text
开发了一个个人文件整理 Agent。
```

这太弱，也容易被认为是脚本或普通 Agent Demo。

应该写成：

```text
设计并实现 LocalFlow Agent，一个面向个人本地 workspace 的 Agent Execution Harness。
系统将 LLM 规划与真实文件 IO 解耦，通过结构化 ActionPlan、Policy Guard、Dry-run、Approval、Rollback Manifest、Independent Verifier 和 Skill Contract Test，实现本地文件整理、PDF 索引和数据分析等任务的安全执行与可恢复控制。
```

如果 MCP approval token 修复完成，可以补充：

```text
支持 CLI 与 MCP Server 双入口，所有入口复用同一 Harness Kernel，保证外部 Agent 客户端无法绕过路径边界、风险检查、rollback 和 verifier。
```

但这句话成立的前提是：

```text
MCP execute approval token 已完成。
```

---

## 8. 下一步明确执行指令

下一阶段建议进入：

```text
Phase 7：Release Hardening
```

执行清单：

```text
1. 修 MCP execute approval：approved=true → approval_token。
2. README 声明 external skills are trusted code, not sandboxed。
3. 整理 README，弱化 Phase 堆叠，强化核心 Harness 卖点。
4. 增加 docs/demo_walkthrough.md，包含 before/after tree 和 artifacts。
5. 添加 GitHub Actions：pytest + ruff + build。
6. 如果源码是大长行，执行 ruff format。
7. 创建 release/tag。
8. 补安全测试矩阵。
```

当前阶段不要继续优先开发：

```text
WebCollect Skill
MCP Client
更多 Memory 类型
更多 Skill Pack
复杂前端
```

因为这些会扩张系统复杂度，而你当前最需要补的是：

```text
可信展示
安全边界
发布质量
工程可维护性
```

---

## 9. 最终结论

LocalFlow 当前方向正确，项目原型已经超过普通学生 Agent 项目。

但当前最大问题不是功能不足，而是：

```text
1. 功能扩展太快，需要收口。
2. MCP execute approval 存在安全边界问题。
3. External Skill 需要明确 trusted code 限制。
4. 缺少强 demo 和 before/after 展示。
5. 需要 GitHub Actions、Release、测试矩阵来提升工程可信度。
```

后续优先级应该是：

```text
先收口、再展示、再加功能。
```

如果完成 Phase 7，LocalFlow 会从“功能较多的 Agent 项目”升级为：

```text
一个有清晰安全模型、可验证执行流程、可恢复机制和可扩展 Skill 架构的 Agent Harness 工程项目。
```
