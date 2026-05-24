# React Loop (Phase 26)

> Status: shipping in v0.24.0. The §10.7 4th deliberate kernel
> exception. Off by default; every opt-in is documented below.

## What it is

Most LocalFlow runs go through one batch: the planner produces an
ActionPlan, you approve it, the executor runs every action in order,
the verifier checks the result. The **react loop** is an alternate
shape of the *execute stage only* — same plan, same dry-run, same
approval, same verifier, same rollback, but between actions the
executor asks the LLM what to do next. The LLM picks one of five
shapes:

- **CONTINUE** — run the next planned action unchanged (the common
  case; most loop turns are CONTINUE).
- **REPLACE** — swap the next planned action with a different one.
  Costs one drift step.
- **INSERT** — insert a new action before the next planned action
  (the plan continues after). Costs one drift step.
- **SKIP** — drop the next planned action. Costs one drift step.
- **ABORT** — stop the loop, hand back to verify with what ran so
  far. NOT a hard failure — verify + rollback still run.

When the **drift budget** is exhausted (default: 3 steps), REPLACE /
INSERT / SKIP get silently downgraded to CONTINUE — the remaining
queue runs as planned.

## What it is NOT

- **Not a smarter planner.** The plan you approved is still the plan.
  React only edits within the drift budget.
- **Not a way to bypass approval.** Every dispatched action — original,
  REPLACE substitute, INSERT addition — passes through `policy_guard`
  before it runs. Pre-existing `forbidden_actions` / `forbidden_paths`
  still hold.
- **Not free.** Every loop turn is one LLM round-trip. For an
  N-action plan, react mode roughly doubles the LLM cost.
- **Not deterministic.** Same plan, same input, different runs may
  produce different observation-driven decisions. Keep batch mode for
  reproducibility-sensitive workloads.
- **Not a security sandbox.** Same as the rest of the harness — see
  `docs/SECURITY.md`.

## Turning it on

Three opt-in paths:

### 1. CLI per-run

```
localflow execute --task-id <id> --yes --react
localflow execute --task-id <id> --yes --react --react-max-drift 5
```

Requires the `ANTHROPIC_API_KEY` env var (same key the LLM planner
uses). The first line in the output reads
`react_mode=ON  drift_budget=3  (see docs/REACT_LOOP.md)`.

### 2. Recipe-level opt-in

In a recipe YAML / JSON:

```yaml
name: my_recipe
enable_react_mode: true   # Phase 26 — opt into mid-execute LLM decisions
stages:
  - stage_id: s1_organize
    ...
```

The recipe AUTHOR's explicit acknowledgement that mid-execute LLM
deviation is acceptable for this workload. Grep
`enable_react_mode: true` across `recipes/` to enumerate every
recipe that uses the feature.

### 3. Python API

```python
from app.schemas import ReactConfig
from app.harness.executor import Executor
from app.agent.client import AnthropicClient

config = ReactConfig(enabled=True, max_drift=3)
executor.execute(
    plan,
    approved=True,
    react_mode=True,
    react_config=config,
    llm_client=AnthropicClient(),
)
```

## Decision heuristic (what the LLM is told)

The system prompt instructs the model to:

- pick CONTINUE when the prior observation looks fine and the plan
  is on track (default for most turns)
- pick REPLACE when the prior observation reveals the planned next
  action is now wrong (e.g. file was already renamed earlier)
- pick INSERT when a discovered prerequisite was missed (e.g. need
  a MKDIR before the planned MOVE)
- pick SKIP when the next action is now redundant
- pick ABORT when something is so wrong that continuing would do
  more harm than good
- when in doubt, ABORT — let the human review rather than thrash
  the drift budget

## Failure modes + fallbacks

The runtime has three failsafes that downgrade react to batch:

1. **Drift budget exhausted.** After `max_drift` non-CONTINUE
   decisions, any further REPLACE / INSERT / SKIP is downgraded to
   CONTINUE (a `loop.decision.applied` trace event with
   `status=blocked` records the override). The remaining queue
   runs as planned.

2. **LLM call fails or times out.** A `LoopDecisionError` (network
   timeout, auth failure, malformed schema response) flips the
   internal `fallback_to_batch` flag — the rest of the queue runs
   through the regular policy-checked batch dispatch with no more
   LLM consultations.

3. **Policy_guard rejects a REPLACE / INSERT action.** The
   substitute is treated like any other rejected action: it lands as
   an `ExecutionRecord(status=FAILED, error=policy_violation: ...)`
   and the loop continues with the next decision. The user's
   approved plan boundary is never escaped.

## Trace events

Every loop turn emits three rows into `trace.jsonl`:

```
loop.decision.requested  → before LLM call
loop.decision.decided    → LLM returned a LoopDecision (or fail status if not)
loop.decision.applied    → the decision was applied (action dispatched / queue mutated)
```

Inspect with:

```
localflow trace show --task-id <id> --event-type loop.decision.applied
localflow trace summary --task-id <id>
```

## How react mode closes the Phase 23 ComputeAction gap

v0.23.0 shipped `ActionType.PYTHON_COMPUTE` end-to-end (schema +
sandbox + executor dispatch + verifier) but no production code path
emitted one — `app/skills/agent` did not list `python_compute` in
`allowed_actions`, and the planner's tool schema enum did not expose
it. From the user's POV the feature was unreachable.

The react loop closes this gap *without* per-skill manifest patching:
when the LLM is consulted mid-loop with
`Recipe.allow_compute_action: true` AND
`ReactConfig.allow_new_action_types: true`, the loop's tool schema
includes `python_compute` in its action_type enum. The LLM can REPLACE
or INSERT a `PYTHON_COMPUTE` action when the observation reveals
typed primitives are insufficient (e.g. a CSV whose shape the static
data_analyzer skill cannot clean). Phase 26.3 wires the
`examples/compute_action_pack/` recipe to demonstrate this end-to-end.

## When NOT to use react mode

- **Deterministic / reproducible pipelines.** If two runs must produce
  identical output (CI, regulated workflows, eval harnesses), stay in
  batch.
- **Cost-sensitive workloads.** Each react turn is a paid LLM call.
- **Plans with > ~30 actions.** Each action costs one LLM round-trip,
  so for very long plans the latency dominates. Consider whether
  decomposing into separate `pack` runs is a better shape.
- **Rule-only skills.** `workspace_visualizer`, `folder_organizer`'s
  rule mode, etc. — these emit plans the LLM doesn't know how to
  evaluate. React still works but adds no value and costs API calls.

## Reference

- [docs/PHASE_26_DESIGN.md](PHASE_26_DESIGN.md) — full design + 8
  acceptance questions + risk table.
- [docs/research/OPENHANDS_HARNESS_STUDY.md](research/OPENHANDS_HARNESS_STUDY.md)
  — the source-evidence study that motivated the design.
- [app/schemas/react.py](../app/schemas/react.py) — `LoopDecision` +
  `ReactConfig` contracts.
- [app/harness/react_loop.py](../app/harness/react_loop.py) — the
  loop implementation.
- [app/agent/react_prompts.py](../app/agent/react_prompts.py) — the
  LLM-facing prompt + tool schema.
