"""CSV / tabular data inspection — Phase 3.1 / outline §14 DataOps.

Read-only utilities that pandas does the heavy lifting for. Used by the
``data_reporter`` skill to synthesize a workspace-wide data report.

Per outline §7.1 / §3.7 design constraint #5: the model never writes
arbitrary code; these are LocalFlow-owned pandas calls invoked from the
skill's deterministic planner.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MAX_ROWS = 10_000
"""Cap rows read per file to keep memory bounded on large CSVs. The
report is summary-level, so a sample is sufficient."""

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
"""Skip files larger than 50 MB to avoid surprise memory spikes."""

logging.getLogger("pandas").setLevel(logging.ERROR)


@dataclass
class ColumnSummary:
    name: str
    dtype: str
    non_null: int
    nulls: int
    unique: int
    sample_values: list[str] = field(default_factory=list)
    numeric_stats: dict[str, float] | None = None  # min/max/mean/std for numeric


@dataclass
class DataFrameSummary:
    path: str
    """Workspace-relative path (Excel sheets append ``(sheet: Name)``)."""

    rows_read: int
    rows_truncated: bool
    cols: int
    columns: list[ColumnSummary]
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    """If reading failed, ``error`` is set and other fields are best-effort."""


@dataclass
class TabularRead:
    """One sheet/file of tabular data with its DataFrame attached for
    downstream chart generation. Phase 3.2 introduced this; previous
    callers can still use :func:`read_and_describe` to get only the
    JSON-safe :class:`DataFrameSummary` slice.

    Failures (parse errors, oversized files, missing files) surface as
    a ``TabularRead`` with ``df=None`` and ``error`` set, so the planner
    can render an "error" section in the report without aborting.
    """

    display_path: str
    df: Any | None  # pandas.DataFrame; loosely typed to avoid hard import
    rows_truncated: bool
    error: str | None = None


CSV_SUFFIXES: frozenset[str] = frozenset({".csv", ".tsv"})
EXCEL_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xls", ".xlsm"})


def is_csv_like(path: Path) -> bool:
    return path.suffix.lower() in CSV_SUFFIXES


def is_excel_like(path: Path) -> bool:
    return path.suffix.lower() in EXCEL_SUFFIXES


def is_supported_tabular(path: Path) -> bool:
    return is_csv_like(path) or is_excel_like(path)


# --------------------------------------------------------------------- entry points


def read_tabular(
    abs_path: Path,
    rel_path: str,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[TabularRead]:
    """Read a tabular file and return DataFrames paired with metadata.

    CSV / TSV → list with a single ``TabularRead``.
    Excel    → list with one ``TabularRead`` per sheet (each
               ``display_path`` rendered as ``foo.xlsx  (sheet: Sheet1)``).

    The DataFrames are needed by ``chart_ops`` to render charts; the
    caller typically passes them to :func:`summarize_dataframe` to also
    get a JSON-safe summary for the markdown report.
    """
    if not abs_path.exists() or not abs_path.is_file():
        return [TabularRead(display_path=rel_path, df=None, rows_truncated=False, error="file not found")]

    try:
        size = abs_path.stat().st_size
    except OSError as exc:
        return [TabularRead(display_path=rel_path, df=None, rows_truncated=False, error=f"stat failed: {exc}")]

    if size > max_bytes:
        return [TabularRead(
            display_path=rel_path,
            df=None,
            rows_truncated=False,
            error=f"file too large to summarize ({size / 1_048_576:.1f} MB > {max_bytes / 1_048_576:.0f} MB cap)",
        )]

    try:
        import pandas as pd  # noqa: F401
    except ImportError as exc:
        return [TabularRead(display_path=rel_path, df=None, rows_truncated=False, error=f"pandas not installed: {exc}")]

    if is_excel_like(abs_path):
        return _read_excel_sheets(abs_path, rel_path, max_rows)
    return [_read_csv_one(abs_path, rel_path, max_rows)]


def read_and_describe(
    abs_path: Path,
    rel_path: str,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    sample_n: int = 3,
) -> list[DataFrameSummary]:
    """Backward-compatible wrapper: returns only ``DataFrameSummary``
    list (no DataFrames). Older callers (e.g. earlier tests) keep
    working; new code (Phase 3.2+) should use :func:`read_tabular`
    directly so chart_ops can reuse the DataFrame.
    """
    return [
        summarize_dataframe(
            tr.display_path, tr.df,
            sample_n=sample_n, truncated=tr.rows_truncated, error=tr.error,
        )
        for tr in read_tabular(abs_path, rel_path, max_rows=max_rows, max_bytes=max_bytes)
    ]


def summarize_dataframe(
    display_path: str,
    df: Any | None,
    *,
    sample_n: int = 3,
    truncated: bool = False,
    error: str | None = None,
) -> DataFrameSummary:
    """Pure function: build a JSON-safe summary from an in-memory df.

    No I/O. Phase 3.2 split this out so the planner can summarize a
    DataFrame it already has in hand (paired with chart generation)
    without re-reading from disk.
    """
    if error is not None or df is None:
        return DataFrameSummary(
            path=display_path,
            rows_read=0,
            rows_truncated=False,
            cols=0,
            columns=[],
            error=error or "no dataframe",
        )

    return DataFrameSummary(
        path=display_path,
        rows_read=int(len(df)),
        rows_truncated=truncated,
        cols=int(df.shape[1]),
        columns=[_summarize_column(df, name) for name in df.columns],
        sample_rows=_pick_sample_rows(df, sample_n),
    )


# --------------------------------------------------------------------- internals


def _read_csv_one(abs_path: Path, rel_path: str, max_rows: int) -> TabularRead:
    import pandas as pd

    sep = "\t" if abs_path.suffix.lower() == ".tsv" else ","
    try:
        df = pd.read_csv(abs_path, sep=sep, nrows=max_rows + 1)
    except Exception as exc:
        return TabularRead(
            display_path=rel_path, df=None, rows_truncated=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    truncated = len(df) > max_rows
    if truncated:
        df = df.iloc[:max_rows]
    return TabularRead(display_path=rel_path, df=df, rows_truncated=truncated)


def _read_excel_sheets(abs_path: Path, rel_path: str, max_rows: int) -> list[TabularRead]:
    import pandas as pd

    try:
        sheets = pd.read_excel(abs_path, sheet_name=None, nrows=max_rows + 1)
    except Exception as exc:
        return [TabularRead(
            display_path=rel_path, df=None, rows_truncated=False,
            error=f"{type(exc).__name__}: {exc}",
        )]

    if not sheets:
        return [TabularRead(display_path=rel_path, df=None, rows_truncated=False, error="workbook has no sheets")]

    out: list[TabularRead] = []
    for sheet_name, df in sheets.items():
        truncated = len(df) > max_rows
        if truncated:
            df = df.iloc[:max_rows]
        out.append(TabularRead(
            display_path=f"{rel_path}  (sheet: {sheet_name})",
            df=df,
            rows_truncated=truncated,
        ))
    return out


def _summarize_column(df: Any, name: str) -> ColumnSummary:
    series = df[name]
    non_null = int(series.notna().sum())
    nulls = int(series.isna().sum())
    try:
        unique = int(series.nunique(dropna=True))
    except (TypeError, ValueError):
        unique = -1  # unhashable values

    sample_values: list[str] = []
    try:
        for v in series.dropna().head(3).tolist():
            sample_values.append(str(v)[:50])
    except Exception:
        pass

    numeric_stats: dict[str, float] | None = None
    try:
        import pandas as pd

        if pd.api.types.is_numeric_dtype(series):
            non_null_series = series.dropna()
            if len(non_null_series) > 0:
                numeric_stats = {
                    "min": float(non_null_series.min()),
                    "max": float(non_null_series.max()),
                    "mean": float(non_null_series.mean()),
                    "std": float(non_null_series.std()) if len(non_null_series) > 1 else 0.0,
                }
    except Exception:
        pass

    return ColumnSummary(
        name=str(name),
        dtype=str(series.dtype),
        non_null=non_null,
        nulls=nulls,
        unique=unique,
        sample_values=sample_values,
        numeric_stats=numeric_stats,
    )


def _pick_sample_rows(df: Any, n: int) -> list[dict[str, Any]]:
    if n <= 0 or len(df) == 0:
        return []
    head = df.head(n)
    out: list[dict[str, Any]] = []
    for _, row in head.iterrows():
        out.append({str(k): _stringify_cell(v) for k, v in row.to_dict().items()})
    return out


def _stringify_cell(value: Any) -> str:
    try:
        import pandas as pd

        if pd.isna(value):
            return ""
    except Exception:
        pass
    s = str(value)
    if len(s) > 60:
        return s[:57] + "..."
    return s
