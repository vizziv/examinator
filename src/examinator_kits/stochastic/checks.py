"""Scaffolding for a sampler check: draw, test, and show the verdict.

    def check(ctx):
        return checks.check_source(ctx.input('parse'), LANGUAGE, ['2.1', '2.2'],
                                   lambda program: [part_one(program), part_two(program)])

    def part_one(program):
        part = checks.Part('2.1')
        if program.is_blank('f'):
            return part.settle('blank')
        try:
            xs = checks.draw(program, 'f', (2,), n=2000, deadline=time.monotonic() + 20, seed=SEED)
        except checks.Stop as stop:
            return part.stop("f(2)", stop)
        part.add("f(2)", checks.fit_cdf(xs, cdf, lo=0, title="f(2)"), xs)
        return part.combine()

What a run-time failure proves is policy, and the exam's: `draw` treats an
exhausted budget and syntax outside the language as undecided always, and a
`Failure` as undecided when its kind is in `undecided` (or `undecided(failure)`
is true); any other failure is a demonstrated mistake.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field

import numpy as np

from examinator import Evidence
from examinator.std import pseudocode
from examinator.std.blocks import Code, Details, Note, Table

from . import fit as fits
from .fit import Fit

UNDECIDED = ('undefined', 'unsupported')    # default: a name nobody provides, a limit of the checker


class Stop(Exception):
    """Sampling ended early. `outcome` is 'incorrect' or None (undecided);
    `error` is the interpreter's exception, if one stopped it."""

    def __init__(self, outcome: str | None, note: str, error: Exception | None = None):
        super().__init__(note)
        self.outcome, self.note, self.error = outcome, note, error


def draw(program: pseudocode.Program, entry: str, args=(), *, n: int, deadline: float | None = None,
         seconds: float | None = None, seed: int = 0, primitives=None, constants=None,
         max_steps: int | None = 2_000_000, rng=None,
         undecided: Collection[str] | Callable[[pseudocode.Failure], bool] = UNDECIDED) -> list:
    """`n` results of `entry(*args)`, or `Stop`. `deadline` is a `time.monotonic()`
    value; `seconds` sets one from now. `rng` is passed to `Program.sample`."""
    if seconds is not None:
        deadline = time.monotonic() + seconds
    try:
        return program.sample(entry, args, n=n, seed=seed, deadline=deadline, max_steps=max_steps,
                              primitives=primitives, constants=constants, rng=rng)
    except (pseudocode.Budget, pseudocode.Unsupported) as e:
        raise Stop(None, str(e), e) from None
    except pseudocode.Failure as e:
        open_ = undecided(e) if callable(undecided) else e.kind in undecided
        raise Stop(None if open_ else 'incorrect', str(e), e) from None


_VECTOR_FAILURE = re.compile(r'vectors |cannot compute .*[\[(]')


def vector_arithmetic(failure: pseudocode.Failure) -> bool:
    """A type failure of arithmetic involving a list or tuple (with `vectors` on:
    points of different sizes, a point plus a number, ...)."""
    return failure.kind == 'type' and bool(_VECTOR_FAILURE.match(failure.detail))


def accept_reject(program: pseudocode.Program, entry: str) -> str:
    """Why `entry` may not stop after a fixed number of draws: 'while loop',
    'recursion', or "" when it reaches neither."""
    if program.has_loop(entry):
        return "while loop"
    return "recursion" if program.is_recursive(entry) else ""


# --- support, then goodness of fit ------------------------------------------------------

def fit_cdf(samples, cdf, *, lo=None, hi=None, title: str = "") -> Fit:
    """Support first; then KS against the CDF."""
    result = fits.support(samples, lo=lo, hi=hi, title=title)
    return result if result.outcome != 'correct' else fits.ks(samples, cdf, title=title)


def fit_pmf(samples, pmf, *, title: str = "") -> Fit:
    """Nonnegative integers first; then chi-square against the PMF."""
    result = fits.support(samples, lo=0, integer=True, title=title)
    return result if result.outcome != 'correct' else fits.chi_square(samples, pmf, title=title)


def matrix(samples, d: int) -> np.ndarray:
    """Samples as an n x d array of finite numbers, or `Stop('incorrect')`."""
    for v in samples:
        ok = isinstance(v, (list, tuple)) and len(v) == d and all(
            isinstance(c, (int, float)) and not isinstance(c, bool) and np.isfinite(c) for c in v)
        if not ok:
            raise Stop('incorrect', f"result is not a point of {d} finite numbers: {str(v)[:40]}")
    return np.asarray(samples, dtype=float).reshape(len(samples), d)


def inside(mask: np.ndarray, points: np.ndarray, plot=None) -> Fit:
    """A support test from a boolean mask over the samples."""
    n, off = len(mask), int((~mask).sum())
    if off == 0:
        return Fit('correct', None, None, f"support ok (n = {n})", plot)
    example = ", ".join(f"{c:.4g}" for c in np.atleast_1d(points[~mask][0]))
    return Fit('incorrect', None, None, f"{off} of {n} outside the region, e.g. ({example})", plot)


def table_pmf(probs):
    """A PMF on 0 .. len(probs) - 1 from a table, for `fit.chi_square`."""
    probs = np.asarray(probs, dtype=float)

    def pmf(k):
        k = np.asarray(k)
        return np.where((k >= 0) & (k < len(probs)), probs[np.clip(k, 0, len(probs) - 1).astype(int)], 0.0)
    return pmf


def preview(samples, limit: int = 8) -> str:
    """The first samples, short."""
    def show(v):
        if isinstance(v, float):
            return f"{v:.4g}"
        if isinstance(v, (list, tuple)):
            return "[" + ", ".join(show(c) for c in v) + "]"
        return str(v)[:40]
    shown = ", ".join(show(v) for v in samples[:limit])
    return shown + (f", ... ({len(samples)})" if len(samples) > limit else "")


# --- one part's verdict -------------------------------------------------------------------

@dataclass
class Part:
    """One part's verdict (a field of the evidence) and what a grader sees for it."""
    label: str
    outcome: str | None = None
    note: str = ""
    plots: list = field(default_factory=list)
    tests: list = field(default_factory=list)       # rows: [test, outcome, note]
    dumps: list = field(default_factory=list)       # rows: [what, first samples]
    fits: list = field(default_factory=list)
    shown: int = 8                                  # samples previewed per dump

    def add(self, name: str, fit: Fit, samples=None, *, plot: bool = True) -> Fit:
        """Record a test; its plot is shown if `plot` or if it failed."""
        self.fits.append(fit)
        self.tests.append([name, fit.outcome or '?', fit.note])
        if fit.plot is not None and (plot or fit.outcome == 'incorrect'):
            self.plots.append(fit.plot)
        if samples is not None:
            self.dumps.append([name, preview(samples, self.shown)])
        return fit

    def stop(self, name: str, stop: Stop) -> 'Part':
        """Sampling stopped: record why and settle on its outcome."""
        self.tests.append([name, stop.outcome or '?', stop.note])
        return self.settle(stop.outcome)

    def settle(self, outcome: str | None, note: str = "") -> 'Part':
        """Set the verdict; without a note, the first failed test explains it."""
        self.outcome = outcome
        failed = [f"{name}: {why}" for name, result, why in self.tests if result != 'correct']
        self.note = note or (failed[0] if failed and outcome != 'correct' else "")
        return self

    def combine(self) -> 'Part':
        """Incorrect if any test is; undecided if any could not decide; else correct."""
        return self.settle(fits.combine(*self.fits))


def evidence(parts: Part | Sequence[Part], source: str, ran: str | None = None) -> Evidence:
    """Plots first, then one line per part; tests, samples and the source collapsed."""
    parts = [parts] if isinstance(parts, Part) else list(parts)
    data = {'fields': {p.label: {'outcome': p.outcome, 'note': p.note} for p in parts}}
    blocks = [plot for p in parts for plot in p.plots]
    blocks.append(Table(['', '', ''], [[p.label, p.outcome or '?', p.note] for p in parts]))
    for p in parts:
        if p.tests:
            blocks.append(Details("Tests" if len(parts) == 1 else f"{p.label} tests",
                                  Table(["test", '', ''], p.tests)))
    dumps = [row for p in parts for row in p.dumps]
    if dumps:
        blocks.append(Details("Samples", Table(['', "first samples"], dumps), audience='grader'))
    blocks.append(Details("Source", Code(source), audience='grader'))
    if ran and ran != source:
        blocks.append(Details("As run", Code(ran), audience='grader'))
    return Evidence(data, blocks)


def unparsed(labels: str | Sequence[str], source: str, error: pseudocode.Unsupported) -> Evidence:
    """The source is outside the language: every part is undecided."""
    labels = [labels] if isinstance(labels, str) else list(labels)
    data = {'fields': {label: {'outcome': None, 'note': str(error)} for label in labels}}
    return Evidence(data, [Note(str(error), 'warn'),
                           Details("Source", Code(source, [error.line] if error.line else []),
                                   audience='grader')])


def check_source(source: str, language: pseudocode.Language | None, labels: str | Sequence[str],
                 judge: Callable[[pseudocode.Program], Part | Sequence[Part]], *,
                 ran: str | None = None) -> Evidence:
    """Parse `source` (`ran`, if given, is what is run instead: a rewrite that keeps
    line numbers), judge the program, and show the parts. Source outside the
    language leaves every part undecided."""
    try:
        program = pseudocode.parse(source if ran is None else ran, language)
    except pseudocode.Unsupported as e:
        return unparsed(labels, source, e)
    return evidence(judge(program), source, ran)
