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


def test_get_tool_returns_none_for_unknown(isolated_home: Path) -> None:
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
    drive a real LocalFlow lifecycle.

    Phase 7 / Issue 2 fix: execute_plan now requires an approval_token
    minted by dry_run (not just `approved=true`).
    """
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
    # Phase 7 / Issue 2: dry_run must now mint an approval token
    assert "approval_token" in dry
    assert "approval_expires_at" in dry
    token = dry["approval_token"]
    assert isinstance(token, str) and len(token) >= 32

    # Execute without token → rejected.
    with pytest.raises(ValueError, match="missing required argument: 'approval_token'"):
        get_tool("execute_plan").handler({"task_id": task_id})

    # Execute with token → succeeds.
    exec_result = get_tool("execute_plan").handler({
        "task_id": task_id,
        "approval_token": token,
    })
    assert exec_result["success"] is True
    assert exec_result["verification_passed"] is True
    # At least the move actions ran.
    assert exec_result["executed_count"] >= 1

    # After execute, the files have been categorized
    categorized = list(mini_workspace.rglob("*.pdf"))
    assert any(p.parent.name == "papers" for p in categorized)

    # Token is one-shot — second execute with same token must fail.
    with pytest.raises(ValueError, match="no approval token found"):
        get_tool("execute_plan").handler({
            "task_id": task_id,
            "approval_token": token,
        })

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


def test_memory_unforbid_writes_through(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """unforbid is dangerous by default — opt in via env var to test it."""
    monkeypatch.setenv("LOCALFLOW_MCP_ALLOW_DANGEROUS", "1")
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


# --------------------------------------------------------------- dangerous tool gating (Issue 3 fix)


def test_memory_unforbid_is_hidden_by_default(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`memory_unforbid_path` REMOVES a user-set safety boundary, so it
    must NOT be advertised by the MCP server unless explicitly opted in.
    A buggy MCP client that calls it before our env flag is set should
    see it as an unknown tool, not silently weaken the user's settings."""
    from app.mcp.tools import visible_tools

    monkeypatch.delenv("LOCALFLOW_MCP_ALLOW_DANGEROUS", raising=False)
    names = {t.name for t in visible_tools()}
    assert "memory_unforbid_path" not in names
    # Lookup via get_tool returns None too (treated as unknown).
    assert get_tool("memory_unforbid_path") is None


def test_memory_unforbid_visible_when_opted_in(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.mcp.tools import visible_tools

    monkeypatch.setenv("LOCALFLOW_MCP_ALLOW_DANGEROUS", "1")
    names = {t.name for t in visible_tools()}
    assert "memory_unforbid_path" in names
    assert get_tool("memory_unforbid_path") is not None


def test_safe_tools_always_visible(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-only + adds-restriction tools must remain visible
    regardless of the env flag."""
    from app.mcp.tools import visible_tools

    monkeypatch.delenv("LOCALFLOW_MCP_ALLOW_DANGEROUS", raising=False)
    names = {t.name for t in visible_tools()}
    safe = {
        "inspect_workspace",
        "list_skills",
        "list_runs",
        "read_memory_prefs",
        "create_plan",
        "dry_run",
        "execute_plan",
        "rollback_run",
        "memory_forbid_path",
        "memory_set_naming_style",
    }
    assert safe.issubset(names)


def test_dangerous_env_flag_truthy_values(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.mcp.tools import _dangerous_enabled

    for value in ("1", "true", "True", "yes", "on", "TRUE"):
        monkeypatch.setenv("LOCALFLOW_MCP_ALLOW_DANGEROUS", value)
        assert _dangerous_enabled(), f"value {value!r} should enable"
    for value in ("0", "false", "no", "off", "", "anything-else"):
        monkeypatch.setenv("LOCALFLOW_MCP_ALLOW_DANGEROUS", value)
        assert not _dangerous_enabled(), f"value {value!r} should NOT enable"


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


# --------------------------------------------------------------- approval tokens
#
# Phase 7 / Issue 2 fix — `execute_plan` requires an `approval_token`
# minted by a prior `dry_run` call. Tests below pin the contract:
#   1. dry_run mints token; execute consumes it
#   2. no token → reject
#   3. wrong token → reject
#   4. expired token → reject
#   5. plan modification after token mint → reject (hash drift)
#   6. workspace mismatch → reject (defense in depth)


def _plan_with_token(workspace: Path) -> tuple[str, str]:
    """Helper: create_plan + dry_run, return (task_id, token)."""
    create = get_tool("create_plan").handler({
        "workspace": str(workspace),
        "goal": "organize",
        "skill": "folder_organizer",
    })
    dry = get_tool("dry_run").handler({"task_id": create["task_id"]})
    return create["task_id"], dry["approval_token"]


def test_execute_plan_requires_token(isolated_home: Path, mini_workspace: Path) -> None:
    task_id, _ = _plan_with_token(mini_workspace)
    with pytest.raises(ValueError, match="missing required argument"):
        get_tool("execute_plan").handler({"task_id": task_id})


def test_execute_plan_rejects_wrong_token(
    isolated_home: Path, mini_workspace: Path
) -> None:
    task_id, _ = _plan_with_token(mini_workspace)
    with pytest.raises(ValueError, match="approval token rejected"):
        get_tool("execute_plan").handler({
            "task_id": task_id,
            "approval_token": "not-the-real-token-just-some-junk-string",
        })


def test_execute_plan_rejects_token_after_plan_modification(
    isolated_home: Path, mini_workspace: Path
) -> None:
    """If the plan.json file is modified after the token is minted
    (e.g., user re-planned), the token must become invalid — otherwise
    we lose the binding between what the user dry-ran and what runs."""
    from app.storage.run_store import RunStore

    task_id, token = _plan_with_token(mini_workspace)
    # Tamper with plan.json — overwrite with a trivially modified copy.
    store = RunStore(task_id=task_id)
    plan = store.load_plan()
    # Modify the summary; everything else stays.
    plan_dict = plan.model_dump(mode="json")
    plan_dict["summary"] = plan_dict["summary"] + " (modified)"
    import json
    store.plan_path.write_text(
        json.dumps(plan_dict, indent=2), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="plan.json has changed"):
        get_tool("execute_plan").handler({
            "task_id": task_id,
            "approval_token": token,
        })


def test_execute_plan_rejects_expired_token(
    isolated_home: Path, mini_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fast-forward time past the 10-minute TTL."""
    from datetime import datetime, timedelta, timezone

    from app.mcp import approval

    task_id, token = _plan_with_token(mini_workspace)

    # Patch _utc_now to return a time 11 minutes in the future.
    real_now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        approval, "_utc_now", lambda: real_now + timedelta(minutes=11)
    )
    with pytest.raises(ValueError, match="expired"):
        get_tool("execute_plan").handler({
            "task_id": task_id,
            "approval_token": token,
        })


def test_dry_run_minted_token_includes_expiry(
    isolated_home: Path, mini_workspace: Path
) -> None:
    """The dry_run response must include both the token and its
    expiry, so the client can decide if it's stale before retrying."""
    task_id, _ = _plan_with_token(mini_workspace)
    dry = get_tool("dry_run").handler({"task_id": task_id})
    assert "approval_token" in dry
    assert "approval_expires_at" in dry
    # Expiry must be a parseable ISO string in the future.
    from datetime import datetime
    expires = datetime.fromisoformat(dry["approval_expires_at"])
    from datetime import timezone
    assert expires > datetime.now(timezone.utc)


def test_dry_run_remints_token_when_called_again(
    isolated_home: Path, mini_workspace: Path
) -> None:
    """Calling dry_run twice should issue a fresh token; the old one
    becomes invalid (file overwritten)."""
    task_id, first_token = _plan_with_token(mini_workspace)
    second_dry = get_tool("dry_run").handler({"task_id": task_id})
    second_token = second_dry["approval_token"]
    assert first_token != second_token

    # First token now points at a file containing the second token's data.
    with pytest.raises(ValueError, match="approval token rejected"):
        get_tool("execute_plan").handler({
            "task_id": task_id,
            "approval_token": first_token,
        })

    # Second token works.
    result = get_tool("execute_plan").handler({
        "task_id": task_id,
        "approval_token": second_token,
    })
    assert result["success"] is True
