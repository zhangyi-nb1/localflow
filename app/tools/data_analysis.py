"""Phase 3.3 — execute a typed AnalysisSpec against a DataFrame.

The translation layer between LLM-emitted (or rule-emitted) typed specs
and actual pandas operations. **No model-supplied code is ever
executed** (outline §7.1). Every operation is a hard-coded pandas call
keyed by Pydantic-validated enum values.

The engine is pure: ``execute_analysis(df, spec) -> AnalysisResult``.
Read I/O lives in ``data_ops.read_tabular``; chart rendering lives in
``chart_ops``. We just glue them together by interpreting the spec.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.schemas.analysis import (
    AnalysisOutcome,
    AnalysisResult,
    AnalysisSpec,
    ChartRequest,
    Filter,
    FilterOp,
    GroupBy,
)
from app.tools import chart_ops

logging.getLogger("pandas").setLevel(logging.ERROR)


MAX_OUTPUT_ROWS = 5_000
"""Hard cap on rows the result can carry. Prevents a wide groupby +
``limit=None`` from producing a multi-MB JSON-encoded plan."""


def execute_analysis(df: Any, spec: AnalysisSpec) -> AnalysisResult:
    """Apply ``spec`` against ``df`` (a pandas DataFrame).

    Pure function. Never modifies ``df`` in place; pandas operations
    return new frames at each step.
    """
    if df is None:
        return _err(spec, AnalysisOutcome.READ_ERROR, "dataframe is None")

    try:
        import pandas as pd  # noqa: F401
    except ImportError as exc:
        return _err(spec, AnalysisOutcome.EXECUTION_ERROR, f"pandas not installed: {exc}")

    # Step 1 — column existence check up front. Refusing early gives a
    # better error than a pandas KeyError mid-pipeline.
    missing = _missing_columns(df, spec)
    if missing:
        return _err(
            spec,
            AnalysisOutcome.INVALID_SPEC,
            f"columns referenced by spec but absent from source: {sorted(missing)}; "
            f"available: {list(df.columns)}",
        )

    working = df

    # Step 2 — filters (each one prunes rows).
    try:
        for f in spec.filters:
            working = _apply_filter(working, f)
    except Exception as exc:
        return _err(spec, AnalysisOutcome.EXECUTION_ERROR, f"filter step failed: {exc}")

    # Step 3 — groupby + aggregation.
    grouped_columns: list[str] | None = None
    try:
        if spec.groupby is not None:
            working, grouped_columns = _apply_groupby(working, spec.groupby)
    except Exception as exc:
        return _err(spec, AnalysisOutcome.EXECUTION_ERROR, f"groupby step failed: {exc}")

    # Step 4 — sort.
    try:
        if spec.sort_by:
            sort_keys = [k for k in spec.sort_by if k in working.columns]
            if sort_keys:
                working = working.sort_values(by=sort_keys, ascending=not spec.sort_descending)
    except Exception as exc:
        return _err(spec, AnalysisOutcome.EXECUTION_ERROR, f"sort step failed: {exc}")

    # Step 5 — limit.
    raw_row_count = int(len(working))
    truncated = False
    effective_limit = min(spec.limit or MAX_OUTPUT_ROWS, MAX_OUTPUT_ROWS)
    if raw_row_count > effective_limit:
        working = working.head(effective_limit)
        truncated = True

    # Step 6 — chart (optional).
    chart_b64: str | None = None
    chart_error: str | None = None
    if spec.chart is not None:
        try:
            chart_bytes = _render_chart(working, spec.chart, grouped_columns)
            if chart_bytes:
                chart_b64 = base64.b64encode(chart_bytes).decode("ascii")
        except Exception as exc:
            # Chart failure should not kill the whole result — caller
            # gets the data + a chart_error in the summary.
            chart_error = f"chart render failed: {type(exc).__name__}: {exc}"

    # Step 7 — package the result.
    rows = _frame_to_rows(working)
    if not rows and spec.groupby is None and not spec.filters:
        # Empty source + no operations → still call it OK so the
        # caller can decide; but flag empty_result if filtering DID
        # produce nothing.
        outcome = AnalysisOutcome.EMPTY_RESULT
    elif not rows:
        outcome = AnalysisOutcome.EMPTY_RESULT
    else:
        outcome = AnalysisOutcome.OK

    summary = _summary_text(spec, raw_row_count, truncated, outcome, chart_error)
    return AnalysisResult(
        spec=spec,
        outcome=outcome,
        error=chart_error,  # nonfatal note
        columns=list(working.columns.astype(str)),
        rows=rows,
        row_count=raw_row_count,
        rows_truncated=truncated,
        chart_png_b64=chart_b64,
        summary=summary,
    )


# --------------------------------------------------------------------- filter


def _apply_filter(df: Any, f: Filter) -> Any:

    col = df[f.column]
    op = f.op
    if op == FilterOp.IS_NULL:
        return df[col.isna()]
    if op == FilterOp.NOT_NULL:
        return df[col.notna()]
    if op == FilterOp.IN:
        return df[col.isin(_as_list(f.value))]
    if op == FilterOp.NOT_IN:
        return df[~col.isin(_as_list(f.value))]
    if op == FilterOp.CONTAINS:
        s = col.astype(str)
        return df[s.str.contains(str(f.value), na=False)]
    # Comparison ops
    if op == FilterOp.EQ:
        return df[col == f.value]
    if op == FilterOp.NE:
        return df[col != f.value]
    if op == FilterOp.GT:
        return df[col > f.value]
    if op == FilterOp.LT:
        return df[col < f.value]
    if op == FilterOp.GTE:
        return df[col >= f.value]
    if op == FilterOp.LTE:
        return df[col <= f.value]
    raise ValueError(f"unsupported filter op: {op}")


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if value is None:
        return []
    return [value]


# --------------------------------------------------------------------- groupby


def _apply_groupby(df: Any, g: GroupBy) -> tuple[Any, list[str]]:
    """Apply GroupBy returning a flat DataFrame + the list of group-key
    column names (handy for downstream chart logic)."""
    agg_dict = {col: op.value for col, op in g.aggregations.items()}
    grouped = df.groupby(g.by, dropna=False).agg(agg_dict).reset_index()
    # Make sure the agg columns retain their original names (they do,
    # because we used a dict-style agg).
    return grouped, list(g.by)


# --------------------------------------------------------------------- chart


def _render_chart(df: Any, req: ChartRequest, group_keys: list[str] | None) -> bytes | None:
    """Bridge from AnalysisSpec's chart request to chart_ops calls.

    Histogram → ``chart_ops.histogram_png(series)`` directly on x column.
    Bar       → ``chart_ops.bar_png(x→y dict)`` from two columns.
    Line      → matplotlib direct (chart_ops doesn't have line_png yet).
    """
    title = req.title or _default_title(req)

    if req.kind == "histogram":
        series = df[req.x]
        return chart_ops.histogram_png(series, title=title, xlabel=req.x)

    if req.kind == "bar":
        if req.y is None or req.y not in df.columns:
            return None
        # Collapse to {x_value: y_value} for chart_ops.bar_png.
        sub = df[[req.x, req.y]].copy()
        # If the x column duplicates rows, sum y for plotting purposes.
        sub = sub.groupby(req.x, dropna=False)[req.y].sum().reset_index()
        counts = {str(k): _to_number(v) for k, v in zip(sub[req.x], sub[req.y])}
        return chart_ops.bar_png(counts, title=title, xlabel=req.x)

    if req.kind == "line":
        # chart_ops doesn't have line_png; render inline via matplotlib.
        return _render_line(df, req, title)

    return None


def _render_line(df: Any, req: ChartRequest, title: str) -> bytes | None:
    if req.y is None or req.y not in df.columns:
        return None
    import io

    plt = _lazy_matplotlib()
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    ax.plot(df[req.x].tolist(), df[req.y].tolist(), marker="o")
    ax.set_title(title)
    ax.set_xlabel(req.x)
    ax.set_ylabel(req.y)
    ax.grid(True, alpha=0.3)
    for label in ax.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _lazy_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _default_title(req: ChartRequest) -> str:
    if req.y:
        return f"{req.y} by {req.x}"
    return f"Distribution of {req.x}"


def _to_number(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------- helpers


def _missing_columns(df: Any, spec: AnalysisSpec) -> set[str]:
    have = set(df.columns.astype(str))
    referenced: set[str] = set()
    for f in spec.filters:
        referenced.add(f.column)
    if spec.groupby is not None:
        referenced.update(spec.groupby.by)
        referenced.update(spec.groupby.aggregations.keys())
    if spec.chart is not None:
        referenced.add(spec.chart.x)
        if spec.chart.y:
            referenced.add(spec.chart.y)
    # sort_by columns are checked best-effort inside the sort step
    # because they might reference aggregated columns that only exist
    # AFTER groupby. We can't statically validate without simulating
    # the pipeline; accept them at this step.
    return {
        c
        for c in referenced
        if c not in have and c not in (spec.groupby.by if spec.groupby else [])
    }


def _frame_to_rows(df: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        record: dict[str, Any] = {}
        for k, v in row.to_dict().items():
            record[str(k)] = _stringify_value(v)
        rows.append(record)
    return rows


def _stringify_value(value: Any) -> Any:
    """Convert pandas types to JSON-safe equivalents.

    We keep ``int``, ``float``, ``bool``, ``str``, and ``None``; everything
    else (Timestamp, numpy.int64, etc.) becomes a string. Pydantic on the
    AnalysisResult model rejects non-JSON values without this conversion.
    """
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float, bool, str)) and isinstance(value, bool) is not False:
        return value
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, (float,)):
        return float(value)
    # numpy scalars
    try:
        import numpy as np

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
    except Exception:
        pass
    return str(value)


def _summary_text(
    spec: AnalysisSpec,
    raw_row_count: int,
    truncated: bool,
    outcome: AnalysisOutcome,
    chart_error: str | None,
) -> str:
    """One-line natural-language description of what we computed."""
    pieces: list[str] = [f"source = {spec.source_file}"]
    if spec.sheet:
        pieces.append(f"sheet = {spec.sheet}")
    if spec.filters:
        f_desc = ", ".join(
            f"{f.column} {f.op.value}"
            + ("" if f.op in (FilterOp.IS_NULL, FilterOp.NOT_NULL) else f" {f.value}")
            for f in spec.filters
        )
        pieces.append(f"filters[{f_desc}]")
    if spec.groupby is not None:
        agg_desc = ", ".join(f"{c}={op.value}" for c, op in spec.groupby.aggregations.items())
        pieces.append(f"groupby({', '.join(spec.groupby.by)}, agg[{agg_desc}])")
    if spec.sort_by:
        direction = "desc" if spec.sort_descending else "asc"
        pieces.append(f"sort {','.join(spec.sort_by)} {direction}")
    if spec.limit:
        pieces.append(f"limit {spec.limit}")
    if spec.chart:
        c_desc = f"{spec.chart.kind}({spec.chart.x}"
        if spec.chart.y:
            c_desc += f", {spec.chart.y}"
        c_desc += ")"
        pieces.append(c_desc)
    head = " | ".join(pieces)
    suffix = f" → {outcome.value}, {raw_row_count} row(s)"
    if truncated:
        suffix += " (truncated)"
    if chart_error:
        suffix += f"; {chart_error}"
    return head + suffix


def _err(spec: AnalysisSpec, outcome: AnalysisOutcome, msg: str) -> AnalysisResult:
    return AnalysisResult(
        spec=spec,
        outcome=outcome,
        error=msg,
        summary=f"{spec.source_file}: {outcome.value} — {msg}",
    )
