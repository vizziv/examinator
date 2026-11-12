"""Scaffolding for a sampler check: draw, test, and show the verdict.

    def check(ctx):                     # one check per field: its grade is the part's outcome
        return checks.check_source(ctx.read('2.1'), LANGUAGE, '2.1', part_one)

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

What a stopped run proposes (a grader reviews every proposed grade and may override):
1. It ran and failed: incorrect. Every `Failure`, whatever its kind (an
   'unsupported' one from a primitive included), and a `Limitation`.
2. It ran out of budget: undecided. Every `Budget` ('steps', 'depth', 'size',
   'time', or an exam's own): a grader may want to run it longer.
3. It could not be run: undecided. Source outside the language (`Unsupported`
   from `parse` in `check_source`, or from the interpreter while running).
An exam may pass `draw(undecided=...)`, a predicate over the error, instead.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from examinator import Judgement, Result
from examinator.std import pseudocode
from examinator.std.blocks import Code, Details, Note, Table

from . import fit as fits
from .fit import Fit


def undecided_by_default(error: Exception) -> bool:
    """Out of budget, or not runnable: undecided. Any other failure: incorrect."""
    return isinstance(error, (pseudocode.Budget, pseudocode.Unsupported))


class Stop(Exception):
    """Sampling ended early. `outcome` is 'incorrect' or None (undecided);
    `error` is the interpreter's exception, if one stopped it."""

    def __init__(self, outcome: str | None, note: str, error: Exception | None = None):
        super().__init__(note)
        self.outcome, self.note, self.error = outcome, note, error


def draw(program: pseudocode.Program, entry: str, args=(), *, n: int, deadline: float | None = None,
         seconds: float | None = None, seed: int = 0, primitives=None, constants=None,
         max_steps: int | None = 2_000_000, rng=None,
         undecided: Callable[[Exception], bool] = undecided_by_default) -> list:
    """`n` results of `entry(*args)`, or `Stop`. `deadline` is a `time.monotonic()`
    value; `seconds` sets one from now. `rng` is passed to `Program.sample`.
    `undecided(error)` says whether a stop is undecided (module docstring)."""
    if seconds is not None:
        deadline = time.monotonic() + seconds
    try:
        return program.sample(entry, args, n=n, seed=seed, deadline=deadline, max_steps=max_steps,
                              primitives=primitives, constants=constants, rng=rng)
    except (pseudocode.Failure, pseudocode.Limitation, pseudocode.Budget, pseudocode.Unsupported) as e:
        raise Stop(None if undecided(e) else 'incorrect', str(e), e) from None


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


def result(part: Part, source: str, ran: str | None = None) -> Result:
    """The part's outcome as the grade, and its verdict beside its field; plots
    first, then the tests, samples and the source collapsed."""
    blocks: list = list(part.plots)
    if part.tests:
        blocks.append(Details("Tests", Table(["test", '', ''], part.tests)))
    if part.dumps:
        blocks.append(Details("Samples", Table(['', "first samples"], part.dumps), audience='grader'))
    blocks.append(Details("Source", Code(source), audience='grader'))
    if ran and ran != source:
        blocks.append(Details("As run", Code(ran), audience='grader'))
    note = part.note or None
    return Result(part.outcome, {part.label: Judgement(part.outcome, note=note)}, blocks,
                  reason=(note or "undecided") if part.outcome is None else None)


def unparsed(label: str, source: str, error: pseudocode.Unsupported) -> Result:
    """The source is outside the language: undecided."""
    return Result(None, {label: Judgement(None, note=str(error))},
                  [Note(str(error), 'warn'),
                   Details("Source", Code(source, [error.line] if error.line else []), audience='grader')],
                  reason=str(error))


def check_source(source: str, language: pseudocode.Language | None, label: str,
                 judge: Callable[[pseudocode.Program], Part], *, ran: str | None = None) -> Result:
    """Parse `source` (`ran`, if given, is what is run instead: a rewrite that keeps
    line numbers), judge the program (`judge(program) -> Part`), and show the
    part. Source outside the language is undecided (shown beside field `label`)."""
    try:
        program = pseudocode.parse(source if ran is None else ran, language)
    except pseudocode.Unsupported as e:
        return unparsed(label, source, e)
    return result(judge(program), source, ran)
