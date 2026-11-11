"""Shared pieces of the pseudocode checks: drawing samples from a student's
function, and assembling a verdict with its tests into evidence."""
from dataclasses import dataclass, field

import numpy as np

from examinator import Evidence
from examinator.std import pseudocode, sampling
from examinator.std.blocks import Code, Details, Note, Table

SEED = 4580
UNDECIDED_KINDS = ('undefined', 'unsupported')      # `Failure.kind`s that prove nothing


class Stop(Exception):
    """Sampling ended early. `outcome` is 'incorrect' or None (undecided)."""

    def __init__(self, outcome: str | None, note: str):
        super().__init__(note)
        self.outcome, self.note = outcome, note


def draw(program: pseudocode.Program, entry: str, args=(), *, n: int, deadline: float,
         seed: int = SEED, primitives=None, max_steps: int | None = 2_000_000) -> list:
    """`n` results of `entry(*args)`, or `Stop`.

    A budget that runs out, an unsupported construct and an unknown name are
    undecided; any other run-time failure is a demonstrated mistake.
    """
    try:
        return program.sample(entry, args, n=n, seed=seed, deadline=deadline, max_steps=max_steps,
                              primitives=primitives)
    except (pseudocode.Budget, pseudocode.Unsupported) as e:
        raise Stop(None, str(e)) from None
    except pseudocode.Failure as e:
        raise Stop(None if e.kind in UNDECIDED_KINDS else 'incorrect', str(e)) from None


def matrix(samples, d: int) -> np.ndarray:
    """Samples as an n x d array of finite numbers, or `Stop('incorrect')`."""
    for v in samples:
        ok = isinstance(v, (list, tuple)) and len(v) == d and all(
            isinstance(c, (int, float)) and not isinstance(c, bool) and np.isfinite(c) for c in v)
        if not ok:
            raise Stop('incorrect', f"result is not a point of {d} finite numbers: {str(v)[:40]}")
    return np.asarray(samples, dtype=float).reshape(len(samples), d)


def inside(mask: np.ndarray, points: np.ndarray, plot=None) -> sampling.Fit:
    """A support test from a boolean mask over the samples."""
    n, off = len(mask), int((~mask).sum())
    if off == 0:
        return sampling.Fit('correct', None, None, f"support ok (n = {n})", plot)
    example = ", ".join(f"{c:.4g}" for c in np.atleast_1d(points[~mask][0]))
    return sampling.Fit('incorrect', None, None, f"{off} of {n} outside the region, e.g. ({example})", plot)


def table_pmf(probs):
    """A PMF on 0 .. len(probs) - 1 from a table, for `sampling.chi_square`."""
    probs = np.asarray(probs, dtype=float)

    def pmf(k):
        k = np.asarray(k)
        return np.where((k >= 0) & (k < len(probs)), probs[np.clip(k, 0, len(probs) - 1).astype(int)], 0.0)
    return pmf


def preview(samples, limit: int = 8) -> str:
    def show(v):
        return f"{v:.4g}" if isinstance(v, float) else "[" + ", ".join(show(c) for c in v) + "]" \
            if isinstance(v, (list, tuple)) else str(v)[:40]
    return ", ".join(show(v) for v in samples[:limit]) + (f", ... ({len(samples)})" if len(samples) > limit else "")


@dataclass
class Part:
    """One part's verdict and what a grader sees for it."""
    label: str
    outcome: str | None = None
    note: str = ""
    plots: list = field(default_factory=list)
    tests: list = field(default_factory=list)       # rows: [test, outcome, note]
    dumps: list = field(default_factory=list)       # rows: [what, first samples]
    fits: list = field(default_factory=list)

    def add(self, name: str, fit: sampling.Fit, samples=None, *, plot: bool = True) -> sampling.Fit:
        self.fits.append(fit)
        self.tests.append([name, fit.outcome or '?', fit.note])
        if fit.plot is not None and (plot or fit.outcome == 'incorrect'):
            self.plots.append(fit.plot)
        if samples is not None:
            self.dumps.append([name, preview(samples)])
        return fit

    def stop(self, name: str, stop: Stop) -> 'Part':
        self.tests.append([name, stop.outcome or '?', stop.note])
        return self.settle(stop.outcome)

    def settle(self, outcome: str | None, note: str = "") -> 'Part':
        self.outcome = outcome
        failed = [f"{name}: {why}" for name, result, why in self.tests if result != 'correct']
        self.note = note or (failed[0] if failed and outcome != 'correct' else "")
        return self

    def combine(self) -> 'Part':
        """Incorrect if any test is; undecided if any could not decide; else correct."""
        return self.settle(sampling.combine(*self.fits))


def evidence(part: Part, source: str, ran: str | None = None) -> Evidence:
    """Plots first, then the verdict; tests, samples and the source collapsed."""
    data = {'fields': {part.label: {'outcome': part.outcome, 'note': part.note}}}
    blocks = [*part.plots, Table(['', '', ''], [[part.label, part.outcome or '?', part.note]])]
    if part.tests:
        blocks.append(Details("Tests", Table(["test", '', ''], part.tests)))
    if part.dumps:
        blocks.append(Details("Samples", Table(['', "first samples"], part.dumps), audience='grader'))
    blocks.append(Details("Source", Code(source), audience='grader'))
    if ran and ran != source:
        blocks.append(Details("As run", Code(ran), audience='grader'))
    return Evidence(data, blocks)


def unparsed(label: str, source: str, error: pseudocode.Unsupported) -> Evidence:
    """The source is outside the language: undecided."""
    data = {'fields': {label: {'outcome': None, 'note': str(error)}}}
    return Evidence(data, [Note(str(error), 'warn'),
                           Details("Source", Code(source, [error.line] if error.line else []),
                                   audience='grader')])
