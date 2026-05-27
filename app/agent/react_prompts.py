"""Phase 30.1 — back-compat re-export.

The prompt templates + tool schema for the react loop moved to
``localflow_kernel/react_prompts.py`` so the kernel package stays free
of ``app.agent.*`` imports (boundary lint enforced in
``tests/test_kernel_boundary.py``).

Every existing ``from app.agent.react_prompts import ...`` keeps
working because we re-export the public names here. If new callers
appear post-Phase 30, prefer importing directly from
``localflow_kernel.react_prompts``.
"""

from __future__ import annotations

from localflow_kernel.react_prompts import (
    SYSTEM_PROMPT,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    build_loop_decision_tool_schema,
    render_loop_user_prompt,
)

__all__ = [
    "SYSTEM_PROMPT",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "build_loop_decision_tool_schema",
    "render_loop_user_prompt",
]
