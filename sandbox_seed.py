"""Sandbox seeder for hands-on validation.

Creates a richly varied workspace in a folder of your choice, without
touching anything outside that folder. Refuses to overwrite an existing
non-empty directory unless you pass ``--force``.

Default destination: ``./sandbox`` relative to the project root.

Usage:
    python sandbox_seed.py
    python sandbox_seed.py --dest C:\\some\\path
    python sandbox_seed.py --force          # re-seed an existing sandbox
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


# --------------------------------------------------------------------- real PDF builder
# Same helper used in the test suite — pasted here so this script can be
# run without importing test modules.


def _make_real_pdf(path: Path, body: str) -> Path:
    """Hand-build a minimal valid PDF whose first text line equals ``body``."""
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n",
    ]
    stream = b"BT /F1 12 Tf 72 720 Td (" + body.encode("latin-1", "replace") + b") Tj ET"
    objects.append(
        b"4 0 obj << /Length " + str(len(stream)).encode() + b" >> stream\n"
        + stream + b"\nendstream endobj\n"
    )
    objects.append(
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    )
    header = b"%PDF-1.4\n"
    body_bytes = b"".join(objects)
    offsets, pos = [0], len(header)
    for obj in objects:
        offsets.append(pos)
        pos += len(obj)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n" + str(pos).encode() + b"\n%%EOF\n"
    path.write_bytes(header + body_bytes + xref + trailer)
    return path


# --------------------------------------------------------------------- file fixtures


REAL_PDFS = [
    ("agent_memory_survey.pdf", "Agent Memory: A Comprehensive Survey of Techniques"),
    ("transformers_paper.pdf", "Attention Is All You Need"),
    ("rlhf_v2.pdf", "Training Language Models to Follow Instructions with Human Feedback"),
    ("subdir/older_paper.pdf", "Distributed Representations of Words and Phrases"),
]

TEXT_FILES = {
    "todo.md": "# Project Todo\n\n- Finish LocalFlow demo\n- Write Phase 3 design doc\n- Review pull requests\n",
    "meeting_notes.md": "# Q2 Roadmap Meeting\n\nDate: 2026-04-15\n\n## Decisions\n- Ship Phase 2 by end of May.\n- Defer mobile to Q3.\n",
    "reading_list.md": "# Reading List\n\n1. Agent memory papers\n2. RLHF survey\n3. Sparse attention\n",
    "readme.txt": "Local backup of project notes. Safe to reorganize.\n",
    "subdir/deep_note.md": "# Nested Idea\n\nA stray brainstorm that ended up in a subfolder.\n",
}

CODE_FILES = {
    "scratch.py": "from typing import Any\n\ndef hello(name: str) -> str:\n    return f'hello {name}'\n",
    "build.js": "console.log('build script placeholder');\n",
    "subdir/helper.py": "# unused helper\ndef noop():\n    pass\n",
}

DATA_FILES = {
    "telemetry_q1.csv": "timestamp,metric,value\n2026-01-01,latency_ms,12.3\n2026-02-01,latency_ms,9.7\n",
    "users.csv": "id,name,joined\n1,alice,2025-11-03\n2,bob,2026-02-14\n",
    "config.json": '{\n  "service": "localflow",\n  "version": "0.1.0",\n  "features": ["dry_run", "rollback"]\n}\n',
    "deploy.yaml": "service: localflow\nreplicas: 1\nresources:\n  cpu: 100m\n  memory: 128Mi\n",
}

BINARY_PLACEHOLDERS = {
    # Real magic bytes so file_type detection still works, but content is fake.
    "screenshot.png": b"\x89PNG\r\n\x1a\n" + b"fake-png-payload" * 4,
    "diagram.jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 0 + b"fake-jpeg",
    "talk.mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"fake-mp3-bytes",
    "demo.mp4": b"\x00\x00\x00\x20ftypisom" + b"fake-mp4-bytes",
    "backup.zip": b"PK\x03\x04" + b"fake-zip-payload",
}

# Pairs that should be detected as duplicates (same sha256).
DUPLICATE_GROUPS = {
    "duplicate_paper.pdf": "Agent Memory: A Comprehensive Survey of Techniques",  # matches REAL_PDFS[0]
    "subdir/dup_notes.md": TEXT_FILES["todo.md"],
}


def seed(dest: Path, *, force: bool = False) -> None:
    if dest.exists() and any(dest.iterdir()):
        if not force:
            raise SystemExit(
                f"\nRefusing to write into non-empty: {dest}\n"
                f"Either delete it manually, or rerun with --force.\n"
            )
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    total = 0

    # Real PDFs (so pdf_indexer actually extracts titles).
    for rel, title in REAL_PDFS:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        _make_real_pdf(target, title)
        total += 1

    # Duplicate group — same content as agent_memory_survey.pdf so sha256 matches.
    for rel, dup_source in DUPLICATE_GROUPS.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".pdf"):
            _make_real_pdf(target, dup_source)
        else:
            target.write_text(dup_source, encoding="utf-8")
        total += 1

    # Text files with real content (so LLM previews are useful).
    for rel, content in TEXT_FILES.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        total += 1

    # Code & structured & tabular.
    for collection in (CODE_FILES, DATA_FILES):
        for rel, content in collection.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            total += 1

    # Binary placeholders.
    for rel, content in BINARY_PLACEHOLDERS.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        total += 1

    print(f"\nSeeded {total} file(s) into: {dest}\n")
    print("File tree:")
    _print_tree(dest, dest)
    print(
        "\nWhat's in there:\n"
        "  - 4 real PDFs with meaningful titles (pdf_indexer will extract them)\n"
        "  - 1 duplicate PDF (same content as one of the above) for dedup demo\n"
        "  - 5 markdown notes with real prose (LLM previews will see them)\n"
        "  - 3 code files (.py, .js)\n"
        "  - 4 data files (.csv, .json, .yaml)\n"
        "  - 5 binary placeholders (.png, .jpg, .mp3, .mp4, .zip)\n"
        "  - some files in subdir/\n"
    )


def _print_tree(root: Path, base: Path, prefix: str = "  ") -> None:
    entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    for entry in entries:
        rel = entry.relative_to(base).as_posix()
        if entry.is_dir():
            print(f"{prefix}{rel}/")
            _print_tree(entry, base, prefix + "  ")
        else:
            print(f"{prefix}{rel}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", default="./sandbox", help="Target folder (default: ./sandbox)")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing non-empty folder")
    args = parser.parse_args()
    seed(Path(args.dest), force=args.force)


if __name__ == "__main__":
    main()
