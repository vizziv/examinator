"""The check for question 7: `poisson_process()` (7.1) and `poisson_dist(m)` (7.2)."""
import math
import time
from itertools import combinations

import numpy as np
from scipy import stats

from examinator.std import pseudocode
from examinator.std.blocks import Plot
from examinator_kits.stochastic import checks
from examinator_kits.stochastic import fit as sampling

from .language import COLLECTIONS as LANGUAGE, draw

MEANS = (0.5, 3, 10)            # m for 7.2
N_DIST = 2000
REALIZATIONS = 512              # per seed, for 7.1
SEEDS = (1, 2, 3)
ATTEMPT = 15.0                  # seconds of sampling per part

LO, HI = -5.0, 5.0
TOTAL = 25.0                                        # integral of |t| over [-5, 5]
EDGES = (LO, -math.sqrt(12.5), 0.0, math.sqrt(12.5), HI)    # four intervals of equal intensity
BINS = (5, 8)                   # interval counts are binned as <= 4, 5-7, >= 8 for independence
GROUPS = (("low", 0, 22), ("middle", 23, 27), ("high", 28, None))   # total count, for the conditional law


def location_cdf(t):
    t = np.clip(np.asarray(t, dtype=float), LO, HI)
    return (TOTAL + np.sign(t) * t ** 2) / (2 * TOTAL)


def location_density(t):
    return np.abs(t) / TOTAL


def trusted_poisson(rng, m):
    """The `poisson_dist(m)` the exam permits in 7.1, independent of the student's 7.2."""
    if not 0 <= m < math.inf:
        raise ValueError("poisson_dist needs a finite mean >= 0")
    return int(rng.poisson(m))


def check_dist(program: pseudocode.Program) -> checks.Part:
    part = checks.Part('7.2', shown=12)
    if program.is_blank('poisson_dist'):
        return part.settle('blank')
    deadline = time.monotonic() + ATTEMPT
    for m in MEANS:
        name = f"poisson_dist({m:g})"
        try:
            xs = draw(program, 'poisson_dist', (m,), n=N_DIST, deadline=deadline)
        except checks.Stop as stop:
            return part.stop(name, stop)
        part.add(name, checks.fit_pmf(xs, stats.poisson(m).pmf, title=name), xs)
    return part.combine()


def invalid(realizations: list) -> str:
    """Why the output is not a set of distinct points in [-5, 5]; "" if it is."""
    odd = [r for r in realizations if not isinstance(r, (list, tuple))]
    if odd:
        return f"{len(odd)} of {len(realizations)} results are not collections, e.g. {checks.preview(odd, 1)}"
    events = [x for r in realizations for x in r]
    bad = [x for x in events if isinstance(x, bool) or not isinstance(x, (int, float))]
    if bad:
        return f"{len(bad)} events are not numbers, e.g. {checks.preview(bad, 1)}"
    repeats = sum(len(set(r)) < len(r) for r in realizations)
    if repeats:
        return f"repeated events in {repeats} of {len(realizations)} realizations"
    return ""


def independence(a: np.ndarray, b: np.ndarray, title: str) -> sampling.Fit:
    """Chi-square test of independence between two interval counts, binned in three."""
    table = np.zeros((3, 3))
    np.add.at(table, (np.searchsorted(BINS, a, side='right'), np.searchsorted(BINS, b, side='right')), 1)
    table = table[table.sum(axis=1) > 0][:, table.sum(axis=0) > 0]
    if min(table.shape) < 2:
        return sampling.Fit(None, None, None, "too few cells", None)
    result = stats.chi2_contingency(table, correction=False)
    stat, p = float(result.statistic), float(result.pvalue)
    if not math.isfinite(p):
        return sampling.Fit(None, None, None, "chi-square did not run", None)
    failed = p < sampling.ALPHA
    plot = Plot.scatter(a, b, title=title, xlabel="count in first interval",
                        ylabel="count in second interval") if failed else None
    return sampling.Fit('incorrect' if failed else 'correct', p, stat, f"independence p = {p:.2g}", plot)


def interval_name(i: int) -> str:
    return f"[{EDGES[i]:.3g}, {EDGES[i + 1]:.3g}]"


def check_process(program: pseudocode.Program) -> checks.Part:
    part = checks.Part('7.1', shown=12)
    if program.is_blank('poisson_process'):
        return part.settle('blank')
    deadline = time.monotonic() + ATTEMPT
    realizations = []
    for seed in SEEDS:
        try:
            realizations += draw(program, 'poisson_process', n=REALIZATIONS, seed=seed,
                                       deadline=deadline, primitives={'poisson_dist': trusted_poisson})
        except checks.Stop as stop:
            return part.stop("poisson_process()", stop)
    part.dumps.append(["poisson_process()", checks.preview(realizations, 3)])
    problem = invalid(realizations)
    if problem:
        part.tests.append(["events", 'incorrect', problem])
        return part.settle('incorrect')

    counts = np.array([len(r) for r in realizations])
    events = [np.asarray(r, dtype=float) for r in realizations]
    pooled = np.concatenate(events) if events else np.zeros(0)
    total = stats.poisson(TOTAL)

    # Plots first: CDF overlays for count and location, then histograms of both.
    part.plots.append(Plot.cdf(counts, total.cdf, title="number of events", xlabel="events"))
    in_range = part.add("events in [-5, 5]", sampling.support(pooled, lo=LO, hi=HI), plot=False)
    part.add("location", sampling.ks(pooled, location_cdf, title="event locations"))
    part.add("number of events", sampling.chi_square(counts, total.pmf, title="number of events"))
    if len(pooled):
        part.plots.append(Plot.hist(pooled, bins=40, density=location_density, title="event locations",
                                    xlabel="t"))
    if in_range.outcome != 'correct':
        return part.combine()

    # Counts in four intervals of equal intensity: each Poisson(25/4), and independent.
    cell = stats.poisson(TOTAL / 4)
    inside = np.array([np.bincount(np.searchsorted(EDGES[1:-1], e, side='right'), minlength=4) for e in events])
    for i in range(4):
        part.add(f"count in {interval_name(i)}",
                 sampling.chi_square(inside[:, i], cell.pmf, title=f"events in {interval_name(i)}"), plot=False)
    for i, j in combinations(range(4), 2):
        name = f"counts in {interval_name(i)} and {interval_name(j)}"
        part.add(name, independence(inside[:, i], inside[:, j], name))

    # The location law must not depend on how many events there are.
    for name, lo, hi in GROUPS:
        chosen = [e for e, n in zip(events, counts) if n >= lo and (hi is None or n <= hi)]
        some = np.concatenate(chosen) if chosen else np.zeros(0)
        title = f"location, {name} count"
        part.add(title, sampling.ks(some, location_cdf, title=title), plot=False)
    return part.combine()


def check(ctx):
    return checks.check_source(ctx.input('parse'), LANGUAGE, ['7.1', '7.2'],
                               lambda program: [check_process(program), check_dist(program)])
