"""Generate a synthetic messy folder for demo runs.

Usage::

    python examples/seed.py            # writes to examples/messy_downloads
    python examples/seed.py --dest X   # writes to X
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

FIXTURE = {
    "agent_memory_survey.pdf": "PDF: Survey of agent memory architectures.",
    "transformer_paper.pdf": "PDF: Attention is all you need (reproduction).",
    "duplicate_paper.pdf": "PDF: Survey of agent memory architectures.",  # dup of #1
    "lecture_notes.docx": "DOCX: Distributed systems lecture transcript.",
    "budget.xlsx": "XLSX: monthly budget spreadsheet (fake).",
    "telemetry.csv": "timestamp,value\n2026-05-01T00:00,1.0\n2026-05-02T00:00,2.0\n",
    "todo.md": "# Todo\n- finish localflow demo\n- write report\n",
    "readme.txt": "Random readme.",
    "beach.jpg": b"\xff\xd8\xff\xe0fake-jpeg-bytes",
    "diagram.png": b"\x89PNG\r\n\x1a\nfake-png",
    "song.mp3": b"ID3\x03fake-mp3",
    "clip.mp4": b"\x00\x00\x00\x20ftypisomfake-mp4",
    "backup.zip": b"PK\x03\x04fake-zip",
    "main.py": "print('hello world')\n",
    "app.js": "console.log('hi')\n",
    "config.json": '{"hello": "world"}\n',
    "data.yaml": "key: value\n",
    "subdir/deep_note.md": "# Nested note\nThis lives under subdir/.\n",
    "subdir/another.pdf": "PDF: nested paper.",
}


def seed(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel, content in FIXTURE.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    print(f"Seeded {len(FIXTURE)} files into {dest}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", default="examples/messy_downloads")
    args = parser.parse_args()
    seed(Path(args.dest))


if __name__ == "__main__":
    main()
