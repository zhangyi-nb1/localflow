"""Phase 23 — Scratch workspace lifecycle for ``PYTHON_COMPUTE`` actions.

Owns the on-disk layout under ``<home>/scratch/<task_id>/<action_id>/``:

    inputs/    — workspace files copied in by the executor (read-only intent)
    outputs/   — script-produced artifacts (declared via ArtifactSpec)
    script.py  — the script that will be executed by SandboxRuntime
    stdout.log — captured stdout (last N bytes)
    stderr.log — captured stderr (last N bytes)

The scratch directory is **outside the user's workspace** by design —
Principle #2: outputs must never land in the workspace directly, even
accidentally. A subsequent pack stage (MOVE / COPY) is required to
promote a declared artifact into the workspace.

§10.7 invariant: this is application-layer plumbing on top of the
kernel's PYTHON_COMPUTE dispatch. The kernel touch is the
``DELETE_SCRATCH_DIR`` rollback op (registered alongside the action
type as part of the same deliberate exception row in the ledger).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.schemas.compute import ComputeInputRef


@dataclass
class ScratchLayout:
    """Resolved paths for one PYTHON_COMPUTE action's scratch dir."""

    root: Path
    inputs_dir: Path
    outputs_dir: Path
    script_path: Path
    stdout_path: Path
    stderr_path: Path


class ScratchWorkspace:
    """Owns ``<home>/scratch/`` — one subtree per task, one sub-subtree per action."""

    SCRATCH_DIR_NAME = "scratch"
    INPUTS_DIR_NAME = "inputs"
    OUTPUTS_DIR_NAME = "outputs"
    SCRIPT_NAME = "script.py"
    STDOUT_NAME = "stdout.log"
    STDERR_NAME = "stderr.log"

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.root = self.home / self.SCRATCH_DIR_NAME

    def task_dir(self, task_id: str) -> Path:
        return self.root / task_id

    def action_dir(self, task_id: str, action_id: str) -> Path:
        return self.task_dir(task_id) / action_id

    def create_for_action(self, task_id: str, action_id: str) -> ScratchLayout:
        """Create (or recreate) the per-action scratch directory.

        If the directory already exists from a prior run, it is wiped
        first — scratch is per-execute, never resumed. The host action
        already has an ``action_id`` unique to this run.
        """
        action_root = self.action_dir(task_id, action_id)
        if action_root.exists():
            shutil.rmtree(action_root)
        inputs = action_root / self.INPUTS_DIR_NAME
        outputs = action_root / self.OUTPUTS_DIR_NAME
        inputs.mkdir(parents=True, exist_ok=True)
        outputs.mkdir(parents=True, exist_ok=True)
        return ScratchLayout(
            root=action_root,
            inputs_dir=inputs,
            outputs_dir=outputs,
            script_path=action_root / self.SCRIPT_NAME,
            stdout_path=action_root / self.STDOUT_NAME,
            stderr_path=action_root / self.STDERR_NAME,
        )

    def copy_inputs(
        self,
        layout: ScratchLayout,
        workspace_root: Path,
        inputs: list[ComputeInputRef],
    ) -> list[Path]:
        """Copy each declared input from ``workspace_root`` into the
        scratch ``inputs/`` directory.

        Refuses to follow symlinks that point outside the workspace —
        the executor's policy_guard already rejects such inputs at plan
        time, but defense-in-depth is cheap here.
        """
        workspace_root = workspace_root.resolve()
        copied: list[Path] = []
        for ref in inputs:
            normalized = ref.rel_path.replace("\\", "/").lstrip("/")
            src = (workspace_root / normalized).resolve()
            try:
                src.relative_to(workspace_root)
            except ValueError as exc:
                raise ValueError(
                    f"input {ref.rel_path!r} resolves outside workspace"
                ) from exc
            if not src.is_file():
                raise FileNotFoundError(f"input not a file: {ref.rel_path}")
            # Preserve the relative structure under inputs/ so scripts
            # can read inputs/sub/dir/foo.csv unambiguously.
            dst = layout.inputs_dir / normalized
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)
        return copied

    def cleanup_action(self, task_id: str, action_id: str) -> bool:
        """Remove one action's scratch subtree. Returns True if anything was removed."""
        action_root = self.action_dir(task_id, action_id)
        if not action_root.exists():
            return False
        shutil.rmtree(action_root)
        # Best-effort: if the task dir is now empty, prune it too.
        task_root = self.task_dir(task_id)
        try:
            if task_root.exists() and not any(task_root.iterdir()):
                task_root.rmdir()
        except OSError:
            pass
        return True

    def cleanup_task(self, task_id: str) -> bool:
        """Remove the whole task's scratch subtree."""
        task_root = self.task_dir(task_id)
        if not task_root.exists():
            return False
        shutil.rmtree(task_root)
        return True
