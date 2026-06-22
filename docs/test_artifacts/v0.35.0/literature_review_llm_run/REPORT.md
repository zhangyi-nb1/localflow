# 真实 LLM 跑测 + grounding gate 修复报告

> 日期:2026-05-29 · 基于 v0.35.0 + Option 1 demo · 模型:智谱 **glm-4.5**(临时替代,
> 原因见 §4)· 工作区:`sandbox/lit_llm_on/`(12 篇源 → 真实 `pack run`)

本报告分三部分,对应你的要求:**① 发现的情况 · ② 所做的修改 · ③ 修改后的测试**。

---

## ① 发现的情况

### 发现 1(主)—— flagship 的 grounding gate 在真实 `pack run` 里**静默跳过**

第一次真跑 `localflow pack run literature_review_pack`(glm-4.5)结果:

| 阶段 / 校验 | 结果 |
|---|---|
| s1_organize | ✅ 12 篇分进 `papers/`+`notes/`+`misc/` |
| s2_synthesize (agent) | ✅ 写出 `review.md` + `SOURCES.md`(论断准确,无幻觉) |
| deliverable_completeness | ✅ PASS |
| source_ledger | ✅ 12 条引用 resolve |
| **claim_grounding_verifier** | ❌ **SKIPPED** — "no source fragments under summaries/" |

**根因**:`load_source_fragments()` 只 glob `summaries/*.md|txt`。真实 run 里 agent 产出
`review.md`+`SOURCES.md`,**从不产出 `summaries/`**;源论文被 folder_organizer 分到了
`papers/`。于是 grounding pool 为空 → gate 跳过。

**为什么之前没发现**:确定性 demo(`seed.py --check`)**预置了 `summaries/`**,把这个缺口
完全掩盖了。后果很严重:**在真实端到端跑里,guard ON 与 OFF 无法区分(两边 gate 都跳过)
—— 整个 verify-as-gate flagship 实际上不触发。** 这正是该在写进简历前抓出来的硬伤。

### 发现 2(次,由修复后测试引出)—— gate 对真实综述里的**非事实句误报**

修好发现 1 后,用生产路径(LLM judge / glm-4.5)在真实 review 上跑 ablation:

- **干净 review(glm-4.5 产出,无幻觉)**:gate **FAIL**,27/35 grounded,ratio **0.77 < 0.80**。
  被误标的 8 条**不是幻觉**,而是综述的**建议 / 展望 / 元陈述**,例如:
  - "Based on this synthesis, several research directions emerge:"
  - "Extend checkpoint-rollback mechanisms to handle the six identified failure modes"
  - "The findings collectively point toward a future where agents can reason effectively…"
  - "All findings are grounded in the source materials documented in `SOURCES.md`…"

  这些句子**本就不该被当作可溯源的事实论断**。现有 `_is_claimworthy` 已经会过滤
  "This review…" 类 framing,但没覆盖"建议 / 未来工作 / 总结性元陈述"。
  → 这是 **claim-splitter 精度问题**,与发现 1 的修复无关,属新发现。

---

## ② 所做的修改(仅发现 1;均 eval 层,零 kernel)

### 修改 A — grounding pool 回退到真实源文档
`app/eval/grounding/engine.py::load_source_fragments`:
- 先试 `summaries/*`(确定性 demo 用),**为空时回退**到组织后的源文档
  `papers/ · notes/ · sources/ · 工作区根`(`*.md|txt`)。
- **排除**生成物 / 索引:`review.md / SOURCES.md / README.md / summary.md /
  literature_review.md / review_queue.md / index.md`(防止论断"自我溯源"或对着
  folder_organizer 生成的 index 溯源)。

### 修改 B — 修正校验器跳过文案
`app/eval/recipe_verifiers/grounding.py`:skip detail 从 "no source fragments under
summaries/" 改为 "no source documents (summaries/, papers/, notes/, sources/)"。

> 设计取舍:**fallback-only** —— `summaries/` 存在时行为完全不变,所以确定性 demo
> 与既有测试不受影响。

---

## ③ 修改后的测试

### 单元测试(确定性)
- 新增 `tests/test_grounding_fragments.py`(3):
  - `summaries/` 存在 → 优先用它(忽略 papers/);
  - 无 `summaries/` → 回退到 `papers/`+`notes/`,且 `review.md`/`SOURCES.md`/`index.md` 被排除;
  - 空工作区 → 0 fragments。
- 更新 `tests/test_recipe_verifier_grounding.py::test_skips_when_no_sources`:行为不变
  (只有 review.md → 0 fragments → 仍跳过),改的是断言的提示文案。
- **全量套件:1123 passed / 29 skipped / 0 failed**(较修复前 1120 +3),ruff check/format 全绿。
- 确定性 demo `seed.py --check` 仍 exit 0(召回 6/6、误报 0/12,行为未变)。

### 集成 / 真实 LLM 验证(glm-4.5)
| 验证 | 修复前 | 修复后 |
|---|---|---|
| `load_source_fragments(真实工作区)` | **0** fragments → gate skip | **12** fragments(papers/+notes/)→ gate **fires** ✅ |
| LLM judge · 干净 review | 不触发 | gate **FAIL** 27/35(0.77)— 见发现 2 ⚠️ |
| LLM judge · 注入 2 条幻觉 | 不触发 | **2/2 被抓** ✅(`Framework Helios…37%…SWE-bench-Live`、`Okafor and Reyes (2023)…`)|

**核心目标达成**:修复让 gate 在真实 run 上真正运行,并且 **LLM judge 精确抓出注入的捏造论断
(2/2)** —— verify-as-gate 的价值在真实路径上得到证明。

证据文件(本目录):`review_llm.md`(glm-4.5 综述)、`SOURCES_llm.md`、
`pack_run_guard_on.log`(首跑,gate SKIPPED)、`llm_judge_ablation.log`(修复后 ablation)。

---

## ④ 临时改用智谱(Zhipu)的接入说明

- OpenAI 网关本周对所有模型 `/chat/completions` + `/responses` 持续 503(疑似额度/上游),
  故按你授权临时用 offerclaw 的 `ZHIPU_API_KEY`。
- 智谱 v4(`https://open.bigmodel.cn/api/paas/v4`)**raw key 直接当 Bearer 即可**(无需 JWT),
  localflow 的 openai client 用命令行 env 覆盖直接驱动,**未改你的 `.env`**。
- 模型可用性:**glm-4.5 / glm-4-plus / glm-4-air** 支持 forced tool-call(本次用 glm-4.5);
  `glm-4-flash` 不服从强制工具调用、`glm-4.6` 报 400 —— 不要用这两个。
- **下周切回 OpenAI 时**:`.env` 里 `LOCALFLOW_OPENAI_MODEL=GPT-5.3-Codex-Spark` 是大写,
  代理登记的是小写 `gpt-5.3-codex-spark`,建议改成小写以免 404。

---

## ⑥ 修复 C(发现 2:非事实句误报)—— 已做

两处改动(均 eval 层,零 kernel):
- `app/eval/grounding/engine.py::_is_claimworthy`:新增 `_is_nonfactual()` —— 过滤
  "建议 / 未来工作 / 元陈述 / 总结"类**非事实句**(lead-ins + "**Label**: 祈使动词")。
  **关键护栏:含任何数字的句子永不过滤** —— 数字是可核验断言(真实发现 *或* 捏造统计),
  必须进闸门。
- 精炼 `_LLM_JUDGE_SYSTEM`:非事实句(建议/局限/元陈述)判 verdict=true(无可捏造),
  只对**具体事实断言**(实体/数字/发现)判 grounding。
- 新增 `tests/test_grounding_claims.py`(3,确定性):非事实句被丢、事实句(含数字句)保留。

**修复 C 效果(真实 review,glm-4.5 judge)**:claims 35→**28**(过滤 7 条非事实句),
干净 review 从 **0.77 → 0.93**,误报 8 条 → **2 条**;注入 2 条幻觉仍 **2/2 被抓**。

**一个关键发现**:剩下的 2 条 flag 里,`"...76% of claims may still lack grounding"` 的
**76% 是 glm-4.5 自己编进综述的统计**(源里没有)—— **gate 正确抓到了模型自己的幻觉**。
也就是说这篇"干净" review 其实不干净,gate FAIL 是**对的**。另一条是 synthesis 泛化句的
**真·误报**(LLM judge 偏严,残留)。结论:`max_ungrounded=0`(零容忍)+ 一条真捏造 + 一条
误报 → FAIL。这跟原来的"gate 根本不触发"已是两个量级的问题。

## ⑦ 延迟分析与提速(回答"智谱是不是不行")

**智谱 API 正常**,trivial 调用 0.56s。慢的根因是 **重推理模型 × 大上下文 × 串行多调用**:

| | glm-4.5 | glm-4-air + 并行(8) |
|---|---|---|
| 单次 grounding 调用 | **21.9s** | **1.7s**(~13×) |
| 干净 review(28 claims) | ~613s(~10 min) | **7s**(~85×) |
| 注入幻觉抓取 | 2/2 | **2/2**(一致) |

- 单次慢因:claim + 12 段源(~10K 字符)+ 强制 tool schema → glm-4.5 reasoning 长推理。
- air 当 judge 精度同档(0.93,抓 76% + 注入 2/2),只是把那条 synthesis 误报换成了另一句
  泛化句 —— **换模型并不能消掉残留误报,这是 LLM judge 对泛化句偏严的固有现象**。

## ⑤ 状态与下一步(更新)

| 项 | 状态 |
|---|---|
| 发现 1(gate 不触发)+ 修复 A/B | ✅ 已修、已验证(gate fires,真实 run 抓幻觉 2/2) |
| 发现 2(非事实句误报)+ 修复 C | ✅ 已修(0.77→0.93,误报 8→2) |
| 全量测试零回归 | ✅ **1126 passed** / 29 skipped / 0 failed |
| 延迟 | ✅ 定位 + 验证提速(air+并行 20min→~13s) |

**残留 / 可选下一步(均非 blocker)**:
1. **synthesis 泛化句 1 条误报** —— LLM judge 固有偏严,换模型/调 prompt 收益递减,建议**接受为已知小限制**(诚实标注)。
2. **生产 gate 仍串行** —— 若要真实 run 的 gate 不卡,值得把 `ground_review` 改成并行调用(本次已验证 ~85× 提速)。这是一个独立的 eval 层小改动,可单独做。
3. **gate 策略 `max_ungrounded=0`(零容忍)** —— 任何 1 条 flag 即 FAIL。是否对真实 review 放宽到小 N,是产品取舍,留给你定。
4. **judge 模型选型** —— 真实跑建议 judge 用 `glm-4-air`(快 13×、精度同档),综述生成可保留 glm-4.5。

## ⑧ 真实模型 gpt-5.4-mini 端到端验证(2026-05-31,OpenAI 额度恢复后)

OpenAI 网关恢复、用户把 `.env` 模型设为 `gpt-5.4-mini`。在用户**真实生产栈**上重跑完整
flagship,确认前述修复全部成立。先 smoke:连通 + forced tool-call 均 OK(2.2s/call),
client 配置干净(`reasoning_effort` 未设即不发送,无冲突)。

**完整 `pack run literature_review_pack`(gpt-5.4-mini,en-US,53s)**:

| 阶段 / 校验 | 结果 |
|---|---|
| s1_organize / s2_synthesize | ✅ PASSED(38s / 15s)|
| deliverable_completeness | ✅ all 3 present |
| **claim_grounding_verifier** | ✅ **PASS — 33/33 grounded, ratio 1.00 (judge llm)** |
| source_ledger_verifier | ⚠️ skipped(见下「小发现」)|

**注入幻觉 ablation(gpt-5.4-mini judge,并行,8s)**:dirty review 35 claims → gate **FAIL**
33/35,**注入 2 条全部被抓(2/2)、无其它误报**。

→ **教科书级 ablation:干净 review ship(1.00 PASS)/ 脏 review rollback(精确抓 2 条)。**
Fix C 在 gpt-5.4-mini 上完全成立 —— 无 synthesis 误报。这是最初想要的"真实栈上 flagship
端到端 work"的证据。

**小发现(非 blocker)**:`source_ledger_verifier` skip ——
gpt-5.4-mini 的 `SOURCES.md` 把路径写成 `## papers/grounding.txt` 标题,而非反引号内联
`` `papers/grounding.txt` ``,校验器找不到 path 引用 → 保守跳过(不 fail)。路径其实都在,
只是格式不同。可选修:微调 agent prompt 让它写反引号路径引用,或让 source_ledger_verifier
也认 `## <path>` 标题。

**证据**:`review_gpt54mini.md`、`SOURCES_gpt54mini.md`、`pack_run_gpt54mini_guard_on.log`。

**结论**:35→36→37 的 flagship,经本次修复(A/B 让 gate 在真实 run fires、C 降误报),在
gpt-5.4-mini 真实栈上达成**完整可演示的 verify-as-gate**:干净 PASS、脏 FAIL、抓幻觉 2/2、
零误报、53s 一把跑完。下周保持 gpt-5.4-mini 即可;若用 codex 等 reasoning 模型,再按需开
`LOCALFLOW_OPENAI_REASONING_EFFORT`。


## ⑩ 收尾两项（Task 1 + Task 2，均 eval 层零 kernel）

**Task 1 — `source_ledger_verifier` 认 heading 形式引用**
`app/eval/recipe_verifiers/structural.py`：新增 `_LEDGER_HEADING_RX`，除反引号
`` `papers/x.txt` `` 外，也认 markdown 标题路径 `## papers/x.txt`（纯路径才算，
`## Research Papers` 这类散文标题不算）。在真实 gpt-5.4-mini 的 SOURCES.md
（标题路径形式）上：之前被判 "no path citations" 而 skip，现在
**all 12 citations resolve → PASS**。测试：`tests/test_source_ledger_headings.py`（4）。

**Task 2 — `ground_review` 并行判定**
`app/eval/grounding/engine.py`：每条 claim 的 judge 调用改为 `ThreadPoolExecutor`
（`max_workers=8`，顺序保留、各 claim 独立 → 结果与串行一致；`max_workers<=1` 回退串行）。
LLM judge 是 I/O bound，真实 review 的 gate 从分钟级降到秒级（参§⑦ air 并行 7s/28 claims）。
测试：`tests/test_grounding_parallel.py`（并行==串行 + 每 claim 恰好一次调用）。

**验证**：5 个新测试全过；真实 SOURCES.md · source_ledger PASS（12/12）；全量 pytest
**rc=0 / 0 failed**；ruff check + format 全绿。

> 过程诚实记：本轮遇到工具输出渲染抖动 + 一次 `git checkout` 误撤了 Task 1，随后干净
> 重施；文档字符串里的 `\`` 转义（W605）改用注释避开。最终状态经 rc/grep/inspect 反复核实。
