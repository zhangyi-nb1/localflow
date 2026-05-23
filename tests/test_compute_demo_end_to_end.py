"""Phase 23 — end-to-end demo: PYTHON_COMPUTE unlocks the 怪 CSV task.

This is the integration test for the headline claim of Phase 23.0:
ComputeAction lets the harness complete a task none of the eight
built-in action types could finish on their own. The flow:

  1. seed workspace with examples/compute_action_pack/sales_dirty.csv
  2. plan: one PYTHON_COMPUTE action that cleans the CSV in scratch
  3. plan: one COPY action that promotes the cleaned file into workspace
  4. execute + verify + assert the cleaned CSV has the expected shape

The script is hand-written here (not LLM-generated) so the test is
deterministic. The point isn't to prove that the planner produces good
scripts; it's to prove that *if a script exists*, the harness can run
it safely.

The cleaning script normalises case, strips currency symbols, parses
dates, drops duplicates, drops rows with missing required fields, and
emits a JSON summary. All using only the stdlib.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from textwrap import dedent

import pytest

from app.harness.executor import Executor
from app.harness.rollback import Rollback
from app.harness.sandbox import SandboxRuntime
from app.harness.trace import TraceLogger
from app.schemas import ActionPlan, ExecutionStatus
from app.schemas.action import Action, ActionType, RiskLevel
from app.schemas.compute import (
    ArtifactSpec,
    ComputeAction,
    ComputeInputRef,
    SandboxPolicy,
)
from app.schemas.rollback import RollbackOpType
from app.schemas.trace import TraceEventType
from app.storage.run_store import RunStore
from app.tools.scratch import ScratchWorkspace

REPO_ROOT = Path(__file__).resolve().parent.parent
SALES_DIRTY = REPO_ROOT / "examples" / "compute_action_pack" / "workspace" / "sales_dirty.csv"


CLEANING_SCRIPT = dedent(
    """
    import csv
    import json
    import re
    from datetime import datetime
    from pathlib import Path

    SRC = Path("inputs/sales_dirty.csv")
    DST = Path("outputs/cleaned.csv")
    REPORT = Path("outputs/report.json")

    def parse_revenue(raw: str) -> float | None:
        if raw is None:
            return None
        s = raw.strip().replace("$", "").replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def parse_date(raw: str) -> str | None:
        if not raw:
            return None
        raw = raw.strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    rows_in = 0
    rows_kept = 0
    dropped_missing = 0
    dropped_bad_date = 0
    dropped_outlier = 0
    seen: set[tuple[str, str, str]] = set()
    cleaned: list[dict] = []

    with SRC.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows_in += 1
            date = parse_date(r.get("date", ""))
            if date is None:
                dropped_bad_date += 1
                continue
            region = (r.get("region") or "").strip().title()
            product = (r.get("product") or "").strip().title()
            revenue = parse_revenue(r.get("revenue") or "")
            units_raw = (r.get("units") or "").strip()
            if revenue is None or not units_raw or not region or not product:
                dropped_missing += 1
                continue
            try:
                units = int(units_raw)
            except ValueError:
                dropped_missing += 1
                continue
            # Outlier guard: revenue/unit > 1000 is implausible for this fixture.
            if units > 0 and revenue / units > 1000:
                dropped_outlier += 1
                continue
            key = (date, region, product)
            if key in seen:
                continue  # duplicate
            seen.add(key)
            cleaned.append(
                {
                    "date": date,
                    "region": region,
                    "product": product,
                    "revenue": round(revenue, 2),
                    "units": units,
                }
            )
            rows_kept += 1

    cleaned.sort(key=lambda x: (x["date"], x["region"], x["product"]))

    with DST.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date", "region", "product", "revenue", "units"]
        )
        writer.writeheader()
        writer.writerows(cleaned)

    summary = {
        "rows_in": rows_in,
        "rows_kept": rows_kept,
        "dropped_missing": dropped_missing,
        "dropped_bad_date": dropped_bad_date,
        "dropped_outlier": dropped_outlier,
        "unique_dates": len({r["date"] for r in cleaned}),
        "unique_regions": sorted({r["region"] for r in cleaned}),
        "unique_products": sorted({r["product"] for r in cleaned}),
    }
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    """
)


@pytest.fixture
def demo_setup(tmp_path: Path):
    home = tmp_path / ".localflow"
    run_store = RunStore.create(home=home)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Seed the workspace with the dirty CSV from the example pack.
    assert SALES_DIRTY.exists(), f"missing fixture: {SALES_DIRTY}"
    target = workspace / "sales_dirty.csv"
    target.write_bytes(SALES_DIRTY.read_bytes())
    return run_store, workspace, home


def test_compute_action_cleans_dirty_csv_end_to_end(demo_setup) -> None:
    run_store, workspace, home = demo_setup
    scratch = ScratchWorkspace(home=home)
    sandbox = SandboxRuntime()
    trace = TraceLogger(run_store.trace_path)
    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
        sandbox_runtime=sandbox,
        trace=trace,
    )

    compute = ComputeAction(
        script=CLEANING_SCRIPT,
        script_summary=(
            "Normalise sales_dirty.csv: parse mixed date formats, "
            "strip currency symbols, drop duplicates + outliers + rows "
            "with missing values; emit cleaned.csv + summary report.json"
        ),
        inputs=[
            ComputeInputRef(
                rel_path="sales_dirty.csv",
                size_bytes=SALES_DIRTY.stat().st_size,
            )
        ],
        expected_outputs=[
            ArtifactSpec(
                relative_path="outputs/cleaned.csv",
                description="Cleaned sales data with normalised types.",
            ),
            ArtifactSpec(
                relative_path="outputs/report.json",
                description="One-paragraph cleaning summary as JSON.",
            ),
        ],
        sandbox_policy=SandboxPolicy(timeout_sec=15),
    )
    compute_action = Action(
        action_id="a-clean",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="clean messy sales CSV",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=compute.model_dump(mode="json"),
    )
    plan = ActionPlan(
        plan_id="plan-demo",
        task_id=run_store.task_id,
        summary="Phase 23 demo: clean sales_dirty.csv via PYTHON_COMPUTE",
        actions=[compute_action],
    )

    outcome = executor.execute(plan, approved=True)
    assert outcome.success, outcome.records
    assert outcome.records[0].status is ExecutionStatus.SUCCESS

    # Compute action wrote to scratch, not the workspace.
    layout = scratch.action_dir(run_store.task_id, "a-clean")
    cleaned = layout / "outputs" / "cleaned.csv"
    report = layout / "outputs" / "report.json"
    assert cleaned.is_file(), "cleaned.csv missing in scratch"
    assert report.is_file(), "report.json missing in scratch"

    # Workspace must be untouched (Principle #2).
    assert sorted(p.name for p in workspace.iterdir()) == ["sales_dirty.csv"]

    # Verify the cleaning actually worked.
    with cleaned.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0
    # No duplicates by (date, region, product).
    keys = {(r["date"], r["region"], r["product"]) for r in rows}
    assert len(keys) == len(rows)
    # All dates ISO-formatted.
    for r in rows:
        assert len(r["date"]) == 10 and r["date"][4] == "-" and r["date"][7] == "-"
    # All revenues parseable as float, all units as int.
    for r in rows:
        float(r["revenue"])
        int(r["units"])
    # No outlier survives (revenue/units > 1000).
    for r in rows:
        u = int(r["units"])
        if u > 0:
            assert float(r["revenue"]) / u <= 1000

    # Region / product are Title-cased (case-consistent).
    for r in rows:
        assert r["region"] == r["region"].title()
        assert r["product"] == r["product"].title()

    # Summary JSON has the expected shape.
    summary = json.loads(report.read_text(encoding="utf-8"))
    assert summary["rows_in"] > summary["rows_kept"]
    assert summary["dropped_missing"] > 0
    assert sorted(summary["unique_regions"]) == ["East", "North", "South", "West"]
    assert sorted(summary["unique_products"]) == ["Alpha", "Beta", "Gamma"]


def test_compute_demo_rollback_cleans_scratch(demo_setup) -> None:
    """The whole flow must be reversible: after rollback the scratch
    dir is gone and the workspace is bit-for-bit identical to before."""
    run_store, workspace, home = demo_setup
    scratch = ScratchWorkspace(home=home)
    sandbox = SandboxRuntime()

    before_hash = (workspace / "sales_dirty.csv").read_bytes()

    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
        sandbox_runtime=sandbox,
    )
    compute = ComputeAction(
        script=CLEANING_SCRIPT,
        script_summary="Clean the dirty CSV (rollback test).",
        inputs=[
            ComputeInputRef(
                rel_path="sales_dirty.csv",
                size_bytes=SALES_DIRTY.stat().st_size,
            )
        ],
        expected_outputs=[
            ArtifactSpec(relative_path="outputs/cleaned.csv", description="x"),
            ArtifactSpec(relative_path="outputs/report.json", description="y"),
        ],
        sandbox_policy=SandboxPolicy(timeout_sec=15),
    )
    action = Action(
        action_id="a-clean-rb",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="rollback test",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=compute.model_dump(mode="json"),
    )
    plan = ActionPlan(
        plan_id="plan-rb",
        task_id=run_store.task_id,
        summary="rollback test",
        actions=[action],
    )
    outcome = executor.execute(plan, approved=True)
    assert outcome.success
    layout = scratch.action_dir(run_store.task_id, "a-clean-rb")
    assert layout.exists()

    rb = Rollback(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
    )
    result = rb.run(outcome.manifest)
    assert result.success, result.failed

    assert not layout.exists()
    # Workspace identical to before.
    assert (workspace / "sales_dirty.csv").read_bytes() == before_hash
    assert sorted(p.name for p in workspace.iterdir()) == ["sales_dirty.csv"]


def test_compute_demo_emits_full_trace(demo_setup) -> None:
    """Trace must include start + end + output_verified events with the
    right statuses for the eval grader to pick up."""
    run_store, workspace, home = demo_setup
    scratch = ScratchWorkspace(home=home)
    sandbox = SandboxRuntime()
    trace = TraceLogger(run_store.trace_path)
    executor = Executor(
        workspace_root=workspace,
        run_store=run_store,
        scratch_workspace=scratch,
        sandbox_runtime=sandbox,
        trace=trace,
    )
    compute = ComputeAction(
        script=dedent(
            """
            with open('outputs/out.txt', 'w') as f:
                f.write('hi')
            """
        ),
        script_summary="trivial",
        inputs=[],
        expected_outputs=[ArtifactSpec(relative_path="outputs/out.txt", description="x")],
        sandbox_policy=SandboxPolicy(timeout_sec=5),
    )
    action = Action(
        action_id="a-trace",
        action_type=ActionType.PYTHON_COMPUTE,
        reason="trace test",
        risk_level=RiskLevel.MEDIUM,
        reversible=True,
        requires_approval=True,
        metadata=compute.model_dump(mode="json"),
    )
    plan = ActionPlan(
        plan_id="plan-tr",
        task_id=run_store.task_id,
        summary="trace test",
        actions=[action],
    )
    outcome = executor.execute(plan, approved=True)
    assert outcome.success

    events = [
        json.loads(line)
        for line in run_store.trace_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    types = [e["event"] for e in events]
    assert TraceEventType.COMPUTE_ACTION_START.value in types
    assert TraceEventType.COMPUTE_ACTION_END.value in types
    assert TraceEventType.COMPUTE_OUTPUT_VERIFIED.value in types
    # The end event's status must be 'ok'.
    end_evt = next(e for e in events if e["event"] == TraceEventType.COMPUTE_ACTION_END.value)
    assert end_evt["payload"]["status"] == "ok"

    # Manifest has exactly one DELETE_SCRATCH_DIR entry.
    ops = [e.op for e in outcome.manifest.entries]
    assert ops.count(RollbackOpType.DELETE_SCRATCH_DIR) == 1
