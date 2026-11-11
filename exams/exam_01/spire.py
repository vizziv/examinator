"""The check for question 6: `spire()` samples from PDF(f) for the printed

    f(x) = (a + sin(w x)) / x^p   on 0 < x < hi,

which is not normalized and oscillates quickly. A KS test barely sees the
oscillation, so the position within a period is tested as well.
"""
import math
import time
from dataclasses import dataclass
from functools import cached_property

import numpy as np

from examinator.std import pseudocode
from examinator_kits.stochastic import checks
from examinator_kits.stochastic import fit as sampling

from .language import COLLECTIONS, draw

LABEL = '6'
ENTRY = 'spire'
N = 4000
ATTEMPT = 25.0          # seconds of sampling
MAX_STEPS = 20_000_000  # per sample: rejection loops may be long, so the time limit governs
GRID = 400_000
PHASES = 8


@dataclass(frozen=True)
class Density:
    a: float
    w: float
    p: float
    hi: float

    def __call__(self, x: float) -> float:
        return (self.a + math.sin(self.w * x)) / x ** self.p if 0 < x < self.hi else 0.0

    @cached_property
    def _table(self):
        """With u = x^(1-p) the integrand is smooth: midpoints, their mass, and the running integral."""
        q = 1 - self.p
        edges = np.linspace(0.0, self.hi ** q, GRID + 1)
        mid = (edges[:-1] + edges[1:]) / 2
        x = mid ** (1 / q)
        running = np.cumsum(self.a + np.sin(self.w * x))
        return edges, x, np.diff(running, prepend=0.0) / running[-1], np.concatenate([[0.0], running / running[-1]])

    def cdf(self, x):
        edges, _, _, total = self._table
        u = np.clip(np.asarray(x, dtype=float), 0.0, self.hi) ** (1 - self.p)
        return np.interp(u, edges, total)

    def phase(self, x) -> np.ndarray:
        """Which of `PHASES` equal slices of the sine's period each x falls in."""
        turn = np.mod(self.w * np.asarray(x, dtype=float), 2 * math.pi) / (2 * math.pi)
        return np.minimum((turn * PHASES).astype(int), PHASES - 1)

    @cached_property
    def phase_pmf(self) -> np.ndarray:
        _, x, mass, _ = self._table
        return np.bincount(self.phase(x), weights=mass, minlength=PHASES)


DENSITIES = {'original': Density(a=3, w=40, p=1 / 5, hi=2), 'alternate': Density(a=5, w=20, p=1 / 3, hi=4)}


def given_f(density: Density):
    """The printed `f(x)`, trusted: it replaces whatever copy the parse carries."""
    def f(x):
        return density(float(x))
    return pseudocode.Primitive(f, "`f(x)`")


def check_program(program: pseudocode.Program, density: Density) -> checks.Part:
    part = checks.Part(LABEL)
    if program.is_blank(ENTRY):
        return part.settle('blank')
    try:
        xs = draw(program, ENTRY, n=N, deadline=time.monotonic() + ATTEMPT, max_steps=MAX_STEPS,
                        primitives={'f': given_f(density)})
    except checks.Stop as stop:
        return part.stop("spire()", stop)
    if part.add("support", sampling.support(xs, lo=0, hi=density.hi, title="spire()"), xs).outcome != 'correct':
        return part.combine()
    part.add("CDF", sampling.ks(xs, density.cdf, title="spire()"))
    part.add("position within a period", sampling.chi_square(
        density.phase(xs), checks.table_pmf(density.phase_pmf), title="spire(): slice of the sine's period"))
    return part.combine()


def check(ctx):
    density = DENSITIES[ctx.variant]
    return checks.check_source(ctx.input('parse'), COLLECTIONS, LABEL, lambda program: check_program(program, density))
