"""Phase 11 — extract_tabular_preview surfaces cell data into the
WorkspaceSnapshot so the LLM can interpret spreadsheets instead of
guessing from filename alone.

The v0.11 bug was an Excel file landing in front of the agent with
zero content preview. The fix is the new
``data_ops.extract_tabular_preview`` helper + a file_scan dispatch
branch that calls it for ``file_type in ("excel", "tabular")``. These
tests pin both the helper's output shape and the scanner integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

from app.tools.data_ops import extract_tabular_preview  # noqa: E402
from app.tools.file_scan import scan_workspace  # noqa: E402


def _make_xlsx(tmp_path: Path, sheets: dict[str, pd.DataFrame]) -> Path:
    """Write a multi-sheet Excel file. ``openpyxl`` is the engine pandas
    picks up automatically; it's in the [data] extra so any environment
    running these tests has it."""
    xlsx_path = tmp_path / "data.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return xlsx_path


def test_preview_returns_markdown_table_for_simple_xlsx(tmp_path: Path) -> None:
    """A 3-row spreadsheet renders as a markdown table with header +
    separator + data rows. Header text is preserved verbatim so the
    LLM can recognize column names."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            # Note: avoid "NA" as a string value — pandas reads it back as
            # a NaN sentinel by default, which would surface as "nan" in
            # the markdown preview and confuse a reader without telling
            # them anything about extract_tabular_preview itself.
            "Sheet1": pd.DataFrame({"region": ["North", "EU", "APAC"], "revenue": [100, 200, 150]})
        },
    )
    preview = extract_tabular_preview(xlsx)
    assert preview is not None
    assert "region" in preview and "revenue" in preview
    assert "North" in preview and "APAC" in preview
    # markdown table separator row
    assert "---" in preview


def test_preview_includes_sheet_labels_for_multisheet(tmp_path: Path) -> None:
    """Multi-sheet workbook produces one block per sheet, each prefixed
    with ``### (sheet: <name>)`` so the LLM can address sheets by name."""
    xlsx = _make_xlsx(
        tmp_path,
        {
            "Q1": pd.DataFrame({"month": ["Jan", "Feb"], "rev": [10, 20]}),
            "Q2": pd.DataFrame({"month": ["Apr", "May"], "rev": [40, 50]}),
        },
    )
    preview = extract_tabular_preview(xlsx)
    assert preview is not None
    assert "(sheet: Q1)" in preview
    assert "(sheet: Q2)" in preview


def test_preview_capped_at_max_chars(tmp_path: Path) -> None:
    """A spreadsheet with very long cell values must still produce a
    preview ≤ max_chars. Beyond the cap the tail is truncated with a
    `...` ellipsis. Prevents WorkspaceSnapshot from ballooning."""
    long_text = "x" * 500
    xlsx = _make_xlsx(
        tmp_path,
        {"Sheet1": pd.DataFrame({"a": [long_text] * 20, "b": [long_text] * 20})},
    )
    preview = extract_tabular_preview(xlsx, max_chars=400)
    assert preview is not None
    assert len(preview) <= 400


def test_preview_returns_none_on_missing_file(tmp_path: Path) -> None:
    """Defensive: scanner shouldn't crash if a file vanishes mid-scan."""
    preview = extract_tabular_preview(tmp_path / "nope.xlsx")
    assert preview is None


def test_scanner_populates_text_preview_for_excel(tmp_path: Path) -> None:
    """End-to-end: place an .xlsx in a workspace, run scan_workspace,
    assert the resulting FileMeta has a non-empty text_preview. This
    pins the file_scan.py dispatch branch for file_type='excel'."""
    _make_xlsx(
        tmp_path,
        {"Sheet1": pd.DataFrame({"x": [1, 2, 3], "y": [10, 20, 30]})},
    )
    snap = scan_workspace(tmp_path, task_id="t-test", compute_preview=True)
    excel_files = [f for f in snap.files if f.file_type == "excel"]
    assert len(excel_files) == 1
    assert excel_files[0].text_preview is not None
    assert "x" in excel_files[0].text_preview
    assert "y" in excel_files[0].text_preview
