# Phase 29 — DockerWorkspace

**起草日期**：2026-05-25
**前置条件**：Phase 28 (Workspace 抽象 + LocalWorkspace) 已发布 v0.26.0
**§10.7 影响**：否 — 新增 Workspace 实现，不动 kernel
**预计周期**：3 天
**版本目标**：`v0.27.0`

---

## 0. TL;DR

Phase 28 把 fs 操作抽到 `Workspace` Protocol 后端，LocalWorkspace 实现
覆盖 95% 本地用例。Phase 29 引入 **DockerWorkspace** —— 在 Docker 容器
内跑用户工作区 + 所有 fs 操作通过 `docker exec` 路由进容器。

兑现的承诺：
- Phase 23 `PYTHON_COMPUTE` 的 "isolation, best-effort" 真正升级为
  **强隔离**（容器 namespace + 可选 `--network=none` + cgroup limits）
- 跑陌生 plan / 不被信任的脚本时多一层安全
- CI / eval 测试可重复性更强（每次 fresh 容器）

§10.7 不变 —— policy_guard 还是路径越界唯一权威；rollback / trace /
verifier 全部 untouched。DockerWorkspace **只是** Phase 28
`Workspace` Protocol 的另一个实现，drop-in 替换 LocalWorkspace。

---

## 1. 命题

> 在不动 kernel 接口的前提下，给 LocalFlow 一个 `DockerWorkspace`
> 后端，让用户能用 `localflow execute --workspace docker:<image>`
> 把执行阶段路由进隔离容器。

Phase 29 成功 = DockerWorkspace 实现 + 跑通 Phase 28 的 27 contract
test 套件（parameterized） + 一个 demo task 在容器里跑通 + Docker
不可用时优雅降级到 LocalWorkspace。

---

## 2. 设计

### 2.1 实现策略：subprocess + `docker exec`

不引入新依赖（不用 `docker` Python SDK）。直接 subprocess 调 docker CLI：

```
docker run -d --name <task_id>-ws <image> sleep infinity   # 起容器
docker exec <id> mkdir -p /workspace/<rel>                 # mkdir
docker exec <id> mv /workspace/<src> /workspace/<dst>      # move
docker exec -i <id> sh -c "cat > /workspace/<rel>" < file  # write
docker exec <id> cat /workspace/<rel>                      # read
docker exec <id> sha256sum /workspace/<rel>                # sha256
docker rm -f <id>                                          # close
```

容器内 workspace_root 固定为 `/workspace`，host 不挂 volume —— 容器
有自己的 fs，跟 host 完全隔离。这是 isolation strong 模式（牺牲性能
换隔离）。

> 备选模式：`-v <host>:/workspace` bind mount。失去隔离性但保留共享
> 工作目录。**Phase 29.0 默认 NOT 用 bind mount**；Phase 29.x 可加
> opt-in flag。

### 2.2 路径越界防御

Host 侧每个 method 进口先用同样的 `resolve_inside`-style 规则验证
（拒 `..`、绝对路径、驱动器前缀、UNC 路径）。容器内的 path 由 host
拼接 `/workspace/<rel>`，shell 不会重新解析特殊字符。**defense in
depth**：host + container 两层验证。

### 2.3 容器镜像

默认 `python:3.12-slim`（~50 MB，公共镜像，无需自建）。给所有 fs 操作
够用了。后续 Phase 29.x 可以加自定义 `ghcr.io/localflow/agent:latest`
镜像（预装 pandas 等数据栈）。

### 2.4 生命周期

```python
ws = DockerWorkspace(image="python:3.12-slim", workspace_root_inside="/workspace")
ws.start()             # 启容器，运行 sleep infinity
try:
    ws.mkdir("sub/")
    ws.write_text("note.md", "hello")
    # ... use through Executor ...
finally:
    ws.close()         # docker rm -f
```

Or context-manager: `with DockerWorkspace(...) as ws: ...`。

`Executor` 的 `workspace=` kwarg 接进来后正常用。Phase 23
`PYTHON_COMPUTE` 的 `SandboxRuntime` 不变 —— scratch 仍跑 host
subprocess；DockerWorkspace 只接管 user workspace 那一侧。

### 2.5 Docker 不可用时的降级

构造 DockerWorkspace 时 ping `docker version` 检查；失败抛
`DockerUnavailable` exception。CLI / Recipe 层捕获后给清晰错误信息
（"Docker not detected; falling back to LocalWorkspace" 或 fail-stop，
看 Recipe 设置）。

### 2.6 §10.7 不变

- `policy_guard.resolve_inside` 仍是路径越界**程序化**权威
- DockerWorkspace 不参与决策；它只搬运
- rollback manifest 仍每写必记，rollback 时通过同一个 DockerWorkspace
  反向调用（容器还活着才能 rollback；rollback 后 close 容器）
- trace 事件依旧通过 TraceLogger 发，DockerWorkspace 不发自己的事件

### 2.7 文件清单

**新增**：
- `app/tools/docker_workspace.py` — `DockerWorkspace` + `DockerUnavailable`
- `tests/test_workspace_docker.py` — 跑 Phase 28 contract test 套件，但
  fixture 用 DockerWorkspace；Docker 不在 PATH 时整个 file `pytest.skip`
- `tests/_workspace_contract.py`（可选）— 抽 LocalWorkspace 测试体为
  shared helpers，两边共用
- `docs/PHASE_29_DESIGN.md`（本文件）
- `docs/DOCKER_WORKSPACE.md` — 用户视角文档

**修改**：
- `app/tools/workspace.py` — 无需改 Protocol；可能加一个工厂函数
  `parse_workspace_spec("docker:python:3.12-slim") -> Workspace`
- `app/cli.py` — `--workspace docker:<image>` flag（可选 Phase 29.3）

---

## 3. 切片

### Phase 29.0 — DockerWorkspace 核心实现

**目标**：DockerWorkspace 类 + 容器生命周期 + 27 contract test 全过。

**交付清单**：
1. `app/tools/docker_workspace.py`：
   - `DockerWorkspace` 类（实现 Workspace Protocol）
   - `DockerUnavailable` exception
   - subprocess + docker CLI 路由
   - context manager 支持 (`__enter__` / `__exit__`)
2. `tests/test_workspace_docker.py`：
   - module-level `pytest.skip_if_no_docker` fixture
   - 跑 Phase 28 的 contract 集（mkdir / move / copy / read / write /
     stat / sha256 / list_dir / path-traversal-defence）

**验收**：900 + ~27 测试通过；DockerWorkspace 单独可用。Docker 不可用时
跳过该测试文件，不破其他测试。

### Phase 29.1 — Executor 注入 + demo

**目标**：用 Executor 注入 DockerWorkspace 跑一个真实 plan，证明
Workspace 抽象的 drop-in 价值。

**交付清单**：
1. `tests/test_executor_docker_workspace.py`：~3-5 测试，用
   DockerWorkspace 跑 MKDIR / MOVE / COPY / INDEX 的小 plan
2. demo 脚本（可选）：跑 examples/messy_downloads 等

**验收**：Executor 注入 DockerWorkspace 后行为与注入 LocalWorkspace
**完全一致**（同一 plan / 同样的 rollback manifest / 同样的 trace）。

### Phase 29.2 — CLI flag + Recipe 字段 + docs

**目标**：让用户能从 CLI 一键启用。

**交付清单**：
1. `localflow execute --workspace docker:<image>` flag
2. `parse_workspace_spec(spec: str) -> Workspace` 工厂
3. `RecipeSpec.workspace_backend` 字段（可选）
4. `docs/DOCKER_WORKSPACE.md` 用户文档
5. PHASES.md ledger row + CLAUDE.md 锁定 Phase 30

**验收**：`localflow execute --task-id <id> --yes --workspace docker:python:3.12-slim` 跑通。

---

## 4. 不在 Phase 29 做

- ❌ 自定义 LocalFlow agent image（用 `python:3.12-slim` 公共镜像）
- ❌ HTTP agent-server（直接 docker exec 够用；改 HTTP 是 Phase 29.x 优化）
- ❌ Bind mount opt-in（强隔离优先）
- ❌ 网络断开 (`--network=none`) 默认（容器需要拉 input 时可能需要网络；
  留给 Recipe 级配置）
- ❌ RemoteWorkspace —— Phase 30 候选
- ❌ harness 拆独立包 —— Phase 31+ 候选

---

## 5. 风险与缓解

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| 1 | Docker daemon 没装 / 没起 | 高 | `DockerUnavailable` exception + 清晰 CLI 错误信息；测试跳过 |
| 2 | 镜像第一次拉很慢 | 中 | docs 明说预拉；CI 用本地缓存的 image layer |
| 3 | 每次 docker exec 100-300ms 慢 | 中 | Phase 29.x 可上 HTTP agent-server；当前性能够用 |
| 4 | 容器内的文件转 host 麻烦 | 中 | `docker cp` 现成；只在 rollback / verify 阶段需要 |
| 5 | Windows CI 没默认装 Docker | 低 | Linux ubuntu-latest runner 跑 Docker 测试；mac/Windows runner skip |
| 6 | DockerWorkspace 跟 SandboxRuntime（scratch）冲突 | 低 | 它们是独立 Workspace 实例；scratch 仍在 host 上 |

---

## 6. 立即执行的第一步

Phase 29.0 第一个 PR：纯 `DockerWorkspace` 实现 + contract test。
**~半天工作**。
