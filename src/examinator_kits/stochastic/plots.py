"""Figures of samples against a target, rendered to PNG `Image` blocks.

    plots.cdf(xs, stats.expon.cdf, title="f(2)")              # empirical CDF over the target's
    plots.hist(ks, pmf=pmf, title="g()")                       # frequencies against a PMF
    plots.hist(xs, bins=40, density=pdf, title="h()")          # a density histogram against a density
    plots.scatter(p[:, 0], p[:, 1], title="point()", xlabel="x", ylabel="y")
    plots.cdfs({"samples": a, "reference": b}, title="...")    # empirical CDFs together

A figure is a pure function of its arguments: the same arguments give the same
PNG bytes in any process, whatever matplotlib settings are about (a user's
matplotlibrc, an exam helper changing `rcParams`). Each is drawn under
matplotlib's built-in defaults plus `STYLE`, on a light background at a fixed
size, and the PNG carries no metadata. Text (titles, labels, names) is literal;
`math=True` renders `$...$` as mathtext. Every size is bounded by one rule: a
count asked for is at most its `MAX_*`, and longer data is thinned to evenly
spaced elements. A figure that cannot be drawn (nothing finite, a reference
function that fails, bad math) is a short warning `Note` instead, so a figure
never breaks a check and never vanishes silently.
"""
from __future__ import annotations

import io
import threading
from typing import Callable, Mapping, Sequence

import matplotlib
import matplotlib.style
import numpy as np
from matplotlib.figure import Figure

from examinator.std.blocks import Image, Note

SIZE = (4.5, 3.0)       # inches
DPI = 110
COLORS = ('#2a78d6', '#eb6834', '#1baf7a', '#c98500', '#d55181', '#2a9d2a')
MAX_POINTS = 2000       # per series drawn; more adds time, not evidence
MAX_BARS = 400          # bars, or histogram bins
MAX_SERIES = len(COLORS)
GRID = 200              # points on a reference curve
STYLE = {'font.family': 'DejaVu Sans', 'figure.facecolor': 'white', 'axes.facecolor': 'white',
         'savefig.facecolor': 'white', 'savefig.dpi': DPI, 'path.simplify': True}

# rcParams are process-wide: a figure is drawn under a fixed style, one at a time.
_DRAWING = threading.Lock()


class _Nothing(ValueError):
    """Nothing finite to draw (the message is shown)."""


def _count(n, most: int) -> int:
    """A count asked for, between 1 and `most`."""
    return int(min(max(int(n), 1), most))


def _thin(*arrays: np.ndarray, most: int = MAX_POINTS) -> tuple[np.ndarray, ...]:
    """Every array at the same at most `most` evenly spaced indices."""
    n = len(arrays[0])
    if n <= most:
        return arrays
    idx = np.unique(np.linspace(0, n - 1, most).round().astype(int))
    return tuple(a[idx] for a in arrays)


def _finite(a) -> np.ndarray:
    x = np.asarray(a, dtype=float).ravel()
    return x[np.isfinite(x) & (np.abs(x) < 1e12)]     # keep the helpers total on absurd values


def _some(x: np.ndarray) -> np.ndarray:
    if not len(x):
        raise _Nothing("nothing finite to plot")
    return x


def _figure(draw: Callable, title: str, xlabel: str, ylabel: str, math: bool) -> Image | Note:
    """`draw(ax)` on fresh axes under the fixed style, as a PNG block; or a Note."""
    try:
        with _DRAWING, matplotlib.style.context(['default', {**STYLE, 'text.parse_math': bool(math)}]), \
                np.errstate(all='ignore'):
            fig = Figure(figsize=SIZE, dpi=DPI)
            ax = fig.add_subplot()
            if title:
                ax.set_title(title, fontsize=10)
            ax.set_xlabel(xlabel, fontsize=9)
            ax.set_ylabel(ylabel, fontsize=9)
            ax.tick_params(labelsize=8)
            ax.grid(True, color='#e4e4e4', linewidth=0.6)
            ax.set_axisbelow(True)
            for side in ('top', 'right'):
                ax.spines[side].set_visible(False)
            draw(ax)
            if ax.get_legend_handles_labels()[0]:
                ax.legend(fontsize=8, frameon=False)
            out = io.BytesIO()
            fig.savefig(out, format='png', metadata={'Software': None}, bbox_inches='tight', pad_inches=0.06)
    except Exception as e:
        why = str(e) if isinstance(e, _Nothing) else f"{type(e).__name__}"
        return Note(f"{title or 'plot'}: not drawn ({why})", 'warn')
    return Image(out.getvalue(), alt=title or "plot", title=title)


def _ecdf(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(x)
    return _thin(x, np.arange(1, len(x) + 1) / len(x))


def cdfs(samples: Mapping[str, Sequence[float]], *, title: str = "", xlabel: str = "",
         math: bool = False) -> Image | Note:
    """Empirical CDFs of named samples (the first MAX_SERIES), as steps on one axes."""
    def draw(ax):
        drawn = 0
        for i, (name, values) in enumerate(list(samples.items())[:MAX_SERIES]):
            x = _finite(values)
            if len(x):
                ax.step(*_ecdf(x), where='post', color=COLORS[i], linewidth=1.5, label=str(name))
                drawn += 1
        if not drawn:
            raise _Nothing("nothing finite to plot")
    return _figure(draw, title, xlabel, "CDF", math)


def cdf(samples, cdf: Callable[[np.ndarray], np.ndarray] | None = None, *, title: str = "",
        name: str = "samples", reference: str = "target", xlabel: str = "", math: bool = False) -> Image | Note:
    """Empirical CDF of `samples` (a step), with a reference CDF over it."""
    def draw(ax):
        x = _some(_finite(samples))
        ax.step(*_ecdf(x), where='post', color=COLORS[0], linewidth=1.5, label=name)
        if cdf is not None:
            lo, hi = float(x.min()), float(x.max())
            grid = np.linspace(lo, hi if hi > lo else lo + 1, GRID)
            ax.plot(grid, cdf(grid), color=COLORS[1], linewidth=1.5, label=reference)
    return _figure(draw, title, xlabel, "CDF", math)


def _bins(bins) -> int | np.ndarray:
    """Histogram bins by the one rule: a count at most MAX_BARS; edges thinned to MAX_BARS + 1."""
    if isinstance(bins, (int, np.integer)):
        return _count(bins, MAX_BARS)
    if isinstance(bins, str):
        raise ValueError("bins is a count or edges")
    edges = np.unique(_finite(bins))
    if len(edges) < 2:
        raise _Nothing("fewer than two finite bin edges")
    return _thin(edges, most=MAX_BARS + 1)[0]


def hist(samples, *, bins: int | Sequence[float] = 30, density: Callable | None = None,
         pmf: Callable | None = None, title: str = "", name: str = "samples",
         reference: str = "target", xlabel: str = "", math: bool = False) -> Image | Note:
    """Histogram of `samples`. With `pmf`, frequencies of integers against a PMF
    (at most MAX_BARS values from the least); otherwise a normalized histogram,
    against `density` if given."""
    def draw(ax):
        x = _some(_finite(samples))
        if pmf is not None:
            x = np.round(x)
            lo = int(x.min())
            ks = np.arange(lo, min(int(x.max()), lo + MAX_BARS - 1) + 1)
            freq = np.bincount((x[x <= ks[-1]] - lo).astype(int), minlength=len(ks)) / len(x)
            ax.set_ylabel("P", fontsize=9)
            ax.bar(ks, freq, width=0.8, color=COLORS[0], alpha=0.45, label=name)
            ax.plot(ks, pmf(ks), 'o', color=COLORS[1], markersize=3.5, label=reference)
            return
        _, edges, _ = ax.hist(x, bins=_bins(bins), density=True, color=COLORS[0], alpha=0.45, label=name)
        if density is not None:
            grid = np.linspace(edges[0], edges[-1], GRID)
            ax.plot(grid, density(grid), color=COLORS[1], linewidth=1.5, label=reference)
    return _figure(draw, title, xlabel, "density", math)


def scatter(x, y, *, title: str = "", name: str = "samples", xlabel: str = "", ylabel: str = "",
            math: bool = False) -> Image | Note:
    def draw(ax):
        a, b = np.asarray(x, dtype=float).ravel(), np.asarray(y, dtype=float).ravel()
        if len(a) != len(b):
            raise ValueError("x and y differ in length")
        keep = np.isfinite(a) & np.isfinite(b) & (np.abs(a) < 1e12) & (np.abs(b) < 1e12)
        a, b = _thin(a[keep], b[keep])
        _some(a)
        ax.scatter(a, b, s=4, color=COLORS[0], alpha=0.6, linewidths=0, label=name)
    return _figure(draw, title, xlabel, ylabel, math)
