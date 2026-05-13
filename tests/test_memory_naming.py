"""Phase 5 — naming style transform unit tests."""
from __future__ import annotations

import pytest

from app.memory import NamingStyle, apply_naming_style


# --------------------------------------------------------------- original


def test_original_returns_unchanged() -> None:
    for s in ("Report.pdf", "中文.csv", "x", ""):
        assert apply_naming_style(s, "original") == s


def test_original_enum_form_also_works() -> None:
    assert apply_naming_style("Report.pdf", NamingStyle.ORIGINAL) == "Report.pdf"


def test_unknown_style_is_treated_as_original() -> None:
    """Robustness: a typo'd style at planner time shouldn't crash."""
    assert apply_naming_style("Report.pdf", "PascalCase") == "Report.pdf"


# --------------------------------------------------------------- snake_case


@pytest.mark.parametrize("inp,expected", [
    ("Report (Final).pdf", "report_final.pdf"),
    ("My Notes.txt", "my_notes.txt"),
    ("data-set v2.csv", "data_set_v2.csv"),
    ("ALREADY_SNAKE.md", "already_snake.md"),
    ("[draft] paper.pdf", "draft_paper.pdf"),
    ("Q1+Q2 results.xlsx", "q1_q2_results.xlsx"),
])
def test_snake_case_examples(inp: str, expected: str) -> None:
    assert apply_naming_style(inp, "snake_case") == expected


def test_snake_case_preserves_unicode_letters() -> None:
    # spaces collapse, CJK letters preserved
    assert apply_naming_style("中文 文档.pdf", "snake_case") == "中文_文档.pdf"


def test_snake_case_preserves_dotfile() -> None:
    """``.gitignore`` is a dot-leading file with no extension; the
    transform must NOT eat the leading dot."""
    assert apply_naming_style(".gitignore", "snake_case") == ".gitignore"


def test_snake_case_multidot_extension() -> None:
    """``archive.tar.gz`` — PurePosixPath.stem is 'archive.tar', .suffix
    is '.gz'. snake_case on 'archive.tar' lowercases but keeps the dot
    (dots are not separators in our rule)."""
    assert apply_naming_style("Archive.tar.gz", "snake_case") == "archive.tar.gz"


def test_snake_case_collapses_runs_of_separators() -> None:
    assert apply_naming_style("a   b___c.txt", "snake_case") == "a_b_c.txt"


def test_snake_case_strips_edge_underscores() -> None:
    assert apply_naming_style("  hello  .txt", "snake_case") == "hello.txt"


# --------------------------------------------------------------- kebab-case


@pytest.mark.parametrize("inp,expected", [
    ("Report (Final).pdf", "report-final.pdf"),
    ("snake_case_name.md", "snake-case-name.md"),
    ("Q1+Q2 results.xlsx", "q1-q2-results.xlsx"),
])
def test_kebab_examples(inp: str, expected: str) -> None:
    assert apply_naming_style(inp, "kebab-case") == expected


# --------------------------------------------------------------- lower


def test_lower_lowercases_stem_and_keeps_extension_case() -> None:
    # User chose 'lower' so they get a verbatim lowercase of the stem;
    # extension preserved so Report.PDF → report.PDF (not report.pdf).
    assert apply_naming_style("Report.PDF", "lower") == "report.PDF"


def test_lower_keeps_spaces_and_punctuation() -> None:
    assert apply_naming_style("My Notes (2024).md", "lower") == "my notes (2024).md"


# --------------------------------------------------------------- edge cases


def test_empty_string_returns_empty() -> None:
    assert apply_naming_style("", "snake_case") == ""


def test_all_punctuation_stem_falls_back_to_original() -> None:
    """If the transform would zero out the stem (e.g. ``()_.pdf``), we
    fall back to the original to avoid producing a nameless file."""
    assert apply_naming_style("()_.pdf", "snake_case") == "()_.pdf"
