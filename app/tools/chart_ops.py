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
from typing import TYPE_CHECKING, Any, Iterable, Mapping

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
    items.sort(key=lambda kv: -float(kv[1]))
    if len(items) > max_bars:
        other_total = sum(float(v) for _, v in items[max_bars:])
        items = items[:max_bars] + [("(other)", other_total)]
    labels = [_clip(str(k), 20) for k, _ in items]
    values = [float(v) for _, v in items]

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


# --------------------------------------------------------------------- Phase 11: pie + line


MAX_PIE_SLICES = 8
"""Cap pie slices — beyond 8, the chart becomes unreadable.

Slices beyond the cap are collapsed into ``(other)`` so the proportion
view still adds up to 100%.
"""


def pie_png(
    counts: Mapping[str, float] | Iterable[tuple[str, float]],
    *,
    title: str,
    max_slices: int = MAX_PIE_SLICES,
) -> bytes:
    """Render a pie chart of category → value. Returns PNG bytes.

    Accepts the same shapes as :func:`bar_png`. Slices beyond
    ``max_slices`` are merged into ``(other)``. Negative values are
    clamped to zero because a negative slice has no geometric meaning;
    the planner heuristic only routes truly positive series to pie.
    """
    plt = _lazy_import_matplotlib()
    items = list(counts.items()) if isinstance(counts, Mapping) else list(counts)
    cleaned: list[tuple[str, float]] = []
    for k, v in items:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv < 0:
            fv = 0.0
        cleaned.append((str(k), fv))
    cleaned.sort(key=lambda kv: -kv[1])
    if len(cleaned) > max_slices:
        other_total = sum(v for _, v in cleaned[max_slices:])
        cleaned = cleaned[:max_slices] + [("(other)", other_total)]

    fig, ax = plt.subplots(figsize=(7.0, 5.0))
    total = sum(v for _, v in cleaned)
    if total <= 0:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    else:
        labels = [_clip(k, 20) for k, _ in cleaned]
        values = [v for _, v in cleaned]
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
        ax.axis("equal")
    ax.set_title(_clip(title))
    return _fig_to_png(fig, plt)


def line_png(
    x_values: Iterable[Any],
    y_values: Iterable[Any],
    *,
    title: str,
    xlabel: str,
    ylabel: str,
) -> bytes:
    """Render a line chart with one series. Returns PNG bytes.

    ``x_values`` may be datetimes, numbers, or strings — matplotlib
    handles the conversion. ``y_values`` are coerced to float;
    non-numerics drop their (x, y) pair.
    """
    plt = _lazy_import_matplotlib()
    xs_raw = list(x_values)
    ys_raw = list(y_values)
    xs: list[Any] = []
    ys: list[float] = []
    for x, y in zip(xs_raw, ys_raw):
        try:
            yv = float(y)
        except (TypeError, ValueError):
            continue
        xs.append(x)
        ys.append(yv)

    fig, ax = plt.subplots(figsize=(8.0, 4.0))
    if not xs:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
    else:
        ax.plot(xs, ys, marker="o")
        for label in ax.get_xticklabels():
            label.set_rotation(30)
            label.set_ha("right")
    ax.set_title(_clip(title))
    ax.set_xlabel(_clip(xlabel, 50))
    ax.set_ylabel(_clip(ylabel, 50))
    ax.grid(True, alpha=0.3)
    return _fig_to_png(fig, plt)


# --------------------------------------------------------------------- internals


_CJK_FONT_INSTALLED = False


def _lazy_import_matplotlib():
    """Import matplotlib only when chart functions actually run. CLI
    commands that don't touch charts (inspect, rollback, plan for skills
    without charts) shouldn't pay the ~500 ms import cost.

    v0.9.1: matplotlib moved out of the base install. If a plan tries
    to render a chart in an environment without the ``[data]`` extra,
    raise a friendly ImportError that names the right pip command
    instead of letting the stack trace bubble up unchanged.

    Also configures a CJK-capable font fallback chain on first import
    so Chinese / Japanese / Korean titles and labels render correctly
    instead of as tofu boxes.
    """
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover — exercised via friendly message
        raise ImportError(
            "matplotlib is required for chart rendering but is not installed. "
            "Install with: pip install 'localflow-agent[data]' "
            "(or [all] for everything). Original error: " + str(exc)
        ) from exc

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
