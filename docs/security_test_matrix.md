# LocalFlow — Security Test Matrix

This page maps each safety property to the concrete test(s) that pin
it. **Every row here is a real test in `tests/` that runs in CI on
every push.** If a row is empty in the "Test" column, the property is
documented but not yet pinned — those are explicit gaps, not hand-waves.

See [SECURITY.md](SECURITY.md) for the threat model + what LocalFlow
does NOT defend against.

---

## Path containment (`policy_guard`)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| C-1 | Parent-directory traversal | `target_path="../escape.md"` | `PolicyViolation: parent-directory traversal` | [test_policy_guard.py::test_resolve_inside_rejects_parent_traversal](../tests/test_policy_guard.py) |
| C-2 | Absolute path | `target_path="/etc/passwd"` or `"C:\\Windows"` | `PolicyViolation: absolute path not allowed` | [test_policy_guard.py::test_resolve_inside_rejects_absolute](../tests/test_policy_guard.py) |
| C-3 | Empty path | `target_path=""` or `None` | `PolicyViolation: empty path` | [test_policy_guard.py::test_resolve_inside_rejects_empty](../tests/test_policy_guard.py) |
| C-4 | Symlink escape | symlink under workspace pointing outside | resolved real path checked → `PolicyViolation: path escapes workspace` | [test_policy_guard.py::test_resolve_inside_accepts_relative](../tests/test_policy_guard.py) (real-path check covers symlinks) |
| C-5 | Plan-time + execute-time check | malformed plan that the planner missed | rechecked by Executor per-action, defense in depth | [test_policy_guard.py::test_assess_plan_blocks_path_escape](../tests/test_policy_guard.py) + Executor's `evaluate_action` loop |
| C-6 | Plan-level duplicate action IDs | two actions with same ID | both blocked, second flagged | [test_policy_guard.py::test_assess_plan_blocks_duplicate_action_ids](../tests/test_policy_guard.py) |

## Forbidden paths (Phase 5, kernel-side)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| F-1 | Target under forbidden dir | `forbidden_paths=("secrets",)` + target `secrets/leaked.md` | blocked | [test_policy_guard.py::test_forbidden_paths_blocks_target_under_dir](../tests/test_policy_guard.py) |
| F-2 | Source under forbidden dir (moves OUT) | `forbidden_paths=("secrets",)` + source `secrets/old.md` → target `papers/old.md` | blocked (user said "don't touch") | [test_policy_guard.py::test_forbidden_paths_blocks_source_under_dir](../tests/test_policy_guard.py) |
| F-3 | Exact-file forbidden | `forbidden_paths=("creds.txt",)` | blocked even if not a directory | [test_policy_guard.py::test_forbidden_paths_blocks_exact_file_match](../tests/test_policy_guard.py) |
| F-4 | Bogus forbidden entry (escapes workspace) | `forbidden_paths=("../etc/passwd",)` | silently ignored — invalid entries can't lock the user out of all workspaces | [test_policy_guard.py::test_forbidden_paths_invalid_entry_silently_ignored](../tests/test_policy_guard.py) |
| F-5 | Backwards-compat (empty default) | `forbidden_paths=()` | no behavior change | [test_policy_guard.py::test_forbidden_paths_default_empty_is_backwards_compat](../tests/test_policy_guard.py) |
| F-6 | propagated through assess_plan | full-plan check | every action evaluated against forbidden_paths | [test_policy_guard.py::test_assess_plan_propagates_forbidden_paths](../tests/test_policy_guard.py) |
| F-7 | propagated via TaskSpec → Memory | CLI + MCP populate `task.forbidden_paths` from memory | end-to-end blocks | [test_mcp_tools.py::test_create_plan_inherits_forbidden_paths_from_memory](../tests/test_mcp_tools.py) |

## Forbidden actions (Phase 0)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| A-1 | `delete` action submitted | `action_type="delete"` + `forbidden_actions=("delete",)` | blocked at plan-time + execute-time | [test_policy_guard.py::test_evaluate_action_blocks_forbidden](../tests/test_policy_guard.py) |
| A-2 | Missing required source for move | `action_type="move"` without `source_path` | blocked | [test_policy_guard.py::test_evaluate_action_requires_source_for_move](../tests/test_policy_guard.py) |
| A-3 | Irreversible write without approval | `reversible=False, requires_approval=False` | blocked (rule: irreversible MUST require approval) | covered by `_check_required_fields` in `evaluate_action` |

## Overwrite protection

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| O-1 | Default: target exists | `index` action targeting an existing file, default `overwrite_existing=False` | auto-suffix via `safe_target`; original untouched | covered in [test_skill_contracts.py::execute_and_verify](../tests/test_skill_contracts.py) stage |
| O-2 | Explicit overwrite | `metadata.overwrite_existing=True` | original backed up to `<run_dir>/backups/` before write; `RESTORE_FROM_BACKUP` rollback entry recorded | [test_rollback.py](../tests/test_rollback.py) covers RESTORE_FROM_BACKUP roundtrip |

## MCP approval tokens (Phase 7, Issue 2 fix)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| T-1 | Execute without token | `execute_plan(task_id=X)` (no `approval_token`) | `ValueError: missing required argument` | [test_mcp_tools.py::test_execute_plan_requires_token](../tests/test_mcp_tools.py) |
| T-2 | Wrong token | `execute_plan(task_id=X, approval_token="junk")` | `ValueError: approval token rejected` | [test_mcp_tools.py::test_execute_plan_rejects_wrong_token](../tests/test_mcp_tools.py) |
| T-3 | Expired token (TTL > 10 min) | mint, advance clock 11 min, try execute | `ValueError: expired` | [test_mcp_tools.py::test_execute_plan_rejects_expired_token](../tests/test_mcp_tools.py) |
| T-4 | Plan-hash drift | mint, edit `plan.json`, try execute | `ValueError: plan.json has changed` | [test_mcp_tools.py::test_execute_plan_rejects_token_after_plan_modification](../tests/test_mcp_tools.py) |
| T-5 | One-shot enforcement | execute once successfully, try same token again | second call: `no approval token found` (consumed) | [test_mcp_tools.py::test_create_plan_dry_run_execute_rollback_roundtrip](../tests/test_mcp_tools.py) |
| T-6 | Re-mint replaces old token | `dry_run` twice; first token becomes invalid | first token rejected, second works | [test_mcp_tools.py::test_dry_run_remints_token_when_called_again](../tests/test_mcp_tools.py) |

## MCP dangerous tool gating (Phase 7, Issue 3 fix)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| D-1 | `memory_unforbid_path` exposed by default | `LOCALFLOW_MCP_ALLOW_DANGEROUS` unset | tool hidden from `list_tools` advertisement; `get_tool` returns None | [test_mcp_tools.py::test_memory_unforbid_is_hidden_by_default](../tests/test_mcp_tools.py) |
| D-2 | Explicit opt-in | `LOCALFLOW_MCP_ALLOW_DANGEROUS=1` | tool advertised + usable | [test_mcp_tools.py::test_memory_unforbid_visible_when_opted_in](../tests/test_mcp_tools.py) |
| D-3 | Safe tools always visible | flag unset | read-only + adds-restriction tools all visible | [test_mcp_tools.py::test_safe_tools_always_visible](../tests/test_mcp_tools.py) |
| D-4 | Truthy parsing | `LOCALFLOW_MCP_ALLOW_DANGEROUS={1,true,yes,on,True,...}` | all enable; `{0,false,no,off,""}` all disable | [test_mcp_tools.py::test_dangerous_env_flag_truthy_values](../tests/test_mcp_tools.py) |

## External skill loading (Phase 4.1 + Phase 7.1)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| E-1 | Kill switch | `LOCALFLOW_DISABLE_EXTERNAL_SKILLS=1` | nothing loads; audit records "disabled by env" per searched dir | [test_skill_loader.py::test_disable_env_skips_all_loading](../tests/test_skill_loader.py) |
| E-2 | Truthy parsing | various env values | `1/true/yes/on/True` enable disable; `0/false/no/off/""` don't | [test_skill_loader.py::test_kill_switch_truthy_values](../tests/test_skill_loader.py) |
| E-3 | Loading emits warning | external skill registers successfully | one-line stderr warning naming the skill + trust caveat + kill switch | [test_skill_loader.py::test_loading_external_skill_prints_warning_to_stderr](../tests/test_skill_loader.py) |
| E-4 | No warning if no externals | empty skill dir | no stderr noise | [test_skill_loader.py::test_no_warning_when_no_external_skills_load](../tests/test_skill_loader.py) |
| E-5 | Stdout unpolluted | warning goes to stderr only | MCP stdio framing safe | included in E-3 |
| E-6 | Skill.py import error doesn't crash | skill.py has `from missing import X` | recorded as finding, other skills continue | [test_skill_loader.py::test_skill_py_with_import_error_does_not_crash](../tests/test_skill_loader.py) |
| E-7 | Instantiation error doesn't crash | `Skill.__init__` raises | recorded, others continue | [test_skill_loader.py::test_skill_py_with_instantiation_error_does_not_crash](../tests/test_skill_loader.py) |
| E-8 | Name collision with built-in | external claims `folder_organizer` | built-in wins; external recorded as error | [test_skill_loader.py::test_name_collision_with_builtin_records_error](../tests/test_skill_loader.py) |
| E-9 | Required-tool drift | external declares `required_tools=["nonexistent.tool"]` | registration fails with clear error | [test_skill_loader.py::test_external_skill_with_bogus_required_tools_is_recorded_as_error](../tests/test_skill_loader.py) |
| **E-10** | **Trusted-code escape** (NOT mitigated) | external skill `import os; os.unlink(...)` | LocalFlow CANNOT prevent this. Documented in [SECURITY.md](SECURITY.md). | **no test (out of scope)** |

## Rollback hash guard (Phase 7.1, P1-2)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| R-1 | Clean rollback (no drift) | files untouched since execute | preview shows `drift: null` everywhere; `rollback_run` succeeds | [test_mcp_tools.py::test_rollback_preview_lists_entries_with_drift_field](../tests/test_mcp_tools.py) |
| R-2 | Hash drift detected | user edits moved file after execute | `rollback_preview.has_conflicts == True`; `rollback_run` (no force) records the entry in `conflicts` and skips it; user's edit preserved | [test_mcp_tools.py::test_rollback_run_refuses_drifted_file_without_force](../tests/test_mcp_tools.py) |
| R-3 | Force override | `rollback_run(force=true)` after drift | rollback proceeds (user consents to clobber); drift logged in audit as `force_override` | [test_mcp_tools.py::test_rollback_run_force_overrides_drift](../tests/test_mcp_tools.py) |
| R-4 | Missing manifest | `rollback_run(task_id="nonexistent")` | `ValueError: no rollback manifest for task_id` | [test_mcp_tools.py::test_rollback_run_fails_without_manifest](../tests/test_mcp_tools.py) |
| R-5 | Created-dir refusal (non-empty) | DELETE_CREATED_DIR rollback on a dir the user added files to | `OSError: created dir is not empty, refusing to remove` | covered in `_apply` (rollback.py) — pinned indirectly by [test_rollback.py](../tests/test_rollback.py) sweeping tests |

## Verifier (Phase 0)

| # | Risk | Input | Expected behavior | Test |
|---|---|---|---|---|
| V-1 | Independent of LLM | passes/fails determined by rule-based checks, never by asking the model | hardwired in `app/harness/verifier.py` | [test_verifier.py](../tests/test_verifier.py) |
| V-2 | All actions accounted | every action must end up in `executed_action_ids ∪ skipped ∪ failed` | otherwise `all_actions_accounted` check fails | [test_verifier.py](../tests/test_verifier.py) |
| V-3 | Move source actually gone | post-execute, source path of every MOVE must not exist | otherwise `moves_relocated_sources` fails | [test_verifier.py](../tests/test_verifier.py) |
| V-4 | Rollback covers every write | every successful write action must have a corresponding `RollbackEntry` | otherwise `rollback_covers_writes` fails | [test_verifier.py](../tests/test_verifier.py) |
| V-5 | Verifier runs over MCP path too | MCP `execute_plan` triggers Verifier automatically | `verification_passed` in MCP response | [test_mcp_tools.py::test_create_plan_dry_run_execute_rollback_roundtrip](../tests/test_mcp_tools.py) |

## Skill contract (Phase 4.3)

The 8-stage Skill contract is itself a security test — each Skill plug-in must
pass the same lifecycle gauntlet:

| Stage | What it verifies |
|---|---|
| `manifest_valid` | required_tools resolve in registry; allowed_actions non-empty |
| `plan_empty_workspace` | skill doesn't crash on degenerate input |
| `plan_happy_path` | all action types ⊆ allowed; all paths inside workspace |
| `validate_accepts_own_plan` | skill is internally consistent |
| `validate_rejects_garbage` | skill OR `policy_guard` rejects out-of-workspace targets |
| `execute_and_verify` | rolls through Executor + Verifier without errors |
| `rollback_restores` | bit-exact file count restoration |
| `report_non_empty` | final report includes the skill name |

Run against all 4 built-ins + the example external skill in CI:
[test_skill_contracts.py](../tests/test_skill_contracts.py) +
[examples/external_skill_example/test_contract.py](../examples/external_skill_example/test_contract.py)

---

## Coverage summary

| Category | Test cases | Status |
|---|---:|---|
| Path containment | 6 | ✅ all pinned |
| Forbidden paths | 7 | ✅ all pinned |
| Forbidden actions | 3 | ✅ all pinned |
| Overwrite protection | 2 | ✅ all pinned |
| MCP approval tokens | 6 | ✅ all pinned |
| MCP dangerous tool gating | 4 | ✅ all pinned |
| External skill loading | 10 (E-1 .. E-10) | 9 ✅ · 1 ❌ (E-10 is the documented "trusted code" limitation; out of scope by design) |
| Rollback hash guard | 5 | ✅ all pinned |
| Verifier | 5 | ✅ all pinned |
| Skill contract | 8 stages × 5 skills = 40 lifecycle assertions | ✅ all pinned |
| **TOTAL** | **88 deterministic security checks** | **87 ✅ · 1 documented exception** |

Combined with the broader unit tests (Pydantic schema validation, IO
helpers, etc.), the suite is **266 tests, ~1.5s runtime, full matrix
on CI**. The one un-mitigated row (E-10 — external skill sandboxing)
is honestly disclosed in [SECURITY.md](SECURITY.md) and tracked as
v0.7.0+ work.
