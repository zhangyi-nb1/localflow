# Literature Review Pack — the flagship demo

**Phase 36 / v0.34.0** (grounding gate) · **Option 1 scale-up**
(complex-task demo, see [`docs/DEMO_AND_LONGTASK_GUIDE.md`](../../docs/DEMO_AND_LONGTASK_GUIDE.md)).
The concrete answer to LocalFlow's flagship positioning: a **verifiable
LLM-artifact pipeline** where an independent **grounding gate** decides
ship-or-rollback — not a post-hoc dashboard.

> Honesty (rule F): this demo runs a **complex, multi-stage,
> content-heavy** generation task. It does **not** claim to be
> *long-running* — that needs stage-level checkpoint/resume (Option 2 /
> Phase 38, not yet built).

## The problem this answers

Ask an LLM to synthesise 20 papers into a review and you get fluent,
citation-shaped prose — but you can't tell which "Study X found a 12%
improvement" is real and which is fabricated. In 2025–26 even 3–5 expert
reviewers miss fabricated citations in accepted papers
([PHASE_35_PLAN.md §4](../../docs/PHASE_35_PLAN.md)). RAG doesn't fix it:
given the right context, models still misread, merge conflicting
evidence, or won't admit they don't know.

## What LocalFlow does about it

The review is split into individual **claims**; each claim is checked
against the per-source summaries it should trace to. Claims with no
traceable source are flagged into a **human-review queue**, and if too
many are ungrounded the artifact is **gated as not-shippable** and the
synthesise stage is re-run (auto-repair) or rolled back.

```
sources ──► summarise ──► synthesise review ──► GROUNDING GATE ──┐
            (LLM)          (LLM)                  per-claim:       │
                                                  grounded?       ▼
                                            ship  ◄─ pass | fail ─► rollback
                                                                  + review_queue.md
```

## Run the deterministic demo (no API key needed)

The gate's behaviour is reproducible without an LLM. The seed plants a
**complex** review — **12 sources**, **19 claims**, of which **6 are
fabricated** (four hallucination classes) plus **1 hard case** — and runs
the deterministic lexical judge with a quantified scorecard:

```bash
python examples/literature_review_pack/seed.py --check
```

Expected scorecard:

```
  hallucination recall   : 6/6 = 100%   (target 100%)
  grounded false-pos rate: 0/12 = 0%    (target < 10%)
  hard-case contradiction: MISSED (expected — lexical blind spot); caught only by the LLM judge
  decision               : ROLLBACK / not-shippable (exit 3)
```

The 6 fabrications each introduce a **novel entity / number / citation**
that appears in no source (e.g. `Framework Helios`, `Okafor and Reyes
(2023)`, `the Atlas planner`) — the lexical judge catches every one with
zero false positives on the 12 real claims.

### The hard case — an honest limitation

Claim `c11` rewrites a real source's "**92%** of corrupted runs" as
"**29%**". The salient terms still overlap the source, so the
**deterministic lexical baseline keeps it** (a false negative). This is
deliberately on the table: a same-vocabulary numeric contradiction needs
the **LLM judge** (real-LLM mode below), not lexical overlap. We pin this
in `tests/test_grounding_demo.py` rather than pretend the baseline is
perfect.

## Guard ON vs OFF — the ablation (what the gate buys you)

Two recipes, identical except for the grounding gate:

```bash
export ANTHROPIC_API_KEY=sk-ant-...           # or configure .env (auto-loaded since v0.34.1)
python examples/literature_review_pack/seed.py            # plant sources/

# Guard OFF — naive agent: fabricated claims ship silently, exit 0
localflow pack run literature_review_pack_nogate --workspace examples/literature_review_pack/workspace

# Guard ON — flagship: gate flags ungrounded claims, exit 3, routes to review_queue.md
localflow pack run literature_review_pack        --workspace examples/literature_review_pack/workspace
```

Put the two `review.md` outputs side by side: the OFF run ships the
fabricated lines as fact; the ON run flags them and refuses to ship. That
one frame is the harness's value proposition.

With a key, the `agent` stage generates the summaries + `review.md` and
the gate runs the **LLM-as-judge** per claim (which also catches the hard
case). Without a key both synthesise stages degrade to SKIPPED — the
deterministic demo above is what proves the gate.

## Record the 60-second demo

A scriptable [`vhs`](https://github.com/charmbracelet/vhs) tape ships next
to this README so the GIF is reproducible:

```bash
vhs examples/literature_review_pack/demo.tape   # -> demo.gif
```

## Artifacts produced

| File | What |
|---|---|
| `review.md` | The synthesised review |
| `summaries/*.md` | Per-source summaries (the grounding pool) |
| `claim_grounding.json` | Machine-readable evidence: every claim + grounded/ungrounded + source + judge |
| `review_queue.md` | Human-review list of ungrounded claims |
| `SOURCES.md` | File-level sources ledger |

A real-LLM run's artifacts (review.md / SOURCES.md / claim_grounding.json
/ trace.jsonl) should be archived under
`docs/test_artifacts/<version>/literature_review_llm_run/` and linked from
the top-level README §1.

## How it's measured

- `tests/test_grounding_eval.py` — recall = 1.0 / FP rate = 0.0 on the
  lexically-detectable fabrications (the reproducible public number).
- `tests/test_grounding_demo.py` — the demo is genuinely complex
  (≥12 sources/claims), all four hallucination classes are flagged, the
  hard case is a documented lexical blind spot, and the guard ON/OFF
  recipes differ only by the grounding gate.

## Design + boundaries

- Engine: [`app/eval/grounding/`](../../app/eval/grounding/) (pure,
  unit-tested, no LLM required for the lexical path).
- Gate: [`app/eval/recipe_verifiers/grounding.py`](../../app/eval/recipe_verifiers/grounding.py)
  (`claim_grounding_verifier`).
- Design + acceptance: [`docs/PHASE_36_DESIGN.md`](../../docs/PHASE_36_DESIGN.md).
- **§10.7: zero kernel touch.** Grounding is post-execute verification —
  no new `ActionType`, no executor / policy_guard / kernel-schema edits.

> Honesty (rule F): the lexical judge is a crude reproducible baseline
> ("do the claim's distinctive terms appear in some source?"), **not**
> semantic understanding. Production grounding uses the LLM judge. Both
> emit the same verdict shape; the gate logic is identical.
