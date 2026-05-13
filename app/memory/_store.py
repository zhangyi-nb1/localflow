"""Phase 5 — MemoryStore: persist user preferences with audit.

On-disk layout::

    ~/.localflow/memory/
        prefs.json     ← MemoryPreferences (Pydantic-serialized)
        audit.jsonl    ← One JSON object per mutation (timestamped)

Every write is atomic (write-temp + rename) so a crash leaves either
the old prefs or the new one — never a half-written file.

Every mutation appends to audit.jsonl with timestamp + event + diff so
the user can trace exactly when a preference was set / changed / cleared.
This is the **only** persistent record of memory changes — there is no
"undo last memory change" in this MVP (the user re-issues an opposite
mutation manually).

The store is **independent of TaskSpec / run_store**. Memory lives
across tasks; it is not part of any single run's state.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.memory._schema import MemoryPreferences, NamingStyle

PREFS_JSON = "prefs.json"
AUDIT_JSONL = "audit.jsonl"


class MemoryStoreError(RuntimeError):
    """Raised on schema mismatch or IO corruption."""


@dataclass
class MutationResult:
    """Returned by mutation methods so the CLI can report what changed."""

    event: str
    changed: bool
    detail: str = ""


def _memory_home(base: Path | None = None) -> Path:
    """Resolve the memory directory.

    Precedence:
      1. ``base`` argument (tests)
      2. ``$LOCALFLOW_HOME/memory`` (env var override)
      3. ``~/.localflow/memory``
    """
    if base is not None:
        return Path(base)
    env = os.environ.get("LOCALFLOW_HOME")
    if env:
        return Path(env) / "memory"
    return Path.home() / ".localflow" / "memory"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    """Owns prefs.json + audit.jsonl under ``home``.

    Stateless beyond the directory path — every operation re-reads from
    disk so concurrent CLI invocations don't race on cached state.
    """

    def __init__(self, home: Path | None = None) -> None:
        self.home = _memory_home(home)
        self.home.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------

    @property
    def prefs_path(self) -> Path:
        return self.home / PREFS_JSON

    @property
    def audit_path(self) -> Path:
        return self.home / AUDIT_JSONL

    # -- load / save ---------------------------------------------------

    def load(self) -> MemoryPreferences:
        """Return the persisted prefs or factory defaults if absent.

        Raises MemoryStoreError on JSON/schema corruption — better to
        fail loudly than silently apply defaults over real user state.
        """
        if not self.prefs_path.exists():
            return MemoryPreferences()
        try:
            raw = self.prefs_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return MemoryPreferences.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise MemoryStoreError(f"corrupt prefs.json at {self.prefs_path}: {exc}") from exc

    def save(self, prefs: MemoryPreferences) -> None:
        """Atomic write: temp file in same dir, then rename."""
        payload = prefs.model_dump(mode="json")
        text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".prefs_", suffix=".tmp", dir=str(self.home))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.write("\n")
            os.replace(tmp_path, self.prefs_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # -- audit ---------------------------------------------------------

    def _audit(self, event: str, **fields: Any) -> None:
        record = {"ts": _utc_iso(), "event": event, **fields}
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    def read_audit(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return audit entries, newest last. ``limit=None`` means all."""
        if not self.audit_path.exists():
            return []
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip corrupt lines rather than refusing to print any
                # audit at all — the user came here BECAUSE memory is
                # broken, don't make recovery harder.
                continue
        if limit is not None:
            return entries[-limit:]
        return entries

    # -- mutations: forbidden_paths -----------------------------------

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Canonicalize a workspace-relative path for storage.

        Strip leading/trailing whitespace and trailing slashes; reject
        absolute paths and parent-directory traversals (those would be
        meaningless as workspace-relative entries). Forward slashes
        preserved as the canonical separator (matches the rest of
        LocalFlow — see Action.target_path)."""
        p = path.strip().replace("\\", "/")
        if not p:
            raise ValueError("path is empty")
        if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
            raise ValueError(f"absolute paths not allowed in memory: {path!r}")
        if any(part == ".." for part in p.split("/")):
            raise ValueError(f"parent-directory traversal not allowed: {path!r}")
        return p.rstrip("/")

    def add_forbidden_path(self, path: str) -> MutationResult:
        canonical = self._normalize_path(path)
        prefs = self.load()
        if canonical in prefs.forbidden_paths:
            return MutationResult(
                event="memory.forbid.noop",
                changed=False,
                detail=f"{canonical!r} already in forbidden_paths",
            )
        before = list(prefs.forbidden_paths)
        prefs.forbidden_paths = sorted(prefs.forbidden_paths + [canonical])
        self.save(prefs)
        self._audit(
            "memory.forbid",
            path=canonical,
            before=before,
            after=prefs.forbidden_paths,
        )
        return MutationResult(
            event="memory.forbid",
            changed=True,
            detail=f"added {canonical!r} to forbidden_paths",
        )

    def remove_forbidden_path(self, path: str) -> MutationResult:
        canonical = self._normalize_path(path)
        prefs = self.load()
        if canonical not in prefs.forbidden_paths:
            return MutationResult(
                event="memory.unforbid.noop",
                changed=False,
                detail=f"{canonical!r} was not in forbidden_paths",
            )
        before = list(prefs.forbidden_paths)
        prefs.forbidden_paths = [p for p in prefs.forbidden_paths if p != canonical]
        self.save(prefs)
        self._audit(
            "memory.unforbid",
            path=canonical,
            before=before,
            after=prefs.forbidden_paths,
        )
        return MutationResult(
            event="memory.unforbid",
            changed=True,
            detail=f"removed {canonical!r} from forbidden_paths",
        )

    # -- mutations: naming_style ---------------------------------------

    def set_naming_style(self, style: str) -> MutationResult:
        try:
            new_style = NamingStyle(style)
        except ValueError:
            valid = ", ".join(s.value for s in NamingStyle)
            raise ValueError(f"unknown naming_style {style!r}; valid: {valid}") from None
        prefs = self.load()
        if prefs.naming_style == new_style:
            return MutationResult(
                event="memory.set.noop",
                changed=False,
                detail=f"naming_style already {new_style.value!r}",
            )
        before = prefs.naming_style.value
        prefs.naming_style = new_style
        self.save(prefs)
        self._audit(
            "memory.set",
            key="naming_style",
            before=before,
            after=new_style.value,
        )
        return MutationResult(
            event="memory.set",
            changed=True,
            detail=f"naming_style: {before} → {new_style.value}",
        )

    def clear_naming_style(self) -> MutationResult:
        return self.set_naming_style(NamingStyle.ORIGINAL.value)
