# LocalFlow — Flagship Demo (Option 1) + 长任务搭建 (Option 2) 指南

> 状态:操作手册 / 实操指导　·　日期:2026-05-29　·　基于 commit `4b9d7f3`(v0.35.0)
>
> 这份文档分两部分:
> - **Option 1 — 立刻能做**:把已有的 flagship(`literature_review_pack`)放到一个复杂任务上**真跑一遍并录屏**,演示"agent 在复杂多步骤生成里抓出幻觉"。这是你说的"项目落地"的最短路径,一个周末能出活。
> - **Option 2 — 选做**:搭建 checkpoint / resume / handoff,补上 benchmark 第 3 行 `context_rot` 的 gap,才能诚实地 claim "长任务优越"。约 1–2 周。
>
> 建议归档:`docs/DEMO_AND_LONGTASK_GUIDE.md`。Option 2 真正落地时再拆 `docs/PHASE_38_DESIGN.md`。
>
> **贯穿全文的一条红线(诚实纪律 rule F):benchmark 怎么标的,简历/README 就怎么说。**
> Option 1 演示的是"复杂任务(complex / multi-stage / content-heavy)",**不是 "long-running"**;
> "long-running / 抗 context-rot" 这个招牌只有做完 Option 2 才能挂。

---

## TL;DR

| | Option 1(推荐先做) | Option 2(选做) |
|---|---|---|
| 证明什么 | 复杂任务里**抓出幻觉**(benchmark 第 2 行 false_completion ✅) | **长任务不丢状态**(benchmark 第 3 行 context_rot,目前 gap) |
| 要不要写新功能 | **不用**,flagship 已具备,只需"真跑 + 录屏" | **要**,新增 checkpoint/resume/handoff |
| 工作量 | 一个周末 | 1–2 周 |
| 简历措辞 | "complex, multi-stage generation task" | 做完后才能写 "interruptible long task" |
| 是否动 kernel | 否 | 预期否(facade 层) |

---

# Part 0 — 共同前置

### 0.1 装好、确认能跑

```bash
git clone https://github.com/zhangyi-nb1/localflow.git
cd localflow
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
git config core.hooksPath .githooks

# 确认 CLI 在
.venv/bin/localflow --version
.venv/bin/localflow pack describe literature_review_pack
```

### 0.2 两种运行模式(很重要,影响 demo 怎么录)

flagship 的生成阶段(`s2_synthesize`,`agent` skill,`planner: llm`)**需要 LLM key**。没 key 时该阶段 `failure_policy: skip`,grounding gate 也会一起 skip(没东西可验)。所以:

- **确定性模式(无 key):** 用注入好的 `review.md` fixture,gate 在固定输入上跑。**可复现、可证伪**,适合做主干 demo 和自动化测试。
- **真 LLM 模式(有 key):** 在真实源上让 LLM 真写综述,gate 抓真实的无源论断。**适合录一份"在真实输出上 work"的产物**,堵住面试官"是不是注入出来骗人的"那条缝。

配 key(v0.34.1 起自动读项目根的 `.env`):

```bash
# .env(二选一)
# OpenAI 兼容:
LOCALFLOW_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=...        # 用第三方网关时填
# 或 Anthropic:
# LOCALFLOW_LLM_PROVIDER=anthropic
# ANTHROPIC_API_KEY=sk-ant-...
```

**两种模式都要做**:确定性模式作 demo 主干 + 进 CI;真 LLM 模式录一份产物挂进 README。

---

# Part 1 — Option 1:复杂任务抓幻觉 Demo(立刻做)

## 1.1 目标与"落地"验收标准

**目标:** 录一段让人 30 秒看懂的演示——喂进一批论文 → agent 写出综述 → 综述里混了几条编造的话 → 你的 grounding gate 当场把它们标出来、拦住不交付;同一份综述在"关掉 gate"时则把假话静默 ship 进成品。

**"算落地"的验收标准(达到即可写进简历):**
- [ ] 一条命令能复现整个流程(确定性模式)。
- [ ] gate **召回率达标**:注入的 K 条编造论断,被标出 ≥ K-? 条(目标先定 100%,见 1.5)。
- [ ] **误报率可接受**:真实有出处的论断被错标的比例低(目标 < 10%)。
- [ ] guard ON/OFF 的产物 diff 清晰可见(OFF 的 `review.md` 里赫然写着假话;ON 标红 + gate 成 not-shippable / exit 3 + 转人工)。
- [ ] 有一段 ≤ 60 秒录屏 + 一份真 LLM 模式跑出的产物挂进 README。

## 1.2 搭一个"够复杂"的输入

复用现成的 `examples/research_pack/seed.py` 当模板,扩成一个**主题连贯、规模够大**的源集:

- **规模:** ~10–12 个源(混合 PDF + `.md` 笔记)。源多 → 综述长 → 论断多 → 任务诚实地"复杂"。
- **主题连贯:** 全部围绕一个你熟的主题(例如"LLM agent 评估方法"),这样综述会真的去做跨源综合,而不是各说各话。
- **内容你已知:** 每个源里放几条**明确的、可核对的事实**(具体数字、具体结论),这样后面才能判断综述里的论断到底有没有出处。
- 落地:写一个 `examples/literature_review_pack/seed.py`(参考 research_pack 的写法,真 `%PDF` 头让 pypdf 能抽文字),`python examples/literature_review_pack/seed.py` 一键铺好工作区。

## 1.3 注入幻觉的两层(都诚实——注入已知错误是 eval 标准做法)

**第一层(主干,无 key,确定性):一份注入好的 `review.md` fixture。**
写一份"综述成品",里面含 M 条论断,其中 **K 条是故意编造的**。给 `seed.py` 加 `--check`(对齐你 recipe 里已写的 `seed.py --check` 约定),它直接铺一份这样的 `review.md`,然后跑 `claim_grounding_verifier`,断言 gate 正好标出这 K 条。

四类经典幻觉(每类放 1–2 条,覆盖 gate 该抓的情形):
1. **无出处的统计** — 例:"该方法在基准上把准确率提升了 23%",但源集里没有这个数字。
2. **凭空引用** — 例:"如 Smith (2024) 所述……",但源集里没有 Smith 2024。
3. **与源矛盾** — 源里写"提升 5%",综述写成"提升 50%"。
4. **过度泛化** — 源里只在一个数据集上验证,综述写"在所有任务上都成立"。

再放 ~2 条**真实有出处**的论断作对照(确认 gate 不会误标它们)。

**第二层(加分,有 key):真 LLM 跑一次并录产物。**
```bash
.venv/bin/localflow pack run literature_review_pack --workspace ./examples/literature_review_pack/workspace
```
真实 LLM 综述本来就会冒出无源论断;gate 把它们标出。把这次 run 的 `review.md` + `SOURCES.md` + verifier 报告 + `trace.jsonl` 存一份到 `docs/test_artifacts/<version>/literature_review_llm_run/`,README 里链过去。

## 1.4 guard ON / OFF 对照(harness 价值的可视化)

你 Phase 37 已经有 ON/OFF ablation 机制;这里把它放到上面这个更大、更真实的输入上,做出**两份并排的成品**:

- **Guard OFF(模拟朴素 agent):** 做一个 recipe 变体 `literature_review_pack_nogate.yaml`——`verifiers:` 里**去掉** `claim_grounding_verifier`(或保留但不 gate)。跑出来的 `review.md` 把那 K 条编造论断**静默 ship**,无任何报警,exit 0。
- **Guard ON(flagship):** 原 `literature_review_pack`。同一份综述 → gate 把 K 条标红 → 超阈值 gate 成 not-shippable(**exit code 3**)→ `repair_target_map` 重放 `s2_synthesize`(auto-repair)→ ungrounded 论断进"待人工核验"清单。

**并排展示**:左边 OFF 的成品里写着"准确率提升 23%";右边 ON 把这句标成 ungrounded + 整篇 gate 住。这一帧就是 harness 的卖点。

## 1.5 要打印 / 展示的指标

让 demo 输出这些数(都来自 verifier 报告 / `recipe_verification.json`):
- `claims_total` / `claims_grounded` / `claims_flagged`
- **幻觉召回率** = 标出的编造论断数 / 注入的编造论断数(K)。目标 **100%**(确定性 fixture 上应能做到)。
- **grounded 误报率** = 被错标的真实论断数 / 真实论断数。目标 **< 10%**。
- 最终 **ship / rollback 决定 + exit code**(ON 应为 not-shippable / 3)。

> 这几个数也直接回答面试官"你怎么量化 gate 的好坏":召回率 + 误报率,而不是模型自评。

## 1.6 录屏分镜(≤ 60 秒)

1. **(5s)** 一句话标题卡:"LLM 写的综述里混了编造的论断 —— LocalFlow 的 grounding gate 当场拦住。"
2. **(10s)** `seed.py` 铺好 12 个源 + 一份含 K 条假话的综述;终端列出工作区。
3. **(15s)** 跑 **Guard OFF**:成品 `review.md` 滚动,镜头停在那句"准确率提升 23%"(假话被 ship,exit 0)。
4. **(20s)** 跑 **Guard ON**:verifier 表打印 → 那 K 条标 `ungrounded` → "gated: not shippable (exit 3)" → 待人工核验清单。
5. **(10s)** 收尾卡:召回率 100% / 误报率 X% / 决定 = rollback。

工具:`asciinema` 录终端,或 `vhs`(scriptable,出 GIF,可重录、稳定)。GIF 放 README §1 flagship 段落正下方。

## 1.7 诚实措辞红线(写 README / 简历时)

- ✅ 可以写:"a complex, multi-stage, content-heavy generation task"、"catches fabricated/ungrounded claims via a verify-as-gate"、"hallucination recall X% on injected failures"。
- ❌ 不要写:"long-running"、"excels at long tasks"、"survives context overflow"——这些是 Option 2 的招牌,现在写会被面试官翻你 benchmark 第 3 行戳穿。

**做完 Part 1,你的 bar 就满足了,项目可以算落地。**

---

# Part 2 — Option 2:长任务 checkpoint / resume / handoff(选做)

## 2.1 目标与诚实边界

**目标:** 让一个长任务在**中途被打断**(崩溃 / 关机 / 上下文塞满)后能**从存档点续跑**、不重做、不丢已完成的工作;并据此把 benchmark 第 3 行 `context_rot` 从 `gap` 翻成 `mitigated`。

**诚实边界(必须在文档和简历里说清楚):**
- 这是**阶段级(between-stage)** checkpoint/resume:在阶段之间存档/续跑,**不是**在一个半截的阶段内部续跑(mid-stage)。
- "长"指**多阶段、可中断续跑、合理规模**,**不是**"连跑好几天"。
- 一句话定位:"stage-level checkpoint & resume for interruptible multi-stage tasks",别夸成别的。

## 2.2 和现在的区别(一句话回顾)

现在:一个复杂任务在**一次运行里从头跑到尾**;断了 → 从头再来。
做完 Option 2:断了 → `localflow resume <task_id>` → 从最近完成的阶段接着跑。
就像游戏从"无存档、一死重来"变成"有存档点"。

## 2.3 要新增的三样东西(设计 + 放哪)

> 全部在 facade 层(`app/schemas/`、`app/harness/` 的编排模块、`app/cli.py`),**预期零 kernel 触碰**——和 Phase 21 给 taskgraph_runner 加 `stage_hints`(+6 行、不算 kernel touch)是同一类。详见 2.8。

**① CheckpointState(新 schema)** — `app/schemas/checkpoint.py`(facade)
持久化"做到哪了"。建议字段:
```
task_id: str
graph_id / graph_hash: str          # 防止用旧 checkpoint 续跑改过的 graph
completed_stage_ids: list[str]      # 已 PASSED 的阶段
stage_status: dict[str, str]        # stage_id -> passed/failed/skipped
locale: str
updated_at: datetime
```
落盘:`<run_dir>/checkpoint.json`,**每个阶段完成后写一次**(原子写,复用你已有的 atomic write 工具)。

**② resume(续跑入口)**
- 在 `app/harness/taskgraph_runner.py` 的 `run_taskgraph` 加一个 `resume: bool = False`(或 `resume_from: str | None`)。`resume=True` 时:读 `checkpoint.json` → 校验 `graph_hash` 一致 → **跳过 `completed_stage_ids` 里的阶段**(它们的产物已在 `stages/<id>/`)→ 从第一个未完成阶段继续。
- 复用已有的 `replay_from_stage`(Phase 15 原语)思路:它已经会"从某阶段往后处理 + 处理 rollback manifest",resume 是它的"前向、复用已完成产物"版本。
- 新 CLI:`localflow resume <task_id> [--yes]`(`app/cli.py`,facade)。

**③ handoff.md(交接单)**
一个 renderer:读 `checkpoint.json` + `taskgraph.json` + `recipe_verification.json` → 产出 `<run_dir>/handoff.md`,三段:
- **Done** — 已完成阶段 + 各自产物路径
- **Remaining** — 未跑阶段
- **Blocked** — verifier 失败 / gate 拦住的项 + suggested_hint

## 2.4 长任务 recipe 变体(让 resume 有意义)

现在的 `literature_review_pack` 把"总结所有源 + 合成"压在一个 `s2_synthesize` 阶段里——阶段太少,resume 没东西可演示。做一个变体 `literature_review_pack_long.yaml`,**把逐源总结拆成独立阶段**:
```
stages:
  - s1_organize            (rule)
  - s_summarize_paper_01   (llm)   # 每源一个阶段
  - s_summarize_paper_02   (llm)
  - ...
  - s_summarize_paper_12   (llm)
  - s_synthesize           (llm)   # 合成
verifiers: [claim_grounding_verifier, ...]   # gate 不变
```
这样任务变成 ~14 个阶段、真的"长",中断/续跑也看得见(能看到它不重做前面已完成的论文总结)。

## 2.5 中断-续跑 Demo 怎么跑

```bash
# 1) 起一个长任务,跑到一半手动 kill(例如总结完第 6 篇时 Ctrl-C / kill -9)
.venv/bin/localflow pack run literature_review_pack_long --workspace ./papers
# ...总结到 paper_06... ^C

# 2) 看 checkpoint:应显示 completed = [s1_organize, ...paper_01..06]
cat .localflow/runs/<task_id>/checkpoint.json

# 3) 续跑:从 paper_07 接着,跳过 1..6
.venv/bin/localflow resume <task_id> --yes

# 4) 证明 1..6 没重做:看 trace 里 paper_01..06 没有新的 action.start;
#    或看 stages/paper_01..06/ 的产物时间戳早于续跑时刻
.venv/bin/localflow trace summary <task_id>

# 5) handoff 单
cat .localflow/runs/<task_id>/handoff.md
```
**对照(harness 价值):** 朴素 agent 没有 checkpoint —— kill 后重启 = 12 篇全部重新总结(浪费时间/token)、或直接丢失进度。录屏把"我们的:从 paper_07 续上"和"朴素的:从 paper_01 重来"并排放。

**录屏分镜(≤ 60s):** 起任务 → 总结到第 6 篇 → **kill** → `cat checkpoint.json`(看到 6 个已完成)→ `resume` → 镜头停在"skipping paper_01..06, resuming at paper_07" → 跑完 → `handoff.md`。

## 2.6 测试怎么写(对齐你的测试惯例)

`tests/test_checkpoint_resume.py`:
- [ ] 每个阶段完成后 `checkpoint.json` 被写、内容正确(completed 列表递增)。
- [ ] `resume` 跳过 completed 阶段:不重新调用这些阶段的 planner/executor(mock 计数为 0)。
- [ ] **等价性(最关键):** "跑到一半 kill + resume" 的最终产物,和"一次跑完"的最终产物**一致**(同样的 `review.md` / gate 结果)。
- [ ] `graph_hash` 不一致时 resume 拒绝(防止用旧 checkpoint 续跑改过的 recipe)。
- [ ] `handoff.md` 的 Done/Remaining/Blocked 内容正确。
- [ ] 确定性:不需要 LLM key 的路径用 mock/stub skill 跑(参考你现有的 `--no-llm` / stub registry 做法)。

## 2.7 跑完后:更新 benchmark + README(诚实地翻牌)

这是 Option 2 回报最大的部分——**"我发现一个 gap,然后把它填了"**:
- 在 `app/eval/failure_modes` 加一个 `context_rot` 的 ON/OFF 场景:**OFF** = 重启丢状态(预算内跑不完 / 重做)→ ❌ ships;**ON** = resume 续跑、完成 → ✅ caught。
- 把 README §3 表第 3 行从 `❌ ships / gap (honest)` 改成 `✅ caught / mitigated (stage-level)`。
- **诚实标注**保留:写明这是 **stage-level、可中断续跑**,不是"跑好几天"。把 honesty note 从"这是 gap"改成"this is stage-level resume, not mid-stage / not multi-day"。
- 简历叙事:"我给自己的 harness 做了 failure-mode benchmark,它诚实暴露了一个 context-rot gap;随后我用 stage-level checkpoint/resume 把它补上,benchmark 第 3 行从 gap 翻成 mitigated。"——比一开始就 6/6 更显工程成熟度。

## 2.8 §10.7 注意事项

- 预期 **零 kernel 触碰**:CheckpointState 是新 schema(facade);resume 是 `taskgraph_runner` + `cli` 的编排扩展(像 Phase 21 加 kwarg);handoff 是 renderer。`app/harness/{executor,verifier,rollback}.py` 和 `localflow_kernel/*` 应保持 byte-identical。
- 若实现中发现**必须**动 kernel(理论上不该),按你的纪律:开 issue → 写 `docs/PHASE_38_DESIGN.md` → 在 `docs/PHASES.md` 登记 ledger 行。
- 跑完后维护文档一致性:`docs/PHASES.md` 加 Phase 38 条目;README §10.7 与 §14.4 的 ledger 数字保持一致(顺手修掉 §14.4 现在还写着的 stale `4/41`)。

---

# Part 3 — 决策与顺序

1. **先做 Option 1。** 精确匹配你"落地"的 bar、复用现有 flagship、一个周末能出 demo + 录屏。措辞守住"复杂任务 ≠ long-running"即可,简历上立刻能用。
2. **Option 2 只在两种情况下做:** 你特别想要"长任务"这块招牌,或时间充裕想加分。它的核心回报是"发现 gap 并填上"的面试叙事,但别为它拖慢落地。
3. **永远的红线:** benchmark 怎么标的,简历就怎么说。

---

## 附:两条路线的验收清单

**Option 1 done(可写进简历):**
- [ ] `examples/literature_review_pack/seed.py`(+`--check`)铺复杂源集 + 注入 K 条假话的 fixture
- [ ] 一条命令复现;gate 召回率 100%、误报率 < 10%
- [ ] `literature_review_pack_nogate.yaml` 对照;ON = exit 3 + 转人工,OFF = 静默 ship
- [ ] ≤ 60s 录屏(GIF 进 README §1)
- [ ] 真 LLM 模式跑一份产物存 `docs/test_artifacts/`
- [ ] README/简历措辞:complex multi-stage,**非** long-running

**Option 2 done(才能 claim 长任务):**
- [ ] `CheckpointState` schema + 每阶段落盘 `checkpoint.json`
- [ ] `localflow resume <task_id>` 跳过已完成阶段、从断点续跑
- [ ] `handoff.md`(Done/Remaining/Blocked)
- [ ] `literature_review_pack_long.yaml`(逐源拆阶段)
- [ ] 中断-续跑 demo + 录屏 + 对照朴素 agent
- [ ] `tests/test_checkpoint_resume.py`(含"kill+resume 等价于一次跑完")
- [ ] benchmark 第 3 行翻成 mitigated(stage-level)+ 诚实标注
- [ ] 零 kernel 触碰(或按 §10.7 登记)；修掉 README §14.4 的 stale `4/41`

---

*遵循 CLAUDE.md rule F + §10.7:演示与 claim 必须与 benchmark 一致;任何 kernel 边界变更须经 issue + 设计文档 + ledger 登记。*
