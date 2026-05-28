# Literature Review Pack — the flagship demo

**Phase 36 / v0.34.0.** The concrete answer to LocalFlow's flagship
positioning: a **verifiable LLM-artifact pipeline** where an independent
**grounding gate** decides ship-or-rollback — not a post-hoc dashboard.

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

The gate's behaviour is reproducible without an LLM — the demo seeds a
review with **two deliberately planted hallucinations** and runs the
deterministic lexical judge:

```bash
python examples/literature_review_pack/seed.py --check
```

Expected: the gate **FAILs** (3/5 claims grounded) and flags exactly the
two fabricated claims —

```
  [c4] ✗ UNGROUNDED   Method C reduced training cost by 40 percent across all datasets.
  [c5] ✗ UNGROUNDED   A transformer architecture achieved state-of-the-art results ...
gate: FAIL  (3/5 grounded, ratio 0.60)
```

`Method C` and the unnamed "transformer architecture" appear in **no**
source — that's the planted fabrication, and the gate catches it with
zero false positives on the three real claims.

## Run the full pack (LLM-backed)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python examples/literature_review_pack/seed.py        # seed sources/
localflow pack run literature_review_pack --workspace examples/literature_review_pack/workspace
```

With a key, the `agent` stage generates the per-source summaries +
`review.md`, and the gate runs the **LLM-as-judge** per claim. Without a
key the synthesise stage degrades to SKIPPED and the gate skips (nothing
to ground) — the deterministic demo above is what proves the gate.

## Artifacts produced

| File | What |
|---|---|
| `review.md` | The synthesised review |
| `summaries/*.md` | Per-source summaries (the grounding pool) |
| `claim_grounding.json` | Machine-readable evidence: every claim + grounded/ungrounded + source + judge |
| `review_queue.md` | Human-review list of ungrounded claims |
| `SOURCES.md` | File-level sources ledger |

## How it's measured

`tests/test_grounding_eval.py` runs the gate against the planted-
hallucination ground truth and asserts **hallucination recall = 1.0**
(catches every fabrication) and **grounded false-positive rate = 0.0**
(never flags a real claim) on the deterministic baseline. These are the
reproducible numbers Phase 37 surfaces publicly.

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
