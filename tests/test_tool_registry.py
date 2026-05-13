"""Phase 4.2 — Tool Registry tests.

Verifies the contracts that make the Tool Registry a useful declarative
audit surface:
  * register / get / has / list operate correctly
  * duplicate registration raises (mirror SkillRegistry behavior)
  * unknown name raises with available-names hint
  * ToolSpec is immutable (frozen dataclass)
  * the default factory registers every callable the built-in skills
    declare in their required_tools, with the expected categories
  * ``file_ops.*`` is intentionally NOT registered (kernel-only IO)
"""

from __future__ import annotations

import pytest

from app.tools import (
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
    get_default_tool_registry,
)


def _make_spec(name: str = "x.y", category: str = "read") -> ToolSpec:
    return ToolSpec(
        name=name,
        callable_ref=lambda: None,
        module="app.tools.fake",
        category=category,  # type: ignore[arg-type]
        description="test",
    )


# --------------------------------------------------------------------- basics


def test_register_and_get_roundtrip() -> None:
    reg = ToolRegistry()
    spec = _make_spec("a.b")
    reg.register(spec)
    assert reg.has("a.b")
    assert reg.get("a.b") is spec


def test_list_names_sorted() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("zeta.x"))
    reg.register(_make_spec("alpha.x"))
    reg.register(_make_spec("mu.x"))
    assert reg.list_names() == ["alpha.x", "mu.x", "zeta.x"]


def test_list_specs_preserves_order_of_list_names() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("zeta.x"))
    reg.register(_make_spec("alpha.x"))
    specs = reg.list_specs()
    assert [s.name for s in specs] == reg.list_names()


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("a.b"))
    with pytest.raises(ToolRegistryError, match="already registered"):
        reg.register(_make_spec("a.b"))


def test_unknown_tool_get_raises_with_hint() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("known.tool"))
    with pytest.raises(ToolRegistryError, match="unknown tool"):
        reg.get("nope.nada")


def test_contains_and_len() -> None:
    reg = ToolRegistry()
    reg.register(_make_spec("a.b"))
    reg.register(_make_spec("c.d"))
    assert "a.b" in reg
    assert "z.z" not in reg
    assert len(reg) == 2


def test_tool_spec_is_frozen() -> None:
    spec = _make_spec("x.y")
    with pytest.raises((AttributeError, TypeError)):
        spec.name = "different"  # type: ignore[misc]


# --------------------------------------------------------------------- default registry


def test_default_registry_is_singleton() -> None:
    a = get_default_tool_registry()
    b = get_default_tool_registry()
    assert a is b


def test_default_registry_has_expected_tools() -> None:
    """Every tool the built-in skills declare in required_tools MUST be
    present in the default registry — that's the whole contract Phase 4.2
    upholds."""
    reg = get_default_tool_registry()
    expected = {
        # file_scan + hash
        "file_scan.scan_workspace",
        "file_scan.classify",
        "hash_ops.sha256_file",
        # previews
        "pdf_ops.extract_text_preview",
        "text_ops.extract_text_preview",
        "text_ops.can_preview_as_text",
        # tabular
        "data_ops.is_csv_like",
        "data_ops.is_excel_like",
        "data_ops.is_supported_tabular",
        "data_ops.read_tabular",
        "data_ops.read_and_describe",
        "data_ops.summarize_dataframe",
        # analysis + render
        "data_analysis.execute_analysis",
        "chart_ops.histogram_png",
        "chart_ops.bar_png",
    }
    actual = set(reg.list_names())
    assert expected.issubset(actual), f"missing: {expected - actual}"


def test_default_registry_categories_are_valid() -> None:
    reg = get_default_tool_registry()
    for spec in reg.list_specs():
        assert spec.category in ("read", "transform", "render"), (
            f"{spec.name} has invalid category {spec.category!r}"
        )


def test_default_registry_chart_ops_are_render() -> None:
    reg = get_default_tool_registry()
    assert reg.get("chart_ops.histogram_png").category == "render"
    assert reg.get("chart_ops.bar_png").category == "render"


def test_default_registry_execute_analysis_is_transform() -> None:
    reg = get_default_tool_registry()
    assert reg.get("data_analysis.execute_analysis").category == "transform"


def test_default_registry_callables_are_real_callables() -> None:
    reg = get_default_tool_registry()
    for spec in reg.list_specs():
        assert callable(spec.callable_ref), f"{spec.name}.callable_ref is not callable"


def test_file_ops_intentionally_excluded() -> None:
    """``file_ops.*`` is mutating IO that only the Executor may call.
    Registering it would blur the Skills-don't-write boundary. This test
    pins that decision so a future contributor doesn't add them by reflex.
    """
    reg = get_default_tool_registry()
    forbidden_prefixes = ("file_ops.",)
    for name in reg.list_names():
        for prefix in forbidden_prefixes:
            assert not name.startswith(prefix), (
                f"file_ops.* must NOT be registered (kernel-only IO); found {name!r}"
            )


def test_default_registry_has_no_side_effects_flagged() -> None:
    """Every Phase 4.2 tool is side-effect-free (Skills don't perform IO
    — the Executor does). If we ever register a tool that writes, this
    invariant should fail loudly and force re-thinking the boundary."""
    reg = get_default_tool_registry()
    for spec in reg.list_specs():
        assert spec.side_effects is False, (
            f"{spec.name}: side_effects=True breaks the Skills-don't-IO contract"
        )
