"""Phase 4.2 — Tool Registry.

Inventories the shared, side-effect-free callables Skills are allowed to
use, and gives each one structured metadata (name, source module,
category, description). Per outline §13.7 "Tool Registry — Composio,
Activepieces: 工具注册、权限声明和可用范围控制".

This is a documentation + verification surface, not a sandbox. Python
imports remain unconstrained — a Skill can still bypass the registry
and ``import`` whatever it wants. The registry's value is:

  * Skills DECLARE their tool dependencies in ``SkillManifest.required_tools``.
  * ``SkillRegistry.register`` verifies the names resolve here.
  * The CLI surfaces the catalog (``localflow tools``) and the per-skill
    dependency list (``localflow skills``).

``app/tools/file_ops.py`` is intentionally **not** registered. Those are
mutating IO primitives only the Executor may call; exposing them via the
Registry would blur the "Skills produce Actions; Executor performs IO"
boundary that铁律 ② / ③ enforce.

Outline §10.7 compliance: nothing here touches ``app/harness/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

ToolCategory = Literal["read", "transform", "render"]


@dataclass(frozen=True)
class ToolSpec:
    """Static, declarative metadata for one tool.

    ``callable_ref`` lets the registry double as a lookup table, but in
    practice Skills will continue to import the function directly — the
    registry exists to *declare* and *verify* the dependency, not mediate
    every call.
    """

    name: str
    callable_ref: Callable
    module: str
    category: ToolCategory
    description: str
    side_effects: bool = False


class ToolRegistryError(RuntimeError):
    """Base class for tool registry errors."""


class ToolRegistry:
    """Process-wide registry of ToolSpec. Mirror ``SkillRegistry``'s
    pattern for symmetry — register at import time, query by name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ToolRegistryError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = spec

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise ToolRegistryError(
                f"unknown tool: {name!r}; available: {', '.join(self.list_names())}"
            )
        return self._tools[name]

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_specs(self) -> list[ToolSpec]:
        return [self._tools[n] for n in self.list_names()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def _build_default_registry() -> ToolRegistry:
    """Register every shared, side-effect-free tool. ``file_ops.*`` is
    deliberately excluded (kernel-only)."""
    from app.tools import (
        chart_ops,
        data_analysis,
        data_ops,
        file_scan,
        hash_ops,
        pdf_ops,
        text_ops,
    )

    registry = ToolRegistry()

    # ----- read: scan / hash / preview ------------------------------------
    registry.register(ToolSpec(
        name="file_scan.scan_workspace",
        callable_ref=file_scan.scan_workspace,
        module="app.tools.file_scan",
        category="read",
        description="Walk a directory and return a WorkspaceSnapshot of every file (with optional SHA-256 + text previews).",
    ))
    registry.register(ToolSpec(
        name="file_scan.classify",
        callable_ref=file_scan.classify,
        module="app.tools.file_scan",
        category="read",
        description="Classify a path by extension into a file category (pdf/excel/text/...).",
    ))
    registry.register(ToolSpec(
        name="hash_ops.sha256_file",
        callable_ref=hash_ops.sha256_file,
        module="app.tools.hash_ops",
        category="read",
        description="Streaming SHA-256 of a file; returns the hex digest.",
    ))

    # ----- read: text / pdf previews --------------------------------------
    registry.register(ToolSpec(
        name="pdf_ops.extract_text_preview",
        callable_ref=pdf_ops.extract_text_preview,
        module="app.tools.pdf_ops",
        category="read",
        description="Extract a text preview from the first pages of a PDF (graceful return None on encoded/scanned/broken PDFs).",
    ))
    registry.register(ToolSpec(
        name="text_ops.extract_text_preview",
        callable_ref=text_ops.extract_text_preview,
        module="app.tools.text_ops",
        category="read",
        description="Read the first ~2000 chars of a text/code/structured/tabular file (refuses binary).",
    ))
    registry.register(ToolSpec(
        name="text_ops.can_preview_as_text",
        callable_ref=text_ops.can_preview_as_text,
        module="app.tools.text_ops",
        category="read",
        description="True if a file_type is text-like and previewable as text.",
    ))

    # ----- read / transform: tabular --------------------------------------
    registry.register(ToolSpec(
        name="data_ops.is_csv_like",
        callable_ref=data_ops.is_csv_like,
        module="app.tools.data_ops",
        category="read",
        description="True if the path is a CSV/TSV-shaped file we can read with pandas.",
    ))
    registry.register(ToolSpec(
        name="data_ops.is_excel_like",
        callable_ref=data_ops.is_excel_like,
        module="app.tools.data_ops",
        category="read",
        description="True if the path is a workbook-shaped file (.xlsx/.xls/.ods) we can read.",
    ))
    registry.register(ToolSpec(
        name="data_ops.is_supported_tabular",
        callable_ref=data_ops.is_supported_tabular,
        module="app.tools.data_ops",
        category="read",
        description="True if the path is any supported tabular format.",
    ))
    registry.register(ToolSpec(
        name="data_ops.read_tabular",
        callable_ref=data_ops.read_tabular,
        module="app.tools.data_ops",
        category="read",
        description="Read a CSV/TSV/XLSX into one or more TabularRead records (one per sheet for workbooks).",
    ))
    registry.register(ToolSpec(
        name="data_ops.read_and_describe",
        callable_ref=data_ops.read_and_describe,
        module="app.tools.data_ops",
        category="read",
        description="Read and produce JSON-safe DataFrameSummary records (thin wrapper around read_tabular + summarize_dataframe).",
    ))
    registry.register(ToolSpec(
        name="data_ops.summarize_dataframe",
        callable_ref=data_ops.summarize_dataframe,
        module="app.tools.data_ops",
        category="transform",
        description="Compute schema + numeric stats + sample rows from an in-memory DataFrame.",
    ))

    # ----- transform: typed analysis --------------------------------------
    registry.register(ToolSpec(
        name="data_analysis.execute_analysis",
        callable_ref=data_analysis.execute_analysis,
        module="app.tools.data_analysis",
        category="transform",
        description="Run a typed AnalysisSpec (filter → groupby+agg → sort → limit → chart) against a DataFrame.",
    ))

    # ----- render: chart bytes --------------------------------------------
    registry.register(ToolSpec(
        name="chart_ops.histogram_png",
        callable_ref=chart_ops.histogram_png,
        module="app.tools.chart_ops",
        category="render",
        description="Render a histogram of a numeric Series to PNG bytes (matplotlib Agg backend).",
    ))
    registry.register(ToolSpec(
        name="chart_ops.bar_png",
        callable_ref=chart_ops.bar_png,
        module="app.tools.chart_ops",
        category="render",
        description="Render a bar chart of a counts dict to PNG bytes (matplotlib Agg backend).",
    ))

    return registry


_default_registry: ToolRegistry | None = None


def get_default_tool_registry() -> ToolRegistry:
    """Return the process-wide default tool registry, lazily built.

    Lazy so importing ``app.tools`` doesn't immediately pull in
    matplotlib / pandas / pypdf — the existing modules already have
    their own lazy-import dance, and we don't want to break it.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = _build_default_registry()
    return _default_registry
