"""Phase 8.0 / v0.7.0 — Streamlit UI soft-sandbox tests.

The Streamlit UI itself needs a browser runtime to exercise, so the
unit-testable layer is the soft-sandbox helper. These tests pin the
contract that the UI relies on:

  * sandbox_root resolves to ``<cwd>/sandbox/``
  * list_sandbox_workspaces returns subdirs only (not files, not root)
  * validate_workspace refuses out-of-sandbox by default
  * ``?unsafe=1`` query lifts the restriction
  * Truthy-value parsing matches the conventions used elsewhere
    (LOCALFLOW_DISABLE_EXTERNAL_SKILLS, LOCALFLOW_MCP_ALLOW_DANGEROUS)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ui._sandbox import (
    find_eligible_workspace_choices,
    get_unsafe_mode_from_query,
    is_inside_sandbox,
    list_sandbox_workspaces,
    sandbox_root,
    validate_workspace,
)

# --------------------------------------------------------------- sandbox_root


def test_sandbox_root_under_cwd(tmp_path: Path) -> None:
    assert sandbox_root(tmp_path) == (tmp_path / "sandbox").resolve()


# --------------------------------------------------------------- is_inside_sandbox


def test_is_inside_sandbox_accepts_real_subdir(tmp_path: Path) -> None:
    (tmp_path / "sandbox" / "demo").mkdir(parents=True)
    assert is_inside_sandbox(tmp_path / "sandbox" / "demo", cwd=tmp_path)


def test_is_inside_sandbox_rejects_outside(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    assert not is_inside_sandbox(tmp_path / "elsewhere", cwd=tmp_path)


def test_is_inside_sandbox_rejects_nonexistent(tmp_path: Path) -> None:
    """You can't claim a path is 'inside sandbox' before it exists —
    UI surface forces the workspace to be real."""
    assert not is_inside_sandbox(tmp_path / "sandbox" / "ghost", cwd=tmp_path)


def test_is_inside_sandbox_rejects_parent_traversal(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "sandbox").mkdir()
    bad = tmp_path / "sandbox" / ".." / "elsewhere"
    # resolve() collapses '..' so this ends up under elsewhere — outside sandbox.
    assert not is_inside_sandbox(bad, cwd=tmp_path)


# --------------------------------------------------------------- list_sandbox_workspaces


def test_list_workspaces_empty_when_no_sandbox(tmp_path: Path) -> None:
    assert list_sandbox_workspaces(tmp_path) == []


def test_list_workspaces_returns_subdirs_sorted(tmp_path: Path) -> None:
    (tmp_path / "sandbox" / "c_demo").mkdir(parents=True)
    (tmp_path / "sandbox" / "a_demo").mkdir(parents=True)
    (tmp_path / "sandbox" / "b_demo").mkdir(parents=True)
    # A file at sandbox root should NOT be reported as a workspace.
    (tmp_path / "sandbox" / "stray.txt").write_text("hi", encoding="utf-8")
    out = list_sandbox_workspaces(tmp_path)
    assert [p.name for p in out] == ["a_demo", "b_demo", "c_demo"]


# --------------------------------------------------------------- validate_workspace


def test_validate_workspace_accepts_safe(tmp_path: Path) -> None:
    target = tmp_path / "sandbox" / "demo"
    target.mkdir(parents=True)
    got = validate_workspace(target, unsafe_mode=False, cwd=tmp_path)
    assert got == target.resolve()


def test_validate_workspace_rejects_outside_in_safe_mode(tmp_path: Path) -> None:
    (tmp_path / "other_dir").mkdir()
    with pytest.raises(ValueError, match="outside the soft sandbox"):
        validate_workspace(tmp_path / "other_dir", unsafe_mode=False, cwd=tmp_path)


def test_validate_workspace_accepts_outside_in_unsafe_mode(tmp_path: Path) -> None:
    (tmp_path / "other_dir").mkdir()
    got = validate_workspace(tmp_path / "other_dir", unsafe_mode=True, cwd=tmp_path)
    assert got == (tmp_path / "other_dir").resolve()


def test_validate_workspace_rejects_nonexistent(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        validate_workspace(
            tmp_path / "sandbox" / "ghost",
            unsafe_mode=False,
            cwd=tmp_path,
        )


def test_validate_workspace_rejects_file_not_dir(tmp_path: Path) -> None:
    (tmp_path / "sandbox").mkdir()
    f = tmp_path / "sandbox" / "file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a directory"):
        validate_workspace(f, unsafe_mode=False, cwd=tmp_path)


# --------------------------------------------------------------- get_unsafe_mode_from_query


def test_unsafe_mode_truthy_values() -> None:
    for v in ("1", "true", "True", "yes", "on", "TRUE"):
        assert get_unsafe_mode_from_query({"unsafe": v}), v


def test_unsafe_mode_falsy_values() -> None:
    for v in ("0", "false", "no", "off", "", "anything-else"):
        assert not get_unsafe_mode_from_query({"unsafe": v}), v


def test_unsafe_mode_missing_key() -> None:
    assert not get_unsafe_mode_from_query({})


def test_unsafe_mode_list_form() -> None:
    """Older Streamlit versions return query params as lists."""
    assert get_unsafe_mode_from_query({"unsafe": ["1"]})
    assert not get_unsafe_mode_from_query({"unsafe": []})


# --------------------------------------------------------------- find_eligible_workspace_choices


def test_eligible_choices_safe_mode_is_sandbox_subdirs(tmp_path: Path) -> None:
    (tmp_path / "sandbox" / "demo_a").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    out = list(find_eligible_workspace_choices(unsafe_mode=False, cwd=tmp_path))
    assert len(out) == 1
    assert out[0].name == "demo_a"


# ---------------------------------------------------- v0.8.1 unsafe sticky regression


def test_resolve_unsafe_latches_to_session_state(monkeypatch) -> None:
    """v0.8.1 regression: Streamlit multi-page navigation strips URL
    query parameters. Without persistence, the user lands on /Plan
    with ``unsafe=False`` and the custom workspace silently reverts
    to a sandbox subdir. ``_resolve_unsafe`` must latch to a
    session-state bit once seen so the opt-in survives nav.
    """

    # Pure-Python fake of the bits of streamlit _layout reads.
    class _FakeStreamlit:
        def __init__(self) -> None:
            self.query_params: dict = {}
            self.session_state: dict = {}

    import sys

    fake_st = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    from app.ui import _layout

    monkeypatch.setattr(_layout, "st", fake_st)

    # 1. First visit with ?unsafe=1 → returns True AND latches.
    fake_st.query_params = {"unsafe": "1"}
    assert _layout._resolve_unsafe() is True
    assert fake_st.session_state[_layout.SESSION_UNSAFE_KEY] is True

    # 2. Page nav drops the query param. Without latch this would
    #    return False; with latch it stays True.
    fake_st.query_params = {}
    assert _layout._resolve_unsafe() is True

    # 3. Sanity: a fresh session (empty session_state, no query param)
    #    is NOT in unsafe mode. The latch is per-session, not global.
    fake_st.session_state = {}
    fake_st.query_params = {}
    assert _layout._resolve_unsafe() is False


# ---------------------------------------------------- workspace switch state reset


def test_workspace_switch_clears_workspace_scoped_ui_state(monkeypatch, tmp_path: Path) -> None:
    """Switching workspaces must not keep a prior task/preview selected.

    Regression coverage for the UI showing a new active workspace while
    Plan / Execute / Rollback still retained task state from the old one.
    """

    class _FakeStreamlit:
        def __init__(self) -> None:
            self.session_state: dict = {}

    from app.ui import _layout

    fake_st = _FakeStreamlit()
    old_ws = tmp_path / "sandbox" / "old"
    new_ws = tmp_path / "sandbox" / "new"
    old_ws.mkdir(parents=True)
    new_ws.mkdir()
    fake_st.session_state = {
        _layout.SESSION_WORKSPACE_KEY: str(old_ws),
        _layout.SESSION_TASK_KEY: "2026-05-25-001",
        _layout.SESSION_TOKEN_KEY: "tok",
        _layout.SESSION_DRY_RUN_KEY: "dry run",
        "_last_dry_assessment": {"passed": True},
        "_rb_preview": object(),
        "approval_checkbox": True,
        "exec_task_select": "old label",
        "rb_task_select": "old rollback label",
    }
    monkeypatch.setattr(_layout, "st", fake_st)

    assert _layout._sync_workspace_selection(new_ws) == new_ws
    assert fake_st.session_state[_layout.SESSION_WORKSPACE_KEY] == str(new_ws)
    for key in _layout.WORKSPACE_SCOPED_SESSION_KEYS:
        assert key not in fake_st.session_state


def test_same_workspace_preserves_workspace_scoped_ui_state(monkeypatch, tmp_path: Path) -> None:
    """A normal rerun on the same workspace should not clear active task state."""

    class _FakeStreamlit:
        def __init__(self) -> None:
            self.session_state: dict = {}

    from app.ui import _layout

    fake_st = _FakeStreamlit()
    ws = tmp_path / "sandbox" / "same"
    ws.mkdir(parents=True)
    fake_st.session_state = {
        _layout.SESSION_WORKSPACE_KEY: str(ws),
        _layout.SESSION_TASK_KEY: "2026-05-25-001",
    }
    monkeypatch.setattr(_layout, "st", fake_st)

    assert _layout._sync_workspace_selection(ws) == ws
    assert fake_st.session_state[_layout.SESSION_TASK_KEY] == "2026-05-25-001"
