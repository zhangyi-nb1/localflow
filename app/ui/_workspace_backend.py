"""Phase 35.2 — honest UI ↔ Workspace-backend bridge.

Background: Phase 34.2 added a Settings tab + sidebar badge that let
the user pick a Workspace backend (`local` / `docker:<image>` /
`ssh:<host>`) and persisted it to ``memory.workspace_backend_spec``.
But the Plan / Execute pages never consumed that spec — they always
ran the default ``LocalWorkspace``. So picking ``docker:...``, saving,
then hitting Execute silently ran on the host. That "saved but
ignored" mismatch is the half-finished smell the Phase 35 plan §35.2
flags.

Decision (Phase 35.2): rather than fake-drive containers / remote
hosts from inside Streamlit's rerun model — which has real
container-lifecycle fragility and doesn't serve the local-only
flagship (verifiable literature review) — be **honest**:

  * ``local`` is genuinely the UI's execution backend.
  * ``docker:`` / ``ssh:`` are surfaced as a validated spec-builder +
    **CLI bridge**: the UI tells the user the exact
    ``localflow execute --workspace <spec>`` command to run, and is
    explicit that the UI itself executes locally.

This removes the smell (CLAUDE.md rule F — honesty), keeps the four
backends visible + reachable (via the CLI), and needs no fragile
container-in-Streamlit lifecycle code.

This module is a **pure function** (no Streamlit import) so it can be
unit-tested headless. The Streamlit pages render its output.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UIBackendNotice:
    """What the UI should tell the user about the active backend.

    ``executes_locally`` is the load-bearing field: True means the UI
    drives this backend directly (only ``local`` today); False means
    the UI executes against the local sandbox and the user should use
    the ``cli_command`` to run on the chosen backend.
    """

    spec: str
    kind: str  # "local" | "docker" | "ssh" | "unknown"
    executes_locally: bool
    cli_command: str | None
    message: str


def describe_ui_backend(spec: str | None, *, task_id: str | None = None) -> UIBackendNotice:
    """Map a persisted ``workspace_backend_spec`` to a UI notice.

    Pure + total — never raises, never constructs a Workspace. The
    ``task_id`` (when known) is interpolated into the CLI command so
    the user can copy-paste it directly; otherwise a ``<task-id>``
    placeholder is used.
    """
    normalized = (spec or "local").strip()
    task_ref = task_id or "<task-id>"

    if normalized in ("", "local"):
        return UIBackendNotice(
            spec="local",
            kind="local",
            executes_locally=True,
            cli_command=None,
            message="The UI executes against your local sandbox (LocalWorkspace).",
        )

    if normalized.startswith("docker:"):
        kind = "docker"
    elif normalized.startswith("ssh:"):
        kind = "ssh"
    else:
        kind = "unknown"

    cli_command = f"localflow execute --task-id {task_ref} --workspace {normalized}"
    message = (
        f"The `{normalized}` backend runs via the CLI — the UI itself executes "
        f"against your local sandbox. To run this task on `{normalized}`, use the "
        f"command below."
    )
    return UIBackendNotice(
        spec=normalized,
        kind=kind,
        executes_locally=False,
        cli_command=cli_command,
        message=message,
    )


__all__ = ["UIBackendNotice", "describe_ui_backend"]
