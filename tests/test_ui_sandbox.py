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
