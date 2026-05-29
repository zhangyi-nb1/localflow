"""Phase 37 — failure-mode benchmark report shape."""

from __future__ import annotations

from dataclasses import dataclass

# Status values:
#   "mitigated" — a runtime ablation where guard-off ships the failure
#                 and guard-on prevents/flags it.
#   "gap"       — LocalFlow has no runtime guard; both modes fail. Honest.
#   "process"   — mitigated by a process control (ledger / boundary lint),
#                 not a per-task runtime number.
STATUS_MITIGATED = "mitigated"
STATUS_GAP = "gap"
STATUS_PROCESS = "process"


@dataclass(frozen=True)
class FailureModeReport:
    """One failure mode's ablation result.

    ``guarded_failed`` / ``unguarded_failed`` are None for the
    ``process`` status (no per-task runtime number)."""

    feishu_id: int
    mode: str
    mitigation: str
    status: str
    guarded_failed: bool | None
    unguarded_failed: bool | None
    detail: str

    @property
    def guard_helps(self) -> bool:
        """True iff the guard made the difference (mitigated modes)."""
        return self.guarded_failed is False and self.unguarded_failed is True
