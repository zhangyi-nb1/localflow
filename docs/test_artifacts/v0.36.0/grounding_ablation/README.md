# Grounding-gate ablation — R3 evidence (2026-06-22)

Harness optimization log **R3** (`docs/HARNESS_OPTIMIZATION_LOG.md`). Goal:
back the failure-mode benchmark's row 2 (`false_completion`) with a guard
ON/OFF ablation of the flagship claim-grounding gate, on the scaled
literature-review demo (12 sources / 19 claims / 6 fabricated + 1 hard case).

Three distinct, real data points — and one honest finding about *where* the
ablation is valid.

## 1. Deterministic gate ablation — the clean instrument

`seed_check_deterministic.log` = `python examples/literature_review_pack/seed.py --check`.
No API key needed; uses the lexical judge. This is the reproducible-by-anyone
ablation signal because it runs the grounding gate directly on the **dirty
seeded review** (it is never regenerated).

| metric | value |
|---|---|
| claims total | 19 |
| hallucination recall | **6/6 = 100%** (all planted fabrications flagged) |
| grounded false-positive rate | **0/12 = 0%** |
| hard-case contradiction (92%→29%, same vocabulary) | **MISSED by lexical** (documented blind spot; caught only by the LLM judge) |
| gate verdict | `g.passed == False` → **not-shippable → exit 3** + auto-repair in a real pack run |

`seed.py --check` itself exits **0** because it is a *self-test*: exit 0 means
"the gate behaved as designed" (failed on the fabrications, recall 1.0, fpr <
0.10, hard case is the known lexical blind spot). The `(exit 3)` in the
scorecard is the gate's **production** decision, not the self-test's code.

**Guard OFF** = `recipes/literature_review_pack_nogate.yaml` deliberately omits
`claim_grounding_verifier` (`grep -c claim_grounding_verifier` on the OFF run
log = `0`). The same 6 fabricated claims ship unflagged.

## 2. Live-LLM gate on genuine synthesis output

`pack_run_gate_ON_liveLLM.log` = `localflow pack run literature_review_pack -w <ws> -y`
with a live key. The synthesis stage actually ran (~103 s, `judge llm`) and
produced a genuinely grounded review; the gate passed it **8/8 grounded (ratio
1.00)**. Evidence that the gate does **not** false-positive on a real model's
grounded output. (The LLM judge is also what catches the §1 hard-case
contradiction the lexical judge misses — see `../v0.35.0/.../REPORT.md` §⑧.)

## 3. Honest finding — the end-to-end `pack run` exit code does NOT isolate the gate

`pack_run_nogate_OFF_liveLLM.log` exited 3, but on
`deliverable_completeness_verifier` (2/3 deliverables, LLM synthesis variance) —
**not** on grounding (which is absent in nogate). Meanwhile the ON run exited 0
because the live LLM regenerated a clean review.

Two confounds make the raw `pack run` exit code a bad ablation signal:
1. **Live synthesis regenerates the review**, overwriting the dirty seed — so
   planted fabrications never reach the gate in a keyed run.
2. **`deliverable_completeness_verifier` fires independently** of grounding.

Conclusion: the grounding ablation is valid at the **verifier level**
(`seed.py --check` / the `claim_grounding_verifier` directly), not at the
end-to-end pack exit code. Recorded rather than dressed up as a clean
exit-3-vs-exit-0 contrast (rule F). This is the same build-verify-run lesson as
the original silent-skip bug: run it for real, and measure the variable you
actually control.
