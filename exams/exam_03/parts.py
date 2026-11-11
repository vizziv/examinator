"""Shared pieces of the two pseudocode checks: drawing samples from a student's
function, and assembling per-part outcomes into evidence."""
from dataclasses import dataclass, field

import numpy as np

from examinator import Evidence
from examinator.std import pseudocode, sampling
from examinator.std.blocks import Code, Details, Note, Table

SEED = 4580


class Stop(Exception):
    """Sampling ended early. `outcome` is 'incorrect' or None (undecided)."""

    def __init__(self, outcome: str | None, note: str):
        super().__init__(note)
        self.outcome, self.note = outcome, note


def draw(program: pseudocode.Program, entry: str, args=(), *, n: int, deadline: float,
         seed: int = SEED, primitives=None, constants=None,
         max_steps: int | None = 2_000_000) -> list:
    """`n` results of `entry(*args)`, or `Stop`.

    A budget that runs out, an unsupported construct and an unknown name are
    undecided; any other run-time failure is a demonstrated mistake.
    """
    try:
        return program.sample(entry, args, n=n, seed=seed, deadline=deadline, max_steps=max_steps,
                              primitives=primitives, constants=constants)
    except (pseudocode.Budget, pseudocode.Unsupported) as e:
        raise Stop(None, str(e)) from None
    except pseudocode.Failure as e:
        raise Stop(None if e.kind == 'undefined' else 'incorrect', str(e)) from None


def fit_cdf(samples, cdf, *, lo=None, hi=None, title: str = "") -> sampling.Fit:
    """Support first; then KS against the CDF."""
    fit = sampling.support(samples, lo=lo, hi=hi, title=title)
    return fit if fit.outcome != 'correct' else sampling.ks(samples, cdf, title=title)


def fit_pmf(samples, pmf, *, title: str = "") -> sampling.Fit:
    """Nonnegative integers first; then chi-square against the PMF."""
    fit = sampling.support(samples, lo=0, integer=True, title=title)
    return fit if fit.outcome != 'correct' else sampling.chi_square(samples, pmf, title=title)


def preview(samples, limit: int = 12) -> str:
    shown = ", ".join(f"{v:.4g}" if isinstance(v, float) else str(v)[:60] for v in samples[:limit])
    return shown + (f", ... ({len(samples)})" if len(samples) > limit else "")


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

    def stop(self, name: str, stop: Stop) -> None:
        self.tests.append([name, stop.outcome or '?', stop.note])

    def settle(self, outcome: str | None, note: str = "") -> 'Part':
        self.outcome = outcome
        failed = [f"{name}: {why}" for name, result, why in self.tests if result != 'correct']
        self.note = note or (failed[0] if failed and outcome != 'correct' else "")
        return self

    def combine(self) -> 'Part':
        """Incorrect if any test is; undecided if any could not decide; else correct."""
        return self.settle(sampling.combine(*self.fits))


def evidence(parts: list[Part], source: str) -> Evidence:
    """Plots first, then one line per part; tests, samples and the source collapsed."""
    data = {'fields': {p.label: {'outcome': p.outcome, 'note': p.note} for p in parts}}
    blocks = [plot for p in parts for plot in p.plots]
    blocks.append(Table(['', '', ''], [[p.label, p.outcome or '?', p.note] for p in parts]))
    for p in parts:
        if p.tests:
            blocks.append(Details(f"{p.label} tests", Table(["test", '', ''], p.tests)))
    dumps = [row for p in parts for row in p.dumps]
    if dumps:
        blocks.append(Details("Samples", Table(['', "first samples"], dumps), audience='grader'))
    blocks.append(Details("Source", Code(source), audience='grader'))
    return Evidence(data, blocks)


def unparsed(labels: list[str], source: str, error: pseudocode.Unsupported) -> Evidence:
    """The source is outside the language: every part is undecided."""
    data = {'fields': {label: {'outcome': None, 'note': str(error)} for label in labels}}
    return Evidence(data, [Note(str(error), 'warn'),
                           Details("Source", Code(source, [error.line] if error.line else []),
                                   audience='grader')])


def numeric(values) -> np.ndarray:
    return np.asarray(values, dtype=float)
