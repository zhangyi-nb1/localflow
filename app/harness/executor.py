from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.harness.audit import AuditLogger
from app.harness.checkpoint import completed_action_ids
from app.harness.policy_guard import PolicyViolation, evaluate_action, resolve_inside
from app.harness.sandbox import SandboxRuntime
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    ActionTraceEvent,
    ExecutionRecord,
    ExecutionStatus,
    FailureType,
    RollbackEntry,
    RollbackManifest,
    TraceEvent,
    TraceEventType,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent.client import LLMClient
    from app.harness.approval import ApprovalDecision
    from app.schemas import ConfirmationPolicy, ReactConfig
    from app.tools.workspace import Workspace
from app.schemas.action import Action, ActionType
from app.schemas.compute import ComputeAction, ComputeOutcomeStatus
from app.schemas.rollback import RollbackOpType
from app.storage.jsonl_logger import JsonlLogger
from app.storage.run_store import RunStore
from app.tools.scratch import ScratchWorkspace


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ExecutionOutcome:
    run_id: str
    records: list[ExecutionRecord]
    manifest: RollbackManifest
    success: bool


class Executor:
    """Runs an ActionPlan against the real filesystem under harness controls.

    Guarantees:
      * Every action is policy-checked at execution time (defense in depth
        even after the plan-level RiskAssessment).
      * Every successful write produces a rollback entry.
      * The execution log is appended *before and after* each action so a
        crash leaves enough trail for ``completed_action_ids`` to resume.
    """

    def __init__(
        self,
        workspace_root: Path,
        run_store: RunStore,
        forbidden_actions: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        *,
        trace: TraceLogger | None = None,
        scratch_workspace: ScratchWorkspace | None = None,
        sandbox_runtime: SandboxRuntime | None = None,
        workspace: "Workspace | None" = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.run_store = run_store
        self.forbidden_actions = forbidden_actions
        self.forbidden_paths = forbidden_paths
        self.exec_log = JsonlLogger(run_store.execution_log_path)
        self.audit = AuditLogger(run_store.audit_log_path)
        # Phase 28.1 — Workspace facade for all user-side filesystem
        # mutations. Default = LocalWorkspace pointed at workspace_root
        # (preserves v0.25.x behaviour exactly); callers can inject a
        # different implementation (Phase 29 DockerWorkspace, tests'
        # spy workspaces, etc.).
        if workspace is None:
            from app.tools.workspace import LocalWorkspace

            self.workspace = LocalWorkspace(self.workspace_root)
        else:
            self.workspace = workspace
        # Phase 9 — optional trace stream. None = no-op (back-compat
        # with v0.9.1 callers; library tests that don't care about
        # trace see identical behaviour).
        self.trace = trace
        # Phase 23 — both must be present to dispatch PYTHON_COMPUTE.
        # Missing them is fine for the 697 existing tests; the dispatch
        # site raises a clear error if a PYTHON_COMPUTE action shows up
        # without them.
        self.scratch_workspace = scratch_workspace
        self.sandbox_runtime = sandbox_runtime

    def execute(
        self,
        plan: ActionPlan,
        *,
        approved: bool,
        resume: bool = False,
        react_mode: bool = False,
        react_config: "ReactConfig | None" = None,
        llm_client: "LLMClient | None" = None,
        confirmation_policy: "ConfirmationPolicy | None" = None,
        action_approver: "Callable[[Action], ApprovalDecision] | None" = None,
    ) -> ExecutionOutcome:
        if not approved:
            raise RuntimeError("Executor refused: plan not approved")

        # Phase 27.1 — store policy + approver so per-action dispatch
        # can consult them. None for both = v0.24.x behaviour
        # (all actions auto-approved past plan-level gate).
        self._confirmation_policy = confirmation_policy
        self._action_approver = action_approver

        # Phase 26.1 — opt-in react loop dispatch. Default react_mode=False
        # preserves v0.23.x batch behaviour for all existing callers /
        # tests. When react_mode=True, ReactConfig.enabled is treated as
        # the master switch (set internally below so callers can pass
        # just ``react_mode=True`` without constructing a config).
        if react_mode:
            from app.harness.react_loop import run_react_loop
            from app.schemas import ReactConfig as _ReactConfig

            config = react_config or _ReactConfig(enabled=True)
            # Honour an explicit ``enabled=False`` by passing through to
            # the batch path — same shape as react_loop's defensive
            # fallback, but here it costs no extra import / branch.
            if config.enabled:
                # The caller's explicit react_mode=True overrides a
                # missing ReactConfig.enabled — flip it on so the
                # inner loop honours the request.
                if not config.enabled:  # pragma: no cover — defensive
                    config = config.model_copy(update={"enabled": True})
                return run_react_loop(self, plan, llm_client=llm_client, config=config)

        already_done = completed_action_ids(self.exec_log) if resume else set()
        run_id = self.run_store.task_id

        # Load any prior manifest so we keep the rollback entries from
        # earlier (partial) executions when resuming.
        if resume and self.run_store.rollback_path.exists():
            manifest = self.run_store.load_rollback()
        else:
            manifest = RollbackManifest(run_id=run_id, task_id=plan.task_id)

        records: list[ExecutionRecord] = []
        self.audit.log("execute.start", run_id=run_id, plan_id=plan.plan_id, resume=resume)

        all_ok = True
        for action in plan.actions:
            if action.action_id in already_done:
                self.exec_log.write(
                    "action.skip",
                    {"action_id": action.action_id, "reason": "checkpoint"},
                )
                records.append(
                    ExecutionRecord(
                        run_id=run_id,
                        action_id=action.action_id,
                        status=ExecutionStatus.SKIPPED,
                    )
                )
                continue

            # Defense in depth: re-check policy at execute time.
            decision = evaluate_action(
                self.workspace_root,
                action,
                forbidden_actions=self.forbidden_actions,
                forbidden_paths=self.forbidden_paths,
            )
            if not decision.allowed:
                err = "; ".join(decision.reasons)
                self.exec_log.write(
                    "action.end",
                    {
                        "action_id": action.action_id,
                        "status": ExecutionStatus.FAILED.value,
                        "error": f"policy_violation: {err}",
                    },
                )
                self._emit_trace(
                    TraceEventType.POLICY_CHECK,
                    status="blocked",
                    failure_type=_classify_policy_reason(decision.reasons),
                    action_id=action.action_id,
                    detail=err,
                    payload={"task_id": plan.task_id, "reasons": list(decision.reasons)},
                )
                records.append(
                    ExecutionRecord(
                        run_id=run_id,
                        action_id=action.action_id,
                        status=ExecutionStatus.FAILED,
                        ended_at=_utcnow(),
                        error=f"policy_violation: {err}",
                    )
                )
                all_ok = False
                continue

            # Phase 27.1 — per-action approval gate. When no policy is
            # configured (None) the call is a no-op; otherwise we
            # consult the policy + the optional caller-supplied
            # approver. A rejected action lands as FAILED with
            # status=blocked and the loop continues.
            policy_decision = self._policy_check(action)
            if policy_decision is not None and not policy_decision.approved:
                self.exec_log.write(
                    "action.end",
                    {
                        "action_id": action.action_id,
                        "status": ExecutionStatus.FAILED.value,
                        "error": f"policy_rejected: {policy_decision.reason}",
                    },
                )
                self._emit_trace(
                    TraceEventType.POLICY_CHECK,
                    status="blocked",
                    failure_type=FailureType.POLICY_BLOCKED,
                    action_id=action.action_id,
                    detail=f"user rejected via confirmation_policy: {policy_decision.reason}",
                    payload={
                        "task_id": plan.task_id,
                        "policy_decision": policy_decision.reason,
                    },
                )
                records.append(
                    ExecutionRecord(
                        run_id=run_id,
                        action_id=action.action_id,
                        status=ExecutionStatus.FAILED,
                        ended_at=_utcnow(),
                        error=f"user_rejected: {policy_decision.reason}",
                    )
                )
                all_ok = False
                continue

            record = self._run_one(action, run_id, manifest, plan=plan)
            records.append(record)
            if record.status == ExecutionStatus.FAILED:
                all_ok = False

        self.run_store.save_rollback(manifest)
        self.run_store.write_json(
            self.run_store.actions_path,
            [r.model_dump(mode="json") for r in records],
        )
        self.audit.log("execute.end", run_id=run_id, success=all_ok, total=len(records))
        return ExecutionOutcome(run_id=run_id, records=records, manifest=manifest, success=all_ok)

    # -- per-action dispatch ------------------------------------------

    def _run_one(
        self,
        action: Action,
        run_id: str,
        manifest: RollbackManifest,
        *,
        plan: ActionPlan | None = None,
    ) -> ExecutionRecord:
        started = _utcnow()
        self.exec_log.write(
            "action.start",
            {
                "action_id": action.action_id,
                "action_type": action.action_type.value,
                "source": action.source_path,
                "target": action.target_path,
                "started_at": started.isoformat(),
            },
        )
        # Phase 25.1 — pull LLM provenance off the plan (if present). It
        # is plan-level, not per-action, so every ActionTraceEvent for
        # one plan carries the same thought/reasoning. That is the
        # intended shape: in a plan-once-execute-batch model, the LLM's
        # reasoning APPLIES to every action it emitted.
        llm_thought = plan.llm_thought if plan is not None else None
        llm_reasoning = plan.llm_reasoning if plan is not None else None
        llm_tool_call_raw = plan.llm_tool_call_raw if plan is not None else None

        self._emit_trace(
            TraceEventType.ACTION_START,
            action_id=action.action_id,
            detail=f"{action.action_type.value} {action.target_path or ''}",
            payload={
                "action_type": action.action_type.value,
                "source": action.source_path,
                "target": action.target_path,
            },
            thought=llm_thought,
            reasoning=llm_reasoning,
            tool_call_raw=llm_tool_call_raw,
        )
        try:
            hash_before, hash_after, rb = self._dispatch(action, manifest)
        except Exception as exc:
            ended = _utcnow()
            self.exec_log.write(
                "action.end",
                {
                    "action_id": action.action_id,
                    "status": ExecutionStatus.FAILED.value,
                    "ended_at": ended.isoformat(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            self._emit_trace(
                TraceEventType.ACTION_END,
                status="fail",
                action_id=action.action_id,
                duration_ms=_duration_ms(started, ended),
                failure_type=FailureType.UNKNOWN,
                detail=f"{type(exc).__name__}: {exc}",
                thought=llm_thought,
                reasoning=llm_reasoning,
                tool_call_raw=llm_tool_call_raw,
                observation={
                    "error": f"{type(exc).__name__}: {exc}",
                    "action_type": action.action_type.value,
                    "source": action.source_path,
                    "target": action.target_path,
                },
            )
            return ExecutionRecord(
                run_id=run_id,
                action_id=action.action_id,
                status=ExecutionStatus.FAILED,
                started_at=started,
                ended_at=ended,
                error=f"{type(exc).__name__}: {exc}",
            )

        if rb is not None:
            manifest.entries.append(rb)
        ended = _utcnow()
        self.exec_log.write(
            "action.end",
            {
                "action_id": action.action_id,
                "status": ExecutionStatus.SUCCESS.value,
                "ended_at": ended.isoformat(),
                "hash_before": hash_before,
                "hash_after": hash_after,
            },
        )
        self._emit_trace(
            TraceEventType.ACTION_END,
            status="ok",
            action_id=action.action_id,
            duration_ms=_duration_ms(started, ended),
            detail=f"{action.action_type.value} ok",
            payload={
                "hash_before": hash_before,
                "hash_after": hash_after,
            },
            thought=llm_thought,
            reasoning=llm_reasoning,
            tool_call_raw=llm_tool_call_raw,
            observation={
                "action_type": action.action_type.value,
                "source": action.source_path,
                "target": action.target_path,
                "hash_before": hash_before,
                "hash_after": hash_after,
                "rollback_entry": rb.model_dump(mode="json") if rb is not None else None,
            },
        )
        return ExecutionRecord(
            run_id=run_id,
            action_id=action.action_id,
            status=ExecutionStatus.SUCCESS,
            started_at=started,
            ended_at=ended,
            file_hash_before=hash_before,
            file_hash_after=hash_after,
            rollback_action=rb.model_dump(mode="json") if rb else None,
        )

    def _dispatch(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[str | None, str | None, RollbackEntry | None]:
        atype = action.action_type
        if atype == ActionType.MKDIR:
            return self._do_mkdir(action, manifest)
        if atype == ActionType.MOVE or atype == ActionType.RENAME:
            return self._do_move(action, manifest)
        if atype == ActionType.COPY:
            return self._do_copy(action, manifest)
        if atype == ActionType.INDEX:
            return self._do_index(action, manifest)
        if atype == ActionType.SUMMARIZE:
            return self._do_index(action, manifest)
        # v0.16 — second §10.7 exception. FETCH downloads metadata.url
        # into target_path. Reuses INDEX's DELETE_CREATED_FILE rollback.
        if atype == ActionType.FETCH:
            return self._do_fetch(action, manifest)
        # v0.23 — third §10.7 exception. PYTHON_COMPUTE runs an
        # isolated script inside scratch; rollback wipes that scratch
        # subtree via DELETE_SCRATCH_DIR (no workspace mutation).
        if atype == ActionType.PYTHON_COMPUTE:
            return self._do_compute(action, manifest)
        # CONVERT / ANALYZE not supported in Phase 0.
        raise NotImplementedError(f"action_type {atype.value} not implemented in Phase 0")

    def _do_mkdir(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[None, None, RollbackEntry | None]:
        # Phase 28.1 — routed through Workspace facade. The rel_path
        # is what plan/policy_guard already validated; LocalWorkspace
        # re-validates via resolve_inside before touching disk.
        target_rel = action.target_path or ""
        created = self.workspace.mkdir(target_rel)
        if not created:
            return None, None, None
        manifest.created_dirs.append(target_rel)
        return (
            None,
            None,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.DELETE_CREATED_DIR,
                target_path=target_rel,
            ),
        )

    def _do_move(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[str | None, str | None, RollbackEntry]:
        # Phase 28.1 — same flow as before, routed through Workspace.
        source_rel = action.source_path or ""
        target_rel = action.target_path or ""
        if not self.workspace.exists(source_rel):
            raise FileNotFoundError(f"source missing: {action.source_path}")
        chosen_rel = self.workspace.safe_target_rel(target_rel)
        hash_before = self.workspace.sha256(source_rel)
        manifest.file_hashes_before[source_rel] = hash_before or ""
        self.workspace.move(source_rel, chosen_rel)
        hash_after = self.workspace.sha256(chosen_rel)
        return (
            hash_before,
            hash_after,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.MOVE_BACK,
                source_path=chosen_rel,
                target_path=source_rel,
                metadata={"after_hash": hash_after} if hash_after else {},
            ),
        )

    def _do_copy(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[str | None, str | None, RollbackEntry]:
        # Phase 28.1 — same flow, routed through Workspace.
        source_rel = action.source_path or ""
        target_rel = action.target_path or ""
        if not self.workspace.exists(source_rel):
            raise FileNotFoundError(f"source missing: {action.source_path}")
        chosen_rel = self.workspace.safe_target_rel(target_rel)
        hash_before = self.workspace.sha256(source_rel)
        self.workspace.copy(source_rel, chosen_rel)
        hash_after = self.workspace.sha256(chosen_rel)
        manifest.generated_files.append(chosen_rel)
        return (
            hash_before,
            hash_after,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.DELETE_CREATED_FILE,
                target_path=chosen_rel,
                metadata={"after_hash": hash_after} if hash_after else {},
            ),
        )

    def _do_index(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[None, str | None, RollbackEntry]:
        # Phase 28.2 — text + binary writes routed through Workspace.
        # The OVERWRITE-with-backup path still uses shutil.move directly
        # because the backup destination (run_store.backups_dir) lives
        # outside the workspace and isn't a Workspace concern.
        target_rel = action.target_path or ""
        target_abs = resolve_inside(self.workspace_root, target_rel)
        overwrite = bool(action.metadata.get("overwrite_existing", False))

        # Phase 3.2 binary-payload support (base64-encoded PNG charts).
        binary_b64 = action.metadata.get("binary_content_b64")
        payload_bytes: bytes | None = None
        content_text: str | None = None
        if binary_b64 is not None:
            import base64

            try:
                payload_bytes = base64.b64decode(binary_b64)
            except Exception as exc:
                raise ValueError(
                    f"action {action.action_id}: binary_content_b64 is not valid base64: {exc}"
                ) from exc
        else:
            content_text = action.metadata.get("content", "")

        def _write_at(rel: str) -> None:
            """Phase 28.2 closure — write the payload (text or bytes)
            at ``rel`` through the Workspace facade. Captures the
            already-decoded payload so the caller only chooses WHERE."""
            if payload_bytes is not None:
                self.workspace.write_bytes(rel, payload_bytes)
            else:
                self.workspace.write_text(rel, content_text or "")

        # Phase 3.2: record implicit parent dirs the write will create.
        self._record_implicit_parents(target_abs, action.action_id, manifest)

        if overwrite and target_abs.is_file():
            # Backup-before-overwrite. The backup directory is OUTSIDE
            # the user workspace (sibling under run_store.backups_dir),
            # so the move-to-backup stays on shutil — Workspace's job
            # is only the workspace-side write that follows.
            backup_filename = f"{action.action_id}__{target_abs.name}"
            backup_abs = self.run_store.backups_dir / backup_filename
            backup_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target_abs), str(backup_abs))
            _write_at(target_rel)
            hash_after = self.workspace.sha256(target_rel)
            manifest.generated_files.append(target_rel)
            return (
                None,
                hash_after,
                RollbackEntry(
                    action_id=action.action_id,
                    op=RollbackOpType.RESTORE_FROM_BACKUP,
                    target_path=target_rel,
                    # Phase 21.1 relative-path computation unchanged —
                    # see existing comment for the StageRunStore quirk.
                    backup_path=str(
                        backup_abs.relative_to(self.run_store.backups_dir.parent).as_posix()
                    ),
                    metadata={"after_hash": hash_after} if hash_after else {},
                ),
            )

        # Default path — auto-suffix on collision, write fresh.
        chosen_rel = self.workspace.safe_target_rel(target_rel)
        _write_at(chosen_rel)
        hash_after = self.workspace.sha256(chosen_rel)
        manifest.generated_files.append(chosen_rel)
        return (
            None,
            hash_after,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.DELETE_CREATED_FILE,
                target_path=chosen_rel,
                metadata={"after_hash": hash_after} if hash_after else {},
            ),
        )

    def _do_compute(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[None, str | None, RollbackEntry]:
        """v0.23 — execute one PYTHON_COMPUTE action inside the sandbox.

        The host ``action.metadata`` carries the typed ComputeAction;
        we parse it, create the scratch dir, copy declared inputs in,
        delegate the script run to ``SandboxRuntime``, and record a
        DELETE_SCRATCH_DIR rollback entry so rollback can wipe the
        scratch subtree.

        IMPORTANT: this method NEVER mutates the user workspace. Outputs
        live in scratch only; a subsequent MOVE/COPY pack stage is
        required to promote any artifact into the workspace.
        """
        if self.scratch_workspace is None or self.sandbox_runtime is None:
            raise RuntimeError(
                f"action {action.action_id}: PYTHON_COMPUTE requires "
                f"Executor(scratch_workspace=..., sandbox_runtime=...); "
                f"neither was supplied"
            )
        try:
            compute = ComputeAction.model_validate(action.metadata or {})
        except Exception as exc:
            raise ValueError(
                f"action {action.action_id}: PYTHON_COMPUTE metadata is not "
                f"a valid ComputeAction: {exc}"
            ) from exc

        task_id = self.run_store.task_id
        self._emit_trace(
            TraceEventType.COMPUTE_ACTION_START,
            action_id=action.action_id,
            detail=compute.script_summary[:200],
            payload={
                "script_summary": compute.script_summary,
                "input_count": len(compute.inputs),
                "expected_output_count": len(compute.expected_outputs),
                "timeout_sec": compute.sandbox_policy.timeout_sec,
            },
        )

        layout = self.scratch_workspace.create_for_action(task_id, action.action_id)
        try:
            self.scratch_workspace.copy_inputs(layout, self.workspace_root, compute.inputs)
        except (FileNotFoundError, ValueError) as exc:
            # Inputs missing or escape attempt — surface as execution
            # error, still record rollback entry so the scratch dir is
            # cleaned. Re-raise to land in the standard error path.
            self._emit_trace(
                TraceEventType.COMPUTE_ACTION_END,
                status="fail",
                action_id=action.action_id,
                failure_type=FailureType.MISSING_OUTPUT,
                detail=f"input setup failed: {exc}",
            )
            # Best-effort cleanup before re-raising so the scratch dir
            # doesn't leak when no RollbackEntry was returned.
            self.scratch_workspace.cleanup_action(task_id, action.action_id)
            raise

        outcome = self.sandbox_runtime.execute(compute, layout)

        # Record the rollback entry BEFORE any failure-path returns so
        # rollback always cleans up the scratch dir regardless of how
        # the action concluded.
        rb = RollbackEntry(
            action_id=action.action_id,
            op=RollbackOpType.DELETE_SCRATCH_DIR,
            target_path=None,
            metadata={
                "task_id": task_id,
                "action_id": action.action_id,
                "outcome": outcome.model_dump(mode="json"),
            },
        )

        if outcome.status is ComputeOutcomeStatus.SANDBOX_TIMEOUT:
            self._emit_trace(
                TraceEventType.SANDBOX_TIMEOUT,
                status="fail",
                action_id=action.action_id,
                failure_type=FailureType.UNKNOWN,
                detail=outcome.error or "sandbox killed",
                payload={"timeout_sec": compute.sandbox_policy.timeout_sec},
            )

        end_status = "ok" if outcome.status is ComputeOutcomeStatus.OK else "fail"
        self._emit_trace(
            TraceEventType.COMPUTE_ACTION_END,
            status=end_status,
            action_id=action.action_id,
            duration_ms=int(outcome.duration_sec * 1000),
            failure_type=_classify_compute_outcome(outcome.status),
            detail=outcome.error or "",
            payload={
                "outcome_status": outcome.status.value,
                "exit_code": outcome.exit_code,
                "produced_count": len(outcome.produced_artifacts),
                "missing_count": len(outcome.missing_artifacts),
            },
        )
        self._emit_trace(
            TraceEventType.COMPUTE_OUTPUT_VERIFIED,
            status=end_status,
            action_id=action.action_id,
            failure_type=(
                None
                if outcome.status is ComputeOutcomeStatus.OK
                else _classify_compute_outcome(outcome.status)
            ),
            detail=", ".join(a.relative_path for a in outcome.produced_artifacts)[:300],
            payload={
                "produced": [a.relative_path for a in outcome.produced_artifacts],
                "missing": list(outcome.missing_artifacts),
            },
        )

        if outcome.status is not ComputeOutcomeStatus.OK:
            # Append the rollback entry directly to the manifest so the
            # scratch dir is cleaned on rollback, then signal failure to
            # the caller via raise. The caller writes a FAILED record but
            # rollback still works.
            manifest.entries.append(rb)
            raise RuntimeError(
                f"compute action failed: status={outcome.status.value}"
                + (f" — {outcome.error}" if outcome.error else "")
            )

        # OK path — let the standard _run_one append the rollback entry.
        return None, None, rb

    def _do_fetch(
        self, action: Action, manifest: RollbackManifest
    ) -> tuple[None, str | None, RollbackEntry]:
        """v0.16 — execute a FETCH action.

        Reads ``metadata.url`` (https only), GETs it via ``urllib``
        with a 30 s timeout, writes the response body to ``target_path``.
        Rollback semantics are identical to a fresh INDEX write
        (DELETE_CREATED_FILE).

        Domain allowlisting is enforced by the policy_guard BEFORE this
        method runs — when we get here we know the URL host is on the
        task's allowlist.
        """
        import urllib.request

        url = action.metadata.get("url") if action.metadata else None
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError(
                f"action {action.action_id}: FETCH requires metadata.url "
                f"starting with 'https://', got {url!r}"
            )
        # Phase 28.2 — payload write routes through Workspace.
        target_rel = action.target_path or ""
        target_abs = resolve_inside(self.workspace_root, target_rel)
        self._record_implicit_parents(target_abs, action.action_id, manifest)

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "localflow-webcollect/0.16"},
        )
        timeout = float(action.metadata.get("timeout_seconds", 30) or 30)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
        except Exception as exc:
            raise RuntimeError(
                f"action {action.action_id}: FETCH {url!r} failed: {type(exc).__name__}: {exc}"
            ) from exc

        self.workspace.write_bytes(target_rel, payload)
        hash_after = self.workspace.sha256(target_rel)
        manifest.generated_files.append(target_rel)
        return (
            None,
            hash_after,
            RollbackEntry(
                action_id=action.action_id,
                op=RollbackOpType.DELETE_CREATED_FILE,
                target_path=target_rel,
                metadata={"after_hash": hash_after, "fetch_url": url}
                if hash_after
                else {"fetch_url": url},
            ),
        )

    def _record_implicit_parents(
        self, target_abs: Path, action_id: str, manifest: RollbackManifest
    ) -> None:
        """Walk from target's parent upward until we hit an existing dir
        (or workspace_root). Each non-existent level gets a
        DELETE_CREATED_DIR rollback entry. Outer-most first in execution
        order, so reverse-rollback removes inner before outer.

        Skips silently if ``target_abs.parent`` already exists or if the
        target is at the workspace root itself.
        """
        try:
            workspace_root = self.workspace_root.resolve()
        except OSError:
            return
        new_dirs: list[Path] = []
        cursor = target_abs.parent
        while True:
            try:
                resolved = cursor.resolve()
            except OSError:
                break
            if resolved == workspace_root:
                break
            if cursor.exists():
                break
            new_dirs.append(cursor)
            cursor = cursor.parent
        new_dirs.reverse()  # outermost first → execution order
        for d in new_dirs:
            rel_d = self._rel(d)
            manifest.created_dirs.append(rel_d)
            manifest.entries.append(
                RollbackEntry(
                    action_id=action_id,
                    op=RollbackOpType.DELETE_CREATED_DIR,
                    target_path=rel_d,
                )
            )

    def _rel(self, abs_path: Path) -> str:
        try:
            return abs_path.resolve().relative_to(self.workspace_root).as_posix()
        except ValueError as exc:
            raise PolicyViolation(f"path outside workspace: {abs_path}") from exc

    # -- Phase 27.1 confirmation policy gate --------------------------

    def _policy_check(self, action: Action):
        """Phase 27.1 — consult ``self._confirmation_policy`` for this
        action. Returns None when no policy is wired (no-op = v0.24.x
        behaviour) or when the policy auto-approves; otherwise calls
        the configured ``_action_approver`` (or a safe default that
        auto-rejects when no approver is provided to avoid a stuck
        non-interactive run)."""
        policy = getattr(self, "_confirmation_policy", None)
        if policy is None:
            return None
        from app.harness.approval import (
            ApprovalDecision,
            policy_requires_confirmation,
        )

        if not policy_requires_confirmation(action, policy):
            return ApprovalDecision(
                approved=True,
                reason=f"auto-approved by policy={policy.policy_type.value}",
            )
        approver = getattr(self, "_action_approver", None)
        if approver is None:
            # No approver wired — fail closed to avoid silently
            # accepting an action a policy explicitly wanted to gate.
            return ApprovalDecision(
                approved=False,
                reason="confirmation_policy gates this action but no approver wired",
            )
        return approver(action)

    # -- Phase 9 trace emission helper --------------------------------

    def _emit_trace(
        self,
        event_type: TraceEventType,
        *,
        status: str = "ok",
        failure_type: FailureType | None = None,
        action_id: str | None = None,
        duration_ms: int | None = None,
        detail: str = "",
        payload: dict | None = None,
        # Phase 25.1 — ActionTraceEvent fields. All None-default; when
        # any are populated (or when event_type is one of the ACTION_*
        # values), the emitter promotes the row to ActionTraceEvent so
        # downstream readers can rebuild the full action lifecycle
        # from a single trace.jsonl line. Plain TraceEvent stays the
        # default shape for non-action events (LLM_CALL_*, POLICY_CHECK,
        # ROLLBACK_ENTRY, etc.) so v0.23.x grader code keeps working.
        thought: str | None = None,
        reasoning: list[dict] | None = None,
        tool_call_raw: dict | None = None,
        observation: dict | None = None,
        critic_result: dict | None = None,
    ) -> None:
        """No-op when self.trace is None (Phase 9 additive-only rule).

        The trace stream must never raise into the executor's hot path —
        a malformed event should drop on the floor rather than fail an
        action. ``run_id`` and ``task_id`` come from run_store.

        Phase 25.1: when any ActionTraceEvent-specific kwarg is set
        OR the event is an ACTION_START / ACTION_END, the row is
        upgraded to ActionTraceEvent. The richer shape is a strict
        superclass of TraceEvent so it is type-safe to pass through
        the same logger.
        """
        if self.trace is None:
            return
        is_action_event = event_type in (
            TraceEventType.ACTION_START,
            TraceEventType.ACTION_END,
        )
        has_rich_field = any(
            v is not None for v in (thought, reasoning, tool_call_raw, observation, critic_result)
        )
        try:
            common_kwargs = dict(
                task_id=self.run_store.task_id,
                run_id=self.run_store.task_id,
                event_type=event_type,
                status=status,  # type: ignore[arg-type]
                failure_type=failure_type,
                action_id=action_id,
                duration_ms=duration_ms,
                detail=detail[:500],  # cap; eval reports don't need full traces
                payload=payload or {},
            )
            if is_action_event or has_rich_field:
                event = ActionTraceEvent(
                    **common_kwargs,
                    thought=thought,
                    reasoning=reasoning,
                    tool_call_raw=tool_call_raw,
                    observation=observation,
                    critic_result=critic_result,
                )
            else:
                event = TraceEvent(**common_kwargs)
            self.trace.emit(event)
        except Exception:
            # Defensive — trace emission must never break execution.
            pass


def _duration_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1000))


def _classify_policy_reason(reasons: list[str]) -> FailureType:
    """Map policy_guard reason strings onto FailureType buckets so eval
    histograms can separate `path_forbidden` (user-set forbidden_paths
    hit) from generic `policy_blocked` (forbidden action type, etc.)."""
    joined = " ".join(reasons).lower()
    if "forbidden_path" in joined or "forbidden path" in joined:
        return FailureType.PATH_FORBIDDEN
    return FailureType.POLICY_BLOCKED


def _classify_compute_outcome(status: ComputeOutcomeStatus) -> FailureType | None:
    """Map ComputeOutcomeStatus onto the trace FailureType taxonomy so
    the eval histogram can count compute failures alongside the other
    failure modes. ``OK`` returns None (no failure attribution)."""
    if status is ComputeOutcomeStatus.OK:
        return None
    if status is ComputeOutcomeStatus.OUTPUT_MISSING:
        return FailureType.MISSING_OUTPUT
    if status is ComputeOutcomeStatus.OUTPUT_OVER_SIZE:
        return FailureType.MISSING_OUTPUT
    return FailureType.UNKNOWN
