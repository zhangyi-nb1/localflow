# ConfirmationPolicy (Phase 27)

> Status: shipping in v0.25.0. **Not** a kernel exception — approval
> lives in the same application-layer tier as dry_run; the executor
> still refuses to mutate without `approved=True` at plan level.

## What it is

LocalFlow's plan-level approval (the post-dry-run yes/no gate) has
always been binary: you either confirm the whole plan or you don't.
v0.25.0 adds a second, **per-action** gate on top of plan-level
approval. You choose how granular this gate is via one of four
policy values:

| Policy | When does the executor pause to ask? |
|---|---|
| **NEVER** | Never. Matches the historical `--yes` / `auto_approve` path. |
| **ALWAYS** | Before every action (subject to `auto_approve_index`). |
| **ON_HIGH_RISK** | Before any write-class action with `risk_level >= risk_threshold` (default threshold: HIGH). |
| **ON_WRITE** | Before any write-class action — MKDIR / MOVE / COPY / RENAME / INDEX / CONVERT / FETCH. |

`auto_approve_index` (default `True`) short-circuits INDEX / SUMMARIZE
actions to auto-approve regardless of policy — these write only
markdown/JSON artefacts and are trivially rollback-safe, so gating
them adds friction without safety value. Set it to `False` for
audit-strict workflows.

## What it is NOT

- **Not a kernel exception.** Policy lives application-side, like
  `dry_run`. The kernel's eight iron rules (no LLM-side dispatch,
  rollback always emitted, policy_guard always checks paths, …)
  are untouched.
- **Not a replacement for the plan-level gate.** Both gates run.
  Approving at plan level still happens first; per-action prompts
  fire only for actions the policy decides need them.
- **Not a way to override `policy_guard`.** A path-traversal action
  is still rejected at the policy_guard layer even if a user "yes"
  reaches the per-action prompt — the order is `policy_guard →
  ConfirmationPolicy → dispatch`, all three must pass.

## Turning it on

Three opt-in paths:

### 1. CLI per-run

```
localflow execute --task-id <id> --yes --confirm-policy on_high_risk
localflow execute --task-id <id> --yes --confirm-policy always
```

The first line in the output reads
`confirm_policy=on_high_risk — per-action prompts will appear for
gated actions (see docs/PHASE_27_DESIGN.md)`. Each gated action then
shows:

```
Approval needed for action a-007 (move, risk=high):
  source: research/draft.pdf
  target: archive/draft.pdf
  reason: deprecated draft moved to archive
  (Y = approve this, N = reject, A = approve all remaining)
Approve? [y/N/a]:
```

The **A** shortcut (when `allow_approve_rest=True`, the default)
flips the rest of the run to NEVER — useful after the first 2-3
prompts when the pattern is clear.

### 2. Recipe-level opt-in

```yaml
name: my_recipe
confirmation_policy:
  policy_type: on_high_risk
  risk_threshold: medium     # ask more often
  auto_approve_index: true
  allow_approve_rest: true
stages:
  - stage_id: s1_organize
    ...
```

Recipes with a configured `confirmation_policy` use that as the
default; the CLI `--confirm-policy` flag overrides per-invocation.

### 3. Python API

```python
from app.schemas import ConfirmationPolicy, ConfirmationPolicyType, RiskLevel
from app.harness.executor import Executor
from app.harness.approval import ask_action_approval

policy = ConfirmationPolicy(
    policy_type=ConfirmationPolicyType.ON_HIGH_RISK,
    risk_threshold=RiskLevel.HIGH,
)

def approver(action):
    return ask_action_approval(action, policy=policy)

executor.execute(
    plan,
    approved=True,
    confirmation_policy=policy,
    action_approver=approver,
)
```

For automated workflows (CI, recipe tests, eval graders), supply a
non-interactive approver that returns `ApprovalDecision(approved=...)`
based on whatever heuristic the test needs.

## Behaviour matrix

| policy_type | LOW + write | MEDIUM + write | HIGH + write | INDEX (with auto_approve_index=True) |
|---|---|---|---|---|
| `NEVER` | auto-approve | auto-approve | auto-approve | auto-approve |
| `ALWAYS` | **ask** | **ask** | **ask** | auto-approve |
| `ON_HIGH_RISK` (default threshold=HIGH) | auto-approve | auto-approve | **ask** | auto-approve |
| `ON_HIGH_RISK` (threshold=MEDIUM) | auto-approve | **ask** | **ask** | auto-approve |
| `ON_WRITE` | **ask** | **ask** | **ask** | auto-approve |

A non-write action under any policy = auto-approve (only PYTHON_COMPUTE
is technically not in `WRITE_ACTIONS`, but the planner side still
flags PYTHON_COMPUTE as `requires_approval=True` at plan level).

## Fail-closed default

If a policy gates an action AND no `action_approver` callback is
wired (e.g. you pass `ConfirmationPolicy(...)` from the Python API
but forget the approver), the executor refuses the action with
`ApprovalDecision(approved=False, reason="...no approver wired")`.
A FAILED `ExecutionRecord` lands in the run history; the loop
continues with the next action.

This is intentional: silently accepting would defeat the purpose of
opting into a policy at all.

## Failed/rejected actions and rollback

A user-rejected action lands as a `FAILED` `ExecutionRecord` with
`error="user_rejected: <reason>"`. The `POLICY_CHECK` trace event
has `status="blocked"` and `failure_type=POLICY_BLOCKED` so eval
graders bucket it under the policy_blocked failure mode.

`rollback` works normally — only successfully executed actions land
in the manifest, so rejected actions need no undoing.

## Reference

- [docs/PHASE_27_DESIGN.md](PHASE_27_DESIGN.md) — full design + integration plan.
- [app/schemas/approval.py](../app/schemas/approval.py) — typed contract.
- [app/harness/approval.py](../app/harness/approval.py) —
  `policy_requires_confirmation` + `ask_action_approval`.
- [app/harness/executor.py](../app/harness/executor.py) —
  `_policy_check` + per-action gate in the batch path.
- [app/harness/react_loop.py](../app/harness/react_loop.py) —
  `_dispatch_one` calls the same gate so react-mode REPLACE / INSERT
  honours the policy.
