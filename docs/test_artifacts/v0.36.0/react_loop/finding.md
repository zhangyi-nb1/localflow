# React loop — first real-provider run (R4 finding · 2026-06-22)

Harness optimization log **R4**. Goal: capture a real `trace.jsonl` showing the
in-stage react loop driving execution with `LOOP_DECISION_*` events — converting
the Control-layer's headline mechanism from `shipped_untested_in_real_run` into
a cited artifact.

## What happened

Ran `localflow execute --task-id <id> --react` on a 40-action folder-organize
plan against the **live OpenAI provider** (gpt-style proxy). The run reported
`react_mode=ON`, executed 40 actions, verify passed, exit 0 — **but the react
loop never actually drove a single action.** Event counts from the trace:

```
loop.decision.requested : 1
loop.decision.decided   : 1   (status=fail)
loop.decision.applied   : 0
action.start / action.end : 40 / 40   (all via batch fallback)
```

The loop consulted the LLM once, the consult **failed**, and the loop fell back
to batch for all 40 actions (`_LoopState.fallback_to_batch`). See
`trace_loop_decisions.jsonl` for the two raw rows.

## Two stacked root causes — the react loop had NEVER run end-to-end

**Bug #1 — CLI hard-coded the wrong provider client (FIXED, app-layer).**
`app/cli.py` instantiated `AnthropicClient()` for `--react`, which raises
`ANTHROPIC_API_KEY not set` in any OpenAI-provider setup (the project default).
So `--react` was *unreachable* outside an Anthropic config. Fixed to use the
provider-aware `get_default_client_or_none()` like every other LLM path.

**Bug #2 — the loop-decision tool schema isn't OpenAI-strict-compatible (OPEN).**
With the client fixed, the first consult now reaches OpenAI and 400s:

```
Invalid schema for function 'submit_loop_decision':
In context=(…'replacement_action','anyOf','1','properties','metadata'),
'additionalProperties' is required to be supplied and to be false.
```

`build_loop_decision_tool_schema` (in `localflow_kernel/react_prompts.py`)
embeds the full `Action` schema for `replacement_action`, and `Action.metadata`
is a free-form `dict` → an `object` with no `additionalProperties: false`.
OpenAI strict function-calling requires *every* nested object (including inside
`anyOf` branches) to set `additionalProperties: false` — which fundamentally
conflicts with a free-form dict. So the consult is rejected and the loop
degrades to batch.

## Why this matters (honest)

The benchmark/README call the react loop a Control-layer mechanism with a drift
budget. This run is the first proof that — against the project's own default
provider — it had **never executed an LLM-driven decision**: bug #1 stopped it
from starting, and bug #2 stops the first consult. The batch fallback is itself
a correct safety behaviour (a failed consult must not wedge the run), which is
exactly why the failure stayed invisible until a real run with trace inspection.

## Fix #2 options (decision pending — touches kernel, rule H)

1. **App-layer schema sanitizer** (`openai_client.py`): recursively set
   `additionalProperties: false` on every object before sending. Hardens all
   schemas, but breaks free-form `metadata` (model can no longer add keys).
2. **Kernel schema change** (`react_prompts.py` / `Action`): give
   `replacement_action` a strict, fixed-shape `metadata` (or drop it from the
   loop schema). Touches kernel-resident code → §10.7 ledger + user confirm.
3. **Per-call strict=False** for the loop tool only: lets the free-form dict
   through but loses OpenAI's schema enforcement on the decision.

Recommendation: option 1 or 2, decided with the user (rule H), then re-run and
confirm `loop.decision.applied` events with real CONTINUE/REPLACE decisions.

## RESOLVED — fix #2 landed (app-layer sanitizer, option 1)

Chose option 1. Added `_force_strict_object_schema` in `app/agent/openai_client.py`:
before every OpenAI structured call it recursively (a) forces
`additionalProperties: false`, (b) sets `required` = all property keys, and
(c) **drops free-form dict fields** (object schemas with no declared
properties — e.g. `metadata`) which can't be expressed under strict mode.
Runs on a deep copy, so the caller's schema is untouched. It only enforces
what strict mode already requires, so it can't regress a working OpenAI path.

It took three live 400s to surface all three strict rules (additionalProperties
→ required-completeness → property-less-object); switched to **offline schema
validation** to find the rest in one pass instead of one live call each.

Re-run on a clean folder-organize plan (`trace_after_fix2_working.jsonl`):

```
loop.decision.requested : 10
loop.decision.decided   : 10  (all status=ok)
loop.decision.applied   : 10  (all CONTINUE)
```

The react loop now drives every action via real live-LLM consultations. All
decisions were `CONTINUE` — correct: on a clean rule-built plan the model has
no reason to deviate. Eliciting `REPLACE`/`SKIP`/`ABORT` needs a deviation
scenario (a redundant/wrong planned action) and is a follow-up; the mechanism
itself is now proven against the real provider. Full suite still green
(1132 passed); sanitizer unit-tested in `tests/test_openai_strict_schema.py`.
