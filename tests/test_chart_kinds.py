"""Phase 11 — pie + line chart kinds in chart_ops.

v0.11 chart_ops only had bar + histogram. data_analysis already
routed kind='line' to an inline matplotlib call (legacy code path).
v0.12 consolidates everything in chart_ops and adds pie support so
data_analyzer's planner can produce real proportion views from
≤6-category groupby results — the missing chart kind the v0.11 bug
report user explicitly asked for.
"""

from __future__ import annotations

import pytest

pytest.importorskip("matplotlib")

from app.schemas.analysis import ChartRequest  # noqa: E402
from app.tools.chart_ops import bar_png, line_png, pie_png  # noqa: E402


def test_pie_png_returns_png_bytes() -> None:
    """Smoke test: pie with 4 categories produces a non-empty PNG
    byte string starting with the PNG magic signature."""
    counts = {"NA": 100, "EU": 200, "APAC": 150, "LATAM": 50}
    png = pie_png(counts, title="Revenue share by region")
    assert isinstance(png, bytes)
    assert len(png) > 100
    # PNG magic number
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_pie_png_collapses_slices_beyond_max() -> None:
    """A pie with 10 slices and max_slices=5 should still produce a
    valid PNG — the renderer collapses the tail into '(other)'."""
    counts = {f"cat_{i}": i + 1 for i in range(10)}
    png = pie_png(counts, title="Distribution", max_slices=5)
    assert isinstance(png, bytes)
    assert len(png) > 100


def test_pie_png_handles_zero_total_gracefully() -> None:
    """All zeros → renderer emits a 'no data' placeholder image rather
    than crashing matplotlib's pie() (which rejects all-zero values)."""
    png = pie_png({"a": 0, "b": 0}, title="Empty pie")
    assert isinstance(png, bytes)
    assert len(png) > 100


def test_line_png_renders_simple_series() -> None:
    xs = [1, 2, 3, 4, 5]
    ys = [10, 11, 12, 13, 14]
    png = line_png(xs, ys, title="Trend", xlabel="quarter", ylabel="revenue")
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_line_png_handles_string_x_axis() -> None:
    """matplotlib accepts string x-values — useful for monthly labels
    coming from CSV columns parsed as object dtype."""
    xs = ["Jan", "Feb", "Mar"]
    ys = [10.0, 20.0, 15.0]
    png = line_png(xs, ys, title="Monthly", xlabel="month", ylabel="value")
    assert len(png) > 100


def test_bar_png_still_renders_after_pie_addition() -> None:
    """Regression guard: adding pie / line should not break the
    existing bar code path the v0.11 eval suite depends on."""
    png = bar_png({"a": 1, "b": 2, "c": 3}, title="t", xlabel="x")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_chart_request_accepts_pie_kind() -> None:
    """The Literal in ChartRequest.kind must include 'pie' for the
    data_analyzer planner heuristic to emit it without a schema
    rejection further down the pipeline."""
    req = ChartRequest(kind="pie", x="region", y="revenue", title="Share")
    assert req.kind == "pie"


def test_chart_request_rejects_unknown_kind() -> None:
    """Pydantic should fence off non-allowed kinds — guards against an
    LLM hallucinating 'scatter' or 'heatmap' which chart_ops can't
    render today."""
    with pytest.raises(Exception):
        ChartRequest(kind="scatter", x="a", y="b")


# ──────────────────────────────────── data_analyzer planner heuristic


def test_planner_picks_pie_for_low_cardinality_groupby() -> None:
    """When the dominant categorical has ≤6 distinct values the planner
    should pick 'pie' (proportion view) instead of bar — matches the
    v0.12.0 motivating user request ('用饼图展示分类占比')."""
    pd = pytest.importorskip("pandas")
    from app.skills.data_analyzer.planner import _choose_default_spec

    df = pd.DataFrame(
        {
            "region": ["North", "South", "East", "West"] * 3,
            "revenue": [100, 200, 150, 50, 110, 210, 160, 60, 120, 220, 170, 70],
        }
    )
    spec = _choose_default_spec("sales.csv", df, sheet=None)
    assert spec is not None
    assert spec.chart is not None
    assert spec.chart.kind == "pie"


def test_planner_picks_bar_for_higher_cardinality_groupby() -> None:
    """7+ categories → readability flips to bar (existing v0.11 behaviour)."""
    pd = pytest.importorskip("pandas")
    from app.skills.data_analyzer.planner import _choose_default_spec

    df = pd.DataFrame(
        {
            "region": [f"R{i}" for i in range(10)] * 2,
            "revenue": list(range(100, 120)),
        }
    )
    spec = _choose_default_spec("sales.csv", df, sheet=None)
    assert spec is not None
    assert spec.chart is not None
    assert spec.chart.kind == "bar"


def test_planner_picks_line_for_datetime_plus_numeric() -> None:
    """A datetime-like column + numeric → line chart of the trend.
    Even string-typed date columns work via the parseable-fallback path."""
    pd = pytest.importorskip("pandas")
    from app.skills.data_analyzer.planner import _choose_default_spec

    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=5, freq="D"),
            "revenue": [100, 110, 105, 130, 140],
        }
    )
    spec = _choose_default_spec("trend.csv", df, sheet=None)
    assert spec is not None
    assert spec.chart is not None
    assert spec.chart.kind == "line"
    assert spec.chart.x == "date"
    assert spec.chart.y == "revenue"
