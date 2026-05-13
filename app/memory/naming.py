"""Phase 5 — Filename naming style transforms.

Pure function: no IO, no globals, deterministic. The transform is
applied to the file's STEM only; the extension is preserved verbatim
(so ``Report.PDF`` → ``report.PDF`` in lower mode, not ``report.pdf`` —
the user gets to decide if they want extensions lowercased).

Multi-dot extensions (``.tar.gz``) treat only the LAST dot as the
extension boundary — matches Path.stem / Path.suffix semantics.

CJK / unicode characters: kept verbatim. Only ASCII whitespace and
common ASCII punctuation (parens, brackets, plus, ampersand etc.) are
folded into the separator character.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.memory._schema import NamingStyle

_SEPARATOR_CHARS = re.compile(r"[\s\(\)\[\]\{\}+&@,;'\"!?]+")
_RUN_OF_UNDERSCORES = re.compile(r"_+")
_RUN_OF_DASHES = re.compile(r"-+")


def _split_stem_suffix(name: str) -> tuple[str, str]:
    """Split ``name`` into (stem, suffix) using the last dot. Preserves
    leading-dot filenames (``.gitignore`` → ('.gitignore', ''))."""
    p = PurePosixPath(name)
    if not p.suffix or name.startswith("."):
        return name, ""
    return p.stem, p.suffix


def _to_snake(stem: str) -> str:
    s = _SEPARATOR_CHARS.sub("_", stem)
    s = s.replace("-", "_")
    s = _RUN_OF_UNDERSCORES.sub("_", s)
    return s.strip("_").lower()


def _to_kebab(stem: str) -> str:
    s = _SEPARATOR_CHARS.sub("-", stem)
    s = s.replace("_", "-")
    s = _RUN_OF_DASHES.sub("-", s)
    return s.strip("-").lower()


def apply_naming_style(name: str, style: str | NamingStyle) -> str:
    """Transform ``name`` according to ``style``. Returns ``name``
    unchanged for the ORIGINAL style or an unrecognized string.

    Unknown styles are treated as ORIGINAL rather than raising — keeps
    the planner robust against typo'd preferences (the CLI's set command
    rejects unknown values at write time anyway).
    """
    style_value = style.value if isinstance(style, NamingStyle) else style

    if style_value == NamingStyle.ORIGINAL.value or not name:
        return name

    stem, suffix = _split_stem_suffix(name)

    if style_value == NamingStyle.SNAKE_CASE.value:
        new_stem = _to_snake(stem)
    elif style_value == NamingStyle.KEBAB_CASE.value:
        new_stem = _to_kebab(stem)
    elif style_value == NamingStyle.LOWER.value:
        new_stem = stem.lower()
    else:
        return name  # unknown → no-op

    # Guard against empty stem after stripping — keep original in that case
    # (e.g., name was all-punctuation; transformation would yield "").
    if not new_stem:
        return name
    return f"{new_stem}{suffix}"
