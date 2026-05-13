"""Phase 5 — MemoryStore unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.memory import (
    MemoryPreferences,
    MemoryStore,
    MemoryStoreError,
    NamingStyle,
)


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(home=tmp_path / "memory")


def test_load_returns_defaults_when_no_prefs_file(tmp_path: Path) -> None:
    s = _store(tmp_path)
    prefs = s.load()
    assert prefs.forbidden_paths == []
    assert prefs.naming_style == NamingStyle.ORIGINAL
    assert prefs.schema_version == 1
    assert prefs.is_default()


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.save(
        MemoryPreferences(
            forbidden_paths=["secrets", "private/docs"],
            naming_style=NamingStyle.SNAKE_CASE,
        )
    )
    prefs = s.load()
    assert prefs.forbidden_paths == ["secrets", "private/docs"]
    assert prefs.naming_style == NamingStyle.SNAKE_CASE
    assert not prefs.is_default()


def test_add_forbidden_path_is_idempotent(tmp_path: Path) -> None:
    s = _store(tmp_path)
    r1 = s.add_forbidden_path("secrets")
    r2 = s.add_forbidden_path("secrets")
    assert r1.changed is True
    assert r2.changed is False
    assert "already" in r2.detail
    assert s.load().forbidden_paths == ["secrets"]


def test_add_forbidden_path_normalizes_separator(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add_forbidden_path("private\\docs\\")  # windows-y + trailing slash
    assert s.load().forbidden_paths == ["private/docs"]


def test_add_forbidden_path_rejects_absolute_and_traversal(tmp_path: Path) -> None:
    s = _store(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        s.add_forbidden_path("/etc/passwd")
    with pytest.raises(ValueError, match="absolute"):
        s.add_forbidden_path("C:/Windows")
    with pytest.raises(ValueError, match="traversal"):
        s.add_forbidden_path("../escape")


def test_remove_forbidden_path_noop_when_absent(tmp_path: Path) -> None:
    s = _store(tmp_path)
    r = s.remove_forbidden_path("never_added")
    assert r.changed is False


def test_remove_forbidden_path_removes(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add_forbidden_path("a")
    s.add_forbidden_path("b")
    s.remove_forbidden_path("a")
    assert s.load().forbidden_paths == ["b"]


def test_set_naming_style_rejects_unknown(tmp_path: Path) -> None:
    s = _store(tmp_path)
    with pytest.raises(ValueError, match="unknown naming_style"):
        s.set_naming_style("camelCase")  # not in our enum


def test_set_naming_style_then_clear(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.set_naming_style("snake_case")
    assert s.load().naming_style == NamingStyle.SNAKE_CASE
    s.clear_naming_style()
    assert s.load().naming_style == NamingStyle.ORIGINAL


def test_audit_log_appended_in_order(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add_forbidden_path("a")
    s.set_naming_style("snake_case")
    s.remove_forbidden_path("a")
    entries = s.read_audit()
    assert [e["event"] for e in entries] == [
        "memory.forbid",
        "memory.set",
        "memory.unforbid",
    ]
    assert entries[0]["path"] == "a"
    assert entries[1]["key"] == "naming_style"
    assert entries[1]["after"] == "snake_case"


def test_audit_log_noop_writes_nothing(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add_forbidden_path("a")
    s.add_forbidden_path("a")  # noop
    s.set_naming_style("original")  # already default → noop
    entries = s.read_audit()
    # Only the first add_forbidden_path should have written an entry.
    assert len(entries) == 1
    assert entries[0]["event"] == "memory.forbid"


def test_corrupt_prefs_json_raises_memory_store_error(tmp_path: Path) -> None:
    s = _store(tmp_path)
    # write invalid JSON
    s.prefs_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(MemoryStoreError, match="corrupt prefs.json"):
        s.load()


def test_schema_mismatch_raises_memory_store_error(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.prefs_path.write_text(
        json.dumps({"naming_style": "EMOJI_CASE"}),  # not in enum
        encoding="utf-8",
    )
    with pytest.raises(MemoryStoreError, match="corrupt prefs.json"):
        s.load()


def test_save_is_atomic_no_partial_file(tmp_path: Path) -> None:
    """Verify temp-file + rename pattern leaves no orphan ``.tmp`` files
    on success."""
    s = _store(tmp_path)
    s.save(MemoryPreferences(forbidden_paths=["x"]))
    tmps = list(s.home.glob(".prefs_*.tmp"))
    assert tmps == [], f"orphan temp files: {tmps}"
