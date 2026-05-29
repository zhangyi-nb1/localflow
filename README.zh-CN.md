# LocalFlow

**本地优先的 Agent 执行 Harness。** 计划在动文件之前先变成显式产物，
每一步动作都可预览、可审批、可追溯、可校验、可回滚；模型永远拿不到
直接的 shell。

> 🇬🇧 [English README → README.md](README.md)

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   goal ──→ plan ──→ dry-run ──→ approval ──→ execute ──┐        │
│                                                        ▼        │
│                                            verify ◄── trace     │
│                                              │                  │
│                                              ▼                  │
│                                       rollback (随时可用)       │
│                                                                 │
│   ⇧ react loop：每个动作后让 LLM 决策（CONTINUE / REPLACE /     │
│      INSERT / SKIP / ABORT），漂移预算内可控                    │
│   ⇧ Workspace facade：LocalWorkspace · DockerWorkspace ·        │
│      RemoteWorkspace · AgentServerWorkspace 四态可换            │
│   ⇧ ConfirmationPolicy：4 档每动作审批门                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**分支状态** —— `main` 当前 **v0.35.x-dev**。已发版本：
[`v0.35.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.35.0)
（失败模式 ablation benchmark —— `python -m app.eval.failure_modes`）·
[`v0.34.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.34.0)
（旗舰垂直落地——带 claim-level grounding 闸门的可验证文献综述）·
[`v0.33.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.33.0)
（方向收敛——flagship = 可验证 LLM 产物流水线 / verify-as-gate；
UI backend 诚实 CLI bridge）·
[`v0.32.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.32.0)
（UI 对齐 v0.31 CLI 能力——Workspace backend 选择器 / Plan planner
切换 / `--version` / 位置参数 `trace show`）·
[`v0.31.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.31.0)
（DockerWorkspace + RemoteWorkspace 集成 agent-server）·
[`v0.30.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.30.0)
（HTTP agent-server）·
[`v0.29.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.29.0)
（RemoteWorkspace via SSH）·
[`v0.28.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.28.0)
（`localflow_kernel` 独立包）· [`v0.27.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.27.0)
（DockerWorkspace）· [`v0.26.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.26.0)
（Workspace 抽象）· [`v0.25.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.25.0)
（ConfirmationPolicy）· [`v0.24.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.24.0)
（React Loop）· [`v0.23.0`](https://github.com/zhangyi-nb1/localflow/releases/tag/v0.23.0)
（Sandboxed ComputeAction）。**1115 测试通过。** CI 覆盖
macOS / Linux / Windows × Python 3.11 / 3.12 / 3.13。

> **想把 harness 嵌入到自己的工具里？** kernel 是独立可发布的包
> （`localflow_kernel`），带 AST 静态边界 lint —— 见
> [`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md)。

---

## 目录

1. [TL;DR — LocalFlow 是什么？](#1-tldr--localflow-是什么)
2. [LocalFlow 是 / 不是什么](#2-localflow-是--不是什么)
3. [为什么要 harness，不直接给 LLM 一堆工具？](#3-为什么要-harness不直接给-llm-一堆工具)
4. [5 分钟上手](#4-5-分钟上手)
5. [核心概念](#5-核心概念)
6. [三种使用方式](#6-三种使用方式)
7. [功能清单](#7-功能清单)
8. [Workspace 后端](#8-workspace-后端)
9. [配置与持久化](#9-配置与持久化)
10. [重要注意事项（诚信原则）](#10-重要注意事项诚信原则)
11. [故障排除](#11-故障排除)
12. [项目状态](#12-项目状态)
13. [文档地图](#13-文档地图)
14. [开发与贡献](#14-开发与贡献)
15. [License](#15-license)

---

## 1. TL;DR — LocalFlow 是什么？

LocalFlow 是本地 **Agent Execution Harness**，旗舰演示是一条**可验证
LLM 产物流水线**：被 harness 约束的生成过程（typed plan → dry-run →
approval → rollback）产出产物，再由**独立 verifier 作为执行闸门**判定
该 ship 还是 rollback —— 不是事后看板。

模型只输出一份 Pydantic 的 `ActionPlan`。Kernel —— 也只有 kernel ——
碰磁盘。每一道安全防线（预览 / 审批 / 校验 / 回滚 / trace）都可以独立测试。

> **旗舰 demo —— 带出处核验的文献综述。** 喂进一批论文 PDF，LocalFlow
> 逐篇摘要、综合成综述，再由 **grounding gate** 把综述拆成一条条论断，
> 逐条检查能否追溯到源文档片段。追溯不到的论断被标记并转人工复核；
> 若无出处论断超过阈值，产物被闸门判为*不可交付*并回滚。（生成那一环
> 可以不完美——harness 才是让它可用、可审计、可恢复的东西。）这正面回应
> 2025–26 的现实：连 3–5 位专家评审都会漏掉接收论文里编造的引用——见
> [`docs/PHASE_35_PLAN.md`](docs/PHASE_35_PLAN.md) §4。

```bash
# 安装（editable，开发推荐）
pip install -e .

# 30 秒冒烟测试
.venv/bin/localflow pack list           # 列出内置 deliverable packs
.venv/bin/localflow ui-serve            # 打开 Streamlit UI

# CLI 标准流程 —— 先用最简单的确定性任务熟悉循环
.venv/bin/localflow plan ./my-folder --goal "按文件类型整理" --planner rule
.venv/bin/localflow dry-run  --task-id <task_id>
.venv/bin/localflow execute  --task-id <task_id> --yes
.venv/bin/localflow rollback --run-id  <task_id> --yes
```

> "按文件类型整理乱目录"是熟悉 plan → execute → rollback 循环**最简单**的
> 任务，是*入门示例*而非重点——重点是让任意 LLM 驱动的生成变得安全、
> 受闸门约束、可撤销的那套 harness。

---

## 2. LocalFlow 是 / 不是什么

### LocalFlow 是

- **默认安全的本地自动化执行 harness**——文件整理、文档索引、
  数据报告、项目交接等本地任务。
- **kernel + facade 架构**——kernel 是可独立 import 的
  `localflow_kernel`（PEP 561 typed）；facade（`app/*`）在上面叠
  CLI / UI / skills / recipes / eval / MCP server。
- **多后端 Workspace 抽象**——同一份 plan 可以跑在本地文件系统、
  Docker 容器、SSH 远程主机、或容器内 HTTP agent（热路径 10× 提速）。
- **审计就绪的执行流**——每次 run 产生一份 append-only 的
  `trace.jsonl`，记录 LLM 的 thought / reasoning / 原始 tool_use
  以及动作的磁盘 observation。`localflow verify` + 自动 repair 都
  从 trace 读。

### LocalFlow 不是

- **任意代码执行器**。模型没有 `shell()` / `eval()`。它可以写
  Python 脚本（`PYTHON_COMPUTE`），但脚本跑在 scratch workspace 的
  子进程里，有 timeout 上限和 env scrub —— 见
  [`docs/COMPUTE_ACTION.md`](docs/COMPUTE_ACTION.md)。
  **这是 isolation（隔离），不是 security sandbox（安全沙箱）**
  （CLAUDE.md 规则 F）。
- **完全自动化的 agent**。harness 的精髓就是审批门。某些工作流
  可以用 `--yes` 跑无人值守，但项目默认所有 HIGH-risk 动作都要
  `requires_approval`。
- **云服务**。所有东西跑在你的本地机器（或你控制的远程 Linux
  主机）。除非你显式开启带 allowlist 的 WebCollect skill，否则
  数据不出你的网络。

---

## 3. 为什么要 harness，不直接给 LLM 一堆工具？

默认的"LLM + 工具"模式把 `shell(cmd)` 或 `delete(path)` 直接交给
模型。一次幻觉或 prompt-injection，你的文件就没了——没有预览、
没有审批、没有撤销。

LocalFlow 反过来。模型只输出 `ActionPlan`，kernel 是唯一能动磁盘
的代码，每一道安全防线独立可测：

| 特性 | 朴素 tool-call agent | LocalFlow |
|---|---|---|
| 写盘前 dry-run | ✗ | ✓ markdown 预览 + 审批 token |
| Workspace 边界 | 弱（path 前缀） | ✓ `policy_guard.resolve_inside` 唯一权威 |
| 每动作审批粒度 | 全开或全关 | ✓ 4 档 `ConfirmationPolicy`（`never` / `always` / `on_high_risk` / `on_write`） |
| 整个 run 一键回滚 | ✗ | ✓ `RollbackManifest`，漂移感知 + sha-256 校验 |
| 独立 verifier（规则 + LLM-as-judge） | ✗ | ✓ 6 个 structural + 7 个 deliverable + 每动作 critic_result |
| 执行中 LLM 动态决策 | tool-call 全开 | ✓ react loop 漂移预算可控；LLM 决策仍过 policy_guard |
| 沙盒代码执行 | 临时 shell | ✓ 类型化 `PYTHON_COMPUTE`，输出到 scratch，隔离回滚 |
| 动作 trace（审计就绪） | 部分 | ✓ 每 run 一份 `trace.jsonl`，统一 `ActionTraceEvent` 形状 |
| 文件系统后端可换 | 写死 | ✓ `Workspace` Protocol —— Local + Docker + Remote 已 ship |

§10.7 ledger（`docs/PHASES.md`）追踪每一次 kernel 改动：
**44 次交付中 4 次 deliberate exception，40 次零 kernel 触碰**。
这个比例就是项目的身份契约。

### 实测：失败模式 ablation benchmark

上面那张表是定性的。下面是**实测**支撑——对六大失败模式
（见 `docs/research/FEISHU_HARNESS_ENGINEERING_SUMMARY.md` §11）各注入一个按构造的失败，
做 **ablation**（每条防线 开 vs 关）。自己跑：`python -m app.eval.failure_modes`
（确定性、不需 API key）。

| # | 失败模式 | LocalFlow 防线 | 防线关 | 防线开 | 状态 |
|---|---|---|---|---|---|
| 1 | 目标偏移 | react loop drift budget | ❌ ships | ✅ caught | mitigated |
| 2 | 虚假完成 | grounding gate（verify-as-gate）| ❌ ships | ✅ caught | mitigated |
| 3 | Context Rot / 状态丢失 | *（暂无）* | ❌ ships | ❌ ships | **gap（诚实）** |
| 4 | 工具 / 环境失控 | policy_guard | ❌ ships | ✅ caught | mitigated |
| 5 | 质量 / 熵增 | deliverable verifier | ❌ ships | ✅ caught | mitigated |
| 6 | Harness 自身问题 | §10.7 ledger + 边界 lint | n/a | n/a | process control |

**防线在 4/4 个运行时失败模式上起了决定性作用。** 两条故意留在表里的诚信说明：
**Context Rot 是真实 gap**——LocalFlow 没有长任务 handoff/checkpoint/resume，两种模式
都 ship 失败；**Harness 自身问题**是过程控制（边界 lint + ledger），不是 per-task 数字。
这是 ablation（"每条防线买到了什么"），不是和竞品对比；数字证明的是"防线在该触发时确实
触发"（确定性注入失败），不是野外失败率。详见 [`docs/PHASE_37_DESIGN.md`](docs/PHASE_37_DESIGN.md)。

---

## 4. 5 分钟上手

### 4.1 安装

```bash
# clone + 建 venv
git clone https://github.com/zhangyi-nb1/localflow.git
cd localflow
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 启用 pre-push hook（镜像 CI：ruff + pytest）
git config core.hooksPath .githooks
```

可选依赖：

```bash
# 使用 LLM 规划器（Phase 18 goal-interpreter 或 LLM-mode skills）
export ANTHROPIC_API_KEY=sk-ant-...

# 使用 Docker workspace 后端
# （任意 Docker Desktop / Docker Engine，Linux containers 模式）
docker --version

# 使用 SSH workspace 后端
# （免密 ssh 到远程，强制 BatchMode=yes）
ssh -o BatchMode=yes user@host true
```

### 4.2 挑一个 deliverable pack 跑

```bash
# 内置 flagship packs —— 把一个文件夹变成 deliverable
.venv/bin/localflow pack list
.venv/bin/localflow pack describe research_pack
.venv/bin/localflow pack run research_pack --workspace ./my-research-folder
```

每个 pack 是一份 recipe，编译成 TaskGraph（多阶段 plan）。CLI 会
按阶段打印 risk、要审批、执行、verify，并把所有产物存到
`.localflow/runs/<task_id>/`。

### 4.3 CLI：plan → execute → rollback

```bash
# 1. 生成 plan
.venv/bin/localflow plan ./messy-folder \
    --goal "按文件类型整理" \
    --planner rule

# CLI 会打印：Task created: 2026-05-28-001 · Plan: ... · Actions: 11 · Risk: medium

# 2. 预览 dry-run（每动作一行的 markdown 表格）
.venv/bin/localflow dry-run --task-id 2026-05-28-001

# 3. 执行（--yes 跳过交互式审批）
.venv/bin/localflow execute --task-id 2026-05-28-001 --yes

# 4. 看跑了什么
.venv/bin/localflow trace summary 2026-05-28-001
.venv/bin/localflow trace show 2026-05-28-001 --show-observation

# 5. 整体撤销（bit-for-bit）
.venv/bin/localflow rollback --run-id 2026-05-28-001 --yes
```

### 4.4 UI：打开 Streamlit

```bash
.venv/bin/localflow ui-serve --port 8501
# 浏览器自动打开 http://127.0.0.1:8501
```

侧边栏列 8 个页面：**Home / Create Pack / Workspace / Runs /
Settings / Plan / Execute / Rollback**。侧边栏底部的 Workspace
backend 徽章会显示当前激活的后端（Local / Docker / Remote）。

---

## 5. 核心概念

下面是你会遇到的 Pydantic 类型——CLI / UI / MCP 调用方都在生产或
消费它们。

### `TaskSpec` —— 用户的诉求

承载用户的 `goal` / 目标 `workspace_root` / 选用的 `skill` /
`allowed_actions` / `forbidden_actions` / `forbidden_paths` 以及
运行时偏好。落盘为 `task.json`。

### `ActionPlan` —— 规划器的产物

一组 `Action` 对象，每个都是类型化的（`MKDIR` / `MOVE` / `COPY` /
`INDEX` / `FETCH` / `PYTHON_COMPUTE`），带 `source_path` /
`target_path` / `risk_level` / `requires_approval` 以及人类可读的
`reason`。规划器要么是确定性的（`rule`，~300ms），要么是 LLM-
backed（`llm`，~20s，用 Anthropic API）。

### `RiskAssessment` —— policy_guard 的判定

每动作的 verdict（`allow` / `block`）+ 整体 plan 的总结
（`risk_level` / `warnings`）。**在**用户审批之前算好——用户看到的
是 `risk=medium` 配上被 block 的动作数。

### `ConfirmationPolicy` —— 何时暂停

4 档枚举：`NEVER` / `ALWAYS` / `ON_HIGH_RISK` / `ON_WRITE`，默认
`ON_HIGH_RISK`。executor 在每动作前查这个；用户提供
`action_approver` 回调（默认 = CLI prompt 或 UI dialog）做按动作
决策。

### `RollbackManifest` —— 怎么撤销

按动作顺序写入的逆向操作账本。每个成功动作写一条（或多条）；失败
动作也会写 `DELETE_SCRATCH_DIR`（仅 PYTHON_COMPUTE）。`localflow
rollback` 倒序回放，每个文件恢复前都做 sha-256 漂移检测。

### `TraceEvent` / `ActionTraceEvent` —— 实际发生了什么

JSONL 流（`trace.jsonl`），每个 kernel 决策一行。Phase 25.1 的
`ActionTraceEvent` 在动作级别行扩展了：

- `thought` —— LLM 的 chain-of-thought（planner=llm 时）
- `reasoning` —— LLM 的自然语言论证
- `tool_call_raw` —— 模型的原始 tool-use 输入
- `observation` —— 实际发生了什么：size_bytes / sha256_after /
  parent_created / error
- `critic_result` —— 语义 verifier 的 verdict（启用时）

用 `localflow trace show <task_id> --show-thought
--show-observation` 或 UI Runs 页查看。

### `Workspace`（Protocol）—— 文件系统门面

Phase 28 的抽象。定义 `exists / stat / sha256 / list_dir /
read_text / read_bytes / mkdir / move / copy / write_text /
write_bytes / safe_target_rel`。4 个实现已 ship：`LocalWorkspace` /
`DockerWorkspace` / `RemoteWorkspace` / `AgentServerWorkspace`。

---

## 6. 三种使用方式

### 6.1 CLI——完整能力，可脚本化

参考驱动方式。每一个 kernel 能力都能通过 `localflow <subcommand>`
触达：

| 子命令 | 作用 |
|---|---|
| `localflow --version` | 打印 kernel 版本 |
| `localflow plan <ws> --goal "..."` | 生成 ActionPlan |
| `localflow dry-run --task-id <id>` | 渲染 markdown 预览 |
| `localflow execute --task-id <id> [--yes]` | 审批 + 执行 |
| `localflow verify --task-id <id>` | 重跑 structural verifier |
| `localflow rollback --run-id <id> [--yes]` | 撤销以前的 run |
| `localflow status [<task_id>]` | 列出 run / 查看某 task |
| `localflow trace show <task_id>` | 美化打印 trace.jsonl |
| `localflow trace summary <task_id>` | 事件类型直方图 |
| `localflow goal "..."` | Phase 18 自然语言入口 |
| `localflow pack {list,describe,suggest,run}` | Deliverable packs |
| `localflow taskgraph run <yaml>` | 跑多阶段 TaskGraph |
| `localflow eval run` | 跑 eval suite |
| `localflow memory {list,forbid,allow-domain,...}` | 持久化偏好 |
| `localflow skills-sig {sign,verify}` | HMAC skill manifest 签名 |
| `localflow mcp-clients {list,add,probe}` | 外部 MCP 服务器 |
| `localflow mcp-serve` | LocalFlow 作为 MCP 服务器（stdio） |
| `localflow ui-serve [--port]` | Streamlit UI |

任意子命令加 `--help` 看完整选项。

### 6.2 UI——可视化，新手友好

```bash
.venv/bin/localflow ui-serve --port 8501
```

| 页面 | 用途 |
|---|---|
| 🌀 Home | hero + 3 个 featured packs（Research / Data Report / Project Handoff）。点卡片直接跳到 Create Pack 并预填。 |
| 📦 Create Pack | 浏览 recipe catalog；describe / suggest / run 任意 pack。顶部是 Phase 18 goal interpreter。 |
| 🗂️ Workspace | 扫当前 workspace，显示文件数 + 过往 run。 |
| 📋 Runs | 本机跑过的每个 task；点开看 verify 状态、trace 事件数、rollback 可用性。 |
| ⚙️ Settings | 6 个 tab —— Forbidden paths（kernel 强制）/ Naming style / Planner preference / Semantic + Repair / **🛰 Workspace backend**（Phase 34）/ Audit log。 |
| 🧭 Plan | 输入 goal，选 planner（rule / llm）。`ANTHROPIC_API_KEY` 没设时默认 rule + 友好降级提示。 |
| ⚡ Execute | preview → review → approve → execute → check。 |
| ↩️ Rollback | 回放 rollback manifest，带 hash 漂移检测。 |

侧边栏显示当前 workspace、当前 Workspace 后端（local / docker /
ssh）、以及 Memory 摘要。

### 6.3 嵌入——`localflow_kernel` 作为库

供下游工具使用 harness 但不想要 LocalFlow 的 CLI / UI / skills /
recipes：

```python
from pathlib import Path
from localflow_kernel import (
    Action, ActionPlan, ActionType, RiskLevel,
    Executor, RunStore,
    LocalWorkspace,
)

plan = ActionPlan(
    plan_id="my-plan",
    task_id="my-task-1",
    summary="kernel-only usage",
    actions=[
        Action(
            action_id="a1",
            action_type=ActionType.MKDIR,
            target_path="outputs/",
            reason="set up output dir",
            risk_level=RiskLevel.LOW,
            reversible=True,
            requires_approval=False,
        )
    ],
)
run_store = RunStore.create(home=Path(".localflow"))
ws = LocalWorkspace(Path("/tmp/my-workspace"))
ex = Executor(workspace_root=ws.root, run_store=run_store, workspace=ws)
outcome = ex.execute(plan, approved=True)
```

kernel 包的 AST 边界 lint（`tests/test_kernel_boundary.py`）保证
`localflow_kernel.*` 永远不 import 应用层模块。嵌入指南详见
[`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md)。

---

## 7. 功能清单

### A. 规划——确定性 或 LLM-backed

两个规划器：

- **`rule`**（大部分 skill 的默认值，~300ms）：纯 Python 启发式。
  确定性，不需要 API key。
- **`llm`**（需 `ANTHROPIC_API_KEY`，~20s）：goal + workspace
  snapshot + skill manifest 走 Claude；模型输出一份 tool-use
  payload 经 Pydantic 校验。带 cache 的不可变 system prompt；
  adaptive thinking。

两者输出同一份 `ActionPlan`。CLI 默认 `rule`；UI Plan 页让你按
goal 选（没 key 时自动降到 `rule` —— Phase 34）。

### B. 安全——三层防线

1. **`policy_guard.resolve_inside`** —— 路径穿越的唯一权威。
   每次 Workspace 写都会先调它。`..` / 绝对路径 / 盘符 / `~`
   全部 reject。
2. **`policy_guard.evaluate_action`** —— 按动作判定，结合 task 的
   `forbidden_actions` / `forbidden_paths` / （对 `FETCH`）
   `fetch_allowed_domains` 白名单。
3. **`ConfirmationPolicy`** —— 运行时门（NEVER / ALWAYS /
   ON_HIGH_RISK / ON_WRITE）。默认 `ON_HIGH_RISK`：只在高风险
   动作前提示用户。

合起来：要动磁盘的动作必须先过 policy → 再过 confirmation →
再被 executor 校验父目录 / 源文件存在 —— 然后才落盘。

### C. 执行——Workspace facade

所有 kernel 写入走 `Workspace` Protocol 实现。换后端不改 kernel：

```bash
localflow execute --task-id T --workspace local          # 默认
localflow execute --task-id T --workspace docker:python:3.12-slim
localflow execute --task-id T --workspace ssh:user@host
localflow execute --task-id T --workspace ssh:user@host:22:/srv/wkspc
```

后端对比表见 §8。

### D. 校验——structural + semantic

execute 后，独立 verifier（`app/harness/verifier.py`）跑 6 个
structural check：每个 MKDIR target 存在、每个 MOVE source 已没了、
每个 COPY source 保留、每个 INDEX target 非空、没有 workspace 外
的文件被动、rollback manifest 哈希匹配。

可选的 **Phase 13 semantic verifier**（`enable_semantic_verifier`
memory pref）跑 7 个 LLM-as-judge grader：output 是否回应 goal、
summary 是否 grounded、chart 是否匹配数据等。失败可触发 Phase 13
的 auto-repair loop。

### E. 回滚——manifest-replay 带漂移检测

每个成功动作按序写一条或多条 `RollbackEntry`。`localflow rollback`：

1. 倒序读 manifest。
2. 对每条，重算它要恢复的文件/目录当前 sha-256。
3. 与 manifest 记录的"动作后" 哈希匹配 → 跑逆向操作。
4. 不匹配（有人在 LocalFlow 外编辑了文件）→ 标记 **drift** 并
   跳过，配清晰提示（用户可以 `--force` 接受漂移继续）。

`PYTHON_COMPUTE` 的 scratch 目录也通过 `DELETE_SCRATCH_DIR` 回滚
—— 失败时也会追加。

### F. Trace——append-only `trace.jsonl`

每个 kernel 决策都 emit 一个 `TraceEvent`。Phase 25 的
`ActionTraceEvent` 在动作行扩展：

- `thought` —— LLM 的 chain-of-thought（planner=llm 时）
- `reasoning` —— LLM 的自然语言论证
- `tool_call_raw` —— 模型的原始 tool-use 输入
- `observation` —— 实际发生了什么：size_bytes / sha256_after /
  parent_created / error
- `critic_result` —— semantic verifier 的 verdict（启用时）

`localflow trace show <task_id> --show-thought --show-observation`
或 UI Runs 页查看。

### G. React loop——执行中 LLM 决策（Phase 26 / v0.24.0）

`--react` 或 recipe `enable_react_mode: true` 开启。每个动作的
observation 之后请教 LLM，它可以决定：

- **CONTINUE** —— 跑下一个计划动作（不变）
- **REPLACE** —— 用一个不同的 Action 替代（例如发现输出是垃圾，
  提一个修正脚本）
- **INSERT** —— 先插入一个动作，再跑原计划动作
- **SKIP** —— 跳过这个计划动作
- **ABORT** —— 结束 run，交给 verify

三个 fail-safe：drift 预算（默认偏离已批 plan 3 次）、LLM
超时 / 解析错误 → fall back 到 batch、policy_guard 拒掉 LLM 提的
动作 → FAILED 记录但 loop 继续。

### H. 沙盒 PYTHON_COMPUTE（Phase 23 / v0.23.0）

LLM 可以写 Python 脚本。约束：

- 跑在全新 scratch workspace（`<home>/scratch/<task>/<action>/`）
- 子进程隔离（独立 Python 进程，不继承父 env）
- 300 秒墙钟超时（可降不可升）
- env scrub：proxy + AI provider keys 被剥
- Unix-only `RLIMIT_AS` 内存上限（macOS best-effort、Windows
  no-op —— 明确文档化）
- 声明为 `ArtifactSpec` 的 output 成功后被搬进 workspace；
  **其他** 都留在 scratch 并被回滚

Recipe 作者用 `RecipeSpec.allow_compute_action: true` opt in。
默认拒绝任意 allowed_actions 列表里的 `python_compute`。

**这是 isolation，不是 security sandbox** —— 一个能控制脚本的
攻击者照样能读 `/etc/passwd`。LocalFlow 的 compute action 防的
是"LLM 幻觉乱写脚本"，不是"对抗性代码"。

### I. Memory preferences——持久化 UX 状态

`~/.localflow/memory/prefs.json`（Phase 34 起 schema v5）：

| 字段 | 默认 | 含义 |
|---|---|---|
| `forbidden_paths` | `[]` | Kernel 拒绝触碰的 workspace 相对路径 |
| `naming_style` | `original` | folder_organizer 的文件名变换 |
| `prefer_llm_planner` | `false` | UI autodetect 偏向 LLM 规划器 |
| `enable_semantic_verifier` | `false` | 跑 Phase 13 grader + auto-repair |
| `max_auto_repairs` | `2` | 自动 repair 次数上限 |
| `fetch_allowed_domains` | `[]` | FETCH 动作的主机白名单 |
| `workspace_backend_spec` | `"local"` | UI 默认的 Workspace 后端 |

每次变更都审计进 `audit.jsonl`。用 `localflow memory audit` 或
Settings → Audit log tab 查看。

### J. Recipes & Packs——组合优于新原语

**Recipe** 是 YAML / Pydantic 描述的多阶段工作流，编译成
`TaskGraph`。**Pack** 是 recipe + 示例数据 + eval task + README
的打包。

已 ship 的 pack：

- `research_pack` —— **旗舰场景的基础**：把一批研究材料（PDF、笔记）
  变成知识包，含逐篇 PDF summary、综合综述、以及追踪每条论断出处的
  **sources ledger**。Phase 36 在此之上加 claim-level **grounding gate**
  （verify-as-gate）：综述里每条论断必须追溯到源片段，否则被标记转
  人工 + 产物被闸门拦下。见 [`docs/PHASE_35_PLAN.md`](docs/PHASE_35_PLAN.md) §5。
- `data_report_pack` —— 把 CSV / Excel 数据变成 deliverable 报告。
- `project_handoff_pack` —— 把项目中期 workspace（代码、笔记、
  数据、图、log）变成交接文档。

每个 pack 配 `examples/<pack>/seed.py` 1 行命令重建示例。详见
[`docs/PACK_BUILDER.md`](docs/PACK_BUILDER.md)。

### K. MCP——LocalFlow 既是 client 又是 server

- **Server**：`localflow mcp-serve` 通过 stdio 暴露 `plan` /
  `execute` / `verify` / `rollback` / `taskgraph_run` /
  `verify_semantic` / `repair_run`。
- **Client**：`localflow mcp-clients add fs 'mcp-filesystem ...'`
  注册外部 MCP 服务器；它们的工具自动加入 Phase 4.2 Tool Registry。

详见 [`docs/MCP.md`](docs/MCP.md)。

---

## 8. Workspace 后端

| 后端 | 每动作延迟 | 隔离 | 持久性 | 最适合 |
|---|---|---|---|---|
| **`local`**（默认） | ~10 μs | 无 | 持久 | 开发回路、单机工作流 |
| **`docker:<image>`** | ~100-300 ms（`use_agent_server` 模式 ~5-20 ms） | container（完整） | 关闭即清 | 风险 / 实验性 plan、可复现镜像 |
| **`ssh:<host>[:<port>][:<root>]`** | ~100-300 ms + 网络 RTT（agent-server 模式 ~10-50 ms） | 网络 | 持久（用户管理） | 专用远程 worker、实验室 VM |
| **`AgentServerWorkspace`**（编程式） | ~1-5 ms localhost / ~10-50 ms LAN | 视传输而定 | 视传输而定 | 嵌入使用；Docker / Remote 的性能升级 |

CLI flag 或 UI Settings → 🛰 Workspace backend tab 切换。每个
后端都有用户手册：

- [`docs/WORKSPACE.md`](docs/WORKSPACE.md) —— LocalWorkspace + Protocol 契约
- [`docs/DOCKER_WORKSPACE.md`](docs/DOCKER_WORKSPACE.md) —— Docker 后端 + agent-server 模式
- [`docs/REMOTE_WORKSPACE.md`](docs/REMOTE_WORKSPACE.md) —— SSH 后端 + tunnel 模式
- [`docs/AGENT_SERVER.md`](docs/AGENT_SERVER.md) —— HTTP 守护进程的协议

---

## 9. 配置与持久化

### 9.1 `~/.localflow/` 目录树

```
~/.localflow/
├── memory/
│   ├── prefs.json        # MemoryPreferences（schema v5）
│   └── audit.jsonl       # 每次变更，append-only
├── runs/
│   └── <task_id>/
│       ├── task.json
│       ├── workspace_snapshot.json
│       ├── plan.json
│       ├── dry_run.md
│       ├── execution_log.jsonl
│       ├── trace.jsonl       # ActionTraceEvent 流
│       ├── rollback_manifest.json
│       ├── verify_report.json
│       ├── final_report.md
│       └── ...
└── scratch/
    └── <task_id>/<action_id>/    # PYTHON_COMPUTE workspace
        ├── inputs/
        ├── outputs/
        ├── script.py
        ├── stdout.log
        └── stderr.log
```

### 9.2 环境变量

| 变量 | 作用 |
|---|---|
| `ANTHROPIC_API_KEY` | 启用 LLM 规划器 / semantic verifier |
| `LOCALFLOW_LLM_MODEL` | 覆盖默认 `claude-opus-4-7` |
| `LOCALFLOW_ANTHROPIC_TIMEOUT` | 单次调用超时（秒，默认 180） |
| `LOCALFLOW_HOME` | 覆盖 `~/.localflow/` 路径 |
| `LOCALFLOW_REQUIRE_SIGNED_SKILLS` | Phase 16 —— 拒绝未签名的外部 skill |
| `AGENT_SERVER_PORT/TOKEN/WORKSPACE/HOST` | 覆盖 agent-server 默认值 |

### 9.3 选 Workspace 后端

CLI：`--workspace local | docker:<image> | ssh:<host>[:<port>][:<root>]`

UI：Settings → 🛰 Workspace backend tab。选择持久化到
`memory.workspace_backend_spec`，侧边栏徽章显示当前后端。

> **Phase 34.5 备注**：v0.32.0 起，UI 保存选好的后端 + 侧边栏显示
> 但 Plan / Execute 页面运行时仍接 `LocalWorkspace`。把这个偏好
> 真正接到 executor 实例化是下一个 deferred 切片（详见
> `docs/PHASE_34_DESIGN.md` §6）。

### 9.4 Forbidden paths & 域名白名单

```bash
# 拒绝触碰某路径
localflow memory forbid private/secrets

# FETCH 动作允许某域名（默认 = 空 = 完全不允许 fetch）
localflow memory allow-domain raw.githubusercontent.com
```

两者都由 kernel 在 policy-check 时读。kernel **只读** 这些，从不
写——这让 memory 成为用户和 LocalFlow 之间"跨 run 持久化的契约"。

---

## 10. 重要注意事项（诚信原则）

LocalFlow 默认值很强，但绝不假装自己是另一种东西。部署前请读完
本节。

### 10.1 隔离 ≠ 安全沙箱

`PYTHON_COMPUTE` 在子进程跑，带 cwd 限定、env scrub、Unix
`RLIMIT_AS`。**控制脚本的攻击者** 照样能读 host 文件、`/etc/passwd`
等。保证是"LLM 写出错脚本不会污染你的 workspace"；不是"你能放心
跑陌生人的代码"。

### 10.2 DockerWorkspace = 容器隔离，不是网络隔离

默认容器有网络访问。`python:3.12-slim` 镜像带 `pip` + libc loader。
要网络隔离的话用 `--network=none` 跑容器（CLI 还没暴露这个 flag
—— 直接走 Python API）。

### 10.3 SSH RemoteWorkspace 必须免密认证

`BatchMode=yes` 强制。如果你的远程要密码，ssh 进程会无声挂起。
**先在远程配好基于密钥的认证 + 接受 host key 到
`~/.ssh/known_hosts`**，再让 LocalFlow 接这个远程。`close()`
不删远程目录——它是用户管理的目录。

### 10.4 FETCH 动作绝不自动跑

`ActionType.FETCH` 存在（Phase 16），但被三道门管：

1. `fetch_allowed_domains` memory pref（默认空 = 完全不允许 fetch）
2. 动作上的 `requires_approval=true`（总是）
3. 只允许 HTTPS（HTTP scheme 被 policy_guard reject）

按 host 显式 opt in：`localflow memory allow-domain <host>`。

### 10.5 LLM 成本

用 `--planner llm` 或 `enable_semantic_verifier` 时，每次 plan /
verify 调用都打 Anthropic API。一次 plan 约 \$0.01-0.05，取决于
workspace 大小 + model。semantic verifier 按 grader 调用（默认每
阶段 7 个 grader）。要硬上限就设
`LOCALFLOW_ANTHROPIC_TIMEOUT`。

### 10.6 不能撤销的事

rollback manifest 覆盖：

- ✓ MKDIR / MOVE / COPY / INDEX / FETCH / PYTHON_COMPUTE
- ✓ OVERWRITE（通过 pre-action backup）
- ✗ **删除**—— 但 kernel 默认通过
  `forbidden_actions=["delete", "overwrite", "shell"]` 拒掉
  DELETE 动作。默认安全路径是"改名到 quarantine 目录，绝不删除"；
  见 agent meta-skill 的模式。

### 10.7 §10.7 kernel-touch ledger

项目追踪每次 kernel 改动。v0.35.0 状态：**44 次交付中 4 次
deliberate exception，37 次零 kernel 触碰（90.2%）**。如果你提
PR 改 `app/harness/*` 或 `localflow_kernel/*`，要按同样的标准
辩护——见 `docs/PHASES.md` 的先例。

---

## 11. 故障排除

### "ANTHROPIC_API_KEY not set" / LLM 步骤被静默跳过

你想用 `--planner llm`、semantic verifier 或 LLM grounding judge 但没有
可解析的 key。解决：shell 里 export key，**或**放进项目 `.env`——v0.34.1
起 CLI 启动时**自动加载 `.env`**（stdlib，`setdefault` 所以已 export 的
真实变量优先；`LOCALFLOW_NO_DOTENV=1` 关闭）。`LOCALFLOW_LLM_PROVIDER`
选 `openai`（默认，读 `OPENAI_API_KEY` + `OPENAI_BASE_URL`）或 `anthropic`
（读 `ANTHROPIC_API_KEY`）。或直接换 `--planner rule`（不用 LLM 也能跑）。
UI Plan 页没检测到 key 时自动降到 rule；grounding 闸门降到确定性 lexical judge。

> ⚠️ 之前的坑：LocalFlow 一度**不**自动加载 `.env`，所以即使你把 key 写进
> `.env`，不先 `source` 的话 LLM 步骤会静默降级（你以为调了大模型其实没有）。
> v0.34.1 修复。

### "ssh probe to '<host>' failed: Permission denied (publickey)"

远程没配你这个用户的免密认证。`ssh-copy-id user@host` 后用
`ssh -o BatchMode=yes user@host true` 验证（必须 exit 0）。

### "Docker CLI / daemon not reachable"

要么 Docker 没装（装 Docker Desktop 或 Docker Engine），要么
daemon 在 Windows containers 模式（LocalFlow 用 Linux 镜像；
Docker 托盘图标 → Switch to Linux containers）。

### Trace 显示 `policy.check` 行但没 `action.start`

policy_guard 拒了 plan。查 trace：`localflow trace show <task_id>
--event-type policy.check`。`payload.detail` 字段写了原因
（`path_forbidden` / `fetch_domain_not_allowed` 等）。

### Rollback 报 "drift detected on <file>"

有人（或别的进程）在 LocalFlow 记录哈希后改了文件。两种处理：

1. 接受漂移：加 `--force` 重跑（这条 manifest entry 跳过，但
   rollback 继续）。
2. 手动恢复：原文件在 `.localflow/runs/<task_id>/backups/`。

### UI Plan 页按钮转圈不停

很可能你有旧的 `prefs.json` 配了 LLM planner 偏好。试
`localflow memory set prefer_llm_planner false`，刷新。Phase 34
的降级现在应该显示蓝色信息框（没 key 时）。

### "DockerWorkspace agent-server start failed: ..."

bundle 握手没完成（镜像没 `python3`、端口冲突、镜像没装
`pydantic`）。LocalFlow 会 log warning + 自动 fall back 到
`docker exec` per op。要享受 agent-server 加速，用
`python:3.12-slim`（或任何 `pip install pydantic` 过的镜像）。

---

## 12. 项目状态

### Phase ledger（当前）

| 阶段 | Phase 范围 | 亮点 |
|---|---|---|
| **奠基** | 1–8 | 核心 schema、harness kernel、CLI、UI、skills |
| **Trace + Eval** | 9–13 | TraceEvent、TaskGraph、plan refinement、semantic verifier |
| **组合** | 14–22 | Workspace Pack Builder、MCP、Recipes / Packs、goal interpreter |
| **沙盒 + Trace v2** | 23–25 | PYTHON_COMPUTE、ActionTraceEvent + repair feedback |
| **Loop + Approval** | 26–27 | React loop、ConfirmationPolicy 4 档 |
| **Backends** | 28–33 | Workspace 抽象 → Local / Docker / Remote / AgentServer |
| **Distribution + UI parity** | 30, 34 | `localflow_kernel` 包 + 边界 lint、UI 追上 CLI |

完整 phase changelog 在 [`docs/PHASES.md`](docs/PHASES.md)。

### 测试与质量门禁

- **1062 个测试通过**（CI 跨 macOS / Linux / Windows × Python
  3.11 / 3.12 / 3.13）。
- Pre-push hook 镜像 CI：`ruff check` + `ruff format --check` +
  `pytest --tb=no`。新 clone 时一次性激活：`git config
  core.hooksPath .githooks`。
- Kernel 边界 lint（`tests/test_kernel_boundary.py`）：遍历从
  `localflow_kernel.*` 可达的每个模块 + 它们底层的每个 `app.*`
  实现，断言任意一个都没 import `app.{skills,recipes,cli,ui,eval,
  memory,primitives,templates,mcp}` 或 5 个被禁的 harness
  orchestrator。

---

## 13. 文档地图

### 战略 / 方向

- [`docs/PROJECT_DIRECTION.md`](docs/PROJECT_DIRECTION.md) —— harness-first 项目方向，锁定的 Route B 决策
- [`docs/PHASES.md`](docs/PHASES.md) —— 完整 phase changelog + §10.7 ledger（4 个 deliberate kernel exception / 44 次交付 / 40 次零 kernel 触碰）
- [`docs/research/OPENHANDS_HARNESS_STUDY.md`](docs/research/OPENHANDS_HARNESS_STUDY.md) —— 推动 v0.24+ 的 26 KB 一手调研

### 每阶段设计 / 用户向

- [`docs/PHASE_23_PLAN.md`](docs/PHASE_23_PLAN.md) · [`docs/COMPUTE_ACTION.md`](docs/COMPUTE_ACTION.md) —— Sandboxed ComputeAction（隔离，不是安全沙箱）
- [`docs/PHASE_25_PLAN.md`](docs/PHASE_25_PLAN.md) —— ActionTraceEvent 重构
- [`docs/PHASE_26_DESIGN.md`](docs/PHASE_26_DESIGN.md) · [`docs/REACT_LOOP.md`](docs/REACT_LOOP.md) —— react loop
- [`docs/PHASE_27_DESIGN.md`](docs/PHASE_27_DESIGN.md) · [`docs/CONFIRMATION_POLICY.md`](docs/CONFIRMATION_POLICY.md) —— ConfirmationPolicy
- [`docs/PHASE_28_DESIGN.md`](docs/PHASE_28_DESIGN.md) · [`docs/WORKSPACE.md`](docs/WORKSPACE.md) —— Workspace 抽象
- [`docs/DOCKER_WORKSPACE.md`](docs/DOCKER_WORKSPACE.md) —— Phase 29 + Phase 33 agent-server 模式
- [`docs/PHASE_30_DESIGN.md`](docs/PHASE_30_DESIGN.md) · [`docs/KERNEL_PACKAGE.md`](docs/KERNEL_PACKAGE.md) —— `localflow_kernel` 包
- [`docs/PHASE_31_DESIGN.md`](docs/PHASE_31_DESIGN.md) · [`docs/REMOTE_WORKSPACE.md`](docs/REMOTE_WORKSPACE.md) —— RemoteWorkspace（SSH）+ agent-server 模式
- [`docs/PHASE_32_DESIGN.md`](docs/PHASE_32_DESIGN.md) · [`docs/AGENT_SERVER.md`](docs/AGENT_SERVER.md) —— HTTP agent-server
- [`docs/PHASE_33_DESIGN.md`](docs/PHASE_33_DESIGN.md) —— Docker/Remote agent-server 集成
- [`docs/PHASE_34_DESIGN.md`](docs/PHASE_34_DESIGN.md) · [`docs/E2E_TEST_PLAN.md`](docs/E2E_TEST_PLAN.md) —— UI 对齐 + E2E 测试报告

### 架构 / 扩展

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —— 5 层分解 + 8 条铁律 + 扩展指南
- [`docs/RECIPES.md`](docs/RECIPES.md) —— 写一个 recipe / pack
- [`docs/PACK_BUILDER.md`](docs/PACK_BUILDER.md) —— pack 生命周期（5 阶段端到端）
- [`docs/TASKGRAPH.md`](docs/TASKGRAPH.md) —— 用 YAML 手动驱动多阶段 graph
- [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) —— goal interpreter + 类型化 primitive
- [`docs/MCP.md`](docs/MCP.md) —— LocalFlow 作为 MCP 服务器

### 运维

- [`docs/UI.md`](docs/UI.md) · [`docs/UI_zh.md`](docs/UI_zh.md) —— Streamlit UI 详解
- [`docs/SECURITY.md`](docs/SECURITY.md) —— 安全模型（隔离，不是安全沙箱）
- [`docs/EVAL.md`](docs/EVAL.md) —— eval task 撰写
- [`docs/SEMANTIC_VERIFIER.md`](docs/SEMANTIC_VERIFIER.md) —— Phase 13 LLM-as-judge grader
- [`docs/REFINE.md`](docs/REFINE.md) —— plan refinement loop
- [`docs/demo_walkthrough.md`](docs/demo_walkthrough.md) —— 端到端 demo 脚本

---

## 14. 开发与贡献

### 14.1 本地准备

```bash
git clone https://github.com/zhangyi-nb1/localflow.git
cd localflow
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
git config core.hooksPath .githooks   # 启用 pre-push hook
```

### 14.2 测试约定

- 单元测试在 `tests/test_*.py`，集成测试共享 `tests/` 命名空间
- `pytest --tb=no` 跑全套约 70 秒
- 后端相关测试用 `_skip_no_docker` / `_skip_no_ssh` 标记；CI 矩阵
  覆盖
- 新测试紧挨它覆盖的模块（例如 `tests/test_workspace_remote.py`
  挨着 `app/tools/remote_workspace.py`）

### 14.3 代码风格

- `ruff check app/ tests/ localflow_kernel/`
- `ruff format --check app/ tests/ localflow_kernel/ examples/`
- 两者都在 pre-push hook + CI step 5/6/7

### 14.4 Kernel 边界纪律

Kernel 包圈起来了——`tests/test_kernel_boundary.py` 在 CI 里检
你有没有从 `localflow_kernel.*` 或底层 `app.harness.*` 纯模块
import 应用层代码。如果你**真的**需要碰 kernel（新 ActionType、
新 policy 字段），§10.7 ledger 期待你：

1. 开 issue 说清楚 case
2. 写设计文档到 `docs/PHASE_*_DESIGN.md`
3. 在 `docs/PHASES.md` 写 ledger 行，把这次改动登记为
   deliberate exception

至今接受了 4 个 deliberate exception：

| Phase | 例外 | 理由 |
|---|---|---|
| 5 | `forbidden_paths` | 普世安全 primitive—— kernel 必须强制 |
| 16 | `ActionType.FETCH` | WebCollect 需要 HTTPS GET 作为 typed primitive |
| 23 | `ActionType.PYTHON_COMPUTE` | LLM 写的代码需要 sandboxed exec primitive |
| 26 | react loop kwarg 串联 | 执行中 LLM 决策需要 executor 钩子 |

比例（4/41）就是项目身份契约。

### 14.5 Pull request

- 从 `main` 切分支，push，开 PR。
- Pre-push hook 必须本地过（CI 的镜像）。
- 涉及 kernel 改动的 PR，ledger 行 + 设计文档是必须的。
- PR 描述里 @ 维护者做 §10.7 审查。

---

## 15. License

MIT。详见 [`LICENSE`](LICENSE)。

---

> 用心打造，前提是：**用户——不是模型——永远是意图的来源**。如果
> 你发现 harness 过度信任了模型，请提 issue。诚信原则（CLAUDE.md
> 规则 F）是这个项目值得 ship 的原因。
