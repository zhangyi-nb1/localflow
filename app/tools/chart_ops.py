"""Phase 3.2 — chart generation for tabular data.

Strictly rule-based: each chart targets ONE column and is produced by
LocalFlow code calling matplotlib directly. LLMs never write chart code
or pick visualization styles here — outline §7.1 / §3.7 design
constraint #5 forbids model-driven code execution. Phase 3.3 will let
LLMs choose which column to chart via a *typed* AnalysisSpec, but the
rendering itself stays here.

Each function returns PNG bytes. The data_reporter planner base64-encodes
these into ``metadata.binary_content_b64`` so plan.json stays JSON-safe;
the executor decodes back to bytes when writing.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING, Iterable, Mapping

logging.getLogger("matplotlib").setLevel(logging.ERROR)

if TYPE_CHECKING:
    import pandas as pd

MAX_BARS = 20
"""Cap bar chart entries — beyond this, X-axis labels are unreadable."""

MAX_TITLE_CHARS = 80


def histogram_png(
    series: "pd.Series",
    *,
    title: str,
    xlabel: str,
    bins: int = 20,
) -> bytes:
    """Render a histogram of a numeric series. Returns PNG bytes.

    Accepts a pandas Series, numpy array, or plain Python list/sequence
    — anything that ``pd.Series(x)`` can wrap. NaN is dropped before
    plotting. Raises if matplotlib fails; the planner wraps the call in
    try/except so one bad chart doesn't abort the whole skill.
    """
    import pandas as pd

    if not hasattr(series, "dropna"):
        series = pd.Series(list(series))

    plt = _lazy_import_matplotlib()
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    data = series.dropna()
    if len(data) == 0:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
    else:
        ax.hist(data, bins=bins, edgecolor="white")
    ax.set_title(_clip(title))
    ax.set_xlabel(_clip(xlabel, 50))
    ax.set_ylabel("count")
    ax.grid(True, axis="y", alpha=0.3)
    return _fig_to_png(fig, plt)


def bar_png(
    counts: Mapping[str, int] | Iterable[tuple[str, int]],
    *,
    title: str,
    xlabel: str,
    max_bars: int = MAX_BARS,
) -> bytes:
    """Render a bar chart of category → count. Returns PNG bytes.

    Accepts a dict ``{category: count}`` or any iterable of pairs. Sorts
    descending by count and caps at ``max_bars`` (rest collapsed into
    "(other)" if needed).
    """
    plt = _lazy_import_matplotlib()
    items = list(counts.items()) if isinstance(counts, Mapping) else list(counts)
    items.sort(key=lambda kv: -int(kv[1]))
    if len(items) > max_bars:
        other_total = sum(int(v) for _, v in items[max_bars:])
        items = items[:max_bars] + [("(other)", other_total)]
    labels = [_clip(str(k), 20) for k, _ in items]
    values = [int(v) for _, v in items]

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    if not items:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
    else:
        ax.bar(range(len(values)), values)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_title(_clip(title))
    ax.set_xlabel(_clip(xlabel, 50))
    ax.set_ylabel("count")
    ax.grid(True, axis="y", alpha=0.3)
    return _fig_to_png(fig, plt)


# --------------------------------------------------------------------- internals


_CJK_FONT_INSTALLED = False


def _lazy_import_matplotlib():
    """Import matplotlib only when chart functions actually run. CLI
    commands that don't touch charts (inspect, rollback, plan for skills
    without charts) shouldn't pay the ~500 ms import cost.

    Also configures a CJK-capable font fallback chain on first import
    so Chinese / Japanese / Korean titles and labels render correctly
    instead of as tofu boxes.
    """
    import matplotlib

    matplotlib.use("Agg")  # headless backend, set BEFORE pyplot import
    import matplotlib.pyplot as plt

    global _CJK_FONT_INSTALLED
    if not _CJK_FONT_INSTALLED:
        # Standard Windows CJK fonts (Microsoft YaHei ships on every
        # Win10/11) come first; SimHei is common on Chinese installs;
        # NotoSansCJK is the Linux/macOS-friendly fallback. DejaVu Sans
        # then covers Latin-only labels gracefully.
        matplotlib.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Noto Sans CJK SC",
            "PingFang SC",
            "DejaVu Sans",
            "sans-serif",
        ]
        # Without this, minus signs in numeric tick labels render as
        # tofu when a CJK font is active.
        matplotlib.rcParams["axes.unicode_minus"] = False
        _CJK_FONT_INSTALLED = True

    return plt


def _fig_to_png(fig, plt) -> bytes:
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _clip(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
