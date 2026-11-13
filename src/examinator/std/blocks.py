"""The evidence block vocabulary.

A check returns `Result(grade, fields, blocks)`. Blocks are what graders see; the app
renders them, and exams never write layout. Plots are stored as data and drawn
in the browser. Every block serializes with `to_json()` to a dict with a
`'type'`.

Stored shapes (the renderer's contract):

    {'type': 'note', 'text': str, 'tone': 'info' | 'good' | 'bad' | 'warn'}
    {'type': 'table', 'columns': [str], 'rows': [[cell]]}
    {'type': 'code', 'text': str, 'highlight': [line numbers, 1-based]}
    {'type': 'details', 'summary': str, 'blocks': [block]}       # collapsed
    {'type': 'raw', 'html': str}                                  # SVG or HTML escape hatch
    {'type': 'plot', 'title': str, 'xlabel': str, 'ylabel': str,
     'series': [{'name': str, 'style': 'step' | 'line' | 'bars' | 'points',
                 'x': [float], 'y': [float]}]}

For 'bars', x holds bin edges (len(y) + 1) or bar centres (len(y)).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np

MAX_POINTS = 400   # per series; plots are evidence at a glance, not data dumps


def _floats(a) -> list[float | None]:
    """JSON-safe numbers: non-finite values become null (the renderer skips them)."""
    return [round(float(v), 6) if np.isfinite(v) else None for v in np.asarray(a, dtype=float)]


def ecdf(samples, name: str = "samples") -> 'Series':
    """The empirical CDF of the finite samples as a step series, downsampled."""
    x = np.sort(np.asarray(samples, dtype=float))
    x = x[np.isfinite(x)]
    n = len(x)
    y = np.arange(1, n + 1) / max(n, 1)
    if n > MAX_POINTS:
        idx = np.unique(np.linspace(0, n - 1, MAX_POINTS).round().astype(int))
        x, y = x[idx], y[idx]
    return Series(name, x, y, 'step')


@dataclass
class Note:
    text: str
    tone: str = 'info'

    def to_json(self) -> dict:
        return {'type': 'note', 'text': self.text, 'tone': self.tone}


@dataclass
class Table:
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]

    def to_json(self) -> dict:
        return {'type': 'table', 'columns': list(self.columns), 'rows': [list(r) for r in self.rows]}


@dataclass
class Code:
    text: str
    highlight: Sequence[int] = ()

    def to_json(self) -> dict:
        return {'type': 'code', 'text': self.text, 'highlight': list(self.highlight)}


class Details:
    """Collapsed by default: `Details("Samples", Table(...), Note(...))`."""

    def __init__(self, summary: str, *blocks: Any):
        self.summary = summary
        self.blocks = blocks[0] if len(blocks) == 1 and isinstance(blocks[0], (list, tuple)) else blocks

    def to_json(self) -> dict:
        return {'type': 'details', 'summary': self.summary,
                'blocks': [b.to_json() if hasattr(b, 'to_json') else b for b in self.blocks]}


@dataclass
class Raw:
    """Escape hatch: raw SVG or HTML. Scripts do not run (the app's CSP forbids them)."""
    html: str

    def to_json(self) -> dict:
        return {'type': 'raw', 'html': self.html}


@dataclass(eq=False)
class Series:
    name: str
    x: Sequence[float]
    y: Sequence[float]
    style: str = 'line'

    def to_json(self) -> dict:
        return {'name': self.name, 'style': self.style, 'x': _floats(self.x), 'y': _floats(self.y)}


@dataclass(eq=False)
class Plot:
    title: str = ""
    series: Sequence[Series] = ()
    xlabel: str = ""
    ylabel: str = ""

    def to_json(self) -> dict:
        return {'type': 'plot', 'title': self.title, 'xlabel': self.xlabel,
                'ylabel': self.ylabel, 'series': [s.to_json() for s in self.series]}

    @staticmethod
    def cdf(samples, cdf: Callable[[np.ndarray], np.ndarray] | None = None, *, title: str = "",
            name: str = "samples", reference: str = "target", xlabel: str = "") -> 'Plot':
        """Empirical CDF of `samples` (a step), with a reference CDF over it."""
        step = ecdf(samples, name)
        x, n = step.x, len(step.x)
        series = [step]
        if cdf is not None and n:
            lo, hi = float(x[0]), float(x[-1])
            grid = np.linspace(lo, hi if hi > lo else lo + 1, 200)
            series.append(Series(reference, grid, cdf(grid), 'line'))
        return Plot(title, series, xlabel=xlabel, ylabel="CDF")

    @staticmethod
    def hist(samples, *, bins: int | Sequence[float] = 30, density: Callable | None = None,
             pmf: Callable | None = None, title: str = "", name: str = "samples",
             reference: str = "target", xlabel: str = "") -> 'Plot':
        """Histogram of `samples`. With `pmf`, integer bars against a PMF; with
        `density`, a normalized histogram against a density."""
        x = np.asarray(samples, dtype=float)
        x = x[np.isfinite(x) & (np.abs(x) < 1e12)]      # keep the helpers total on absurd values
        if pmf is not None and len(x):
            x = np.round(x)
            lo, hi = int(x.min()), int(x.max())
            hi = min(hi, lo + MAX_POINTS - 1)
            ks = np.arange(lo, hi + 1)
            counts = np.array([(x == k).sum() for k in ks]) / len(x)
            return Plot(title, [Series(name, ks, counts, 'bars'),
                                Series(reference, ks, pmf(ks), 'points')], xlabel=xlabel, ylabel="P")
        heights, edges = np.histogram(x, bins=bins, density=True) if len(x) else (np.zeros(1), np.array([0.0, 1.0]))
        series = [Series(name, edges, heights, 'bars')]
        if density is not None and len(x):
            grid = np.linspace(edges[0], edges[-1], 200)
            series.append(Series(reference, grid, density(grid), 'line'))
        return Plot(title, series, xlabel=xlabel, ylabel="density")

    @staticmethod
    def scatter(x, y, *, title: str = "", name: str = "samples", xlabel: str = "", ylabel: str = "") -> 'Plot':
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        keep = np.isfinite(x) & np.isfinite(y)
        x, y = x[keep], y[keep]
        if len(x) > MAX_POINTS:
            idx = np.unique(np.linspace(0, len(x) - 1, MAX_POINTS).round().astype(int))
            x, y = x[idx], y[idx]
        return Plot(title, [Series(name, x, y, 'points')], xlabel=xlabel, ylabel=ylabel)
