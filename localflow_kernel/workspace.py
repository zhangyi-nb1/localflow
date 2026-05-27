"""Phase 30.1 — re-export of the Workspace abstraction.

Implementations live in ``app/tools/{workspace,docker_workspace}.py``.
``LocalWorkspace`` is the in-process backend; ``DockerWorkspace`` runs
file mutations inside a container via the docker CLI.

Both honour the same Protocol (``Workspace``), so downstream consumers
swap between them without changing executor wiring:

    from localflow_kernel.workspace import LocalWorkspace
    # or
    from localflow_kernel.workspace import DockerWorkspace
"""

from __future__ import annotations

from app.tools.docker_workspace import (
    DEFAULT_IMAGE as DOCKER_DEFAULT_IMAGE,
)
from app.tools.docker_workspace import (
    DockerWorkspace,
)
from app.tools.workspace import (
    LocalWorkspace,
    Workspace,
    WorkspaceStat,
    parse_workspace_spec,
)

__all__ = [
    "DOCKER_DEFAULT_IMAGE",
    "DockerWorkspace",
    "LocalWorkspace",
    "Workspace",
    "WorkspaceStat",
    "parse_workspace_spec",
]
