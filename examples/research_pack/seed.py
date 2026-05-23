"""Seed the research_pack example workspace — Phase 14 demo.

Plants a fresh ``examples/research_pack/workspace/`` with ~10 messy
files mimicking a real researcher's scratch directory:

  - 3 small PDFs (real minimal %PDF structure so pypdf can extract titles)
  - 1 CSV with synthetic experiment results (30 rows, 3 numeric cols)
  - 1 XLSX with model scores (2 sheets)
  - 2 PNG placeholders
  - 2 text/markdown notes
  - 1 unknown-type stub file (tests folder_organizer's misc/ bucket)

Designed to exercise every stage of the v0.14 workspace_pack.yaml:

    folder_organizer → pdf_indexer → data_analyzer →
    workspace_visualizer → agent (synthesise README + SOURCES)

Usage::

    python examples/research_pack/seed.py

Idempotent — wipes any existing workspace/ first. The xlsx writer
needs ``openpyxl`` (install via ``pip install 'localflow-agent[data]'``)
— without it, the .xlsx slot is replaced with a placeholder bytes
file so the demo still runs (data_analyzer will skip it).
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
from pathlib import Path


def _pdf_bytes(title: str) -> bytes:
    """Build a minimal 1-page PDF with the given title rendered as
    visible text. The byte offsets in the xref table are computed
    dynamically so titles of any length stay valid for pypdf."""
    safe_title = title.replace("(", "\\(").replace(")", "\\)")
    stream_body = f"BT /F1 12 Tf 72 720 Td ({safe_title}) Tj ET\n".encode("ascii")
    stream_length = len(stream_body)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        (
            b"<< /Length "
            + str(stream_length).encode("ascii")
            + b" >>\nstream\n"
            + stream_body
            + b"endstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj ".encode("ascii"))
        out.extend(obj)
        out.extend(b" endobj\n")

    xref_offset = len(out)
    out.extend(b"xref\n")
    out.extend(f"0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(b"trailer ")
    out.extend(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii"))
    out.extend(b"startxref\n")
    out.extend(f"{xref_offset}\n".encode("ascii"))
    out.extend(b"%%EOF\n")
    return bytes(out)


def _csv_bytes() -> bytes:
    """30 rows × 3 cols of synthetic experiment data with a categorical
    'model' column for folder_organizer's data dir + a numeric
    'accuracy' column for data_analyzer to chart."""
    rows = []
    rows.append(["model", "epoch", "accuracy"])
    for i, model in enumerate(["transformer", "lstm", "mlp", "transformer", "lstm"] * 6):
        rows.append([model, i + 1, round(0.5 + 0.4 * (i % 9) / 9, 4)])
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode("utf-8")


def _xlsx_bytes() -> bytes | None:
    """Build a 2-sheet xlsx via openpyxl. Returns None when openpyxl is
    not installed — caller writes a placeholder instead."""
    try:
        from openpyxl import Workbook
    except ImportError:
        return None
    wb = Workbook()
    sheet = wb.active
    sheet.title = "model_scores"
    sheet.append(["model", "f1", "loss"])
    for i, m in enumerate(["A", "B", "C", "D"]):
        sheet.append([m, 0.75 + 0.05 * i, 0.4 - 0.05 * i])
    s2 = wb.create_sheet("hyperparams")
    s2.append(["lr", "batch_size"])
    s2.append([3e-4, 32])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# Tiny valid PNG (1x1 transparent pixel). Enough for chart_ops to
# classify as image; not enough to be visually useful — that's fine
# because the demo is about workflow, not pretty pictures.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63000100000005000156a55d8d0000000049454e44ae42"
    "60820000000000000000"
)[:67]


_FILES = {
    "attention_is_all_you_need.pdf": _pdf_bytes("Attention Is All You Need"),
    "memory_agents_survey.pdf": _pdf_bytes("Memory in LLM Agents - A Survey"),
    "rag_eval_2026.pdf": _pdf_bytes("Evaluating Retrieval Augmented Generation"),
    "experiment_results.csv": _csv_bytes(),
    "architecture.png": _TINY_PNG,
    "loss_curve.png": _TINY_PNG,
    "lecture_notes.txt": (
        "Lecture 4 — agent memory architectures\n\n"
        "Topics: episodic vs. semantic memory, retrieval policies, "
        "context window management.\n"
    ).encode("utf-8"),
    "TODO.md": (
        "# TODO\n\n- finish RAG evaluation script\n"
        "- regenerate transformer charts after epoch 50\n"
        "- email collaborators about the survey draft\n"
    ).encode("utf-8"),
    # An unknown-extension file — folder_organizer routes this to misc/.
    "untitled.dat": b"\x00" * 64,
}


def seed(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    for name, content in _FILES.items():
        (dest / name).write_bytes(content)

    xlsx_bytes = _xlsx_bytes()
    if xlsx_bytes is not None:
        (dest / "model_scores.xlsx").write_bytes(xlsx_bytes)
    else:
        # Placeholder so the file count + folder_organizer routing
        # match the documented expected state. data_analyzer will skip
        # the file (it can't parse non-xlsx bytes).
        (dest / "model_scores.xlsx").write_bytes(b"XLSX_PLACEHOLDER")

    print(f"Seeded {dest} with {len(list(dest.iterdir()))} file(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).parent / "workspace",
        help="Where to plant the seeded workspace (default: ./workspace/).",
    )
    args = parser.parse_args()
    seed(args.dest)


if __name__ == "__main__":
    main()
