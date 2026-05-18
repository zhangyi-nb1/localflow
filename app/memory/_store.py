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

        Phase 13: ``_migrate`` upgrades pre-v3 prefs.json files by
        backfilling new fields with their defaults. No write happens
        here — the upgraded payload is materialised in memory; the
        next save() rewrites the file at the new schema_version.
        """
        if not self.prefs_path.exists():
            return MemoryPreferences()
        try:
            raw = self.prefs_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            data = _migrate(data)
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

    # -- mutations: prefer_llm_planner --------------------------------

    def set_prefer_llm_planner(self, value: bool) -> MutationResult:
        """Persist the ``prefer_llm_planner`` toggle. Read by the UI's
        auto-detect to bypass the goal-text heuristic and always pick
        the LLM planner for skills that support it."""
        prefs = self.load()
        if prefs.prefer_llm_planner == value:
            return MutationResult(
                event="memory.set.noop",
                changed=False,
                detail=f"prefer_llm_planner already {value}",
            )
        before = prefs.prefer_llm_planner
        prefs.prefer_llm_planner = value
        self.save(prefs)
        self._audit(
            "memory.set",
            key="prefer_llm_planner",
            before=before,
            after=value,
        )
        return MutationResult(
            event="memory.set",
            changed=True,
            detail=f"prefer_llm_planner: {before} → {value}",
        )

    def clear_prefer_llm_planner(self) -> MutationResult:
        """Reset to factory default (False)."""
        return self.set_prefer_llm_planner(False)

    # -- mutations: enable_semantic_verifier (Phase 13) ----------------

    def set_enable_semantic_verifier(self, value: bool) -> MutationResult:
        """Persist the Phase 13 semantic-verifier opt-in toggle. When
        True, ``localflow execute`` (CLI + UI) runs LLM-as-judge
        graders after structural verify and can trigger the auto-repair
        loop. Default False because semantic verification adds LLM cost
        on every execute."""
        prefs = self.load()
        if prefs.enable_semantic_verifier == value:
            return MutationResult(
                event="memory.set.noop",
                changed=False,
                detail=f"enable_semantic_verifier already {value}",
            )
        before = prefs.enable_semantic_verifier
        prefs.enable_semantic_verifier = value
        self.save(prefs)
        self._audit(
            "memory.set",
            key="enable_semantic_verifier",
            before=before,
            after=value,
        )
        return MutationResult(
            event="memory.set",
            changed=True,
            detail=f"enable_semantic_verifier: {before} → {value}",
        )

    # -- mutations: max_auto_repairs (Phase 13) ------------------------

    def set_max_auto_repairs(self, value: int) -> MutationResult:
        """Persist the Phase 13 auto-repair attempt cap. 0 means
        'run semantic verifier in report-only mode'. Bounded [0, 5]
        at the schema level so the consumer never sees an unsafe value."""
        prefs = self.load()
        if prefs.max_auto_repairs == value:
            return MutationResult(
                event="memory.set.noop",
                changed=False,
                detail=f"max_auto_repairs already {value}",
            )
        before = prefs.max_auto_repairs
        # Validate via the schema's Field(ge=0, le=5) before we touch disk.
        prefs.max_auto_repairs = value
        # Re-validate to catch out-of-range values cleanly.
        MemoryPreferences.model_validate(prefs.model_dump())
        self.save(prefs)
        self._audit(
            "memory.set",
            key="max_auto_repairs",
            before=before,
            after=value,
        )
        return MutationResult(
            event="memory.set",
            changed=True,
            detail=f"max_auto_repairs: {before} → {value}",
        )

    # -- mutations: fetch_allowed_domains (v0.16) ----------------------

    @staticmethod
    def _normalize_domain(host: str) -> str:
        """Canonicalize: strip whitespace, lowercase, no scheme/path/port.

        Rejects entries containing '/' or '://' since those would be URLs,
        not hostnames. Punycode is accepted as-is — the urllib URL parser
        already normalises IDN at parse time.
        """
        h = host.strip().lower()
        if not h:
            raise ValueError("domain is empty")
        if "://" in h or "/" in h:
            raise ValueError(f"expected bare hostname, got URL-ish value: {host!r}")
        if ":" in h:
            raise ValueError(f"hostname should not include port: {host!r}")
        return h

    def add_fetch_allowed_domain(self, host: str) -> MutationResult:
        canonical = self._normalize_domain(host)
        prefs = self.load()
        if canonical in prefs.fetch_allowed_domains:
            return MutationResult(
                event="memory.allow_domain.noop",
                changed=False,
                detail=f"{canonical!r} already in fetch_allowed_domains",
            )
        before = list(prefs.fetch_allowed_domains)
        prefs.fetch_allowed_domains = sorted(prefs.fetch_allowed_domains + [canonical])
        self.save(prefs)
        self._audit(
            "memory.allow_domain",
            domain=canonical,
            before=before,
            after=prefs.fetch_allowed_domains,
        )
        return MutationResult(
            event="memory.allow_domain",
            changed=True,
            detail=f"added {canonical!r} to fetch_allowed_domains",
        )

    def remove_fetch_allowed_domain(self, host: str) -> MutationResult:
        canonical = self._normalize_domain(host)
        prefs = self.load()
        if canonical not in prefs.fetch_allowed_domains:
            return MutationResult(
                event="memory.disallow_domain.noop",
                changed=False,
                detail=f"{canonical!r} was not in fetch_allowed_domains",
            )
        before = list(prefs.fetch_allowed_domains)
        prefs.fetch_allowed_domains = [d for d in prefs.fetch_allowed_domains if d != canonical]
        self.save(prefs)
        self._audit(
            "memory.disallow_domain",
            domain=canonical,
            before=before,
            after=prefs.fetch_allowed_domains,
        )
        return MutationResult(
            event="memory.disallow_domain",
            changed=True,
            detail=f"removed {canonical!r} from fetch_allowed_domains",
        )


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Upgrade prefs.json payloads from older schema_versions in place.

    Each migration is idempotent: re-running on an already-migrated
    payload is a no-op. New fields get their schema defaults backfilled
    so :func:`MemoryPreferences.model_validate` doesn't reject the load.

    Phase 13 introduces v3 (enable_semantic_verifier, max_auto_repairs).
    v0.16 introduces v4 (fetch_allowed_domains).
    """
    version = int(raw.get("schema_version", 1) or 1)
    if version < 3:
        raw.setdefault("enable_semantic_verifier", False)
        raw.setdefault("max_auto_repairs", 2)
        raw["schema_version"] = 3
    if version < 4:
        raw.setdefault("fetch_allowed_domains", [])
        raw["schema_version"] = 4
    return raw
