"""Phase 3.3 — typed AnalysisSpec for LLM-driven data analysis.

The model never writes Python code (outline §7.1 / §3.7 design constraint
#5). Instead, the LLM (or a rule planner) outputs an ``AnalysisSpec``: a
fully-typed Pydantic model describing WHAT to compute. LocalFlow's
``execute_analysis`` translates the spec into pandas calls.

This file defines the contract. The engine lives in
``app/tools/data_analysis.py``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class FilterOp(str, Enum):
    """Comparison operators allowed in a ``Filter``.

    Kept deliberately small — every op maps to a single pandas
    expression we trust. No regex, no ``eval``-style strings.
    """

    EQ = "=="
    NE = "!="
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    CONTAINS = "contains"  # string substring


class AggregationOp(str, Enum):
    """Aggregation functions allowed in a ``GroupBy``.

    All map to pandas ``.agg(name)`` calls. No raw lambdas — that would
    be a code-execution backdoor.
    """

    SUM = "sum"
    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    NUNIQUE = "nunique"
    STD = "std"
    VAR = "var"
    FIRST = "first"
    LAST = "last"


class Filter(BaseModel):
    """A single row predicate. ``IS_NULL`` and ``NOT_NULL`` ignore
    ``value``; ``IN`` and ``NOT_IN`` require ``value`` to be a list."""

    column: str = Field(..., description="Column name in the source table.")
    op: FilterOp = Field(..., description="Comparison operator.")
    value: Any = Field(default=None, description="Compared against. List for IN/NOT_IN.")


class GroupBy(BaseModel):
    """Group rows by ``by``, then compute one aggregation per
    (column, op) pair in ``aggregations``."""

    by: list[str] = Field(..., min_length=1, description="Columns to group by.")
    aggregations: dict[str, AggregationOp] = Field(
        ...,
        description="Map of column → aggregation. Keys may include the same column "
                    "appearing with different ops by suffixing _2, _3 (e.g. amount_2).",
    )

    @field_validator("aggregations")
    @classmethod
    def _non_empty(cls, v: dict[str, AggregationOp]) -> dict[str, AggregationOp]:
        if not v:
            raise ValueError("aggregations must contain at least one column → op")
        return v


class ChartRequest(BaseModel):
    """A typed description of the chart to render from the result.

    ``kind`` constrains rendering to a small set chart_ops can produce
    safely. ``x`` and ``y`` reference columns in the (possibly
    aggregated) result frame.
    """

    kind: Literal["bar", "histogram", "line"] = Field(
        ..., description="Chart type. 'bar' requires y; 'histogram' uses only x.",
    )
    x: str = Field(..., description="Column for the X axis.")
    y: str | None = Field(default=None, description="Required for 'bar' and 'line'.")
    title: str | None = Field(default=None, description="Plot title; default derived from x/y.")


class AnalysisSpec(BaseModel):
    """A complete typed query against one tabular file/sheet.

    Pipeline applied in order: ``filters`` → ``groupby`` → ``sort_by``
    → ``limit`` → ``chart``. Any field may be omitted; the engine just
    skips that step.
    """

    source_file: str = Field(..., description="Workspace-relative path of the source file.")
    sheet: str | None = Field(default=None, description="Excel sheet name (None → first sheet).")
    filters: list[Filter] = Field(default_factory=list)
    groupby: GroupBy | None = None
    sort_by: list[str] = Field(default_factory=list, description="Columns to sort the result by.")
    sort_descending: bool = False
    limit: int | None = Field(default=None, ge=1, description="Cap output rows after sort.")
    chart: ChartRequest | None = None


class AnalysisOutcome(str, Enum):
    """End status of a single AnalysisSpec execution."""

    OK = "ok"
    EMPTY_RESULT = "empty_result"
    INVALID_SPEC = "invalid_spec"
    READ_ERROR = "read_error"
    EXECUTION_ERROR = "execution_error"


class AnalysisResult(BaseModel):
    """The output of running one ``AnalysisSpec``.

    JSON-safe (no DataFrames). ``rows`` is a list of {column → value}
    after filter+groupby+sort+limit. ``chart_png_b64`` is set iff the
    spec requested a chart and we successfully rendered it.
    """

    spec: AnalysisSpec
    outcome: AnalysisOutcome
    error: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    rows_truncated: bool = False
    chart_png_b64: str | None = None
    summary: str = Field(default="", description="Human-readable one-paragraph summary.")
