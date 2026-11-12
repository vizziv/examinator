"""The check for question 4: `ice_cream()`, uniform on a printed region made of
discs, an upper half disc and a triangle, without accept-reject."""
import math
import time
from dataclasses import dataclass

import numpy as np

from examinator.std import pseudocode
from examinator.std.blocks import Plot
from examinator_kits.stochastic import checks
from examinator_kits.stochastic import fit as sampling

from .language import POINTS, SEED, Draws, draw, settle_direct
from .versions import PRINTING

LABEL = '4'
ENTRY = 'ice_cream'
N = 4000
REFERENCE = 20_000
ATTEMPT = 20.0          # seconds of sampling
EPS = 1e-9
MIN_PIECE = 100         # samples in a piece before its own uniformity test


@dataclass(frozen=True)
class Region:
    """A disjoint union of pieces, as read off the printed figure."""
    discs: tuple            # (cx, cy, r)
    halves: tuple           # upper half discs: (cx, cy, r)
    triangles: tuple        # ((x, y), (x, y), (x, y))

    @property
    def names(self) -> list[str]:
        return ([f"disc at ({x:g}, {y:g})" for x, y, _ in self.discs]
                + [f"half disc at ({x:g}, {y:g})" for x, y, _ in self.halves]
                + ["triangle"] * len(self.triangles))

    @property
    def areas(self) -> np.ndarray:
        return np.array([math.pi * r * r for _, _, r in self.discs]
                        + [math.pi * r * r / 2 for _, _, r in self.halves]
                        + [abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
                           for a, b, c in self.triangles])

    def piece(self, p: np.ndarray) -> np.ndarray:
        """Index of the piece holding each point, or -1."""
        x, y = p[:, 0], p[:, 1]
        masks = [(x - cx) ** 2 + (y - cy) ** 2 <= r * r + EPS for cx, cy, r in self.discs]
        masks += [((x - cx) ** 2 + (y - cy) ** 2 <= r * r + EPS) & (y >= cy - EPS) for cx, cy, r in self.halves]
        for a, b, c in self.triangles:
            sides = [(q[0] - o[0]) * (y - o[1]) - (q[1] - o[1]) * (x - o[0]) for o, q in ((a, b), (b, c), (c, a))]
            masks.append(np.all([s >= -EPS for s in sides], axis=0) | np.all([s <= EPS for s in sides], axis=0))
        out = np.full(len(p), -1)
        for i in reversed(range(len(masks))):
            out[masks[i]] = i
        return out

    def sample(self, n: int, seed: int) -> np.ndarray:
        """Trusted reference points, uniform on the region."""
        rng = np.random.default_rng(seed)
        areas = self.areas
        which = rng.choice(len(areas), size=n, p=areas / areas.sum())
        r, t, u = np.sqrt(rng.random(n)), 2 * math.pi * rng.random(n), rng.random(n)
        out = np.empty((n, 2))
        i = 0
        for cx, cy, rad in (*self.discs, *self.halves):
            m = which == i
            dy = rad * r[m] * np.sin(t[m])
            out[m] = np.column_stack([cx + rad * r[m] * np.cos(t[m]),
                                      cy + (np.abs(dy) if i >= len(self.discs) else dy)])
            i += 1
        for a, b, c in self.triangles:
            m = which == i
            a, b, c = (np.array(v, dtype=float) for v in (a, b, c))
            out[m] = a + r[m, None] * ((1 - u[m, None]) * (b - a) + u[m, None] * (c - a))
            i += 1
        return out


REGIONS = {
    'original': Region(discs=((3, 4, 1),), halves=((3, 2, 1),), triangles=(((2, 2), (4, 2), (3, -1)),)),
    'alternate': Region(discs=((2, 2, 1), (2, 0, 1)), halves=((2, -2, 1),),
                        triangles=(((1, -2), (3, -2), (2, -4)),)),
}


def check_program(program: pseudocode.Program, region: Region) -> checks.Part:
    part = checks.Part(LABEL)
    if program.is_blank(ENTRY):
        return part.settle('blank')
    draws = Draws()
    try:
        raw = draw(program, ENTRY, n=N, deadline=time.monotonic() + ATTEMPT, rng=draws)
        p = checks.matrix(raw, 2)
    except checks.Stop as stop:
        return part.stop("ice_cream()", stop)
    piece = region.piece(p)
    scatter = Plot.scatter(p[:, 0], p[:, 1], title="ice_cream()", xlabel="x", ylabel="y")
    if part.add("region", checks.inside(piece >= 0, p, scatter), raw).outcome != 'correct':
        return part.combine()
    share = region.areas / region.areas.sum()
    part.add("pieces by area", sampling.chi_square(piece, checks.table_pmf(share), title="piece chosen"), plot=False)
    reference = region.sample(REFERENCE, SEED)
    part.add("uniform", sampling.projected_ks(p, reference, seed=SEED, title="ice_cream() against the region"),
             plot=False)
    ref_piece = region.piece(reference)
    for i, name in enumerate(region.names):
        if (piece == i).sum() >= MIN_PIECE:
            part.add(f"uniform on the {name}", sampling.projected_ks(
                p[piece == i], reference[ref_piece == i], seed=SEED, title=name), plot=False)
    return settle_direct(part, program, ENTRY, draws)


def check(ctx):
    region = REGIONS[PRINTING['q4'](ctx)]
    return checks.check_source(ctx.read(LABEL), POINTS, LABEL, lambda program: check_program(program, region))
