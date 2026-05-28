# Phase 36 — Flagship vertical: verifiable literature review (claim-level grounding gate)

> 状态：**设计锁定 / §10.7 预审：零 kernel 触碰**　·　日期：2026-05-29　·　分支：`main`（v0.33.x-dev → v0.34.0）
>
> 本文是 Phase 36 的设计 + 验收文档。它把 `docs/PHASE_35_PLAN.md` §5 的 flagship
> 规格落成可实现的工程契约。遵循纪律「先有验收再实现」。

---

## 1. 一句话目标

把 LocalFlow 的 verify-as-gate 差异化（PROJECT_DIRECTION §7 第 7 条）落成一个能跑、能演示、
能用 eval 数字证明的 flagship：**带出处核验的文献综述**——一批来源（论文 PDF / 文本）经
harness 约束的生成产出一份综述，再由一个 **claim-level grounding 闸门** 逐条判定综述里的每条
论断能否追溯到某个来源片段；追溯不到的被标记并进入"待人工核验"清单，无出处比例超阈值则
**产物判为不可交付 + 触发 repair / rollback**。

---

## 2. 现状复用盘点（基于源码，rule D）

Phase 36 **不造新机制**，而是组合现有的：

| 复用的现有件 | 文件 | Phase 36 怎么用 |
| --- | --- | --- |
| Recipe verifier 注册表 + `run_all` | `app/eval/recipe_verifiers/_registry.py` | 新增 `claim_grounding_verifier`，`@register` 即插 |
| Recipe verifier 契约 | `app/eval/recipe_verifiers/_schema.py`（`RecipeVerifierContext` / `RecipeVerifierVerdict` / `RecipeVerification`） | grounding 闸门产出 `RecipeVerifierVerdict(passed, suggested_hint)` |
| Recipe 闸门 + exit code 3 | `app/cli.py`（`recipe_verification.json` + 质量失败 exit 3） | grounding 失败 → pack run exit 3，已有 |
| Recipe 自动 repair | `app/harness/recipe_repair.py`（`run_recipe_repair` + `repair_policy` + `repair_target_map`） | grounding 失败带 hint → replay 生成阶段，已有 |
| LLM judge helper | `app/agent/judge.py`（`judge()` → `JudgeVerdict`，无 key 时 `get_default_client_or_none()` 返回 None） | 生产路径的 per-claim 判定 |
| 最接近的现有 grounding | `summary_grounding_verifier`（`app/eval/recipe_verifiers/semantic.py`，**整篇级**，非逐条） | Phase 36 把它升级到 **claim-level** |
| Sources ledger schema | `app/schemas/source_ledger.py`（`SourceLedger` / `SourceEntry`，file-level） | 综述的**来源清单**输入；claim-level 证据另存（见 §5） |
| Recipe 系统 | `recipes/*.yaml` + `examples/<pack>/seed.py` | 新增 `literature_review_pack`（组合，非新原语） |

**关键判断**：grounding 是 **execute 之后的验证**，不是新动作。生成那一环复用现有
INDEX / `agent` skill / `PYTHON_COMPUTE`。所以 **不需要新 `ActionType`，不碰 kernel**。

---

## 3. 角色边界（呼应 PHASE_35_PLAN §2.2）

| 角色 | Phase 36 里是谁 | 边界 |
| --- | --- | --- |
| Planner / 生成器 | recipe 的 summarize + synthesize 阶段（skill / agent / compute） | 只产 typed plan + 经 dry-run/approval；产物落 workspace |
| Judge | **新的 claim-grounding 引擎** | 判决进 recipe 闸门；无 key 时退化到确定性 lexical 判定（见 §4.3） |

生成可以"不够聪明"——闸门 + repair + rollback 才是让它可用、可审计、可恢复的东西。

---

## 4. Grounding 引擎设计（最核心，36.3）

放在 **`app/eval/grounding/`**（application-eval 层，**不** 经 `localflow_kernel` 再导出，
所以不进 kernel 边界）。三个纯函数 + 一个可注入的 judge：

### 4.1 Claim 拆分（纯函数，确定性）

`split_claims(review_markdown: str) -> list[Claim]`

- 把综述 markdown 拆成候选论断：散文段落的句子 + 列表项。
- 过滤掉标题、代码块、表格分隔、空行、纯 meta 行（如 "## References"）。
- 每个 `Claim` 带 `claim_id`（稳定序号）+ `text` + `source_line`（行号，便于回指 trace）。
- 完全确定性、可单测、无 LLM。

### 4.2 来源片段加载（纯函数）

`load_source_fragments(workspace, source_glob) -> list[SourceFragment]`

- 读 per-source 摘要（如 `summaries/*.md`）+ 可选原文片段。
- 每个 `SourceFragment` 带 `source_id`（= 文件相对路径）+ `text`。

### 4.3 可注入 claim judge（Protocol）

```python
class ClaimJudge(Protocol):
    def judge_claim(self, claim: Claim, fragments: list[SourceFragment]) -> ClaimVerdict: ...
```

两个实现：

| 实现 | 何时用 | 行为 |
| --- | --- | --- |
| `LLMClaimJudge` | 有 `ANTHROPIC_API_KEY` 时（生产路径） | 对每条 claim 调 `judge()`：问"这条论断能否追溯到下列来源片段之一？若能，给出 source_id + 支撑引文"。准确但非确定性。 |
| `LexicalClaimJudge` | 无 key 时（确定性回退）**且** eval 基线 | salient-term overlap：claim 的关键 token（去停用词 + 数字/实体）与某来源片段的 token 重合度 ≥ 阈值即 grounded，并记录最佳匹配 source_id。crude 但**确定性、可复现、无需 key**。 |

诚信纪律（rule F）：文档明说 lexical 路径是**回退 + eval 基线**，不是"真正理解"；生产 grounding
用 LLM judge。两条路径都产出同形 `ClaimVerdict`。

### 4.4 闸门判定（纯函数）

`evaluate_grounding(verdicts, policy) -> GroundingGateResult`

- `grounded_ratio = grounded / total`
- gate `passed = grounded_ratio >= policy.min_grounded_ratio AND ungrounded_count <= policy.max_ungrounded`
- 失败时产 `suggested_hint`："regenerate the review so every claim cites a source fragment;
  the following N claims have no traceable source: …"（喂给 recipe repair）
- 产 **待人工核验清单**：所有 ungrounded claim 的 `claim_id` + `text` + `source_line`。

`GroundingPolicy` 默认：`min_grounded_ratio=0.8`, `max_ungrounded=0`（综述场景对幻觉零容忍偏严，
阈值可在 recipe 里覆盖）。

---

## 5. 产物结构 + evidence bundle（36.5）

pack run 结束后，workspace / run_dir 里有：

| 产物 | 位置 | 内容 |
| --- | --- | --- |
| 综述 | `review.md`（workspace） | 生成的综述正文 |
| per-source 摘要 | `summaries/<source>.md` | 逐篇摘要（grounding 的来源池） |
| **claim grounding 证据** | `<run_dir>/claim_grounding.json` | 每条 claim：text / grounded / source_id / 支撑引文 / judge 类型 |
| **待人工核验清单** | `review_queue.md`（workspace） | ungrounded claims，human-in-the-loop（呼应 No Free Labels） |
| sources ledger | `SOURCES.md` + `source_ledger.json` | 复用现有 file-level ledger |

`ClaimGroundingResult` / `ClaimVerdict` schema 放 `app/eval/grounding/schema.py`
（eval 层，Pydantic `extra="forbid"`，**不** 进 `localflow_kernel`）。

---

## 6. "完成"的定义（验收标准，PHASE_35_PLAN §5.3）

- **产物** = 综述（`review.md`）+ claim grounding 证据（`claim_grounding.json`）+ 待核验清单（`review_queue.md`）。
- **一条论断"通过"** ⟺ 能追溯到 ≥1 个来源片段（有 source_id）；否则 ungrounded。
- **闸门指标**：
  - **幻觉召回率（hallucination recall）** = 被正确标为 ungrounded 的植入幻觉数 / 总植入幻觉数。目标 = 1.0（不漏）。
  - **grounded 误报率（false-positive rate）** = 被误标为 ungrounded 的真实 grounded claim 数 / 总真实 grounded claim 数。目标尽量低。
  - **overall grounded ratio** vs `policy.min_grounded_ratio`。
- **闸门行为**：grounded_ratio 达标 → pack 通过；否则 `RecipeVerifierVerdict.passed=False` →
  recipe_verification.json 记失败 → exit 3 + （若 repair_policy.enabled）触发 replay 生成阶段。

---

## 7. Flagship recipe + demo（36.1/36.2/36.6）

新增 `recipes/literature_review_pack.yaml`（组合现有 skill，非新原语）：

```
stages:
  s1_organize    — folder_organizer（把来源归类到 sources/）
  s2_summarize   — per-source 结构化摘要 → summaries/*.md（rule/agent）
  s3_synthesize  — 综合成 review.md（agent/llm；failure_policy: skip 无 key 时降级）
verifiers:
  - deliverable_completeness_verifier   # review.md + summaries/ 存在
  - source_ledger_verifier              # SOURCES.md 引用的文件真实存在
  - claim_grounding_verifier            # ★ Phase 36 新增：claim-level 闸门
repair_policy: { enabled: true, max_rounds: 2 }
repair_target_map: { claim_grounding_verifier: s3_synthesize }
```

**Demo（36.6）**：`examples/literature_review_pack/seed.py` 1 命令重建：
- 2-3 个短"论文"文本文件（含明确事实，如"Method A improved accuracy by 12%"）
- 一份**预写的 `review.md`**，其中 1-2 条论断**故意无出处**（植入幻觉，如"Method C reduced cost by 40%"——来源里根本没有 Method C）
- 跑 `claim_grounding_verifier`（确定性 lexical 判定）→ 闸门应**精确抓出**那 1-2 条植入幻觉，
  写进 `review_queue.md`，verdict.passed=False。

这就是简历/面试的钩子："给它一份带编造引用的综述，闸门把编造的那条挑出来并拦下交付。"

---

## 8. eval（36.7）

新增 eval task（`evals/literature_review/` 或现有 eval 目录）+ grounding 引擎单测：

- **grounding 引擎单测**（无需 key，确定性）：`split_claims` 边界、`LexicalClaimJudge` 判定、
  `evaluate_grounding` 闸门、`suggested_hint` 生成。
- **eval task**：用 by-construction 标注的 claim 集（每条预标 grounded/ungrounded）跑确定性
  lexical 引擎，算 **hallucination recall + grounded false-positive rate**，断言达标阈值。
  这给 Phase 37"公开数字"提供可复现的第一个 eval。

诚信：eval 用确定性引擎跑（可复现 / 无 key / CI 可跑）；LLM judge 路径单独标注为生产路径，
其准确率不进 CI 断言（非确定性）。

---

## 9. §10.7 边界论证（预期零 kernel 触碰）

| 改动 | 层 | kernel？ |
| --- | --- | --- |
| `app/eval/grounding/`（引擎 + schema + judge） | application-eval | 否 |
| `app/eval/recipe_verifiers/` 新增 `claim_grounding_verifier` | application-eval | 否 |
| `recipes/literature_review_pack.yaml` | recipe（数据） | 否 |
| `examples/literature_review_pack/seed.py` | 示例 | 否 |
| eval task + 测试 | 测试 | 否 |

- 不新增 `ActionType`（生成复用 INDEX/agent/compute）。
- 不动 `app/harness/executor.py` / `policy_guard.py` / `app/schemas/action.py`。
- 新 schema 放 `app/eval/grounding/schema.py`，**不**经 `localflow_kernel/schemas.py` 再导出 →
  不进 kernel 边界图 → `tests/test_kernel_boundary.py` 保持绿。

**若**实现中发现 grounding 意外需要 kernel 改动（不预期），按 §10.7 停下 → issue + 设计文档
diff + ledger 行 + 用户确认（§H）。当前设计不需要。

---

## 10. 切片计划

| 切片 | 内容 | 验收 |
| --- | --- | --- |
| 36.0 | 本设计文档 | 文件入库（本切片） |
| 36.1/36.2 | `recipes/literature_review_pack.yaml` + summarize/synthesize 阶段跑通（复用 skill）；dry-run 预览产物 | 示例 run + trace |
| 36.3 | `app/eval/grounding/`（split / load / ClaimJudge 双实现 / evaluate）+ `claim_grounding_verifier` 接成闸门 | verifier 接入 recipe；引擎单测 |
| 36.4/36.5 | rollback-on-fail 串联（复用 recipe_repair）+ `claim_grounding.json` evidence + `review_queue.md` 清单 | repair 证据 + 清单文件 |
| 36.6 | `examples/literature_review_pack/seed.py`（含植入幻觉）+ demo 跑通 + 截图 | seed + 本地可观察验证 |
| 36.7 | eval task（hallucination recall / false-positive rate）+ 全套单测 + ledger + commit | eval pass + CI 绿 |

**不在 36 做**（PHASE_35_PLAN §8 减法）：长任务持久化、更多后端、Route A EDIT 动作。

---

## 11. 落地状态（执行追加，2026-05-29）

- **36.0** ✅ 本设计文档。
- **36.3（核心）** ✅ `app/eval/grounding/`（`schema.py` + `engine.py`）：
  `split_claims`（确定性，跳过 heading/code/table/blockquote/HR + 自指框架句过滤）、
  `load_source_fragments`、`ClaimJudge` Protocol + `LexicalClaimJudge`（确定性，salient-term
  overlap，单字母仅大写实体保留）+ `LLMClaimJudge`（生产路径，复用 `app.agent.judge`）、
  `evaluate_grounding`（闸门 + hint）、`ground_review`（编排）。15 引擎单测。
- **36.3 闸门接入** ✅ `app/eval/recipe_verifiers/grounding.py::claim_grounding_verifier`：
  无 key → lexical，有 key → LLM；接进 recipe_verification 闸门 + exit 3 + recipe repair。
  6 verifier 单测（含 autouse fixture 钉死 lexical 判定防 suite 串扰）。
- **36.5 evidence bundle** ✅ verifier 写 `claim_grounding.json`（机器）+ `review_queue.md`
  （待人工核验清单）到 workspace；属验证报告，非 plan action，kernel/rollback 不变。
- **36.4 rollback-on-fail** ✅ 复用现有 recipe repair；recipe `repair_target_map:
  { claim_grounding_verifier: s2_synthesize }` + `repair_policy.enabled`。
- **36.1/36.2 recipe** ✅ `recipes/literature_review_pack.yaml`（组合 folder_organizer +
  agent，非新原语）；`pack list` / `pack describe` 自动发现。无 key 时 synth 阶段 skip →
  闸门 skip（诚实降级）。
- **36.6 demo** ✅ `examples/literature_review_pack/seed.py --check`：植入 2 处幻觉
  （Method C / 未命名 transformer），确定性 lexical 闸门**精确**抓出这 2 条、0 误报、gate FAIL。
  `examples/literature_review_pack/README.md` 说明书。
- **36.7 eval** ✅ `tests/test_grounding_eval.py`：对植入幻觉 ground truth 测
  **hallucination recall = 1.0 + grounded false-positive rate = 0.0**（确定性、可复现、无 key）。
  这是 Phase 37 公开数字的第一个 eval。

**测试**：+23（engine 15 + verifier 6 + eval 2），1070 → 1093，零回归。

**§10.7**：零 kernel 触碰确认——所有改动在 `app/eval/` + `recipes/` + `examples/` + 测试；
新 schema 在 `app/eval/grounding/schema.py`，**不**经 `localflow_kernel` 再导出；
`tests/test_kernel_boundary.py` 保持绿。无新 `ActionType`。

**诚信纪律（rule F）**：lexical judge 文档明标为"确定性回退 + eval 基线"，非语义理解；
生产 grounding 用 LLM judge。两条路径同形 verdict、同一闸门逻辑。
