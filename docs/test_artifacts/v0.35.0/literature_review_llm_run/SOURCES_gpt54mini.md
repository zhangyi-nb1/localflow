# SOURCES

## papers/grounding.txt
- **Topic:** Grounding and citation requirements in generated reports
- **Key claim:** Requiring every report claim to cite a source cut unsupported claims by 24%.
- **Use in synthesis:** Supports a grounded-review approach with explicit sourcing.

## papers/humaneval.txt
- **Topic:** Code synthesis evaluation benchmark
- **Key claim:** HumanEval is a hand-written benchmark; the referenced model reached 67% pass@1.
- **Use in synthesis:** Useful as evidence that benchmark results are often reported with pass@k-style metrics.

## papers/planning.txt
- **Topic:** Task decomposition and planning
- **Key claim:** Plan-then-execute reduced step errors by 18% on WebArena.
- **Use in synthesis:** Supports structuring work before execution.

## papers/react.txt
- **Topic:** Reasoning-action interleaving
- **Key claim:** ReAct cut hallucinated actions by 30% on ALFWorld.
- **Use in synthesis:** Supports combining reasoning with tool use.

## papers/reflexion.txt
- **Topic:** Self-reflection after failure
- **Key claim:** Reflexion improved success rate by 11 points on HotpotQA.
- **Use in synthesis:** Supports post-failure reflection and iterative repair.

## papers/rollback.txt
- **Topic:** Verification-based recovery
- **Key claim:** Rollback on failed verification recovered 92% of corrupted runs.
- **Use in synthesis:** Supports checkpointing and recovery after validation failures.

## papers/sandbox.txt
- **Topic:** Sandbox isolation
- **Key claim:** Container isolation reduced host side-effects to zero across 1000 trials.
- **Use in synthesis:** Supports safe execution boundaries.

## papers/swe_bench.txt
- **Topic:** Software engineering benchmark
- **Key claim:** A baseline agent resolved 41% of issues on SWE-bench-Verified.
- **Use in synthesis:** Provides a reference performance point for agent evaluation.

## papers/toolformer.txt
- **Topic:** Tool-use learning
- **Key claim:** Self-supervised API calls improved zero-shot math accuracy by 9%.
- **Use in synthesis:** Supports learned tool invocation for capability gains.

## papers/voyager.txt
- **Topic:** Open-ended skill accumulation
- **Key claim:** Skill-library reuse increased novel item discovery by 3.3x in Minecraft.
- **Use in synthesis:** Supports reusable skills and long-horizon accumulation.

## notes/note_eval_metrics.md
- **Topic:** Agent evaluation methodology
- **Key claim:** Report recall and false-positive rate rather than self-assessment.
- **Use in synthesis:** Reinforces objective evaluation over subjective confidence.

## notes/note_failure_modes.md
- **Topic:** Common agent failure modes
- **Key claim:** Goal drift, false completion, and context rot are common failures.
- **Use in synthesis:** Helps frame risks and safeguards in the review.

## Synthesis themes
1. **Grounded reporting:** Claims should be tied to sources, not model self-assessment.
2. **Plan-then-act with verification:** Planning, execution, and rollback reduce errors.
3. **Tool-use and reflection:** Interleaving actions with reasoning plus reflection improves outcomes.
4. **Safety and robustness:** Sandboxing and verification prevent side effects and corrupted runs.
5. **Evaluation discipline:** Prefer objective metrics and benchmark references over vague success claims.