"""Phase 30.2 — kernel boundary lint.

Traverses every module reachable from ``localflow_kernel`` and asserts
none of them transitively pull in an application-layer package
(``app.skills``, ``app.recipes``, ``app.cli``, ``app.ui``, ``app.eval``,
``app.memory``, ``app.primitives``, ``app.templates``, ``app.mcp``).

This is the long-term invariant that protects the boundary: if someone
adds an application-layer import to a kernel module, the kernel package
will start re-exporting it transitively and this test trips.

The check is static (parses ``ast`` of each .py file) rather than
runtime (``sys.modules`` after import) because runtime tracking would
catch test-imported app modules too. Static parsing means we look only
at what the kernel modules themselves declare.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import localflow_kernel

# Paths that may NEVER appear in a kernel module's import list.
FORBIDDEN_PREFIXES = (
    "app.skills",
    "app.recipes",
    "app.cli",
    "app.ui",
    "app.eval",
    "app.memory",
    "app.primitives",
    "app.templates",
    "app.mcp",
    "app.main",
    "app.agent.client",  # AnthropicClient / FakeLLMClient — concrete impls
    "app.agent.react_prompts",  # application-layer prompt templates
    "app.agent.prompts",
    "app.agent.planner",
    "app.agent.preview",
)

# Specific files under app/harness/ that are explicitly NOT in the
# kernel (application-layer orchestrators that depend on Skill, eval
# graders, etc.). If a kernel module imports them, the boundary breaks.
FORBIDDEN_HARNESS_MODULES = (
    "app.harness.control_loop",
    "app.harness.repair_loop",
    "app.harness.semantic_verifier",
    "app.harness.recipe_repair",
    "app.harness.taskgraph_runner",
)


def _collect_imports(py_path: Path) -> list[str]:
    """Parse a .py file and return the dotted module paths it imports."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level != 0:
                continue
            # `from app.harness.executor import Executor`
            # → record both the module path and the symbol module path
            # (the latter matters when callers do `from app import x`
            # and `x` is actually a submodule).
            out.append(node.module)
    return out


def _resolve_kernel_module_file(modname: str) -> Path:
    """Find the source file for a kernel-reachable module name."""
    mod = importlib.import_module(modname)
    file = getattr(mod, "__file__", None)
    if file is None:
        raise pytest.skip(f"{modname} has no __file__ (likely a namespace package)")
    return Path(file)


def _walk_kernel_modules() -> list[str]:
    """Names of every module reachable from localflow_kernel.*, plus the
    underlying app.* implementation modules they re-export from."""
    names: set[str] = set()
    # kernel package itself
    pkg = localflow_kernel
    pkg_path = Path(pkg.__file__).parent  # type: ignore[arg-type]
    for entry in pkgutil.iter_modules([str(pkg_path)]):
        names.add(f"localflow_kernel.{entry.name}")
    names.add("localflow_kernel")

    # The underlying app.* modules that the facade re-exports from.
    # These are the modules whose import declarations must also stay
    # clean — facade re-exports inherit whatever the implementation
    # pulls in.
    impl_modules = [
        # schemas (whole package — every submodule)
        "app.schemas",
        "app.schemas.action",
        "app.schemas.approval",
        "app.schemas.compute",
        "app.schemas.execution",
        "app.schemas.plan",
        "app.schemas.react",
        "app.schemas.recipe",
        "app.schemas.risk",
        "app.schemas.rollback",
        "app.schemas.semantic",
        "app.schemas.skill",
        "app.schemas.source_ledger",
        "app.schemas.task",
        "app.schemas.taskgraph",
        "app.schemas.trace",
        "app.schemas.verification",
        "app.schemas.workspace",
        # pure-kernel harness modules (see PHASE_30_DESIGN.md §2.1)
        "app.harness.action_validator",
        "app.harness.approval",
        "app.harness.audit",
        "app.harness.checkpoint",
        "app.harness.context",
        "app.harness.dry_run",
        "app.harness.executor",
        "app.harness.policy_guard",
        "app.harness.react_loop",
        "app.harness.rollback",
        "app.harness.sandbox",
        "app.harness.trace",
        "app.harness.verifier",
        # kernel-pure tools + storage
        "app.tools.workspace",
        "app.tools.docker_workspace",
        "app.tools.file_ops",
        "app.tools.hash_ops",
        "app.tools.scratch",
        "app.storage.run_store",
        "app.storage.jsonl_logger",
    ]
    names.update(impl_modules)
    return sorted(names)


class TestKernelBoundary:
    """Static boundary check: kernel modules must NOT import from
    application-layer packages. The check is module-graph-wide, not
    file-by-file, so a regression anywhere along the reachable graph
    trips this test."""

    def test_no_forbidden_prefix_imports(self):
        offenders: list[str] = []
        for modname in _walk_kernel_modules():
            file = _resolve_kernel_module_file(modname)
            imports = _collect_imports(file)
            for imp in imports:
                for prefix in FORBIDDEN_PREFIXES:
                    if imp == prefix or imp.startswith(prefix + "."):
                        offenders.append(
                            f"{modname} ({file}) imports {imp!r} (forbidden prefix: {prefix})"
                        )
        assert not offenders, "\n".join(offenders)

    def test_no_forbidden_harness_module_imports(self):
        offenders: list[str] = []
        for modname in _walk_kernel_modules():
            file = _resolve_kernel_module_file(modname)
            imports = _collect_imports(file)
            for imp in imports:
                if imp in FORBIDDEN_HARNESS_MODULES:
                    offenders.append(
                        f"{modname} ({file}) imports {imp!r} (application-layer orchestrator)"
                    )
        assert not offenders, "\n".join(offenders)


class TestKernelBoundaryHelpers:
    """Sanity checks for the boundary lint plumbing itself."""

    def test_collect_imports_catches_from_app(self, tmp_path: Path):
        sample = tmp_path / "sample.py"
        sample.write_text(
            "from __future__ import annotations\n"
            "from app.skills._base import Skill\n"
            "import app.cli\n"
            "from app.schemas import ActionPlan\n"
        )
        imports = _collect_imports(sample)
        assert "app.skills._base" in imports
        assert "app.cli" in imports
        assert "app.schemas" in imports

    def test_walk_includes_all_facade_submodules(self):
        names = _walk_kernel_modules()
        for required in (
            "localflow_kernel",
            "localflow_kernel.schemas",
            "localflow_kernel.harness",
            "localflow_kernel.workspace",
            "localflow_kernel.storage",
            "localflow_kernel.llm",
        ):
            assert required in names, f"{required} missing from kernel walk"
