"""The check for question 10: `snow_cone()` samples uniformly from a circular
sector laid over a triangle, without accept-reject.

Support first, then a projected KS against a trusted sampler. A `while` loop or
a function that calls itself cannot be told from accept-reject by a test, so a
sampler that passes with either is left for a person.
"""
import math
from dataclasses import dataclass

import numpy as np

from examinator.std import pseudocode, sampling
from examinator.std.blocks import Plot

from . import samplers

LABEL = 'snow_cone'
N = 4000
REFERENCE = 40_000
SECONDS = 20.0
EDGE = 1e-9                         # slack at the boundary
TRIANGLE = ((1.0, 3.0), (3.0, 3.0), (2.0, 0.0))


@dataclass(frozen=True)
class Region:
    """The triangle together with a sector that points up from `center`."""
    radius: float
    center: tuple[float, float]
    half_angle: float               # either side of straight up

    def inside(self, x, y):
        triangle = (y <= 3 + EDGE) & (y >= 3 * np.abs(x - 2) - EDGE)        # sides run from (2, 0) to (2 ± 1, 3)
        dx, dy = x - self.center[0], y - self.center[1]
        sector = (np.hypot(dx, dy) <= self.radius + EDGE) & \
                 (np.abs(dx) <= dy * math.tan(self.half_angle) + EDGE)
        return triangle | sector

    def reference(self, n: int, seed: int = 0) -> np.ndarray:
        """Trusted uniform points, by rejection from the bounding box."""
        rng = np.random.default_rng(seed)
        top = self.center[1] + self.radius
        out = np.empty((0, 2))
        while len(out) < n:
            xy = rng.uniform((1, 0), (3, top), size=(2 * n, 2))
            out = np.vstack([out, xy[self.inside(xy[:, 0], xy[:, 1])]])
        return out[:n]


REGIONS = {'original': Region(math.sqrt(2), (2.0, 2.0), math.pi / 4),
           'alternate': Region(2.0, (2.0, 3 - math.sqrt(3)), math.pi / 6)}


def points(samples) -> np.ndarray | None:
    """The samples as an n x 2 array, or None if some sample is not a pair of numbers."""
    ok = all(isinstance(p, (list, tuple)) and len(p) == 2 and
             all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p) for p in samples)
    return np.asarray(samples, dtype=float) if ok else None


def check(ctx):
    source = ctx.input('parse')
    region = REGIONS[ctx.variant]
    try:
        program, lines = samplers.prepare(source)
    except pseudocode.Unsupported as e:
        return samplers.unparsed(LABEL, source, e)
    if program.is_blank(LABEL):
        return samplers.evidence(LABEL, 'blank', "", source)
    try:
        samples = samplers.draw(program, lines, LABEL, n=N, seconds=SECONDS)
    except samplers.Stop as stop:
        return samplers.evidence(LABEL, stop.outcome, stop.note, source)
    xy = points(samples)
    if xy is None:
        return samplers.evidence(LABEL, 'incorrect', "a sample is not a point [x, y]", source, samples=samples)
    scatter = Plot.scatter(xy[:, 0], xy[:, 1], title="snow_cone()", xlabel="x", ylabel="y")
    off = ~(np.isfinite(xy).all(axis=1) & region.inside(xy[:, 0], xy[:, 1]))
    if off.any():
        x, y = xy[off][0]
        support = sampling.Fit('incorrect', None, None,
                               f"{int(off.sum())} of {len(xy)} outside the region, e.g. [{x:.4g}, {y:.4g}]", scatter)
        return samplers.evidence(LABEL, 'incorrect', support.note, source, fits=[support],
                                 tests=[samplers.fit_row("support", support)], samples=samples)
    support = sampling.Fit('correct', None, None, f"support ok (n = {len(xy)})", scatter)
    fit = sampling.projected_ks(xy, region.reference(REFERENCE), title="snow_cone() against a uniform reference")
    tests = [samplers.fit_row("support", support), samplers.fit_row("uniform", fit)]
    outcome, note = fit.outcome, "" if fit.outcome == 'correct' else fit.note
    if outcome == 'correct' and (samplers.loops(source) or program.references(LABEL, LABEL)):
        outcome, note = None, "passes, but loops: accept-reject?"
    return samplers.evidence(LABEL, outcome, note, source, fits=[support, fit], tests=tests, samples=samples)
