# Grounded Review

This workspace paints a consistent picture of agent design and evaluation: strong systems are not built on raw capability alone, but on grounding, structured planning, tool use, reflection, verification, and isolation.

The most immediate lesson is that **claims should be source-backed**. The grounding study says requiring citations reduced unsupported claims by 24%, which suggests that a review or report is more trustworthy when each assertion can be traced to evidence. That principle also aligns with the evaluation note, which recommends reporting recall and false-positive rate rather than relying on self-assessment.

A second theme is **plan-then-execute discipline**. The planning study reports an 18% reduction in step errors when agents decompose tasks before acting. ReAct extends that idea by interleaving reasoning with tool actions, reducing hallucinated actions by 30% on ALFWorld. Together, they suggest that a grounded agent should not only plan, but also keep reasoning tightly coupled to actions.

A third theme is **learning from feedback**. Reflexion shows that verbal self-critique can improve success by 11 points, while rollback on failed verification recovers 92% of corrupted runs. In combination, these results argue for systems that can notice mistakes, reflect, and safely revert instead of pressing forward after failure.

The workspace also emphasizes **operational safety**. Sandbox isolation reduced host side-effects to zero across 1000 trials, implying that execution boundaries are not optional in agentic systems. This is especially important when tool use is involved, since Toolformer demonstrates that learned API calls can improve zero-shot accuracy by 9% on math tasks. Capability gains from tools are valuable, but they should be paired with containment and validation.

Finally, the materials point to **long-horizon improvement through reusable structure**. Voyager’s 3.3x increase in novel item discovery suggests that agents can compound competence when they maintain and reuse skills over time. The SWE-bench-Verified reference result of 41% issue resolution provides a concrete benchmark anchor, reminding us that improvements should be measured against realistic external tasks.

## Synthesis
The strongest synthesis across the workspace is that effective agents are **grounded, structured, feedback-driven, and safe**. They should cite sources, plan before acting, interleave reasoning with tools, reflect after failure, verify outputs, and execute within sandboxes. Evaluation should be objective and benchmarked, not self-congratulatory. In short: the path to better agents is not only smarter models, but better process discipline around them.

## Selected evidence
- Grounding study: citations reduced unsupported claims by 24%.
- Planning study: plan-then-execute reduced step errors by 18%.
- ReAct: reasoning-action interleaving cut hallucinated actions by 30%.
- Reflexion: verbal reinforcement improved success by 11 points.
- Rollback: verification-based recovery restored 92% of corrupted runs.
- Sandbox: container isolation reduced host side-effects to zero.
- Toolformer: self-supervised API calls improved zero-shot accuracy by 9%.
- Voyager: skill-library reuse increased novel item discovery by 3.3x.
- SWE-bench-Verified: baseline agent resolved 41% of issues.
- Evaluation note: use recall and false-positive rate rather than self-assessment.
- Failure modes note: guard against goal drift, false completion, and context rot.