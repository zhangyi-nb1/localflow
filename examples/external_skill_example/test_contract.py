"""Demo: how an external skill author proves their Skill is compatible
with LocalFlow's harness.

The 8-stage contract from Phase 4.3 is importable as
``app.skills.run_skill_contract``. Pass a seeder that populates a tmp
workspace with files representative of what your Skill is designed to
plan over, then assert the report is all green.

Run from the project root::

    pytest examples/external_skill_example/test_contract.py -v

This works whether or not the skill is installed under
``.localflow/skills/`` — the test imports ``skill.py`` by file path so
external authors can run their tests before they install.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.skills import run_skill_contract
from app.storage.run_store import RunStore


def _load_skill_module():
    """Import the example skill.py by absolute file path.

    The Phase 4.1 loader does this same trick (with a hashed module
    namespace) — re-implementing it inline here keeps the example
    standalone-readable for external authors.
    """
    here = Path(__file__).parent
    spec = importlib.util.spec_from_file_location("_workspace_stats_under_test", here / "skill.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_skill_module()


def seed_workspace(root: Path) -> None:
    """Tiny mixed-type workspace so the WorkspaceStatsSkill's per-category
    counts are non-trivial."""
    (root / "doc.pdf").write_text("not a real PDF; extension is enough", encoding="utf-8")
    (root / "notes.txt").write_text("note\n", encoding="utf-8")
    (root / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (root / "image.jpg").write_bytes(b"\xff\xd8fakejpg")


def test_workspace_stats_passes_contract(tmp_path: Path) -> None:
    """The Phase 4.1 example external skill must satisfy the same
    lifecycle contract as the built-ins."""
    ws = tmp_path / "ws"
    rs = RunStore.create(home=tmp_path / ".localflow")
    report = run_skill_contract(
        _mod.WorkspaceStatsSkill(),
        workspace_seeder=seed_workspace,
        workspace_root=ws,
        run_store=rs,
    )
    if not report.all_passed:
        failed = "\n".join(f"  - {s}" for s in report.failed_stages())
        pytest.fail(f"Contract failed:\n{failed}\n\nFull report:\n{report}")


def test_workspace_stats_plan_summarizes_categories(tmp_path: Path) -> None:
    """Skill-specific assertion that complements the universal contract.

    Inspects the planned action's metadata directly (NO execute / rollback)
    so it's independent of the contract's lifecycle. This is the recommended
    pattern for "deep" tests: contract handles lifecycle conformance, your
    own tests inspect skill-specific shape.
    """
    from app.schemas import TaskSpec
    from app.storage.run_store import RunStore
    from app.tools.file_scan import scan_workspace

    ws = tmp_path / "ws"
    ws.mkdir()
    seed_workspace(ws)
    rs = RunStore.create(home=tmp_path / ".localflow")
    snap = scan_workspace(ws, rs.task_id, compute_preview=False)
    task = TaskSpec(
        task_id=rs.task_id,
        user_goal="stats",
        workspace_root=str(ws),
        allowed_actions=["index"],
    )

    plan = _mod.WorkspaceStatsSkill().plan(task, snap)
    assert len(plan.actions) == 1
    body = plan.actions[0].metadata["content"]
    assert "pdf" in body
    assert "tabular" in body  # data.csv is classified as tabular
    assert "text" in body
    assert "image" in body
