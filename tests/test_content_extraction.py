"""Phase 2 — content extraction tests for pdf_ops, text_ops, and the
file_scan integration that wires them into FileMeta.text_preview."""
from __future__ import annotations

from pathlib import Path

from app.tools import pdf_ops, text_ops
from app.tools.file_scan import scan_workspace

# --------------------------------------------------------------------- text_ops


def test_text_ops_can_preview_classification() -> None:
    assert text_ops.can_preview_as_text("text") is True
    assert text_ops.can_preview_as_text("code") is True
    assert text_ops.can_preview_as_text("structured") is True
    assert text_ops.can_preview_as_text("tabular") is True
    assert text_ops.can_preview_as_text("pdf") is False  # handled by pdf_ops
    assert text_ops.can_preview_as_text("image") is False
    assert text_ops.can_preview_as_text("audio") is False


def test_text_ops_extracts_short_file(tmp_path: Path) -> None:
    p = tmp_path / "note.md"
    p.write_text("# Hello\n\nThis is a note.", encoding="utf-8")
    preview = text_ops.extract_text_preview(p)
    assert preview is not None
    assert "Hello" in preview
    assert "note" in preview


def test_text_ops_caps_long_file(tmp_path: Path) -> None:
    p = tmp_path / "big.txt"
    p.write_text("x" * 10_000, encoding="utf-8")
    preview = text_ops.extract_text_preview(p, max_chars=200)
    assert preview is not None
    assert len(preview) <= 200


def test_text_ops_returns_none_on_missing(tmp_path: Path) -> None:
    assert text_ops.extract_text_preview(tmp_path / "does_not_exist.txt") is None


def test_text_ops_rejects_binary_with_nul(tmp_path: Path) -> None:
    p = tmp_path / "binary.bin"
    p.write_bytes(b"some text\x00with NUL byte" + b"more")
    # Even though we call this with the file path, the NUL byte sentinel
    # tells us it isn't readable text — return None instead of garbage.
    assert text_ops.extract_text_preview(p) is None


def test_text_ops_handles_non_utf8(tmp_path: Path) -> None:
    p = tmp_path / "latin1.txt"
    # Latin-1 encoded "café" — invalid UTF-8 but should decode with replace
    p.write_bytes(b"caf\xe9 au lait")
    preview = text_ops.extract_text_preview(p)
    assert preview is not None
    # The 0xe9 byte becomes the replacement character — what matters is
    # we didn't crash and the readable parts came through.
    assert "caf" in preview
    assert "au lait" in preview


# --------------------------------------------------------------------- pdf_ops


def test_pdf_ops_returns_none_for_fake_pdf(tmp_path: Path) -> None:
    # Our test fixtures (and seed.py) write plain text with a .pdf
    # extension. pypdf rejects them — extract should return None, not raise.
    fake = tmp_path / "fake.pdf"
    fake.write_text("This is not a real PDF.", encoding="utf-8")
    assert pdf_ops.extract_text_preview(fake) is None


def test_pdf_ops_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert pdf_ops.extract_text_preview(tmp_path / "missing.pdf") is None


def test_pdf_ops_extracts_real_pdf(tmp_path: Path) -> None:
    """Build a real minimal PDF and verify we extract its text."""
    pdf = _make_real_pdf(tmp_path / "real.pdf", "Agent Memory: A Survey")
    preview = pdf_ops.extract_text_preview(pdf)
    assert preview is not None
    assert "Agent Memory" in preview


# --------------------------------------------------------------------- file_scan integration


def test_scan_populates_text_preview_for_text_files(workspace: Path, task) -> None:
    snap = scan_workspace(workspace, task.task_id, compute_preview=True)
    txt_file = next(f for f in snap.files if f.path == "c.txt")
    assert txt_file.text_preview is not None
    assert "note c" in txt_file.text_preview


def test_scan_populates_text_preview_for_csv(workspace: Path, task) -> None:
    snap = scan_workspace(workspace, task.task_id, compute_preview=True)
    csv_file = next(f for f in snap.files if f.path == "d.csv")
    assert csv_file.text_preview is not None
    assert "col1" in csv_file.text_preview


def test_scan_skips_preview_for_image(workspace: Path, task) -> None:
    snap = scan_workspace(workspace, task.task_id, compute_preview=True)
    jpg = next(f for f in snap.files if f.path == "e.jpg")
    assert jpg.text_preview is None


def test_scan_skips_preview_for_fake_pdf(workspace: Path, task) -> None:
    # conftest's a.pdf / b.pdf are plain text with .pdf suffix. pypdf
    # rejects them; file_scan should record text_preview=None.
    snap = scan_workspace(workspace, task.task_id, compute_preview=True)
    a_pdf = next(f for f in snap.files if f.path == "a.pdf")
    assert a_pdf.text_preview is None


def test_scan_compute_preview_false_disables_extraction(workspace: Path, task) -> None:
    snap = scan_workspace(workspace, task.task_id, compute_preview=False)
    assert all(f.text_preview is None for f in snap.files)


# --------------------------------------------------------------------- helpers


def _make_real_pdf(path: Path, body: str) -> Path:
    """Hand-build a minimal valid PDF with the given body text.

    This avoids pulling in reportlab as a test-only dep — we generate a
    bare-bones PDF stream directly. Verified to parse with pypdf 6.x.
    """
    # Each object as text, then we'll wrap with proper byte offsets.
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n",
    ]
    stream = (
        b"BT /F1 12 Tf 72 720 Td (" + body.encode("latin-1") + b") Tj ET"
    )
    objects.append(
        b"4 0 obj << /Length " + str(len(stream)).encode() + b" >> stream\n"
        + stream + b"\nendstream endobj\n"
    )
    objects.append(
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    )

    header = b"%PDF-1.4\n"
    body_bytes = b"".join(objects)
    xref_offset = len(header)
    # Build xref: header offset + cumulative position of each object.
    offsets = [0]
    pos = xref_offset
    for obj in objects:
        offsets.append(pos)
        pos += len(obj)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode("ascii")
    trailer = (
        b"trailer << /Size 6 /Root 1 0 R >>\n"
        b"startxref\n" + str(pos).encode() + b"\n%%EOF\n"
    )
    path.write_bytes(header + body_bytes + xref + trailer)
    return path
