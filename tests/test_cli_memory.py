"""Phase 5 — CLI ``memory`` sub-app round-trip tests.

Uses Typer's CliRunner so we exercise the actual command callbacks and
their argument parsing, not just the underlying MemoryStore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point LOCALFLOW_HOME at tmp_path so memory writes never touch
    the real ``~/.localflow/`` during tests."""
    monkeypatch.setenv("LOCALFLOW_HOME", str(tmp_path))
    return tmp_path


def _runner() -> CliRunner:
    return CliRunner()


def _prefs_path(home: Path) -> Path:
    return home / "memory" / "prefs.json"


def _audit_path(home: Path) -> Path:
    return home / "memory" / "audit.jsonl"


# --------------------------------------------------------------- list


def test_memory_list_shows_defaults_when_empty(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "list"])
    assert r.exit_code == 0, r.stdout
    assert "naming_style" in r.stdout
    assert "original" in r.stdout
    assert "defaults" in r.stdout.lower()


# --------------------------------------------------------------- forbid / unforbid


def test_memory_forbid_writes_prefs_and_audit(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "forbid", "secrets"])
    assert r.exit_code == 0, r.stdout
    assert "added" in r.stdout.lower()

    prefs = json.loads(_prefs_path(isolated_home).read_text(encoding="utf-8"))
    assert prefs["forbidden_paths"] == ["secrets"]

    audit_lines = _audit_path(isolated_home).read_text(encoding="utf-8").splitlines()
    assert any('"memory.forbid"' in line for line in audit_lines)


def test_memory_forbid_then_unforbid_roundtrip(isolated_home: Path) -> None:
    runner = _runner()
    runner.invoke(app, ["memory", "forbid", "a"])
    runner.invoke(app, ["memory", "forbid", "b"])
    r = runner.invoke(app, ["memory", "unforbid", "a"])
    assert r.exit_code == 0
    prefs = json.loads(_prefs_path(isolated_home).read_text(encoding="utf-8"))
    assert prefs["forbidden_paths"] == ["b"]


def test_memory_forbid_idempotent(isolated_home: Path) -> None:
    runner = _runner()
    runner.invoke(app, ["memory", "forbid", "x"])
    r = runner.invoke(app, ["memory", "forbid", "x"])
    assert r.exit_code == 0
    assert "already" in r.stdout.lower()


def test_memory_forbid_rejects_absolute(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "forbid", "/etc/passwd"])
    assert r.exit_code != 0
    assert "absolute" in r.stdout.lower() or "invalid" in r.stdout.lower()


def test_memory_unforbid_noop_when_not_present(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "unforbid", "neverhere"])
    assert r.exit_code == 0
    assert "was not" in r.stdout.lower()


# --------------------------------------------------------------- set / unset


def test_memory_set_naming_style(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "set", "naming_style", "snake_case"])
    assert r.exit_code == 0
    prefs = json.loads(_prefs_path(isolated_home).read_text(encoding="utf-8"))
    assert prefs["naming_style"] == "snake_case"


def test_memory_set_rejects_unknown_key(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "set", "tone", "professional"])
    assert r.exit_code != 0
    assert "unknown" in r.stdout.lower()


def test_memory_set_rejects_unknown_naming_value(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "set", "naming_style", "camelCase"])
    assert r.exit_code != 0
    assert "invalid" in r.stdout.lower() or "unknown" in r.stdout.lower()


def test_memory_unset_naming_style(isolated_home: Path) -> None:
    runner = _runner()
    runner.invoke(app, ["memory", "set", "naming_style", "kebab-case"])
    r = runner.invoke(app, ["memory", "unset", "naming_style"])
    assert r.exit_code == 0
    prefs = json.loads(_prefs_path(isolated_home).read_text(encoding="utf-8"))
    assert prefs["naming_style"] == "original"


# --------------------------------------------------------------- audit


def test_memory_audit_lists_recent_entries(isolated_home: Path) -> None:
    runner = _runner()
    runner.invoke(app, ["memory", "forbid", "secrets"])
    runner.invoke(app, ["memory", "set", "naming_style", "snake_case"])
    r = runner.invoke(app, ["memory", "audit"])
    assert r.exit_code == 0
    assert "memory.forbid" in r.stdout
    assert "memory.set" in r.stdout


def test_memory_audit_empty_when_no_changes(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "audit"])
    assert r.exit_code == 0
    assert "no memory audit entries" in r.stdout.lower()


# --------------------------------------------------------------- v0.8.2 prefer_llm_planner


def test_memory_set_prefer_llm_planner_true(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "set", "prefer_llm_planner", "true"])
    assert r.exit_code == 0, r.stdout
    prefs = json.loads(_prefs_path(isolated_home).read_text(encoding="utf-8"))
    assert prefs["prefer_llm_planner"] is True


def test_memory_set_prefer_llm_planner_accepts_aliases(isolated_home: Path) -> None:
    """Match the truthy-value vocabulary used elsewhere (?unsafe=1 /
    LOCALFLOW_DISABLE_EXTERNAL_SKILLS)."""
    runner = _runner()
    for val in ("1", "yes", "on", "TRUE"):
        r = runner.invoke(app, ["memory", "set", "prefer_llm_planner", val])
        assert r.exit_code == 0, f"{val!r}: {r.stdout}"
    prefs = json.loads(_prefs_path(isolated_home).read_text(encoding="utf-8"))
    assert prefs["prefer_llm_planner"] is True


def test_memory_set_prefer_llm_planner_rejects_garbage(isolated_home: Path) -> None:
    r = _runner().invoke(app, ["memory", "set", "prefer_llm_planner", "maybe"])
    assert r.exit_code != 0
    assert "expected" in r.stdout.lower() or "invalid" in r.stdout.lower()


def test_memory_unset_prefer_llm_planner(isolated_home: Path) -> None:
    runner = _runner()
    runner.invoke(app, ["memory", "set", "prefer_llm_planner", "true"])
    r = runner.invoke(app, ["memory", "unset", "prefer_llm_planner"])
    assert r.exit_code == 0
    prefs = json.loads(_prefs_path(isolated_home).read_text(encoding="utf-8"))
    assert prefs["prefer_llm_planner"] is False


def test_memory_list_includes_prefer_llm_planner(isolated_home: Path) -> None:
    """`memory list` shows the current value for every preference key."""
    r = _runner().invoke(app, ["memory", "list"])
    assert r.exit_code == 0
    assert "prefer_llm_planner" in r.stdout
