"""Phase 7 (Issue 2 fix) — MCP approval tokens.

Replaces the original ``approved: bool`` argument on ``execute_plan``
with a cryptographic token bound to a specific (task, plan, dry-run,
workspace) state. Defends against MCP clients trying to skip the
dry-run inspection step or re-execute a plan after it was modified.

**Why ``approved: bool`` wasn't enough**

The CLI's ``--yes`` flag has a human at the keyboard typing it after
they read the dry-run output. The MCP ``approved=true`` argument is
just a string in a JSON-RPC message — the *MCP client* (Claude Code,
a script, anything else) controls it, not the human. A buggy or
malicious client could:

  1. ``create_plan`` to get a task_id
  2. Skip ``dry_run`` entirely
  3. ``execute_plan(approved=true)`` immediately

That bypasses the whole purpose of dry-run. The fix binds execute to
a previous dry-run via a one-shot token:

  1. Client calls ``dry_run(task_id)`` → server mints a token bound to
     (task_id, plan_hash, dry_run_hash, workspace_root, ttl=10 min)
  2. Token is written to ``<run_dir>/approval_token.json``
  3. Client sees the dry-run markdown + the token in the response
  4. Client calls ``execute_plan(task_id, approval_token=<token>)``
  5. Server validates: token exists, not expired, plan/workspace
     haven't drifted; consumes (deletes file) on success
  6. Future ``execute_plan`` calls with the same token fail

Any of these invalidate the token:
  * 10 minutes pass without execute
  * The plan.json file is rewritten (re-planning)
  * The dry_run.md file is regenerated
  * The workspace_root in the task spec changes
  * Token already consumed (one-shot)

The CLI ``execute --yes`` path doesn't use tokens — the human at the
keyboard *is* the approval. CLI calls ``control_loop.run_execute``
directly without going through this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.storage.run_store import RunStore

# Token TTL — short enough to be safe, long enough that a human reading
# the dry-run output doesn't time out.
TOKEN_TTL = timedelta(minutes=10)
TOKEN_FILE = "approval_token.json"


class ApprovalError(RuntimeError):
    """Raised when an approval token is missing / expired / drifted /
    already consumed."""


@dataclass(frozen=True)
class ApprovalToken:
    """A one-shot approval token bound to a specific task state.

    Stored as JSON in ``<run_dir>/approval_token.json``. Consumed on
    successful execute (the file is deleted). All hashes are SHA-256
    hex digests of the relevant artifact's UTF-8 bytes.
    """

    token: str
    task_id: str
    plan_hash: str
    dry_run_hash: str
    workspace_root: str
    created_at: str  # ISO 8601
    expires_at: str  # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "task_id": self.task_id,
            "plan_hash": self.plan_hash,
            "dry_run_hash": self.dry_run_hash,
            "workspace_root": self.workspace_root,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalToken":
        return cls(
            token=data["token"],
            task_id=data["task_id"],
            plan_hash=data["plan_hash"],
            dry_run_hash=data["dry_run_hash"],
            workspace_root=data["workspace_root"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
        )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _token_path(run_store: RunStore) -> Path:
    return run_store.run_dir / TOKEN_FILE


def mint_token(run_store: RunStore, workspace_root: str) -> ApprovalToken:
    """Issue a fresh token for the task. Overwrites any prior token
    for the same task (re-running dry-run yields a fresh token).

    Caller must ensure ``plan.json`` and ``dry_run.md`` exist for the
    task before calling this — otherwise plan_hash / dry_run_hash
    can't be computed.
    """
    plan_path = run_store.plan_path
    dry_run_path = run_store.dry_run_path
    if not plan_path.exists():
        raise ApprovalError(f"cannot mint token: plan.json missing for {run_store.task_id!r}")
    if not dry_run_path.exists():
        raise ApprovalError(f"cannot mint token: dry_run.md missing for {run_store.task_id!r}")

    now = _utc_now()
    token = ApprovalToken(
        token=secrets.token_urlsafe(32),
        task_id=run_store.task_id,
        plan_hash=_sha256_file(plan_path),
        dry_run_hash=_sha256_file(dry_run_path),
        workspace_root=workspace_root,
        created_at=now.isoformat(timespec="seconds"),
        expires_at=(now + TOKEN_TTL).isoformat(timespec="seconds"),
    )
    _write_atomic(_token_path(run_store), token.to_dict())
    return token


def validate_and_consume(
    run_store: RunStore,
    token_str: str,
    workspace_root: str,
) -> ApprovalToken:
    """Validate ``token_str`` against the stored token and consume it.

    Raises :class:`ApprovalError` on any of:
      * no token file (no dry_run was run, or token already consumed)
      * token string mismatch
      * expired (current time > expires_at)
      * plan_hash drifted (plan.json modified after token was minted)
      * dry_run_hash drifted (dry_run.md modified after token minted)
      * workspace_root mismatch (different from when token was minted)

    On success, deletes the token file (one-shot consumption) and
    returns the validated token for caller inspection / logging.
    """
    token_path = _token_path(run_store)
    if not token_path.exists():
        raise ApprovalError(
            "no approval token found — call dry_run first, then execute_plan "
            "with the token from its response"
        )

    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
        stored = ApprovalToken.from_dict(data)
    except (json.JSONDecodeError, KeyError) as exc:
        raise ApprovalError(f"corrupt approval_token.json: {exc}") from exc

    if not secrets.compare_digest(stored.token, token_str):
        raise ApprovalError("approval_token does not match the stored token for this task")

    now = _utc_now()
    expires_at = datetime.fromisoformat(stored.expires_at)
    if now >= expires_at:
        raise ApprovalError(
            f"approval_token expired at {stored.expires_at} (now {now.isoformat(timespec='seconds')}); "
            "re-run dry_run to mint a new token"
        )

    # Re-hash current plan + dry_run to detect drift.
    if not run_store.plan_path.exists():
        raise ApprovalError("plan.json missing — cannot validate token")
    current_plan_hash = _sha256_file(run_store.plan_path)
    if not secrets.compare_digest(current_plan_hash, stored.plan_hash):
        raise ApprovalError(
            "plan.json has changed since dry_run; the token is invalid. "
            "Re-run dry_run to issue a fresh token bound to the current plan."
        )

    if run_store.dry_run_path.exists():
        current_dry_hash = _sha256_file(run_store.dry_run_path)
        if not secrets.compare_digest(current_dry_hash, stored.dry_run_hash):
            raise ApprovalError(
                "dry_run.md has changed since the token was minted; re-run dry_run."
            )

    if stored.workspace_root != workspace_root:
        raise ApprovalError(
            f"workspace_root drift: token bound to {stored.workspace_root!r}, "
            f"current request is for {workspace_root!r}"
        )

    # Consume — one-shot.
    try:
        token_path.unlink()
    except OSError as exc:
        raise ApprovalError(f"could not consume token file: {exc}") from exc

    return stored


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    """Temp-file + rename. Atomic on Windows (Python 3.3+) and POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".approval_", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
