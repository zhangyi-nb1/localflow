# Phase 17–21 测试报告 (v0.21.0)

> 用途:逐项核验 v0.17.0 → v0.21.0 五个 phase 的端到端能力。
> 跑完后在每个 `☐` 里打勾;任何一项不通过先在底部记录,统一交回。

---

## 0. 验证范围

| Phase | 版本 | 关键能力 |
|---|---|---|
| 17 | v0.17.0 | Recipe / Pack 系统(`pack list/describe/suggest/run`) |
| 18 | v0.18.0 | Goal Interpreter + Capability Primitives(`localflow goal`) |
| 19 | v0.19.0 | Deliverable Verifiers(7 个 verifier + exit code 3) |
| 20 | v0.20.0 | 3 旗舰 pack 正式化 + 3 个真 bug 修复(.dat/SOURCES.md/chart 范围) |
| 21 | v0.21.0 | Recipe Auto-Repair Loop(`recipe_repair.json`) |
| 21.1 | v0.21.1 | 测试反馈批修复(rollback CLI / replay 保护 / 路由澄清门 / UI verifier+repair 表) |

§10.7 不变式:`app/harness/*` kernel 零改动(仅 6 行 `_run_one_stage` 适配 stage_hints + Phase 21.1 的 `persist_graph` 参数,有文档记录)。

---

## 1. 前置准备(一次性)

```powershell
# 进入项目根 + 激活 venv
cd c:\Users\13513\Desktop\XIANGMU\localflow
.\.venv\Scripts\Activate.ps1

# 快捷函数:取最近一次 run id
function Get-LastRun {
    (ls .localflow\runs | Sort-Object Name -Descending | Select-Object -First 1).Name
}
```

**☐ 0.1** `pwd` 输出末尾是 `\localflow`(不是 `\XIANGMU`)
**☐ 0.2** `Get-Command localflow` 显示可执行,路径在 `.venv\Scripts\` 下
**☐ 0.3** `Select-String -Path pyproject.toml -Pattern '^version'` 输出 `version = "0.21.1"` 或更高
**☐ 0.4** `.env` 存在并包含 `OPENAI_API_KEY` + `OPENAI_BASE_URL`(LLM-相关测试需要)

> ⚠️ 路径与退出码约定:
> - run 产物在 `<cwd>\.localflow\runs\<run_id>\`,不是 `$env:USERPROFILE`
> - PowerShell 退出码看 `$LASTEXITCODE`,**不要看 `$?`**(那是布尔)
> - 每次跑 pack 前都重新 seed,避免 workspace 已被上一轮整理过

---

## Track A — 自动化测试 + 静态检查

### A1. 全量单测

**命令**
```powershell
pytest -q
$LASTEXITCODE
```

**预期输出**
```
666 passed in ~60s
0
```

**☐ A1.1** `666 passed`,无 failed/error
**☐ A1.2** `$LASTEXITCODE` = 0

---

### A2. 静态检查(只看主代码)

**命令**
```powershell
ruff check app/ tests/
$LASTEXITCODE
```

**预期输出**
```
All checks passed!
0
```

**☐ A2.1** `All checks passed!`
**☐ A2.2** `$LASTEXITCODE` = 0

> `ruff check .`(范围太宽)会扫到 `examples/external_skill_example/` 和 `sandbox_seed.py` 共 5 个 I001/F401 lint,那是给外部 skill 作者抄的样例文件,**不算 Track A 不过**。要消干净可 `ruff check . --fix`。

---

### A3. Phase 17–21 新增测试的冒烟集

**命令**
```powershell
pytest -q tests/test_recipe_schema.py tests/test_recipe_registry.py `
          tests/test_recipe_router.py tests/test_pack_cli.py `
          tests/test_goal_interpreter.py tests/test_primitives.py `
          tests/test_recipe_verifiers_structural.py `
          tests/test_recipe_verifiers_semantic.py `
          tests/test_recipe_repair.py `
          tests/test_phase_21_1_bugfixes.py
```

**预期**:全部 PASSED,无 SKIP(LLM verifier 的 stub 用 `app.agent.judge` 自动短路)。

**☐ A3.1** 10 个文件全 pass

---

## Track B — Pack CLI 套件 (Phase 17)

### B1. 列出 3 个旗舰 pack

**命令**
```powershell
localflow pack list
```

**预期**:表格中同时出现:
- `research_pack` — Research Pack
- `data_report_pack` — Data Report Pack
- `project_handoff_pack` — Project Handoff Pack

**☐ B1.1** 三个 pack 全部出现
**☐ B1.2** 每行都有 `name` / `title` / `description` / `tags` 列,description 列因长不会把 name 列挤断行(Phase 21.1 修复:`max_width=50`)

---

### B2. describe 各自

**命令**(三条分别跑)
```powershell
localflow pack describe research_pack
localflow pack describe data_report_pack
localflow pack describe project_handoff_pack
```

**逐 pack 预期**

| pack | stages | repair_policy | 关键 verifier |
|---|---|---|---|
| research_pack | 5 (s1_organize→s5_synthesize) | enabled=true, max_rounds=2 | 全部 7 个 |
| data_report_pack | 3 | enabled=true, max_rounds=2 | deliverable + chart_data + summary |
| project_handoff_pack | 3 | enabled=true, max_rounds=2 | deliverable + summary + coverage |

**☐ B2.1** research_pack 显示 5 stages + 7 verifiers
**☐ B2.2** 三个 pack 的 `repair_policy.enabled` 都是 `true`
**☐ B2.3** research_pack 的 `Repair target map` 小表显示 `coverage_verifier → s1_organize` 和 `review_queue_verifier → s1_organize`(Phase 21.1 新增 CLI 渲染)

---

### B3. 路由器打分(`pack suggest`)

> 签名:`localflow pack suggest <workspace_path> [--goal "..."]`
> workspace 是**第一个位置参数**,goal 是可选 `--goal`。

```powershell
# 用 research_pack 自带的 workspace
python examples/research_pack/seed.py
localflow pack suggest examples/research_pack/workspace --goal "整理论文和数据"
```

**预期**:top-1 = `research_pack`,score ≥ +6,why 列含 "goal mentions" + "workspace has"(router 的实际短语,不是 "keywords/kinds")。

```powershell
python examples/data_report_pack/seed.py
localflow pack suggest examples/data_report_pack/workspace --goal "生成数据报告"
```
**预期**:top-1 = `data_report_pack`。

```powershell
python examples/project_handoff_pack/seed.py
localflow pack suggest examples/project_handoff_pack/workspace --goal "项目交接文档"
```
**预期**:top-1 = `project_handoff_pack`。

**☐ B3.1** 三次 suggest 的 top-1 与预期一致
**☐ B3.2** 每次最后一行有 `Suggested: <name>  ·  localflow pack run <name> --workspace ...`

---

### B4. 退出码语义

**命令**
```powershell
localflow pack describe nonexistent_pack
$LASTEXITCODE
```

**预期**:错误提示 + `$LASTEXITCODE = 2`(用户错误)。

**☐ B4.1** 退出码为 2,不是 1

---

## Track C — Goal Interpreter (Phase 18)

> 签名(注意是**单个 leaf 命令**,没有 `interpret` 子命令):
> `localflow goal "<user_goal>" --workspace <path> [--no-llm] [--run] [--yes]`

### C1. Router-confident 路径(免 LLM)

**命令**
```powershell
python examples/research_pack/seed.py
localflow goal "整理论文 PDF 和实验数据成知识包" --workspace examples/research_pack/workspace --no-llm
```

**预期输出片段**(Phase 21.1 起,首行带结构化标签)
```
Decision: pick
Recipe: research_pack
Source: router
Rationale: <中文,提到关键词命中 + 文件类型匹配>
```

**☐ C1.1** decision = `pick`
**☐ C1.2** recipe = `research_pack`
**☐ C1.3** rationale 是中文(走了 i18n)
**☐ C1.4** 整个过程**无 OpenAI API 调用**(无 400/无网络抱怨)

---

### C2. 关键词不足 + 无 LLM → clarify 兜底

**命令**(Phase 21.1 新增澄清门:vague 目标必须 clarify,不再硬选)
```powershell
localflow goal "随便弄一下" --workspace examples/research_pack/workspace --no-llm
```

**预期**:
- decision = `clarify`
- rationale 中文,提到 "未配置 LLM" 且 "路由器没有正向得分的交付包"

**☐ C2.1** decision = `clarify`(Phase 21.1 关键修复;此前会硬选 research_pack)
**☐ C2.2** rationale 中文,语义合理

---

### C3. LLM 介入路径(验 OpenAI strict schema 修复)

**命令**(模棱两可,router 不自信 → 触发 LLM)
```powershell
localflow goal "我要整理一下这堆乱七八糟的文件" --workspace examples/research_pack/workspace
```

**预期**:
- 不再出现 `BadRequestError: 400 ... schema must list all properties`(Phase 18 修复点)
- decision 是 `pick` 或 `clarify` 之一
- rationale 在中文 locale 下为中文

**☐ C3.1** 无 OpenAI 400 错误
**☐ C3.2** 有合理 decision 输出
**☐ C3.3** rationale 中文

---

## Track D — Deliverable Verifiers + 3 个真 bug 修复 (Phase 19+20)

### D1. clean 跑 research_pack

```powershell
python examples/research_pack/seed.py
localflow pack run research_pack --workspace examples/research_pack/workspace --yes
$LASTEXITCODE
```

**预期**(取决于 LLM 状态):
- 有 LLM key + 一切顺利 → 5 stages PASSED, verifiers ≥ 部分 PASS, `$LASTEXITCODE = 0` 或 `3`
- 无 LLM key → s5_synthesize SKIPPED, 其余 4 stage PASSED

**关键不变式**:`$LASTEXITCODE` 只可能是 `0`/`3`,**不应该是 1**(1 = pipeline 崩了)。

**☐ D1.1** stages 全 PASSED 或最后 1 个 SKIPPED
**☐ D1.2** 末尾打印 `Deliverable verifiers: PASSED/FAILED (N)` 表
**☐ D1.3** `$LASTEXITCODE ∈ {0, 3}`

---

### D2. 看 recipe_verification.json

```powershell
$lastRun = Get-LastRun
type .localflow\runs\$lastRun\recipe_verification.json
```

**预期**:JSON 含 `passed` / `failed_count` / `skipped_count` / `verdicts[]`,每个 verdict 有 `name` / `passed` / `detail` / `suggested_hint?`。

**☐ D2.1** 文件存在且 JSON 合法
**☐ D2.2** `verdicts` 数组含 7 个 verifier(若全列在 recipe 里)

---

### D3. 三个 Phase 20 bug 修复点逐项核验

| Bug | 修复前现象 | 修复后核验方法 |
|---|---|---|
| `.dat` 进 misc 而非 review/ | `untitled.dat` 出现在 `misc/` | 看 workspace:`ls examples/research_pack/workspace/review/` 应包含 `untitled.dat` |
| agent 漏 `SOURCES.md` | `expected_outputs` 缺 SOURCES.md | 看 `deliverable_completeness_verifier.detail` —— 不应抱怨 `SOURCES.md`(LLM 路径) |
| chart_data 比错对象 | 抱怨 `images/file_counts.png` 和 CSV 不一致 | 看 `chart_data_consistency_verifier.detail` —— 只应针对 `analysis_charts/*.png`,不应抱怨 `images/file_counts.png` |

**☐ D3.1** `review/untitled.dat` 存在(或 `review/*.md` 中提及它)
**☐ D3.2** 无 LLM 时该项跳过即可;有 LLM 时 SOURCES.md 存在
**☐ D3.3** `chart_data_consistency_verifier` 的 detail 不再提 `images/file_counts.png`

---

### D4. Exit code 3 区分

`$LASTEXITCODE` 的语义在 v0.19 后:

| 退出码 | 含义 |
|---|---|
| 0 | stages 全 pass + verifiers 全 pass / skip |
| 1 | pipeline crash(stage 内部 abort) |
| 2 | 用户输入错误(recipe 名拼错等) |
| 3 | stages 全 pass,但 ≥1 verifier FAIL |

**☐ D4.1** 在 D1 中观察到的退出码符合上表含义

---

## Track E — Recipe Auto-Repair Loop (Phase 21) ⭐

这是本批次的核心新功能,重点测。

### E1. 触发自动 repair

**命令**(需要 LLM key,否则 plan_with_llm 兜底无法跑)
```powershell
python examples/research_pack/seed.py
localflow pack run research_pack --workspace examples/research_pack/workspace --yes
```

**stdout 期望看到的片段**(当首轮 verifier 有 FAIL 时):
```
[recipe-repair] round 1/2: triggered by <verifier_name>
[recipe-repair] target stage: s5_synthesize  (或 s1_organize for coverage/review)
[recipe-repair] hint: <suggested_hint 原文>
[recipe-repair] replay_from_stage(...)
[recipe-repair] post-attempt verification: passed=<N>/skipped=<M>/failed=<K>
```

> 若首轮全 pass,不会进 repair 循环 —— 这正常,跳到 E5 验关闭路径。

**☐ E1.1** 若首轮有 fail:stdout 出现 `[recipe-repair]` 日志
**☐ E1.2** target stage 与 `repair_target_map` 一致(coverage/review→s1_organize,其余→s5_synthesize)
**☐ E1.3** Phase 21.1 修复:同一 verifier 不会被尝试 2 次(`attempted_verifiers` 集合记录,跳过重复)

---

### E2. 检查 recipe_repair.json

```powershell
$lastRun = Get-LastRun
type .localflow\runs\$lastRun\recipe_repair.json
```

**字段核验**

| 字段 | 期望 |
|---|---|
| `repaired` | bool —— 真修好为 true |
| `rounds_used` | 0..max_rounds (= 2) |
| `halt_reason` | `passed` / `exhausted` / `no_repairable_failures` / `replay_error` 之一 |
| `attempts[*].triggered_by_verifier` | 某 verifier 名 |
| `attempts[*].suggested_hint` | 非空字符串 |
| `attempts[*].target_stage` | 合法 stage_id |
| `attempts[*].post_attempt_passed` | bool |
| `attempts[*].failed_after_attempt` | list[str] |
| `attempts[*].duration_ms` | > 0 |
| `final_verification` | 完整 RecipeVerification 对象 |

**☐ E2.1** JSON 文件存在(首轮全 pass 时可缺,见 E5)
**☐ E2.2** 上表所有字段语义合理
**☐ E2.3** `attempts[].suggested_hint` 与首轮 verdict 的 `suggested_hint` 一致

---

### E3. recipe_verification.json 被改写为 post-repair

```powershell
type .localflow\runs\$lastRun\recipe_verification.json
```

**预期**:此文件应反映 repair **之后**的 verdict —— `passed` 数 ≥ E1 首轮的 passed 数(repair 改善了或保持)。

**☐ E3.1** verification.json 反映 post-repair 状态
**☐ E3.2** 若 `recipe_repair.json.repaired = true`,verification.json 的 `passed = true`

---

### E4. taskgraph.json 不被 replay 改写(Phase 21.1)

> Phase 21.1 新增:repair 每轮内部用 `run_taskgraph(sub_graph, persist_graph=False)`,
> 不再让裁剪过的 sub-graph 覆盖原始 `taskgraph.json` 审计记录。

**命令**
```powershell
$lastRun = Get-LastRun
# 看 taskgraph.json 的 stages 数,应 = 原始 recipe stage 数(research_pack = 5)
type .localflow\runs\$lastRun\taskgraph.json | findstr stage_id | Measure-Object | Select-Object Count
```

**☐ E4.1** 即使发生过 repair,`taskgraph.json` 中 `stage_id` 数仍 = 原始 stage 数(不是被截断后的子集)

---

### E5. repair_target_map 自定义路由

研究 `recipes/research_pack.yaml` 末尾:
```yaml
repair_target_map:
  coverage_verifier: s1_organize
  review_queue_verifier: s1_organize
```

**核验方式**:故意制造 coverage_verifier 失败 → 看 `attempts[0].target_stage`。

最简办法:先正常跑一次,如果 E1 触发了 coverage_verifier 或 review_queue_verifier,直接看 target_stage 字段。

**☐ E5.1**(条件性)若 coverage/review 失败:`target_stage = "s1_organize"`,非默认的 s5_synthesize

---

### E6. 关闭 repair 的对照实验

**步骤**
1. 编辑 `recipes/research_pack.yaml`,把
   ```yaml
   repair_policy:
     enabled: true
   ```
   改成 `enabled: false`。
2. 重 seed + 重跑:
   ```powershell
   python examples/research_pack/seed.py
   localflow pack run research_pack --workspace examples/research_pack/workspace --yes
   ```
3. **预期**:
   - stdout 不出现 `[recipe-repair]` 日志
   - `.localflow\runs\<id>\recipe_repair.json` **不应生成**
4. **改回**:把 `enabled` 恢复为 `true`。

**☐ E6.1** 关闭时无 repair 日志
**☐ E6.2** 关闭时无 recipe_repair.json
**☐ E6.3** 改回 enabled=true

---

## Track F — Streamlit UI

> **必须**用 `localflow ui-serve` 启动(走 cli.py 入口才会加载 `.env`)。
> 手动 `streamlit run` 会出 "No LLM client" 警告。

```powershell
# 先清掉残留实例
Get-Process streamlit -ErrorAction SilentlyContinue | Stop-Process -Force

localflow ui-serve
# 浏览器自动开 http://localhost:8501
```

### F1. Pack 页面 i18n

操作:
1. 左上角语言选 **中文**
2. 进入 `0_Pack` 页(默认就在第一个)
3. 切英文,再切回中文

**☐ F1.1** 中文模式下整页文字全中文(无英文残留按钮 / label)
**☐ F1.2** 三个 pack 都能 list,点击任一 pack 显示 stages + verifiers(Phase 21.1 新增的 `Verifiers (N)` popover)
**☐ F1.3** 英文模式下整页英文
**☐ F1.4** 启用 repair 的 pack 在 verifiers popover 内显示 `Repair routing:` 小段,列出 `repair_target_map` 中的自定义条目

---

### F2. Goal Interpreter 入口

**by-design — Goal 页面由 CLI Track C 覆盖。**

Phase 17–21 的 Streamlit UI 把"目标解释"折叠在 `0_Pack` 页内的 `🎯 Interpret a goal` 展开器里,**没有独立的 Goal 页面**。
功能上等价于 CLI 的 `localflow goal "<goal>" --workspace ...`,因此放在 Track C 一并核验,不在 Track F 重复跑。

**☐ F2.1** 在 `0_Pack` 页展开 `🎯 Interpret a goal`,输入 "整理论文" + 不勾 LLM → 显示 router-confident 结果(等同 C1)
**☐ F2.2** 输入 "随便弄一下" + 不勾 LLM → 显示 clarify 形态(等同 C2,Phase 21.1 新行为)

---

### F3. Pack run 后的 verification + repair 表(Phase 21.1)

操作:在 UI 的 Pack 页跑一次 research_pack(等同于 D1)

**☐ F3.1** UI 下方渲染 `Recipe verifiers` 表,每项有 pass/fail/skipped icon、detail、suggested hint
**☐ F3.2** 若发生 repair,显示 `Auto-repair attempts` 表 —— 每行包含 attempt# / verifier / replays stage / hint / outcome / ms
**☐ F3.3** 若 verifier 全 pass,不出现 repair 表(没有 attempts);仅看到 verifier 通过提示

---

## Track G — 三个旗舰 pack 端到端 + rollback

每个 pack 重新 seed → 跑 → 看产物 → rollback。

### G1. research_pack(已在 D/E 覆盖,跳到 G4 验 rollback)

### G2. data_report_pack

```powershell
python examples/data_report_pack/seed.py
localflow pack run data_report_pack --workspace examples/data_report_pack/workspace --yes
$lastRun = Get-LastRun
type .localflow\runs\$lastRun\recipe_verification.json
```

**预期产物**(workspace 内):
- `analysis_report.md`
- `analysis_charts/*.png`
- `README.md`(若 LLM 可用)

**☐ G2.1** 3 stages 全 PASSED(或 LLM stage SKIPPED)
**☐ G2.2** `analysis_report.md` 存在且非空
**☐ G2.3** Phase 21.1 修复:per-stage backup 路径不会泄到 stage run 子目录之外(`backups_dir.parent` 修复)

---

### G3. project_handoff_pack

```powershell
python examples/project_handoff_pack/seed.py
localflow pack run project_handoff_pack --workspace examples/project_handoff_pack/workspace --yes
$lastRun = Get-LastRun
type .localflow\runs\$lastRun\recipe_verification.json
```

**预期产物**:`HANDOFF.md` / `ARCHITECTURE.md` 或类似的项目交接文档(具体看 recipe `expected_outputs`)。

**☐ G3.1** 3 stages 全 PASSED(或 LLM stage SKIPPED)
**☐ G3.2** recipe.expected_outputs 里至少 ≥80% 产物存在

---

### G4. Rollback 验证(任选一个 pack 的 run)— Phase 21.1 修复

> CLI 没有 `rollback preview` 子命令,直接用 `rollback --run-id <id>`。
> 想看 preview 必须经 MCP 工具 `rollback_preview` 或读 `<run_dir>/rollback_manifest.json`。
>
> **Phase 21.1 关键修复**:`localflow rollback` 在 task.json 缺失时
> 会回落到 `taskgraph.json` 读取 workspace_root,而不是直接报"missing task.json"。
> 这让 pack-run 产生的、没有 task.json 的 run 也能从 CLI 回滚。

```powershell
$lastRun = Get-LastRun

# 查 manifest(等价于 preview)
type .localflow\runs\$lastRun\rollback_manifest.json | Select-Object -First 50

# 执行 rollback
localflow rollback --run-id $lastRun --yes
```

**预期**:workspace 回到 seed 后的状态(对应 pack 的 seed.py 的产物)。

```powershell
# 比对一下
ls examples/research_pack/workspace/
# 应该和 seed.py 刚跑完一样:10 个原始文件,无 papers/data/images 子目录,无 *.md 产物
```

**☐ G4.1** `localflow rollback` 返回成功(Phase 21.1:对 pack-only run 也能找到 workspace_root)
**☐ G4.2** workspace 回到 seed 状态(无 pack 生成的子目录 / md)
**☐ G4.3** 无 drift conflict(若有,看是否 workspace 被手动改过)
**☐ G4.4** 完全无 manifest / 无 taskgraph 的 run 仍会被拒绝,退出码 = 2

---

## 总检查表

| Track | 通过判据 | 是否通过 |
|---|---|---|
| A — 自动化测试 | 666 passed + ruff 0 issue | ☐ |
| B — Pack CLI | list/describe/suggest/run 全正常 + 退出码 2 + 描述列不挤断 + repair map 显示 | ☐ |
| C — Goal Interpreter | 三条路径全通 + 无 OpenAI 400 + vague 走 clarify | ☐ |
| D — Deliverable Verifiers | 7 verifier 跑通 + 3 个 bug 修复点验过 + exit code 区分对 | ☐ |
| E — Auto-Repair | recipe_repair.json 字段齐 + 重复 verifier 不再次尝试 + taskgraph.json 不被覆盖 + enabled=false 不触发 | ☐ |
| F — UI | 中英文无残留 + verifier 表 + repair 表渲染对 + Goal 入口集成在 Pack 页 | ☐ |
| G — 三 pack 端到端 + rollback | 三个全跑通 + pack-only run rollback 干净 | ☐ |

---

## 常见陷阱回顾

| 问题 | 现象 | 解法 |
|---|---|---|
| `$?` 不是退出码 | 看起来都是 True | 用 `$LASTEXITCODE` |
| run 产物找不到 | 看 `$env:USERPROFILE\.localflow` | 真实路径在 `<cwd>\.localflow\runs\` |
| Pack 跑完后 verifier 全 fail | folder_organizer 抱怨 `no_file_loss` | 先 `python examples/<pack>/seed.py` 重 seed |
| Streamlit 警告 "No LLM client" | 手动 `streamlit run` 不读 .env | 用 `localflow ui-serve` |
| 8501 端口被占 | UI 启不来 / 旧版本残留 | `Get-Process streamlit \| Stop-Process -Force` |
| LLM repair 不收敛 | `halt_reason: exhausted` | 合法状态,不是 bug;看 attempts 是否在压低 fail 数 |
| 跑 `localflow --version` 报错 | `No such option: --version` | CLI 没挂 version flag;读 `pyproject.toml` 或用 `importlib.metadata` |
| 跑 `localflow goal interpret ...` 报错 | `No such command 'interpret'` | `goal` 是 leaf 命令:`localflow goal "..." --workspace <path>` |
| 跑 `localflow pack suggest "..."` 报错 | 把 goal 当 workspace | `pack suggest` 签名:`<workspace> [--goal "..."]` |
| 跑 `localflow rollback preview` 报错 | 无此子命令 | CLI 直接 `rollback --run-id`;preview 看 manifest 文件或 MCP |

---

## 不通过项记录区

> 跑测试时把任何不符合预期的现象记在这里,统一交回:

```
Track ?.? :
  命令: ...
  实际输出: ...
  预期输出: ...
  备注: ...
```

---

跑完后告知整体结果,任何 ☐ 没勾上的我来一起定位。
