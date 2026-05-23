"""Phase 23 — TaskGraph runner supports PYTHON_COMPUTE stages.

The runner must thread a ScratchWorkspace + SandboxRuntime down into
each stage's Executor so a stage whose plan contains a ComputeAction
can run. Defaults are constructed automatically; hosts can also pass
in tuned instances.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.sandbox import SandboxRuntime
from app.harness.taskgraph_runner import run_taskgraph
from app.harness.trace import TraceLogger
from app.schemas import (
    ActionPlan,
    SkillManifest,
    StageSpec,
    StageStatus,
    TaskGraph,
    TaskSpec,
    VerificationResult,
    WorkspaceSnapshot,
)
from app.schemas.action import Action, ActionType, RiskLevel
from app.schemas.compute import (
    ArtifactSpec,
    ComputeAction,
    ComputeInputRef,
    SandboxPolicy,
)
from app.skills import SkillRegistry
from app.skills._base import Skill
from app.storage.run_store import RunStore
from app.tools.scratch import ScratchWorkspace


class _ComputeStubSkill(Skill):
    """Test-only skill: emits one PYTHON_COMPUTE action that reads
    ``inputs/seed.txt`` and writes ``outputs/result.txt``."""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="compute_stub",
            description="test stub",
            version="0.0.1",
            allowed_actions=["python_compute"],
            requires_approval=["python_compute"],
        )

    def plan(self, task: TaskSpec, snapshot: WorkspaceSnapshot) -> ActionPlan:
        compute = ComputeAction(
            script=(
                "from pathlib import Path\n"
                "Path('outputs').mkdir(exist_ok=True)\n"
                "seed = Path('inputs/seed.txt').read_text(encoding='utf-8')\n"
                "Path('outputs/result.txt').write_text(seed.upper(), encoding='utf-8')\n"
            ),
            script_summary="uppercase seed.txt",
            inputs=[ComputeInputRef(rel_path="seed.txt", size_bytes=10)],
            expected_outputs=[
                ArtifactSpec(relative_path="outputs/result.txt", description="x")
            ],
            sandbox_policy=SandboxPolicy(timeout_sec=10),
        )
        action = Action(
            action_id="a-stub",
            action_type=ActionType.PYTHON_COMPUTE,
            reason="stub",
            risk_level=RiskLevel.MEDIUM,
            reversible=True,
            requires_approval=True,
            metadata=compute.model_dump(mode="json"),
        )
        return ActionPlan(
            plan_id="plan-stub",
            task_id=task.task_id,
            summary="compute stub",
            actions=[action],
        )

    def validate(self, plan: ActionPlan) -> None:
        return None

    def report(
        self,
        *,
        task: TaskSpec,
        plan: ActionPlan,
        outcome,
        verification: VerificationResult,
    ) -> str:
        return "stub report"


@pytest.fixture
def stub_registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.register(_ComputeStubSkill())
    return reg


def _seed(ws: Path) -> None:
    (ws / "seed.txt").write_text("hello", encoding="utf-8")


def test_taskgraph_runs_python_compute_stage_with_default_sandbox(
    tmp_path: Path, stub_registry: SkillRegistry
) -> None:
    """A single-stage graph whose only action is PYTHON_COMPUTE must
    succeed when the runner builds the default ScratchWorkspace +
    SandboxRuntime on demand."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    home = tmp_path / "lf"
    store = RunStore.create(home=home)
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="run compute stage",
        workspace_root=str(ws),
        stages=[
            StageSpec(stage_id="s1", title="Compute", skill="compute_stub"),
        ],
    )
    result = run_taskgraph(
        graph,
        store,
        trace=trace,
        approved=True,
        registry=stub_registry,
    )
    assert result.passed is True, [s.error for s in result.stages]
    assert result.stages[0].status == StageStatus.PASSED
    assert result.stages[0].action_count == 1
    assert result.stages[0].success_count == 1

    # Compute outputs live in scratch (NOT in the workspace).
    assert sorted(p.name for p in ws.iterdir()) == ["seed.txt"]
    # Scratch dir exists under <home>/scratch/<task>/<stage.action_id>/.
    # Note: action_id was prefixed by _prefix_action_ids → "s1.a-stub".
    scratch = ScratchWorkspace(home=home)
    layout = scratch.action_dir(store.task_id, "s1.a-stub")
    assert (layout / "outputs" / "result.txt").read_text(encoding="utf-8") == "HELLO"


def test_taskgraph_accepts_custom_scratch_and_sandbox(
    tmp_path: Path, stub_registry: SkillRegistry
) -> None:
    """Hosts that want to override env-scrub denylist or scratch root
    pass instances in; the runner threads them down."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    scratch = ScratchWorkspace(home=tmp_path / "alt_scratch_home")
    sandbox = SandboxRuntime(extra_env_denylist=("MY_SECRET",))

    graph = TaskGraph(
        user_goal="run compute stage",
        workspace_root=str(ws),
        stages=[StageSpec(stage_id="s1", title="Compute", skill="compute_stub")],
    )
    result = run_taskgraph(
        graph,
        store,
        trace=trace,
        approved=True,
        registry=stub_registry,
        scratch_workspace=scratch,
        sandbox_runtime=sandbox,
    )
    assert result.passed is True, [s.error for s in result.stages]
    # Scratch landed in the alt root, not the default <home>/scratch/.
    alt_layout = scratch.action_dir(store.task_id, "s1.a-stub")
    assert alt_layout.exists()
    assert (alt_layout / "outputs" / "result.txt").exists()


def test_aggregated_rollback_manifest_includes_delete_scratch_dir(
    tmp_path: Path, stub_registry: SkillRegistry
) -> None:
    """The graph-level rollback manifest must include the
    DELETE_SCRATCH_DIR entry from the compute stage so a subsequent
    ``localflow rollback --run-id`` wipes scratch alongside other
    workspace edits."""
    from app.schemas.rollback import RollbackOpType

    ws = tmp_path / "ws"
    ws.mkdir()
    _seed(ws)
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="compute then rollback",
        workspace_root=str(ws),
        stages=[StageSpec(stage_id="s1", title="Compute", skill="compute_stub")],
    )
    result = run_taskgraph(
        graph, store, trace=trace, approved=True, registry=stub_registry
    )
    assert result.passed

    manifest = store.load_rollback()
    delete_scratch_ops = [
        e for e in manifest.entries if e.op == RollbackOpType.DELETE_SCRATCH_DIR
    ]
    assert len(delete_scratch_ops) == 1
    # Stage prefix carried through.
    assert delete_scratch_ops[0].action_id == "s1.a-stub"
    md = delete_scratch_ops[0].metadata or {}
    assert md.get("task_id") == store.task_id
    assert md.get("action_id") == "s1.a-stub"


def test_existing_non_compute_stage_still_works_after_phase23_changes(
    tmp_path: Path,
) -> None:
    """Sanity: the existing folder_organizer-only graph still passes
    after adding scratch_workspace + sandbox_runtime kwargs. Tests the
    default factory branch."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "report.pdf").write_text("doc", encoding="utf-8")
    (ws / "photo.png").write_text("img", encoding="utf-8")
    store = RunStore.create(home=tmp_path / "lf")
    trace = TraceLogger(store.trace_path)

    graph = TaskGraph(
        user_goal="organize only",
        workspace_root=str(ws),
        stages=[StageSpec(stage_id="s1", title="Organize", skill="folder_organizer")],
    )
    result = run_taskgraph(graph, store, trace=trace, approved=True)
    assert result.passed
