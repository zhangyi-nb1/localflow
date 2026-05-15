"""Phase 11 — plan refinement loop unit tests.

The user-facing story: after a plan is generated, the user can supply
a hint and the same task gets a `plan_v(N+1).json` without executing
or rolling back. These tests exercise the storage layer
(:class:`RunStore` plan versioning), the harness layer
(:func:`control_loop.run_revise` + MAX_REVISIONS cap), and the trace
contract (:data:`TraceEventType.PLAN_REVISED`).

The LLM call itself is stubbed via a stand-in Skill that records the
``user_hint`` kwarg and returns a deterministic plan — no real
OpenAI/Anthropic traffic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.harness import control_loop
from app.harness.control_loop import MAX_REVISIONS
from app.harness.trace import TraceLogger
from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    FileMeta,
    RiskLevel,
    SkillManifest,
    TaskSpec,
    WorkspaceSnapshot,
)
from app.schemas.trace import TraceEventType
from app.skills._base import Skill, SkillError
from app.storage.run_store import RunStore


def _task(task_id: str, workspace_root: Path) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        user_goal="seed goal",
        workspace_root=str(workspace_root),
        skill="stub",
        constraints=[],
        allowed_actions=["mkdir", "move", "index"],
        forbidden_actions=["delete", "overwrite", "shell"],
        forbidden_paths=[],
        preferences={},
    )


def _snapshot(task_id: str, workspace_root: Path) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        snapshot_id=f"snap-{task_id}",
        task_id=task_id,
        root=str(workspace_root),
        files=[
            FileMeta(
                path="a.txt",
                file_type="text",
                size_bytes=4,
                modified_at=datetime.now(timezone.utc),
            )
        ],
        total_files=1,
        total_size_bytes=4,
    )


def _seed_plan(task_id: str, plan_id: str, n_actions: int = 1) -> ActionPlan:
    actions = [
        Action(
            action_id=f"a-{i:03d}",
            action_type=ActionType.INDEX,
            target_path=f"out_{i}.md",
            reason="seed",
            risk_level=RiskLevel.LOW,
            reversible=True,
            requires_approval=False,
            metadata={"content": "seed content"},
        )
        for i in range(1, n_actions + 1)
    ]
    return ActionPlan(
        plan_id=plan_id,
        task_id=task_id,
        summary="seed plan",
        actions=actions,
        expected_outputs=[f"out_{i}.md" for i in range(1, n_actions + 1)],
        risk_summary="low",
    )


class _StubSkill(Skill):
    """Records every revise() call and produces a deterministic new
    plan. No LLM contact."""

    def __init__(self, supports_llm: bool = True) -> None:
        self._supports_llm = supports_llm
        self.revise_calls: list[tuple[str, int]] = []  # (hint, prior_action_count)

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="stub",
            description="test stand-in",
            version="0.0.1",
            capabilities=[],
            required_tools=[],
            allowed_actions=["mkdir", "move", "index"],
            requires_approval=[],
            supports_dry_run=True,
            supports_rollback=True,
            supports_verify=True,
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        return _seed_plan(task.task_id, "plan-stub-0", n_actions=1)

    def plan_with_llm(
        self,
        task: TaskSpec,
        snapshot: WorkspaceSnapshot,
        **kwargs,
    ) -> ActionPlan:
        hint = kwargs.get("user_hint") or ""
        prior = kwargs.get("prior_plan_actions") or []
        self.revise_calls.append((hint, len(prior)))
        return _seed_plan(task.task_id, f"plan-stub-revised-{len(self.revise_calls)}", n_actions=2)

    def supports_llm(self) -> bool:
        return self._supports_llm

    def validate(self, plan: ActionPlan) -> None:
        # Trust _seed_plan output.
        return None

    def report(self, **kwargs) -> str:
        return ""


# ───────────────────────────────────── RunStore plan versioning


def test_save_plan_version_writes_plans_subdir_and_mirrors(tmp_path: Path) -> None:
    """save_plan_version must persist plan_v<n>.json under plans/ AND
    update plan.json so existing readers (executor / verifier / rollback)
    keep seeing the latest plan via the old path."""
    store = RunStore(task_id="t-001", home=tmp_path)
    plan = _seed_plan("t-001", "plan-1")
    store.save_plan_version(plan, 1)
    assert store.plan_version_path(1).exists()
    assert store.plan_path.exists()
    # plan.json is the mirror — equal content.
    assert store.plan_version_path(1).read_text(encoding="utf-8") == store.plan_path.read_text(
        encoding="utf-8"
    )


def test_list_plan_versions_returns_sorted(tmp_path: Path) -> None:
    """list_plan_versions skips non-conforming files and returns the
    numeric versions in ascending order."""
    store = RunStore(task_id="t-002", home=tmp_path)
    for v in (3, 1, 2):
        store.save_plan_version(_seed_plan("t-002", f"plan-{v}"), v)
    assert store.list_plan_versions() == [1, 2, 3]


def test_list_plan_versions_empty_when_no_subdir(tmp_path: Path) -> None:
    store = RunStore(task_id="t-003", home=tmp_path)
    assert store.list_plan_versions() == []


# ───────────────────────────────────── control_loop.run_revise


def test_run_revise_happy_path(tmp_path: Path) -> None:
    """v1 → v2 round trip: plans/plan_v1.json (backfilled) +
    plans/plan_v2.json + revisions.jsonl all exist; the trace stream
    gains a PLAN_REVISED event."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = RunStore(task_id="t-100", home=tmp_path)
    task = _task("t-100", workspace)
    store.save_task(task)
    snap = _snapshot("t-100", workspace)
    store.save_workspace(snap)
    plan_v1 = _seed_plan("t-100", "plan-v1")
    store.save_plan(plan_v1)

    skill = _StubSkill()
    trace = TraceLogger(store.trace_path)
    new_plan, version = control_loop.run_revise(
        task, snap, plan_v1, "use a pie chart", skill=skill, run_store=store, trace=trace
    )

    assert version == 2
    assert new_plan.plan_id == "plan-stub-revised-1"
    assert store.plan_version_path(1).exists()  # backfilled
    assert store.plan_version_path(2).exists()
    assert store.revisions_log_path.exists()
    events = trace.read_all()
    assert any(e.event_type == TraceEventType.PLAN_REVISED for e in events)
    revised = next(e for e in events if e.event_type == TraceEventType.PLAN_REVISED)
    assert revised.payload["user_hint"] == "use a pie chart"
    assert revised.payload["version"] == 2


def test_run_revise_passes_hint_and_prior_plan_through(tmp_path: Path) -> None:
    """Skill.revise must receive the user hint AND the prior plan's
    actions list so the LLM has full context for the rewrite."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = RunStore(task_id="t-200", home=tmp_path)
    task = _task("t-200", workspace)
    store.save_task(task)
    snap = _snapshot("t-200", workspace)
    plan_v1 = _seed_plan("t-200", "plan-v1", n_actions=3)
    store.save_plan(plan_v1)

    skill = _StubSkill()
    control_loop.run_revise(task, snap, plan_v1, "more detail please", skill=skill, run_store=store)

    assert skill.revise_calls == [("more detail please", 3)]


def test_run_revise_rejects_empty_hint(tmp_path: Path) -> None:
    store = RunStore(task_id="t-300", home=tmp_path)
    task = _task("t-300", tmp_path)
    snap = _snapshot("t-300", tmp_path)
    plan = _seed_plan("t-300", "plan-1")
    store.save_plan(plan)
    skill = _StubSkill()
    with pytest.raises(SkillError):
        control_loop.run_revise(task, snap, plan, "   ", skill=skill, run_store=store)


def test_run_revise_enforces_max_revisions_cap(tmp_path: Path) -> None:
    """Beyond MAX_REVISIONS the function must refuse — protects users
    from chasing the LLM on a broken initial goal."""
    store = RunStore(task_id="t-400", home=tmp_path)
    task = _task("t-400", tmp_path)
    snap = _snapshot("t-400", tmp_path)
    plan = _seed_plan("t-400", "plan-base")
    # Pretend we've already burned MAX_REVISIONS versions.
    for v in range(1, MAX_REVISIONS + 1):
        store.save_plan_version(_seed_plan("t-400", f"plan-{v}"), v)
    with pytest.raises(SkillError, match="already revised"):
        control_loop.run_revise(task, snap, plan, "one more", skill=_StubSkill(), run_store=store)


def test_run_revise_rejects_non_llm_skill(tmp_path: Path) -> None:
    """A skill without an LLM planner can't honour free-form hints —
    the Skill.revise default must raise a clear error rather than
    silently producing a duplicate of plan v1."""
    store = RunStore(task_id="t-500", home=tmp_path)
    task = _task("t-500", tmp_path)
    snap = _snapshot("t-500", tmp_path)
    plan = _seed_plan("t-500", "plan-1")
    store.save_plan(plan)
    skill = _StubSkill(supports_llm=False)
    with pytest.raises(SkillError, match="refinement"):
        control_loop.run_revise(task, snap, plan, "tweak it", skill=skill, run_store=store)


def test_run_revise_appends_revisions_log_row(tmp_path: Path) -> None:
    """revisions.jsonl is the audit trail — one JSON line per revise.
    Final report renders this table; tests must catch a stale schema."""
    store = RunStore(task_id="t-600", home=tmp_path)
    task = _task("t-600", tmp_path)
    snap = _snapshot("t-600", tmp_path)
    plan_v1 = _seed_plan("t-600", "plan-v1")
    store.save_plan(plan_v1)

    control_loop.run_revise(
        task, snap, plan_v1, "do something else", skill=_StubSkill(), run_store=store
    )

    import json as _json

    lines = store.revisions_log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = _json.loads(lines[0])
    assert row["version"] == 2
    assert row["prior_plan_id"] == "plan-v1"
    assert row["new_plan_id"] == "plan-stub-revised-1"
    assert row["user_hint"] == "do something else"


def test_skill_default_revise_threads_kwargs_through() -> None:
    """The Skill ABC default revise() delegates to plan_with_llm with
    the expected kwargs. Skills inherit this for free."""
    snap = _snapshot("t-700", Path("/tmp/x"))
    task = _task("t-700", Path("/tmp/x"))
    plan_v1 = _seed_plan("t-700", "plan-v1", n_actions=2)
    skill = _StubSkill()
    skill.revise(task, snap, plan_v1, "use line chart")
    assert skill.revise_calls == [("use line chart", 2)]
