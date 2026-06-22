"""Phase 38 — stage-level checkpoint / resume / handoff (the Persist layer).

Gives LocalFlow cross-session continuation: a multi-stage task that is
interrupted can be re-entered, skipping the stages that already ran and
producing a final artifact equivalent to an uninterrupted run. This flips
the failure-mode benchmark's ``context_rot`` row from gap → mitigated
(stage-level) — between-stage resume, NOT mid-stage / multi-day (rule F).

The persisted truth for "which stages are done" is the existing
``taskgraph_result.json`` (per-stage ``StageResult.status``). On top of it
this module writes the KB-prescribed human handoff artifacts: a
``progress.json`` feature-list state machine + a ``handoff.md`` note.

§10.7 invariant: pure orchestration over existing kernel primitives —
``run_taskgraph`` (forward-slice, ``persist_*=False``) + the same
re-stitch ``replay_from_stage`` uses. **Zero edits** to executor /
control_loop / policy_guard / rollback / the ``run_taskgraph`` stage loop.
Same class as Phase 21's ``recipe_repair`` (a documented zero-kernel-touch
phase that also lives under ``app/harness/``).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from app.harness.taskgraph_runner import run_taskgraph
from app.schemas.progress import (
    HandoffNote,
    ProgressState,
    StageProgress,
    StageProgressStatus,
)
from app.schemas.taskgraph import StageResult, StageStatus, TaskGraph, TaskGraphResult

if TYPE_CHECKING:
    from app.harness.trace import TraceLogger
    from app.storage.run_store import RunStore

PROGRESS_JSON = "progress.json"
HANDOFF_MD = "handoff.md"


def compute_graph_hash(graph: TaskGraph) -> str:
    """Stable 16-hex hash of the graph's stage shape (id+skill+planner).

    Resume refuses a graph whose shape changed since the checkpoint —
    otherwise a resumed slice could land in the wrong stage.
    """
    spec = "|".join(f"{s.stage_id}:{s.skill}:{s.planner}" for s in graph.stages)
    return hashlib.sha256(spec.encode("utf-8")).hexdigest()[:16]


def _stage_status(sr: "StageResult | None") -> StageProgressStatus:
    """Map a runner StageResult onto the feature-list state machine.

    PASSED + verifier evidence → VERIFIED; PASSED without evidence or
    SKIPPED → IMPLEMENTED (ran, no verifier gate); FAILED/ABORTED →
    BLOCKED; not present → PENDING.
    """
    if sr is None:
        return StageProgressStatus.PENDING
    if sr.status == StageStatus.PASSED:
        return (
            StageProgressStatus.VERIFIED if sr.verifier_passed else StageProgressStatus.IMPLEMENTED
        )
    if sr.status == StageStatus.SKIPPED:
        return StageProgressStatus.IMPLEMENTED
    return StageProgressStatus.BLOCKED  # FAILED / ABORTED


def _load_result(run_store: "RunStore") -> "TaskGraphResult | None":
    if not run_store.exists(run_store.TASKGRAPH_RESULT_JSON):
        return None
    try:
        return run_store.read_model(run_store.taskgraph_result_path, TaskGraphResult)
    except Exception:
        return None


def derive_progress(
    graph: TaskGraph, run_store: "RunStore", *, goal: str = "", now: str = ""
) -> ProgressState:
    """Build a :class:`ProgressState` from the persisted taskgraph result.

    The done-set comes from ``taskgraph_result.json`` (the machine truth);
    this is the readable handoff layer on top of it.
    """
    result = _load_result(run_store)
    by_id: dict[str, StageResult] = (
        {s.stage_id: s for s in result.stages} if result is not None else {}
    )
    stages: list[StageProgress] = []
    for spec in graph.stages:
        sr = by_id.get(spec.stage_id)
        status = _stage_status(sr)
        evidence = (
            f"{run_store.TASKGRAPH_RESULT_JSON}#{spec.stage_id}"
            if status == StageProgressStatus.VERIFIED
            else None
        )
        stages.append(
            StageProgress(stage_id=spec.stage_id, status=status, verified_evidence=evidence)
        )
    state = ProgressState(
        task_id=run_store.task_id,
        graph_hash=compute_graph_hash(graph),
        current_goal=goal or graph.user_goal,
        stages=stages,
        updated_at=now,
    )
    pending = state.pending_ids()
    state.next_step = pending[0] if pending else "(all stages complete)"
    return state


def read_progress(run_store: "RunStore") -> ProgressState | None:
    p = run_store.path(PROGRESS_JSON)
    if not p.is_file():
        return None
    try:
        return run_store.read_model(p, ProgressState)
    except Exception:
        return None


def write_progress(run_store: "RunStore", state: ProgressState) -> None:
    run_store.write_model(run_store.path(PROGRESS_JSON), state)


def to_handoff(state: ProgressState) -> HandoffNote:
    done = [
        s.stage_id
        for s in state.stages
        if s.status in (StageProgressStatus.VERIFIED, StageProgressStatus.IMPLEMENTED)
    ]
    verified = [s.stage_id for s in state.stages if s.status == StageProgressStatus.VERIFIED]
    unresolved = [s.stage_id for s in state.stages if s.status == StageProgressStatus.BLOCKED]
    return HandoffNote(
        done=done,
        files_changed=[],
        verified=verified,
        unresolved=unresolved,
        next_start=state.next_step,
    )


def render_handoff(state: ProgressState) -> str:
    """Render the handoff note as markdown (KB 5-field exit contract)."""
    h = to_handoff(state)
    pending = state.pending_ids()
    lines = [
        f"# Handoff — {state.task_id}",
        "",
        f"**Goal:** {state.current_goal}",
        f"**Next start:** {h.next_start}",
        "",
        f"## Done ({len(h.done)}/{len(state.stages)})",
        *(
            [f"- {sid}" + (" ✓verified" if sid in h.verified else "") for sid in h.done]
            or ["_none_"]
        ),
        "",
        "## Remaining",
        *([f"- {sid}" for sid in pending] or ["_none — task complete_"]),
        "",
        "## Blocked",
        *([f"- {sid}" for sid in h.unresolved] or ["_none_"]),
    ]
    if state.failed_attempts:
        lines += [
            "",
            "## Failed attempts (do not retry)",
            *[f"- {a}" for a in state.failed_attempts],
        ]
    return "\n".join(lines) + "\n"


def write_handoff(run_store: "RunStore", state: ProgressState) -> None:
    run_store.write_text(run_store.path(HANDOFF_MD), render_handoff(state))


def resume_taskgraph(
    graph: TaskGraph,
    run_store: "RunStore",
    *,
    trace: "TraceLogger | None" = None,
    max_stages: int | None = None,
    goal: str = "",
    now: str = "",
) -> TaskGraphResult | None:
    """Run the not-yet-done stages of ``graph`` on the EXISTING run dir,
    keeping completed work, and re-stitch into the merged result.

    This is ``replay_from_stage`` minus the rollback: replay *redoes* a
    stage range; resume *continues* from where the last session stopped.
    ``max_stages`` bounds how many pending stages this session runs (the
    per-session budget the benchmark uses to model context-window limits);
    None = run all remaining.

    Returns the merged :class:`TaskGraphResult` (or the prior one if
    nothing was pending). Writes ``progress.json`` + ``handoff.md`` for the
    next session. Idempotent on a complete graph.
    """
    # Guard: refuse a graph whose shape changed since the checkpoint.
    prior_progress = read_progress(run_store)
    if prior_progress is not None and prior_progress.graph_hash != compute_graph_hash(graph):
        raise ValueError(
            "resume refused: graph shape changed since checkpoint "
            f"({prior_progress.graph_hash} != {compute_graph_hash(graph)})"
        )

    # Persist the graph on first entry so a fresh session can reattach via
    # run_id and resume without re-specifying the YAML (the cross-session
    # handoff the Persist layer is for). run_taskgraph below runs with
    # persist_graph=False, so it never overwrites this full spec.
    if not run_store.exists(run_store.TASKGRAPH_JSON):
        run_store.write_json(run_store.taskgraph_path, graph.model_dump(mode="json"))

    prior_result = _load_result(run_store)
    done: set[str] = set()
    if prior_result is not None:
        done = {
            s.stage_id
            for s in prior_result.stages
            if s.status in (StageStatus.PASSED, StageStatus.SKIPPED)
        }

    pending_specs = [s for s in graph.stages if s.stage_id not in done]
    slice_specs = pending_specs[:max_stages] if max_stages is not None else pending_specs

    if not slice_specs:
        state = derive_progress(graph, run_store, goal=goal, now=now)
        write_progress(run_store, state)
        write_handoff(run_store, state)
        return prior_result

    prior_manifest = (
        run_store.load_rollback() if run_store.exists(run_store.ROLLBACK_JSON) else None
    )

    sub_graph = graph.model_copy(update={"stages": slice_specs})
    sub_result = run_taskgraph(
        sub_graph,
        run_store=run_store,
        trace=trace,
        approved=True,
        persist_graph=False,
        persist_result=False,
    )

    # Merge the manifest: prior (done) entries + this slice's entries. No
    # overlap — distinct stage prefixes. (replay_from_stage's re-stitch,
    # without the rollback step.)
    new_manifest = run_store.load_rollback()
    if prior_manifest is not None:
        merged_entries = list(prior_manifest.entries) + list(new_manifest.entries)
        run_store.save_rollback(new_manifest.model_copy(update={"entries": merged_entries}))

    # Merge the result: kept (done) stages + freshly-run slice stages.
    if prior_result is not None:
        slice_ids = {s.stage_id for s in slice_specs}
        kept = [s for s in prior_result.stages if s.stage_id not in slice_ids]
        merged_result = TaskGraphResult.from_stages(
            task_id=run_store.task_id,
            stages=kept + list(sub_result.stages),
            aggregated_manifest_path=str(run_store.rollback_path),
            duration_ms=prior_result.duration_ms + sub_result.duration_ms,
        )
    else:
        merged_result = sub_result
    run_store.write_json(run_store.taskgraph_result_path, merged_result.model_dump(mode="json"))

    state = derive_progress(graph, run_store, goal=goal, now=now)
    write_progress(run_store, state)
    write_handoff(run_store, state)
    return merged_result


__all__ = [
    "PROGRESS_JSON",
    "HANDOFF_MD",
    "compute_graph_hash",
    "derive_progress",
    "read_progress",
    "write_progress",
    "to_handoff",
    "render_handoff",
    "write_handoff",
    "resume_taskgraph",
]
