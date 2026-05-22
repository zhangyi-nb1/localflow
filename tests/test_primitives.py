"""Phase 18 — Capability Primitives I/O contracts.

Pins the typed wrapper schemas (Content / Classification / ContentRef)
so that Phase 19's deliverable verifiers can assume a stable shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.primitives import (
    Classification,
    Content,
    ContentKind,
    ContentRef,
    classify_content,
    extract_content,
    get_catalog,
    get_primitive,
    list_names,
    to_abs,
)

# ----------------------------------------------------------------- schemas


def test_content_kind_enum_includes_seven_categories() -> None:
    assert {k.value for k in ContentKind} == {
        "document",
        "note",
        "table",
        "image",
        "code",
        "structured",
        "binary",
    }


def test_content_ref_path_normalises_slashes_via_to_abs(tmp_path: Path) -> None:
    ref = ContentRef(rel_path="sub\\file.md", kind=ContentKind.NOTE, size_bytes=5)
    abs_path = to_abs(tmp_path, ref)
    # to_abs swaps backslashes to forward slashes before joining.
    assert abs_path == tmp_path / "sub" / "file.md"


# ----------------------------------------------------------------- extract_content


def test_extract_content_returns_text_for_markdown(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("# hello\nthis is a note.", encoding="utf-8")
    ref = ContentRef(rel_path="note.md", kind=ContentKind.NOTE, size_bytes=f.stat().st_size)
    result = extract_content(tmp_path, ref)
    assert isinstance(result, Content)
    assert result.error is None
    assert result.preview is not None
    assert "hello" in result.preview


def test_extract_content_marks_images_as_binary(tmp_path: Path) -> None:
    f = tmp_path / "x.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    ref = ContentRef(rel_path="x.png", kind=ContentKind.IMAGE, size_bytes=f.stat().st_size)
    result = extract_content(tmp_path, ref)
    assert result.preview is None
    assert result.error == "binary"


def test_extract_content_reports_missing_file(tmp_path: Path) -> None:
    ref = ContentRef(rel_path="nope.md", kind=ContentKind.NOTE, size_bytes=10)
    result = extract_content(tmp_path, ref)
    assert result.error == "missing"
    assert result.preview is None


# ----------------------------------------------------------------- classify_content


@pytest.mark.parametrize(
    "rel_path,kind,expected_label,expected_conf",
    [
        ("paper.pdf", ContentKind.DOCUMENT, "paper", 1.0),
        ("data.csv", ContentKind.TABLE, "data", 1.0),
        ("notes.md", ContentKind.NOTE, "note", 1.0),
        ("main.py", ContentKind.CODE, "code", 1.0),
        ("config.yaml", ContentKind.STRUCTURED, "structured", 1.0),
        ("photo.jpg", ContentKind.IMAGE, "image", 1.0),
    ],
)
def test_classify_content_known_extensions(
    rel_path: str, kind: ContentKind, expected_label: str, expected_conf: float
) -> None:
    ref = ContentRef(rel_path=rel_path, kind=kind, size_bytes=10)
    c = classify_content(ref)
    assert isinstance(c, Classification)
    assert c.label == expected_label
    assert c.confidence == expected_conf


def test_classify_content_unknown_extension_falls_back_to_kind() -> None:
    ref = ContentRef(rel_path="weird.xyz", kind=ContentKind.NOTE, size_bytes=10)
    c = classify_content(ref)
    assert c.label == "note"
    assert c.confidence == 0.5  # mid-confidence fallback


def test_classify_content_binary_kind_is_low_confidence() -> None:
    ref = ContentRef(rel_path="blob.dat", kind=ContentKind.BINARY, size_bytes=100)
    c = classify_content(ref)
    assert c.label == "binary"
    assert c.confidence == 0.2


# ----------------------------------------------------------------- catalog


def test_catalog_lists_ten_primitives() -> None:
    """Productisation guide §4.3 names 9 primitives; we ship 10 (added
    classify_content as an explicit entry). If the count changes, this
    surfaces the decision instead of a silent drift."""
    assert len(get_catalog()) == 10


def test_catalog_implements_at_least_extract_and_classify() -> None:
    impls = {n for n, e in get_catalog().items() if e.implemented}
    assert {"extract_content", "classify_content"}.issubset(impls)


def test_catalog_entries_have_backed_by_pointers() -> None:
    """Every catalog entry — implemented or not — points at the module
    or skill that holds the actual code, so the docs stay grounded."""
    for entry in get_catalog().values():
        assert entry.backed_by, f"{entry.name} missing backed_by"


def test_catalog_get_raises_on_unknown_name() -> None:
    with pytest.raises(KeyError):
        get_primitive("no_such_primitive")


def test_catalog_list_names_is_sorted() -> None:
    names = list_names()
    assert names == sorted(names)
