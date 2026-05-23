# OpenHands Harness 调研报告

**调研对象**：`All-Hands-AI/agent-sdk@main`（上游已把 harness 内核从 `All-Hands-AI/OpenHands` 主仓拆到独立 SDK 仓 `agent-sdk`，主仓现在只剩 app server / 集成层）
**调研日期**：2026-05-24
**用途**：指导 LocalFlow Phase 24+ 的路线决策与代码改造

---

## A. 6 个维度的源码证据

### A1. Action / Observation 模型

**实现方式**：每个工具自带一对类型化 `Action` + `Observation` Pydantic 类（不是中央枚举），通过
`ToolDefinition[ActionT, ObservationT]` 泛型绑定；Agent 与 EventStore 看到的是 `ActionEvent`
（事件包裹器），不是 Action 本身。

- `openhands-sdk/openhands/sdk/tool/tool.py:197-487`（ToolDefinition 基类）
- `openhands-sdk/openhands/sdk/tool/tool.py:133-178`（ToolExecutor[ActionT, ObservationT]）
- `openhands-sdk/openhands/sdk/event/llm_convertible/action.py:24-90`（ActionEvent）
- `openhands-sdk/openhands/sdk/tool/builtins/__init__.py:37`（内建仅 FinishTool, ThinkTool 两个）
- `openhands-tools/openhands/tools/terminal/definition.py:37-65`（TerminalAction）

```python
# tool.py
133  class ToolExecutor[ActionT, ObservationT](ABC):
137      def __call__(self, action: ActionT, conversation=None) -> ObservationT: ...
167      def interrupt(self) -> None: ...   # 关键：每个工具可被中断
197  class ToolDefinition[ActionT, ObservationT](DiscriminatedUnionMixin, ABC): ...

# event/llm_convertible/action.py
24   class ActionEvent(LLMConvertibleEvent):
40       action: Action | None = Field(default=None, ...)
44       tool_name: str
48       tool_call: MessageToolCall
67       security_risk: risk.SecurityRisk = Field(default=risk.SecurityRisk.UNKNOWN)
```

**Action 数量**：内建只有 2 个（finish / think）。真负载工具：terminal / file_editor /
apply_patch / task / task_tracker / browser_use / delegate（七个子目录）。所有非内建工具单独
打包发布，按需注册。**少而通用，重负载丢给 terminal 这类"会话式 bash"**。

**设计意图**：让 Action 类型边界与 LLM 看到的 tool schema 边界一致；ActionEvent 把
"LLM 想做什么 + 安全评估 + 思考过程" 揉成一条事件。

---

### A2. EventStream / 事件流

**实现方式**：`EventLog`（继承自 `EventsListBase`），每条事件存为单独 JSON 文件
`event-{idx:05d}-{event_id}.json`，用 flock 串行化写，进程间安全。会话状态 =
`base_state.json`（聚合元数据） + `events/` 目录下顺序事件文件。

- `openhands-sdk/openhands/sdk/conversation/event_store.py:25-204`（EventLog）
- `openhands-sdk/openhands/sdk/conversation/persistence_const.py:1-9`（文件名格式）

```python
# event_store.py
119  def append(self, event: Event) -> None:
129      with self._fs.lock(self._lock_path, timeout=LOCK_TIMEOUT_SECONDS):
131          disk_length = self._count_events_on_disk()
132          if disk_length > self._length:
133              self._sync_from_disk(disk_length)       # 进程间同步
142          payload = event.model_dump_json(exclude_none=True)
147          target_path = self._path(self._length, event_id=evt_id)
148          self._fs.write(target_path, payload)
```

**FileStore 抽象**：`openhands/sdk/io` 提供 `InMemoryFileStore`、`LocalFileStore`，企业版另有
S3/GCS 实现 — 同一 EventLog 接口可挂不同后端。

**设计意图**：每个 event 一个文件 = 无需读整段历史就能 append；flock + disk-sync 让两个
进程（CLI + WebSocket）能共写同一 conversation；index 重扫从磁盘恢复 = 进程崩溃也能复原。

---

### A3. AgentController / 控制循环

**实现方式**：没有独立 Controller — `LocalConversation.run()` / `arun()` 就是主循环。状态机
基于 `ConversationExecutionStatus` 枚举（IDLE / RUNNING / PAUSED / WAITING_FOR_CONFIRMATION /
FINISHED / ERROR / STUCK / DELETING）。一次 iteration = `agent.step()` 一次，状态在每轮开头
集中判定。

- `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py:918-1069`（arun 主循环）
- `openhands-sdk/openhands/sdk/conversation/state.py:46-77`（ConversationExecutionStatus）
- `openhands-sdk/openhands/sdk/agent/agent.py:840-861`（confirmation 拦截点）

```python
# local_conversation.py — arun() 主循环骨架
951      while True:
953          with self._state:
954              if status in [PAUSED, STUCK]: break
960              if status == FINISHED:
964                  if self._hook_processor: ...        # stop hook 可拒绝结束
984                  break
986              if self._stuck_detector and self._stuck_detector.is_stuck():
990                  self._state.execution_status = STUCK; continue
995              if status == WAITING_FOR_CONFIRMATION:
999                  self._state.execution_status = RUNNING
1003         await self.agent.astep(self, on_event=..., on_token=...)
1008         iteration += 1
1016         if iteration >= self.max_iteration_per_run:
1027             self._state.execution_status = ERROR
1036  except asyncio.CancelledError:
1050      self._emit_orphaned_action_errors()           # 关键：给孤儿 ActionEvent 补 synth observation
1052      self._state.execution_status = PAUSED
```

**失败/超时/中断**：
- 超时：`max_iteration_per_run` 硬上限 → 状态 ERROR + ConversationErrorEvent
- 中断：`asyncio.Task.cancel()` → 捕获 CancelledError → `_emit_orphaned_action_errors()`
  给挂起的 action 补 synthetic error observation（否则下次 LLM call 会因 tool_call 无
  tool_result 而报错）→ 转 PAUSED 可恢复
- Stuck：`StuckDetector` 检测循环模式 → 状态 STUCK，循环退出

**设计意图**：状态机驱动 + 每轮始终先看状态再 step = 任意时刻可暂停/恢复/拒绝/中断而不
破坏 LLM history 一致性。

---

### A4. Runtime / Sandbox

**实现方式**：策略层是 `BaseWorkspace`（抽象的 execute_command / file_upload / file_download
/ git_diff）。实现有三档：`LocalWorkspace`（直接 host 文件系统，**没有隔离**）、
`DockerWorkspace`（启容器跑 `ghcr.io/openhands/agent-server` 镜像，HTTP API 远控）、
`ApptainerWorkspace` / `CloudWorkspace`。Action 不直接读写磁盘 — 走
`workspace.execute_command()`。

- `openhands-sdk/openhands/sdk/workspace/base.py`（BaseWorkspace）
- `openhands-sdk/openhands/sdk/workspace/local.py:17-69`（LocalWorkspace）
- `openhands-workspace/openhands/workspace/docker/workspace.py:54-100`（DockerWorkspace）

```python
# workspace/local.py
17   class LocalWorkspace(BaseWorkspace):
36       def execute_command(self, command, cwd=None, timeout=30.0) -> CommandResult:
56           result = execute_command(command, cwd=..., timeout=timeout, ...)

# workspace_docker.py
54   class DockerWorkspace(RemoteWorkspace):
72       working_dir: str = Field(default="/workspace")
82       server_image: str = Field(default="ghcr.io/openhands/agent-server:latest-python")
94       mount_dir: str | None = Field(default=None)
```

**隔离性**：
- LocalWorkspace = **无隔离**（文档明说 "suitable for development and testing"）
- DockerWorkspace = 容器隔离 + 端口绑定 + 选择性 mount_dir（生产模式）
- 网络：未见 default-deny；容器内默认通网；只能靠 docker run 参数控
- 文件系统：working_dir 默认 `/workspace`，通过 `volumes` / `mount_dir` 显式暴露

**设计意图**：单一 Workspace 接口让 Agent 代码不感知本地/Docker/远程；真正的安全 boundary
推给容器。

---

### A5. 安全 / 审批模型

**实现方式**：三层串联——
1. **LLM 自评 SecurityRisk**（注入 tool schema 让模型在 tool_call 时附 `security_risk` 字段）
2. **可选 SecurityAnalyzer**（覆盖/校准 LLM 自评）
3. **ConfirmationPolicy**（基于 risk 决定是否需要 user confirm）

没有独立 dry-run；预览靠 LLM 在 `summary` / `thought` 里描述。

- `openhands-sdk/openhands/sdk/security/risk.py:13-23`（SecurityRisk 枚举）
- `openhands-sdk/openhands/sdk/security/confirmation_policy.py:1-61`（策略三件套）
- `openhands-sdk/openhands/sdk/agent/agent.py:840-861`（拦截点）
- `openhands-sdk/openhands/sdk/security/llm_analyzer.py`（LLM-as-analyzer）

```python
# confirmation_policy.py
11   class AlwaysConfirm(ConfirmationPolicyBase):
12       def should_confirm(self, risk=SecurityRisk.UNKNOWN) -> bool: return True
15   class NeverConfirm(ConfirmationPolicyBase):
16       def should_confirm(self, risk=...) -> bool: return False
19   class ConfirmRisky(ConfirmationPolicyBase):
20       threshold: SecurityRisk = SecurityRisk.HIGH
21       confirm_unknown: bool = True
29       def should_confirm(self, risk=SecurityRisk.UNKNOWN) -> bool:
30           if risk == SecurityRisk.UNKNOWN: return self.confirm_unknown
32           return risk.is_riskier(self.threshold)

# agent.py — 拦截点
844      if state.security_analyzer is not None:
845          risks = [risk for _, risk in
                       state.security_analyzer.analyze_pending_actions(action_events)]
852      else:
                risks = [risk.SecurityRisk.UNKNOWN] * len(action_events)
855      if any(state.confirmation_policy.should_confirm(risk) for risk in risks):
856          state.execution_status = WAITING_FOR_CONFIRMATION
859          return True
```

**怎么阻止**：拦截在 _execute 之前；拒绝走 `reject_pending_actions()` → 给 ActionEvent 补
`UserRejectObservation` → LLM 在下一轮看到拒绝原因。

**设计意图**：把"风险评估"和"批准策略"解耦——一个判定 risk、一个决定该 risk 要不要 confirm；
用户可选 Never / Always / ConfirmRisky 三档。

---

### A6. 持久化 / 回滚 / 状态恢复

**实现方式**：会话状态完全可重建 —— `base_state.json`（agent 配置、workspace、policy、stats）
\+ `events/event-NNNNN-<uuid>.json`（顺序事件）。`ConversationState.create()` 是 open-or-create
工厂：磁盘上有就 resume，没有就新建。**没有 rollback / undo 概念** —— 内核哲学是"事件流是
事实，不能撤销，只能补偿"。

- `openhands-sdk/openhands/sdk/conversation/state.py:80-340`（ConversationState + create 工厂）
- `openhands-sdk/openhands/sdk/conversation/event_store.py:175-194`（_sync_from_disk）

```python
# state.py
276      @classmethod
277      def create(cls, id, agent, workspace, persistence_dir=None, ..., max_iterations=500):
288          """Create a new conversation state or resume from persistence.
295          **Restored conversation:**
298          The provided Agent is validated against the persisted agent using agent.load().
299          Tools must match (...), but all other configuration can be freely changed.
```

**注意**：没有 RollbackManifest / Backup-Restore；如果一个 bash 命令把文件删了，Agent 不会
"撤销"——它读取下次 `ls` 看到文件没了，再决定下一步。唯一接近 rollback 的语义是
**UserRejectObservation**：拒绝 action 后给 LLM 一条 observation 说 "user rejected"。

**设计意图**：Agent 操作的是"开发分支 + git"模型——用户管 commit 和 reset。Harness 只管
不让 agent 跑飞、不管能不能"取消已做的事"。

---

## B. LocalFlow vs OpenHands 对比表

| 维度 | LocalFlow（当前） | OpenHands（agent-sdk） |
|---|---|---|
| **Action 模型** | 中央 `ActionType` 枚举（10 种：MKDIR/COPY/MOVE/RENAME/INDEX/SUMMARIZE/CONVERT/ANALYZE/FETCH/PYTHON_COMPUTE），统一 `Action` Pydantic 类 + `metadata` dict 携带类型特定字段 | 每 Tool 自带 `Action`/`Observation` 子类（强类型）；中央只有 `ActionEvent` 包裹。内建仅 finish/think，重负载工具独立分发 |
| **Schema 形式** | Pydantic + Enum（封闭枚举） | Pydantic + DiscriminatedUnionMixin（开放注册） |
| **事件流** | `trace.jsonl`（单文件追加） + `execution_log.jsonl`（kernel 内部）+ `audit.jsonl`（用户操作）共三条流 | 单一 EventLog，每事件独立文件 `event-NNNNN-{id}.json` + flock 进程间同步 |
| **持久化范围** | trace + 执行日志 + plan.json + actions.json + rollback_manifest.json + workspace snapshot | `base_state.json` + `events/`；FileStore 抽象层支持本地/S3/GCS |
| **控制循环** | **阶段式**（plan → risk_check → dry_run → approve → execute → verify），不是 LLM-loop | **LLM-loop**（while True: agent.step()），状态机驱动；停止靠 FinishAction 或 max_iterations |
| **失败/中断处理** | 阶段级 `failure_policy: ABORT/CONTINUE/SKIP/REPAIR`；无中断恢复机制 | 状态机 PAUSED/STUCK/ERROR + `_emit_orphaned_action_errors` 补孤儿 tool_call；`asyncio.cancel()` 可在 LLM 调用中途打断 |
| **Sandbox** | `SandboxRuntime` 仅用于 PYTHON_COMPUTE：subprocess + cwd 限制 + timeout + env scrub + Unix `RLIMIT_AS`；网络隔离是 best-effort | Workspace 抽象：Local（无隔离）/ Docker（容器+端口）/ Apptainer / Cloud；agent-server 容器镜像跑在隔离环境内 |
| **审批模型** | 一次性整批审批：plan 级而非 action 级 | 每 step 后按 risk + ConfirmationPolicy 决定要不要等用户。三档策略，runtime 可中途切换 |
| **Dry-run** | **一等公民**：`dry_run.simulate_action()` 模拟 path 冲突/覆盖/PYTHON_COMPUTE 脚本预览；产出 `dry_run.md` 给用户读 | **无独立 dry-run**；预览靠 LLM 自填 `ActionEvent.summary` + `thought` |
| **Risk 评估** | 规则化 `policy_guard.assess_plan()`（路径越界 / forbidden_paths / 域名白名单）— 程序化 | LLM 自评 `security_risk` 字段 + 可选 SecurityAnalyzer 校准 — 模型化 |
| **Rollback** | **`RollbackManifest` 一等公民**：MOVE_BACK / DELETE_CREATED_FILE / RESTORE_FROM_BACKUP / DELETE_SCRATCH_DIR 四种 op；**hash-drift 检测**避免覆盖用户编辑 | **无 rollback 概念**；UserRejectObservation 是唯一接近的语义 |
| **状态恢复** | `Executor(resume=True)`：靠 `completed_action_ids()` 跳过已完成的 action；阶段级 replay | `ConversationState.create()` open-or-create 工厂；conversation_fork 支持分叉 |
| **Verifier** | 独立组件：结构化 + 语义两层；REPAIR 策略可触发自动 replan | 没有独立 verifier；Critic 可对 ActionEvent 评分但不阻塞 |
| **工具/Skill 注册** | `SkillRegistry`：Skill 是 **plan 生成器**，不是 executor | `register_tool()` + `BUILT_IN_TOOL_CLASSES`；Tool 自带 ActionType/ObservationType/Executor 三件套 |
| **并发** | 单线程顺序执行 actions | `ParallelToolExecutor` 支持并行 tool calls，`DeclaredResources` 声明资源避免竞态 |

---

## C. LocalFlow 可借鉴的 5 个具体改进点

### C1. ActionEvent 模式 — 让 Trace 和 LLM History 共享同一份对象 【高】

**痛点**：LocalFlow 现在有三条流（`trace.jsonl` / `execution_log.jsonl` / `audit.jsonl`），同一个
action 在三处各记一份；从一条流复原"LLM 当时想做什么"很难，因为
thought / tool_call / risk_level / hash_before / hash_after 散在不同流里。LLM 的
reasoning_content 完全没存。

**OpenHands 做法**：`ActionEvent`（`event/llm_convertible/action.py:24-90`）单一对象同时承载
thought、tool_call、action（已校验）、security_risk、reasoning_content、thinking_blocks、
critic_result、summary。一条事件就够 LLM history reconstruct + UI 可视化 + 审计 + grader
评估。`LLMConvertibleEvent.to_llm_message()` 反序列化回 LLM 输入消息。

**LocalFlow 怎么改**：
- `app/schemas/trace.py` 引入 `ActionTraceEvent`（继承 TraceEvent）字段加
  `thought / reasoning / tool_call_raw / critic_result`
- `executor._run_one`（`harness/executor.py:175-265`）只 emit 一条富 ActionTraceEvent，废止
  当前 ACTION_START + ACTION_END 双事件 + 散落 payload
- 单 trace.jsonl 就能驱动 UI、graders、回放

**优先级**：**高**。这是后续"sub-agent / 多 agent 协作"功能的基础——OpenHands 拆出 sub-agent
之所以可行，正因为 ActionEvent 是 self-contained 单元。

---

### C2. ConfirmationPolicy 多档策略 — 替代单点 `auto_approve` 【中】

**痛点**：`harness/approval.py:15-39` 只支持 0/1：要么交互问一次、要么 `auto_approve=True`
全过；plan 级审批粒度过粗，一旦 plan 含 20 个 action 就只能整批 yes/no，没法"高风险逐个
confirm"。

**OpenHands 做法**：`security/confirmation_policy.py:1-61` 三个 Pydantic 类 —
`AlwaysConfirm` / `NeverConfirm` / `ConfirmRisky(threshold=HIGH, confirm_unknown=True)`。运行时
按 action 风险逐个决定；`set_confirmation_policy()` 可热切换。

**LocalFlow 怎么改**：
- `app/schemas/` 加 `ConfirmationPolicy` Pydantic 联合类型（threshold + 默认行为）
- `harness/approval.py` 重写为按 action 迭代询问
- `TaskSpec` 加 `confirmation_policy` 字段（默认 `ConfirmRisky(threshold=HIGH)`）

**优先级**：**中**。LocalFlow 阶段式架构本就强调一次审批 plan，但对 REPAIR / 多阶段长 plan
用户已经报过痛——OpenHands 模型可直接迁移。

---

### C3. Workspace 抽象 — 把 SandboxRuntime 从 PYTHON_COMPUTE 专属升级为通用 Runtime 【中】

**痛点**：`harness/sandbox.py` 现在只为 `PYTHON_COMPUTE` 服务；MOVE/COPY/INDEX/FETCH 全直接
hit 真实文件系统，没有"换装 Docker 跑全套"的能力。一旦想给某个用户跑不被信任的 plan，没有
逃生通道。

**OpenHands 做法**：`workspace/base.py` 抽象 `BaseWorkspace.execute_command / file_upload /
git_diff`；`LocalWorkspace` 直跑本机、`DockerWorkspace` 跑容器、`RemoteWorkspace` 跑远端。
同一 Agent 代码不改一行就能换运行环境。

**LocalFlow 怎么改**：
- 把 `app/tools/file_ops.py` 的写函数抽到 `Workspace` 接口
- `Executor.__init__` 接收 `workspace: Workspace`（默认 `LocalWorkspace`，Phase 23 已有
  `ScratchWorkspace`）
- 后续可加 `DockerWorkspace` 跑陌生 plan

**优先级**：**中**。当前 Phase 23 的 ScratchWorkspace 已经是这个方向的开端 — 再走半步就拿到
OpenHands 同等的可换装能力。

---

### C4. Orphaned-Action 修复 — 给中断/失败补合规 Observation 【高】

**痛点**：LocalFlow 的 `executor._run_one`（`harness/executor.py:202-230`）action 异常时只记
FAILED record，不给 LLM 反馈；若有 REPAIR 阶段，LLM 看不到"上一步为什么失败"的格式化原因，
只能从 verifier failed_checks 里猜。

**OpenHands 做法**：`local_conversation._emit_orphaned_action_errors()`（line 1108-1136）：任何
ActionEvent 没有匹配 ObservationEvent 时，自动补 `AgentErrorEvent` 说"tool call interrupted"。
`reject_pending_actions()`（line 1077-1106）给拒绝的 action 补 `UserRejectObservation`。这样
LLM history 永远是 "tool_call ↔ tool_result" 配对，下一轮 LLM 看到清楚的失败原因。

**LocalFlow 怎么改**：
- 在 `harness/repair_loop.py` 注入一层：action FAILED 时，构造结构化失败说明
  `{action_id, action_type, target_path, error, retry_hint}` 写进 trace + 传给
  `skill.plan_with_llm` 作为 prior_failures 上下文
- `taskgraph_runner._load_prior_actions_unprefixed` 已经是这个方向的雏形

**优先级**：**高**。LocalFlow 已经有 REPAIR 策略和 semantic verifier，缺的就是把失败"以 LLM
能消化的格式"喂回去 — 这一步几乎免费拿到 OpenHands 同级的自修复质量。

---

### C5. EventStore 锁 + 单文件-per-event — 让 Trace 进程间安全 & 可增量恢复 【低】

**痛点**：LocalFlow `harness/trace.py:43-78` 的 `JsonlLogger` 是单文件追加；两个进程同写同一
trace.jsonl 会撕裂（CLI + 未来的 MCP server 并发场景）。崩溃后没法精确知道写到了第几条 —
只能整文件 parse 直到第一行 invalid JSON。

**OpenHands 做法**：`event_store.py:119-157` `EventLog.append()` 用 flock + 写之前
`_sync_from_disk` 同步本地索引；每事件单独文件 `event-NNNNN-{uuid}.json`，崩溃恢复扫目录就能
重建（`_scan_and_build_index`）。

**LocalFlow 怎么改**：保留 `trace.jsonl` 单文件视图（grep 友好），但在 `app/storage/` 加
`EventStore`：内部仍用 jsonl，append 时走 fcntl.flock；index 维护在 sidecar 文件 `trace.index`
（offset + event_id）。或者直接迁移到 OpenHands 风格的 `events/event-NNNNN.json` 目录布局。

**优先级**：**低**。当前单进程 CLI 不痛；等 Phase 24+ 上 MCP server 长跑模式才需要。

---

## D. LocalFlow 应保留的差异化（不要丢）

OpenHands 没有但 LocalFlow 有的设计：

1. **独立 Dry-run 阶段 + `dry_run.md` 产物**（`harness/dry_run.py:11-53`）
   OpenHands 把预览责任完全推给 LLM 自填 `summary` / `thought`；LocalFlow 用确定性
   `simulate_action` 算"这一步会写到哪、会不会冲突、会不会覆盖"，对**非工程师用户**这是
   不可替代的可读性。LLM 写的 summary 会幻觉，simulator 不会。

2. **RollbackManifest + hash-drift 检测**（`harness/rollback.py:131-298`）
   OpenHands 把 "撤销" 完全外包给 git/user。LocalFlow 的用户场景是"管理我的 Downloads /
   论文 / 笔记"，多数文件不在 git 里；MOVE_BACK + RESTORE_FROM_BACKUP + 用 sha256 检测用户
   事后编辑 = **必须保留**。

3. **§10.7 ledger 纪律 / 内核加法不修改**
   OpenHands 每个 PR 都在加 Tool / 加 Hook / 改 ConfirmationPolicy，他们演化快但每个版本
   兼容性都靠测试堆出来。LocalFlow 把所有"违反 8 种 action 闭包"的扩展（FETCH、
   PYTHON_COMPUTE）当作有编号的 deliberate exception 在 ledger 里登记 = 长期可维护性比
   OpenHands 的开放注册更强。

4. **规则化 PolicyGuard + 路径越界静态检查**（`harness/policy_guard.py:21-66`，`resolve_inside`）
   OpenHands 用 LLM-as-analyzer 做风险评估（可被 prompt injection 误导）。LocalFlow 的
   `resolve_inside` 是程序化的：路径不在 workspace_root 内、含 `..`、绝对路径 = 直接拒绝，
   无论 LLM 怎么解释自己的意图。这对面向终端用户的产品语境是**正确取舍**。

5. **独立 Verifier（结构化 + 语义两层）**（`harness/verifier.py`, `semantic_verifier.py`）
   OpenHands 只有 Critic（评分但不阻塞）。LocalFlow 的 Verifier 真的能让一个 plan "执行了
   但没通过验证" → 触发 REPAIR → re-plan with hint。这是**任务式 agent vs 对话式 agent**
   的根本区别，要守住。

6. **每个 Skill 是 plan 生成器而非 executor**（`app/skills/_base.py:41-172`）
   OpenHands Tool 把 plan + execute 揉在一起（LLM 直接发 tool_call）。LocalFlow 让 Skill 先
   产 ActionPlan，policy_guard / dry_run / approval 拦在中间。这个**计划-审批-执行三段式**
   是 LocalFlow 面向"我不懂代码的用户"价值的核心。

---

## E. 关键源码文件清单

### OpenHands（in `agent-sdk@main`）

```
openhands-sdk/openhands/sdk/tool/tool.py                              ToolDefinition / ToolExecutor
openhands-sdk/openhands/sdk/event/base.py                             Event / LLMConvertibleEvent
openhands-sdk/openhands/sdk/event/llm_convertible/action.py           ActionEvent
openhands-sdk/openhands/sdk/conversation/event_store.py               EventLog
openhands-sdk/openhands/sdk/conversation/state.py                     ConversationState + ExecutionStatus
openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py   run/arun 控制循环
openhands-sdk/openhands/sdk/security/risk.py                          SecurityRisk
openhands-sdk/openhands/sdk/security/confirmation_policy.py           Always/Never/ConfirmRisky
openhands-sdk/openhands/sdk/security/llm_analyzer.py                  LLM-based analyzer
openhands-sdk/openhands/sdk/security/defense_in_depth/policy_rails.py 硬规则护栏
openhands-sdk/openhands/sdk/agent/agent.py                            step/astep + 拦截 confirm
openhands-sdk/openhands/sdk/workspace/base.py                         BaseWorkspace
openhands-sdk/openhands/sdk/workspace/local.py                        LocalWorkspace
openhands-workspace/openhands/workspace/docker/workspace.py           DockerWorkspace
openhands-tools/openhands/tools/terminal/definition.py                TerminalTool (重负载工具)
openhands-tools/openhands/tools/file_editor/definition.py             FileEditorTool
openhands-sdk/openhands/sdk/tool/builtins/finish.py                   FinishTool (内建)
```

### LocalFlow（对应文件）

```
app/schemas/action.py          ActionType 枚举 + Action 类
app/schemas/compute.py         ComputeAction (Phase 23)
app/schemas/trace.py           TraceEvent
app/harness/executor.py        阶段式 executor
app/harness/taskgraph_runner.py 多阶段编排
app/harness/sandbox.py         SandboxRuntime (Phase 23)
app/harness/approval.py        ask_approval (一次性)
app/harness/policy_guard.py    resolve_inside + 域名白名单
app/harness/dry_run.py         simulate_action + dry_run.md
app/harness/rollback.py        RollbackManifest + hash-drift
app/harness/trace.py           JsonlLogger
app/harness/verifier.py        结构化 verifier
app/harness/semantic_verifier.py LLM-as-judge verifier
app/skills/_base.py            SkillRegistry
```

---

## F. 调研结论（项目层面）

1. **OpenHands 已经把 harness 内核拆成独立 SDK 仓** = harness 应当是独立产品的强信号。
   LocalFlow 当前所有 skill / pack / UI 与 harness 内核混在 `app/` 下 — Phase 25+ 应考虑
   `localflow-harness` (kernel-only PyPI 包) + `localflow-pack` (应用层) 拆分。

2. **OpenHands 是 LLM-loop，LocalFlow 是阶段式** —— 这是**根本架构差异**，不是细节差异。
   用户的"卡感"很可能源自此：LocalFlow 是 plan-once-execute-batch，复杂任务 plan 不够细
   就卡住。

3. **三大借鉴重点**：
   - **C1 ActionEvent 单一事件模型** = trace / LLM history / UI / grader 统一
   - **C4 Orphaned-Action 修复** = 把 verifier failed_checks 喂回 LLM 做 REPAIR
   - **C2 ConfirmationPolicy 多档策略** = 替代当前 0/1 审批

4. **五大坚守差异化**：Dry-run、Rollback、§10.7 ledger、规则化 PolicyGuard、独立 Verifier、
   Skill-as-planner（不是 executor）。**这些不能丢；丢了 LocalFlow 就变成 OpenHands 的弱
   复制品**。
