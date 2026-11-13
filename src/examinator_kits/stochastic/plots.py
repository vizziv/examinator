"""Figures of samples against a target, rendered to PNG `Image` blocks.

    plots.cdf(xs, stats.expon.cdf, title="f(2)")              # empirical CDF over the target's
    plots.hist(ks, pmf=pmf, title="g()")                       # frequencies against a PMF
    plots.hist(xs, bins=40, density=pdf, title="h()")          # a density histogram against a density
    plots.scatter(p[:, 0], p[:, 1], title="point()", xlabel="x", ylabel="y")
    plots.cdfs({"samples": a, "reference": b}, title="...")    # empirical CDFs together

Each raises ValueError when there is nothing finite to draw. Figures are drawn
with matplotlib's Agg renderer (no pyplot, nothing global), on a light
background at a fixed size, and the PNG carries no metadata: the same input
gives the same bytes, so a recomputed result does not change for nothing.
"""
from __future__ import annotations

import io
from typing import Callable, Mapping, Sequence

import numpy as np
from matplotlib.figure import Figure

from examinator.std.blocks import Image

SIZE = (4.5, 3.0)       # inches
DPI = 110
COLORS = ('#2a78d6', '#eb6834', '#1baf7a', '#c98500', '#d55181', '#2a9d2a')
MAX_POINTS = 2000       # per series drawn; more adds time, not evidence
MAX_BARS = 400


def _finite(a) -> np.ndarray:
    x = np.asarray(a, dtype=float).ravel()
    return x[np.isfinite(x) & (np.abs(x) < 1e12)]     # keep the helpers total on absurd values


def _thin(*arrays: np.ndarray) -> tuple[np.ndarray, ...]:
    """Every array at the same at most MAX_POINTS evenly spaced indices."""
    n = len(arrays[0])
    if n <= MAX_POINTS:
        return arrays
    idx = np.unique(np.linspace(0, n - 1, MAX_POINTS).round().astype(int))
    return tuple(a[idx] for a in arrays)


def _axes(title: str, xlabel: str, ylabel: str):
    fig = Figure(figsize=SIZE, dpi=DPI, facecolor='white')
    ax = fig.add_subplot()
    ax.set_facecolor('white')
    if title:
        ax.set_title(title, fontsize=10)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.tick_params(labelsize=8)
    ax.grid(True, color='#e4e4e4', linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    return fig, ax


def _png(fig: Figure, ax, title: str) -> Image:
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8, frameon=False)
    out = io.BytesIO()
    fig.savefig(out, format='png', metadata={'Software': None}, bbox_inches='tight', pad_inches=0.06)
    fig.clear()
    return Image(out.getvalue(), alt=title or "plot", title=title)


def _ecdf(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(x)
    return _thin(x, np.arange(1, len(x) + 1) / len(x))


def _empty(x: np.ndarray) -> None:
    if not len(x):
        raise ValueError("nothing finite to plot")


def cdfs(samples: Mapping[str, Sequence[float]], *, title: str = "", xlabel: str = "") -> Image:
    """Empirical CDFs of named samples, as steps on one axes."""
    fig, ax = _axes(title, xlabel, "CDF")
    drawn = 0
    for i, (name, values) in enumerate(samples.items()):
        x = _finite(values)
        if len(x):
            ax.step(*_ecdf(x), where='post', color=COLORS[i % len(COLORS)], linewidth=1.5, label=name)
            drawn += 1
    if not drawn:
        raise ValueError("nothing finite to plot")
    return _png(fig, ax, title)


def cdf(samples, cdf: Callable[[np.ndarray], np.ndarray] | None = None, *, title: str = "",
        name: str = "samples", reference: str = "target", xlabel: str = "") -> Image:
    """Empirical CDF of `samples` (a step), with a reference CDF over it."""
    x = _finite(samples)
    _empty(x)
    fig, ax = _axes(title, xlabel, "CDF")
    ax.step(*_ecdf(x), where='post', color=COLORS[0], linewidth=1.5, label=name)
    if cdf is not None:
        lo, hi = float(x.min()), float(x.max())
        grid = np.linspace(lo, hi if hi > lo else lo + 1, 200)
        ax.plot(grid, cdf(grid), color=COLORS[1], linewidth=1.5, label=reference)
    return _png(fig, ax, title)


def hist(samples, *, bins: int | Sequence[float] = 30, density: Callable | None = None,
         pmf: Callable | None = None, title: str = "", name: str = "samples",
         reference: str = "target", xlabel: str = "") -> Image:
    """Histogram of `samples`. With `pmf`, frequencies of integers against a PMF;
    otherwise a normalized histogram, against `density` if given."""
    x = _finite(samples)
    _empty(x)
    if pmf is not None:
        x = np.round(x)
        lo = int(x.min())
        ks = np.arange(lo, min(int(x.max()), lo + MAX_BARS - 1) + 1)
        freq = np.bincount((x[x <= ks[-1]] - lo).astype(int), minlength=len(ks)) / len(x)
        fig, ax = _axes(title, xlabel, "P")
        ax.bar(ks, freq, width=0.8, color=COLORS[0], alpha=0.45, label=name)
        ax.plot(ks, pmf(ks), 'o', color=COLORS[1], markersize=3.5, label=reference)
        return _png(fig, ax, title)
    fig, ax = _axes(title, xlabel, "density")
    _, edges, _ = ax.hist(x, bins=bins, density=True, color=COLORS[0], alpha=0.45, label=name)
    if density is not None:
        grid = np.linspace(edges[0], edges[-1], 200)
        ax.plot(grid, density(grid), color=COLORS[1], linewidth=1.5, label=reference)
    return _png(fig, ax, title)


def scatter(x, y, *, title: str = "", name: str = "samples", xlabel: str = "", ylabel: str = "") -> Image:
    x, y = np.asarray(x, dtype=float).ravel(), np.asarray(y, dtype=float).ravel()
    keep = np.isfinite(x) & np.isfinite(y) & (np.abs(x) < 1e12) & (np.abs(y) < 1e12)
    x, y = _thin(x[keep], y[keep])
    _empty(x)
    fig, ax = _axes(title, xlabel, ylabel)
    ax.scatter(x, y, s=4, color=COLORS[0], alpha=0.6, linewidths=0, label=name)
    return _png(fig, ax, title)
