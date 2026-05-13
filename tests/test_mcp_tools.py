"""Phase 6.1 — MCP tool handler unit tests.

Direct sync calls to each handler in :mod:`app.mcp.tools`. We do NOT
spawn the actual MCP server (stdio JSON-RPC) here — that's an
integration concern. The handlers are the meat of Phase 6.1; testing
them directly is the highest-value, lowest-flake check.

Test isolation: every test uses ``LOCALFLOW_HOME`` pointed at a
tmp_path so we never mutate the real ``~/.localflow/`` state.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.mcp._serialize import to_jsonable
from app.mcp.tools import TOOLS, get_tool


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point LOCALFLOW_HOME at tmp_path so runs/, memory/, etc. are
    sandboxed for each test."""
    monkeypatch.setenv("LOCALFLOW_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def mini_workspace(tmp_path: Path) -> Path:
    """A small, well-behaved workspace folder_organizer can act on."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "report.pdf").write_text("fake pdf content", encoding="utf-8")
    (ws / "notes.txt").write_text("a note", encoding="utf-8")
    (ws / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return ws


# --------------------------------------------------------------- registry


def test_all_tools_registered() -> None:
    """We claim 15 MCP tools in the plan. If someone adds/removes one,
    update the plan + this test."""
    names = {t.name for t in TOOLS}
    assert len(names) == len(TOOLS), "duplicate tool names"
    expected = {
        # read-only
        "inspect_workspace",
        "list_skills",
        "list_tools_catalog",
        "list_runs",
        "read_run",
        "read_memory_prefs",
        "read_memory_audit",
        # state-changing
        "create_plan",
        "dry_run",
        "execute_plan",
        "rollback_run",
        # memory mutations
        "memory_forbid_path",
        "memory_unforbid_path",
        "memory_set_naming_style",
        "memory_unset_naming_style",
    }
    assert names == expected


def test_get_tool_returns_none_for_unknown() -> None:
    assert get_tool("nonexistent") is None


def test_each_tool_has_a_json_schema() -> None:
    for t in TOOLS:
        assert t.input_schema.get("type") == "object"
        assert "properties" in t.input_schema
        assert callable(t.handler)


# --------------------------------------------------------------- read-only


def test_inspect_workspace_returns_snapshot(isolated_home: Path, mini_workspace: Path) -> None:
    result = get_tool("inspect_workspace").handler({"path": str(mini_workspace)})
    assert result["total_files"] == 3
    files = {f["path"] for f in result["files"]}
    assert files == {"report.pdf", "notes.txt", "data.csv"}


def test_inspect_workspace_requires_path(isolated_home: Path) -> None:
    with pytest.raises(ValueError, match="missing required argument"):
        get_tool("inspect_workspace").handler({})


def test_list_skills_returns_builtins(isolated_home: Path) -> None:
    result = get_tool("list_skills").handler({})
    names = {s["name"] for s in result["skills"]}
    # The 4 built-ins MUST be there. External skills (workspace_stats)
    # may or may not be present depending on the user's environment —
    # don't pin on them.
    assert {"folder_organizer", "pdf_indexer", "data_reporter", "data_analyzer"}.issubset(names)
    # Every entry has the expected shape
    for s in result["skills"]:
        assert "name" in s and "version" in s and "origin" in s
        assert "required_tools" in s and "allowed_actions" in s


def test_list_tools_catalog_returns_15_tools(isolated_home: Path) -> None:
    result = get_tool("list_tools_catalog").handler({})
    assert len(result["tools"]) == 15
    categories = {t["category"] for t in result["tools"]}
    assert categories == {"read", "transform", "render"}
    # data_ops.read_tabular should be marked as used by data_reporter + data_analyzer
    by_name = {t["name"]: t for t in result["tools"]}
    assert "data_ops.read_tabular" in by_name
    used = set(by_name["data_ops.read_tabular"]["used_by"])
    assert {"data_reporter", "data_analyzer"}.issubset(used)


def test_list_runs_empty_when_no_runs(isolated_home: Path) -> None:
    result = get_tool("list_runs").handler({})
    assert result == {"runs": []}


def test_read_memory_prefs_returns_defaults(isolated_home: Path) -> None:
    result = get_tool("read_memory_prefs").handler({})
    assert result["naming_style"] == "original"
    assert result["forbidden_paths"] == []
    assert result["schema_version"] == 1


def test_read_memory_audit_empty_initially(isolated_home: Path) -> None:
    result = get_tool("read_memory_audit").handler({"limit": 5})
    assert result == {"entries": []}


# --------------------------------------------------------------- state-changing roundtrip


def test_create_plan_dry_run_execute_rollback_roundtrip(
    isolated_home: Path, mini_workspace: Path
) -> None:
    """The big one: create_plan → dry_run → execute_plan → rollback_run
    must all work in sequence, with workspace state restored at the
    end. This is the single integration assertion that proves MCP can
    drive a real LocalFlow lifecycle."""
    create = get_tool("create_plan").handler({
        "workspace": str(mini_workspace),
        "goal": "organize my files",
        "skill": "folder_organizer",
    })
    assert "task_id" in create
    assert create["action_count"] > 0
    assert create["risk_passed"] is True
    task_id = create["task_id"]

    dry = get_tool("dry_run").handler({"task_id": task_id})
    assert dry["task_id"] == task_id
    assert "markdown" in dry and len(dry["markdown"]) > 0

    # Execute requires explicit approval; missing approval → error.
    with pytest.raises(ValueError, match="approved=true"):
        get_tool("execute_plan").handler({"task_id": task_id})

    exec_result = get_tool("execute_plan").handler({
        "task_id": task_id,
        "approved": True,
    })
    assert exec_result["success"] is True
    assert exec_result["verification_passed"] is True
    # At least the move actions ran.
    assert exec_result["executed_count"] >= 1

    # After execute, the files have been categorized
    categorized = list(mini_workspace.rglob("*.pdf"))
    assert any(p.parent.name == "papers" for p in categorized)

    # Rollback restores them
    rb = get_tool("rollback_run").handler({"task_id": task_id})
    assert rb["success"] is True
    assert len(rb["undone"]) >= 1
    # File is back at root, papers/ dir gone
    assert (mini_workspace / "report.pdf").exists()
    assert not (mini_workspace / "papers" / "report.pdf").exists()


def test_create_plan_rejects_unknown_skill(
    isolated_home: Path, mini_workspace: Path
) -> None:
    with pytest.raises(ValueError, match="unknown skill"):
        get_tool("create_plan").handler({
            "workspace": str(mini_workspace),
            "goal": "x",
            "skill": "nonexistent_skill",
        })


def test_list_runs_shows_created_runs(
    isolated_home: Path, mini_workspace: Path
) -> None:
    create = get_tool("create_plan").handler({
        "workspace": str(mini_workspace),
        "goal": "organize",
    })
    listing = get_tool("list_runs").handler({})
    ids = {r["task_id"] for r in listing["runs"]}
    assert create["task_id"] in ids


def test_read_run_returns_artifacts(
    isolated_home: Path, mini_workspace: Path
) -> None:
    create = get_tool("create_plan").handler({
        "workspace": str(mini_workspace),
        "goal": "organize",
    })
    task_id = create["task_id"]
    run = get_tool("read_run").handler({"task_id": task_id})
    assert run["task"]["task_id"] == task_id
    assert run["task"]["workspace_root"] == str(mini_workspace)
    assert run["plan"]["plan_id"] == create["plan_id"]


def test_rollback_run_fails_without_manifest(isolated_home: Path) -> None:
    with pytest.raises(ValueError, match="no rollback manifest"):
        get_tool("rollback_run").handler({"task_id": "2026-05-13-999"})


# --------------------------------------------------------------- memory mutations


def test_memory_forbid_writes_through(isolated_home: Path) -> None:
    res = get_tool("memory_forbid_path").handler({"path": "secrets"})
    assert res["changed"] is True
    prefs = get_tool("read_memory_prefs").handler({})
    assert "secrets" in prefs["forbidden_paths"]


def test_memory_forbid_idempotent(isolated_home: Path) -> None:
    h = get_tool("memory_forbid_path").handler
    r1 = h({"path": "x"})
    r2 = h({"path": "x"})
    assert r1["changed"] is True
    assert r2["changed"] is False


def test_memory_unforbid_writes_through(isolated_home: Path) -> None:
    get_tool("memory_forbid_path").handler({"path": "tmp"})
    res = get_tool("memory_unforbid_path").handler({"path": "tmp"})
    assert res["changed"] is True
    prefs = get_tool("read_memory_prefs").handler({})
    assert "tmp" not in prefs["forbidden_paths"]


def test_memory_set_naming_style(isolated_home: Path) -> None:
    res = get_tool("memory_set_naming_style").handler({"value": "snake_case"})
    assert res["changed"] is True
    prefs = get_tool("read_memory_prefs").handler({})
    assert prefs["naming_style"] == "snake_case"


def test_memory_set_naming_style_rejects_unknown(isolated_home: Path) -> None:
    with pytest.raises(ValueError, match="unknown naming_style"):
        get_tool("memory_set_naming_style").handler({"value": "camelCase"})


def test_memory_unset_naming_style(isolated_home: Path) -> None:
    get_tool("memory_set_naming_style").handler({"value": "kebab-case"})
    res = get_tool("memory_unset_naming_style").handler({})
    assert res["changed"] is True
    prefs = get_tool("read_memory_prefs").handler({})
    assert prefs["naming_style"] == "original"


def test_memory_audit_records_mcp_mutations(isolated_home: Path) -> None:
    get_tool("memory_forbid_path").handler({"path": "abc"})
    get_tool("memory_set_naming_style").handler({"value": "lower"})
    audit = get_tool("read_memory_audit").handler({"limit": 20})
    events = [e["event"] for e in audit["entries"]]
    assert "memory.forbid" in events
    assert "memory.set" in events


# --------------------------------------------------------------- serialization


def test_to_jsonable_dumps_clean_json() -> None:
    """Every handler return value must survive ``json.dumps``."""
    from datetime import datetime
    from enum import Enum
    from pathlib import Path as P

    class Color(Enum):
        RED = "red"

    payload = {
        "ts": datetime(2026, 5, 13, 12, 0, 0),
        "path": P("/a/b"),
        "color": Color.RED,
        "nested": {"vals": (1, 2, 3)},
        "set_field": {"a", "b"},
    }
    safe = to_jsonable(payload)
    text = json.dumps(safe, ensure_ascii=False)
    parsed = json.loads(text)
    assert parsed["ts"].startswith("2026-05-13T")
    assert parsed["color"] == "red"
    assert sorted(parsed["set_field"]) == ["a", "b"]


# --------------------------------------------------------------- forbidden_paths integration


def test_create_plan_inherits_forbidden_paths_from_memory(
    isolated_home: Path, mini_workspace: Path
) -> None:
    """Memory prefs apply through MCP exactly like they do through CLI:
    forbidden_paths set via memory propagates to the TaskSpec, and the
    risk check flags actions touching forbidden paths."""
    secrets_dir = mini_workspace / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "creds.txt").write_text("apikey", encoding="utf-8")

    get_tool("memory_forbid_path").handler({"path": "secrets"})
    create = get_tool("create_plan").handler({
        "workspace": str(mini_workspace),
        "goal": "organize",
    })
    assert "secrets" in create["applied_preferences"]["forbidden_paths"]
    assert create["risk_passed"] is False
    assert any(
        "forbidden_paths" in w and "secrets" in w
        for w in create["warnings"]
    )
