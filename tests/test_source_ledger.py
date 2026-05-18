"""v0.14.1 — source_ledger schema + construction tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.schemas import (
    Action,
    ActionPlan,
    ActionType,
    FileMeta,
    RiskLevel,
    RollbackManifest,
    SourceEntry,
    SourceLedger,
    WorkspaceSnapshot,
)
from app.tools.source_ledger_ops import build_from_run, build_from_workspace


def test_ledger_schema_round_trips() -> None:
    """Pydantic JSON round-trip through ``model_dump_json`` /
    ``model_validate_json`` for the typed ledger payload."""
    entry = SourceEntry(
        path="papers/attention.pdf",
        file_type="pdf",
        size_bytes=611,
        sha256="abc123",
        category="papers",
        role="moved",
    )
    ledger = SourceLedger(
        task_id="t-1",
        workspace_root="/tmp/ws",
        entries=[entry],
    )
    raw = ledger.model_dump_json()
    parsed = SourceLedger.model_validate_json(raw)
    assert parsed.entries[0].path == "papers/attention.pdf"
    assert parsed.entries[0].role == "moved"
    assert parsed.ledger_schema_version == 1


def test_ledger_rejects_unknown_role() -> None:
    """Pydantic Literal["seed","generated","moved"] fences off typos
    in the role enum."""
    import pytest

    with pytest.raises(Exception):
        SourceEntry(
            path="x",
            file_type="text",
            size_bytes=1,
            role="bogus",  # type: ignore[arg-type]
        )


def test_ledger_groupby_category() -> None:
    """by_category() groups + sorts entries; root files land under '(root)'."""
    ledger = SourceLedger(
        workspace_root="/tmp/ws",
        entries=[
            SourceEntry(path="a.txt", file_type="text", size_bytes=1, role="seed"),
            SourceEntry(
                path="papers/x.pdf", file_type="pdf", size_bytes=2, category="papers", role="moved"
            ),
            SourceEntry(
                path="papers/y.pdf", file_type="pdf", size_bytes=3, category="papers", role="moved"
            ),
            SourceEntry(
                path="data/z.csv", file_type="tabular", size_bytes=4, category="data", role="moved"
            ),
        ],
    )
    grouped = ledger.by_category()
    assert set(grouped) == {"(root)", "papers", "data"}
    assert [e.path for e in grouped["papers"]] == ["papers/x.pdf", "papers/y.pdf"]


def test_build_from_workspace_picks_up_categories(tmp_path: Path) -> None:
    """A real workspace scan produces entries with categories derived
    from the top-level dir name."""
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "x.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")

    ledger = build_from_workspace(tmp_path)
    paths = {e.path for e in ledger.entries}
    assert {"papers/x.pdf", "notes.txt"}.issubset(paths)
    by_cat = ledger.by_category()
    assert "papers" in by_cat
    assert "(root)" in by_cat


def test_build_from_run_classifies_roles_correctly(tmp_path: Path) -> None:
    """When a plan + seed snapshot are provided, role classification
    distinguishes seed (untouched), moved (in plan), and generated
    (post-run, not in seed)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "papers").mkdir()
    # After-state: original file moved + a generated index.md
    (workspace / "papers" / "x.pdf").write_bytes(b"%PDF-1.4")
    (workspace / "papers" / "index.md").write_text("# index", encoding="utf-8")
    (workspace / "stay.txt").write_text("untouched", encoding="utf-8")

    seed_snap = WorkspaceSnapshot(
        snapshot_id="s",
        task_id="t",
        root=str(workspace),
        files=[
            FileMeta(
                path="x.pdf",
                file_type="pdf",
                size_bytes=8,
                modified_at=datetime.now(timezone.utc),
            ),
            FileMeta(
                path="stay.txt",
                file_type="text",
                size_bytes=9,
                modified_at=datetime.now(timezone.utc),
            ),
        ],
        total_files=2,
        total_size_bytes=17,
    )
    plan = ActionPlan(
        plan_id="p",
        task_id="t",
        summary="seed",
        actions=[
            Action(
                action_id="a-001",
                action_type=ActionType.MOVE,
                source_path="x.pdf",
                target_path="papers/x.pdf",
                reason="organize",
                risk_level=RiskLevel.MEDIUM,
                reversible=True,
                requires_approval=True,
            ),
            Action(
                action_id="a-002",
                action_type=ActionType.INDEX,
                target_path="papers/index.md",
                reason="index",
                risk_level=RiskLevel.LOW,
                reversible=True,
                requires_approval=False,
                metadata={"content": "# index"},
            ),
        ],
        expected_outputs=["papers/x.pdf", "papers/index.md"],
        risk_summary="low",
    )
    manifest = RollbackManifest(task_id="t", run_id="t", entries=[], file_hashes_before={})

    ledger = build_from_run(
        workspace,
        seed_snapshot=seed_snap,
        plan=plan,
        manifest=manifest,
        task_id="t",
    )
    role_map = {e.path: e.role for e in ledger.entries}
    assert role_map["stay.txt"] == "seed"
    assert role_map["papers/x.pdf"] == "moved"
    assert role_map["papers/index.md"] == "generated"
