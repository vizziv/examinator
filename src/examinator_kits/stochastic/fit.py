"""Testing a sampler by distribution: closeness tests used by graders.

Draw samples from the function under test, check the support, then run a
goodness-of-fit test against the target. Each helper returns a `Fit`: an
outcome in the standard vocabulary plus plot data for the grader.

Statistical checks are evidence, not proof. A failed precondition (too few
samples, values the test cannot use) is undecided (`None`); only a
demonstrated mismatch is `'incorrect'`.
"""
from __future__ import annotations

import numbers
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy import stats

from examinator.std.blocks import MAX_POINTS, Note, Plot, Series

ALPHA = 1e-4          # default per-test cutoff, no multiple-testing correction

_MAX_CELLS = 1 << 16  # chi-square: cap on single-integer cells before the open tail
_TONES = {'correct': 'good', 'incorrect': 'bad'}


@dataclass(frozen=True)
class Fit:
    outcome: str | None        # 'correct' | 'incorrect' | None (could not decide)
    p: float | None            # p-value, if a test ran
    statistic: float | None
    note: str                  # a few words for the grader
    plot: Plot | None = field(compare=False)   # evidence at a glance; holds arrays, so not compared

    def blocks(self) -> list:
        note = Note(self.note, _TONES.get(self.outcome, 'warn'))
        return [note] if self.plot is None else [self.plot, note]


def combine(*fits_or_outcomes) -> str | None:
    """Any 'incorrect' -> 'incorrect'; else any undecided -> None; else 'correct'.
    Nothing to combine is undecided."""
    outcomes = [f.outcome if isinstance(f, Fit) else f for f in fits_or_outcomes]
    if any(o == 'incorrect' for o in outcomes):
        return 'incorrect'
    if not outcomes or any(o != 'correct' for o in outcomes):
        return None
    return 'correct'


# ---------------------------------------------------------------- input handling

def _items(samples) -> list:
    if isinstance(samples, np.ndarray):
        return list(samples) if samples.ndim else [samples.item()]
    if isinstance(samples, (str, bytes)):
        return [samples]
    try:
        return list(samples)
    except TypeError:
        return [samples]


def _number(v) -> float | None:
    """`v` as a float, or None when it is not a real number."""
    if isinstance(v, np.ndarray) and v.ndim == 0:
        v = v.item()
    if not isinstance(v, (numbers.Real, np.bool_)):
        return None
    try:
        return float(v)
    except OverflowError:
        return float('inf') if v > 0 else float('-inf')
    except (TypeError, ValueError):
        return None


def _values(samples) -> tuple[np.ndarray, int, object]:
    """(numeric samples as floats, number of non-numeric samples, the first such)."""
    if isinstance(samples, np.ndarray) and samples.ndim == 1 and samples.dtype.kind in 'biuf':
        return samples.astype(float), 0, None
    values, bad, example = [], 0, None
    for item in _items(samples):
        v = _number(item)
        if v is None:
            example = item if not bad else example
            bad += 1
        else:
            values.append(v)
    return np.array(values, dtype=float), bad, example


def _usable(samples, min_samples: int) -> tuple[np.ndarray, str]:
    """Samples a test can run on, or the reason it cannot."""
    x, bad, _ = _values(samples)
    if bad:
        return x, f"{bad} non-numeric samples"
    nonfinite = int((~np.isfinite(x)).sum())
    if nonfinite:
        return x, f"{nonfinite} non-finite samples"
    if len(x) < min_samples:
        return x, f"only {len(x)} samples"
    return x, ""


def _show(v) -> str:
    if isinstance(v, (numbers.Real, np.bool_)):
        return f"{float(v):g}" if not isinstance(v, numbers.Integral) or abs(v) < 10**15 else f"{float(v):.3g}"
    text = repr(v)
    return text if len(text) <= 40 else text[:37] + "..."


def _p(p: float) -> str:
    if p >= 0.01:
        return f"{p:.2f}"
    if p <= 0:
        return "0"
    mantissa, exponent = f"{p:.0e}".split('e')
    return f"{mantissa}e{int(exponent)}"


def _plot(make: Callable, *args, **kwargs) -> Plot | None:
    """A plot if one can be drawn; plotting never breaks a check."""
    try:
        with np.errstate(all='ignore'):
            plot = make(*args, **kwargs)
        return plot if any(len(s.x) for s in plot.series) else None
    except Exception:
        return None


def _decide(p: float, cutoff: float) -> str:
    return 'incorrect' if p < cutoff else 'correct'


# ---------------------------------------------------------------- support

def support(samples, *, lo=None, hi=None, integer=False, title="") -> Fit:
    """Every sample finite, inside [lo, hi] and (with `integer`) integral."""
    x, bad, example = _values(samples)
    n = len(x) + bad
    plot = _plot(Plot.hist, x, title=title) if len(x) else None
    if n == 0:
        return Fit(None, None, None, "no samples", None)
    off = ~np.isfinite(x)
    if lo is not None:
        off |= x < lo
    if hi is not None:
        off |= x > hi
    if integer:
        with np.errstate(invalid='ignore'):
            off |= x != np.floor(x)
    count = bad + int(off.sum())
    if count == 0:
        return Fit('correct', None, None, f"support ok (n = {n})", plot)
    if not bad:
        example = x[off][0]
    return Fit('incorrect', None, None, f"{count} of {n} outside support, e.g. {_show(example)}", plot)


# ---------------------------------------------------------------- one-sample tests

def ks(samples, cdf, *, alpha=ALPHA, title="", min_samples=20) -> Fit:
    """One-sample Kolmogorov-Smirnov against a vectorized CDF."""
    x, problem = _usable(samples, min_samples)
    plot = _plot(Plot.cdf, x, cdf, title=title)
    if problem:
        return Fit(None, None, None, problem, plot)
    with np.errstate(all='ignore'):
        result = stats.kstest(x, cdf)
    stat, p = float(result.statistic), float(result.pvalue)
    if not np.isfinite(p):
        return Fit(None, None, None, "KS did not run", plot)
    return Fit(_decide(p, alpha), p, stat, f"KS p = {_p(p)}", plot)


def _merge(expected, min_expected: float) -> list[int]:
    """Start index of each merged cell: runs of cells accumulated from the left
    until each holds `min_expected`; what is left over joins the last one."""
    starts, total, start = [], 0.0, 0
    for i, e in enumerate(expected):
        total += float(e)
        if total >= min_expected:
            starts.append(start)
            total, start = 0.0, i + 1
    return starts or [0]


def _cells(x: np.ndarray, pmf, min_expected: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Chi-square cells for nonnegative integer samples `x`.

    Returns (first value of each cell, observed counts, expected counts). Cells
    start as k = 0, 1, 2, ... plus an open upper tail holding the remaining
    mass, then merge so every expected count reaches `min_expected`. The last
    cell is always open-ended.
    """
    n, size = len(x), 64
    while True:
        prob = np.asarray(pmf(np.arange(size)), dtype=float)
        tail = max(0.0, 1.0 - float(prob.sum()))
        if n * tail < min_expected or size >= _MAX_CELLS:
            break
        size *= 4
    expected = n * np.append(prob, tail)
    observed = np.bincount(np.minimum(x, size).astype(np.int64), minlength=size + 1)
    starts = _merge(expected, min_expected)
    return np.array(starts), np.add.reduceat(observed, starts), np.add.reduceat(expected, starts)


def chi_square(samples, pmf, *, alpha=ALPHA, title="", min_expected=5.0, min_samples=20) -> Fit:
    """Chi-square goodness of fit for nonnegative-integer samples against a vectorized PMF."""
    x, problem = _usable(samples, min_samples)
    if not problem and (x < 0).any():
        problem = f"{int((x < 0).sum())} negative samples"
    if not problem and (x != np.floor(x)).any():
        problem = f"{int((x != np.floor(x)).sum())} non-integer samples"
    plot = None if problem else _plot(Plot.hist, x, pmf=pmf, title=title)
    if problem:
        return Fit(None, None, None, problem, _plot(Plot.hist, x, title=title) if len(x) else None)
    _, observed, expected = _cells(x, pmf, min_expected)
    if len(expected) < 2:
        return Fit(None, None, None, "too few cells for chi-square", plot)
    with np.errstate(all='ignore'):
        terms = np.where(expected > 0, (observed - expected) ** 2 / expected,
                         np.where(observed > 0, np.inf, 0.0))
        stat = float(terms.sum())
        p = float(stats.chi2.sf(stat, len(expected) - 1))
    if not np.isfinite(p):
        return Fit(None, None, None, "chi-square did not run", plot)
    return Fit(_decide(p, alpha), p, stat, f"chi-square p = {_p(p)} ({len(expected)} cells)", plot)


# ---------------------------------------------------------------- multi-dimensional

def _matrix(a) -> np.ndarray | None:
    """`a` as an n x d float array (1-D input is n x 1), or None."""
    try:
        arr = np.asarray(a)
    except Exception:
        return None
    if arr.dtype.kind not in 'biuf':
        return None
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2 or arr.shape[1] == 0:
        return None
    return arr.astype(float)


def _ecdf(name: str, x: np.ndarray) -> Series:
    x = np.sort(x)
    y = np.arange(1, len(x) + 1) / len(x)
    if len(x) > MAX_POINTS:
        idx = np.unique(np.linspace(0, len(x) - 1, MAX_POINTS).round().astype(int))
        x, y = x[idx], y[idx]
    return Series(name, x, y, 'step')


def projected_ks(samples, reference, *, projections=16, seed=0, alpha=ALPHA, title="",
                 min_samples=20) -> Fit:
    """Two-sample KS along random directions and the coordinate axes, against
    samples from a trusted reference sampler. Bonferroni within this one test."""
    def undecided(note: str) -> Fit:
        return Fit(None, None, None, note, None)

    a, b = _matrix(samples), _matrix(reference)
    if a is None:
        return undecided("samples are not an n x d array")
    if b is None:
        return undecided("reference is not an m x d array")
    d = b.shape[1]
    if a.shape[1] != d:
        return undecided(f"samples have dimension {a.shape[1]}, expected {d}")
    if not np.isfinite(a).all():
        return undecided("non-finite samples")
    if not np.isfinite(b).all():
        return undecided("non-finite reference")
    if min(len(a), len(b)) < min_samples:
        return undecided(f"only {min(len(a), len(b))} samples")

    random = np.random.default_rng(seed).normal(size=(max(int(projections), 0), d))
    random /= np.linalg.norm(random, axis=1, keepdims=True)
    directions = np.vstack([random, np.eye(d)])
    with np.errstate(all='ignore'):
        pa, pb = a @ directions.T, b @ directions.T
        if not (np.isfinite(pa).all() and np.isfinite(pb).all()):
            return undecided("values too large to project")
        results = [stats.ks_2samp(pa[:, j], pb[:, j]) for j in range(len(directions))]
    pvalues = np.array([float(r.pvalue) for r in results])
    if not np.isfinite(pvalues).all():
        return undecided("KS did not run")
    worst = int(np.argmin(pvalues))
    p, stat = float(pvalues[worst]), float(results[worst].statistic)
    axis = worst - len(random)
    where = f"axis {axis}" if axis >= 0 else "random direction"
    plot = Plot(title, [_ecdf("samples", pa[:, worst]), _ecdf("reference", pb[:, worst])],
                xlabel=f"projection on {where}", ylabel="CDF")
    return Fit(_decide(p, alpha / len(directions)), p, stat,
               f"projected KS min p = {_p(p)} ({where}, {len(directions)} directions)", plot)
