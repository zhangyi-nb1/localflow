# Phase 28 — Workspace 抽象（LocalWorkspace 单一实现版）

**起草日期**：2026-05-25
**前置条件**：Phase 23 (sandbox) + Phase 27 (ConfirmationPolicy) 已落地
**§10.7 影响**：否 — 改的是工具层接口，不动 kernel 行为
**预计周期**：2-3 天
**版本目标**：`v0.26.0`

---

## 0. TL;DR

LocalFlow 现在直接把文件系统操作硬编码在 `executor.py` / `file_ops.py` /
`sandbox.py` 里 —— 每条新的 action_type 都得自己去拼路径 + 调
`shutil`。OpenHands 调研（[OPENHANDS_HARNESS_STUDY.md](research/OPENHANDS_HARNESS_STUDY.md)
§A4 + §C3）给出更干净的对模型：**Workspace** 抽象出
`execute_command / file_upload / file_download / list_dir / read_file /
write_file / sha256` 一组方法，三种实现：`LocalWorkspace` 直跑 host
文件系统、`DockerWorkspace` 在容器里、`RemoteWorkspace` 走 HTTP API。

**Phase 28 只做 LocalWorkspace + 接口契约。** Docker / Remote 留到
Phase 29 当作 LocalWorkspace 的换装：现在的难点不是 Docker，是
"executor 怎么从直调 shutil 改为通过 Workspace 接口"。这一步做完，
Docker / Remote 都是后续的 2-3 天工作。

**这不是 §10.7 例外** —— 8 条铁律保留：policy_guard 还是路径越界
检查唯一权威；rollback manifest 还是每次写入一条 entry；trace 事件
依旧通过 TraceLogger 发。Workspace 只是个 thin facade。

---

## 1. 命题

> 把所有文件系统写操作从 executor / sandbox / file_ops 抽到统一的
> `Workspace` 接口下，让"换装到 Docker / Remote"成为下游 phase 的
> 几小时工作而不是几周工作。

Phase 28 成功 = `LocalWorkspace` 实现 + executor 完全通过接口写文件 +
全部既有 865 测试通过 + 一个新 `Workspace` 抽象接口的契约测试套件。

---

## 2. 设计

### 2.1 Workspace 接口

```python
class Workspace(Protocol):
    """File-system-side facade. Every kernel write goes through here."""

    root: Path

    # Read
    def list_dir(self, rel_path: str) -> list[str]: ...
    def read_file(self, rel_path: str) -> bytes: ...
    def stat(self, rel_path: str) -> WorkspaceStat | None: ...
    def sha256(self, rel_path: str) -> str | None: ...
    def exists(self, rel_path: str) -> bool: ...

    # Write
    def mkdir(self, rel_path: str, *, parents: bool = True) -> None: ...
    def write_file(self, rel_path: str, content: bytes, *, overwrite: bool = False) -> None: ...
    def move(self, src_rel: str, dst_rel: str) -> None: ...
    def copy(self, src_rel: str, dst_rel: str) -> None: ...
    def delete(self, rel_path: str) -> None: ...

    # Lifecycle (only meaningful for Docker / Remote)
    def is_local(self) -> bool: ...
```

### 2.2 实现：LocalWorkspace

`app/tools/workspace.py::LocalWorkspace` 把现有 `app/tools/file_ops.py`
的散装函数（`safe_move` / `safe_copy` / `write_index` / `mkdir_p`）
包成一个有状态对象，**workspace_root** 在构造时固定。所有路径
传 `rel_path` 进来，内部用 `policy_guard.resolve_inside()` 解析 ——
路径越界检查仍是单一权威。

### 2.3 接入位置（executor）

`Executor.__init__(workspace_root, ...)` 加一个可选 `workspace: Workspace`
参数：
- 不传 → 自动构造 `LocalWorkspace(workspace_root)`，老调用站零变化
- 传 → 接管所有 fs 操作（Phase 29+ 用于注入 Docker）

`_run_one` 内部从 `file_ops.safe_move(src, tgt)` 改为
`self.workspace.move(src, tgt)`，每一类 dispatch 同理。

### 2.4 接入位置（sandbox / scratch）

ScratchWorkspace 仍然是物理 `<home>/scratch/...` —— 它跟用户 workspace
是两个独立的 Workspace 实例。SandboxRuntime 跟 scratch 配对（不变），
跟用户 workspace 解耦（用户 workspace 这边的写法变了，但 sandbox 不
感知）。

### 2.5 §10.7 不变

- policy_guard 仍是路径越界唯一权威（Workspace 的 mkdir/write 都先
  过 `resolve_inside`）
- rollback manifest 仍每写必记
- trace 事件依旧通过 TraceLogger 发
- Executor.execute() 入口签名只多一个**可选**参数

### 2.6 文件清单

**新增**：
- `app/tools/workspace.py` — `Workspace` Protocol + `LocalWorkspace` 实现
- `app/schemas/workspace_facade.py`（可选）— `WorkspaceStat` 等小型 Pydantic 类型，**或** 直接放 workspace.py 内
- `docs/WORKSPACE.md` — 用户视角文档
- `tests/test_workspace_local.py` — 契约测试（每种实现都跑同一套）
- `tests/test_executor_workspace_injection.py` — 注入路径

**修改**：
- `app/harness/executor.py` — `Executor.__init__` 加 `workspace` kwarg；`_run_one` / `_do_*` 改走 `self.workspace`
- `app/tools/file_ops.py` — 不动（LocalWorkspace 内部还在用）

---

## 3. 切片

### Phase 28.0 — 接口 + LocalWorkspace 实现 + 契约测试

**目标**：Workspace 接口存在，LocalWorkspace 实现，**不动 executor**。

**交付清单**：
1. `app/tools/workspace.py` 新建：Workspace Protocol + LocalWorkspace + WorkspaceStat
2. `tests/test_workspace_local.py` ~15 测试：mkdir / move / copy / write / stat / sha256 / exists / path-traversal-rejected
3. **不动** executor / sandbox

**验收**：865 + 15 测试通过；LocalWorkspace 单独可用。

### Phase 28.1 — Executor 接入 LocalWorkspace

**目标**：Executor 内部所有 fs 写入从直调 file_ops 改为 `self.workspace.*`。

**交付清单**：
1. `Executor.__init__` 加 `workspace: Workspace | None = None`；None → `LocalWorkspace(workspace_root)`
2. `_do_mkdir` / `_do_move` / `_do_copy` / `_do_rename` / `_do_index` / `_do_summarize` 全改走 workspace
3. `tests/test_executor_workspace_injection.py` ~5 测试：注入自定义 workspace 能拦截写
4. 既有 executor 测试全跑过

**验收**：全部测试通过；老路径零变化；可以注入 stub workspace 验证写入被路由过来。

### Phase 28.2 — 文档 + CLI 暴露（可选小切片）

**目标**：让用户能 grep 到这个抽象的存在。

**交付清单**：
1. `docs/WORKSPACE.md` 用户文档
2. PHASES.md 加 Phase 28 ledger 行
3. CLAUDE.md 锁定 Phase 29 = Docker 实现

**验收**：文档齐 + ledger 干净。

---

## 4. 不在 Phase 28 做

- ❌ DockerWorkspace 实现 — Phase 29
- ❌ RemoteWorkspace 实现 — Phase 30 候选
- ❌ Sandbox / scratch 重构 — 它们已经是隔离的 Workspace 形态，不动
- ❌ ACL / quota / read-only 子目录 — 不在抽象层做，policy_guard 那一层已经够

---

## 5. 立即执行的第一步

Phase 28.0：纯接口 + 实现，零 executor 改动。**这是 30-60 分钟的 PR**。

完成后立即开 Phase 28.1（executor 接入），那是 1-2 小时的真重构。
