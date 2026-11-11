"""The check for question 5: uniform on the cone x^2 + y^2 < z^2, 0 < z < h,
without accept-reject. The printing sets h and the function's name."""
import math
import time

import numpy as np

from examinator.std import pseudocode, sampling

from . import parts, points

LABEL = '5'
ENTRIES = {'original': 'unit_cone', 'alternate': 'twonit_cone'}
HEIGHTS = {'original': 1.0, 'alternate': 2.0}
N = 4000
REFERENCE = 20_000
ATTEMPT = 20.0          # seconds of sampling
EPS = 1e-9


def reference(h: float, n: int, seed: int) -> np.ndarray:
    """Trusted points, uniform on the cone of height `h`."""
    rng = np.random.default_rng(seed)
    z = h * rng.random(n) ** (1 / 3)
    r, t = z * np.sqrt(rng.random(n)), 2 * math.pi * rng.random(n)
    return np.column_stack([r * np.cos(t), r * np.sin(t), z])


def uniform01(u):
    return np.clip(u, 0.0, 1.0)


def check_program(program: pseudocode.Program, runnable: pseudocode.Program, variant: str) -> parts.Part:
    part = parts.Part(LABEL)
    entry, h = ENTRIES[variant], HEIGHTS[variant]
    if program.is_blank(entry):
        other = next((e for e in ENTRIES.values() if e != entry and not program.is_blank(e)), None)
        return part.settle(None, f"defines {other}, not {entry}: check the printing") if other else part.settle('blank')
    draws = points.Draws()
    name = f"{entry}()"
    try:
        raw = parts.draw(runnable, entry, n=N, deadline=time.monotonic() + ATTEMPT, primitives=draws.primitives())
        p = parts.matrix(raw, 3)
    except parts.Stop as stop:
        return part.stop(name, stop)
    x, y, z = p.T
    rr = x * x + y * y
    ok = (z > -EPS) & (z < h + EPS) & (rr <= z * z + EPS)
    if part.add("cone", parts.inside(ok, p), raw).outcome != 'correct':
        return part.combine()
    part.add("height", sampling.ks(z, lambda v: uniform01(np.asarray(v) / h) ** 3, title=f"{name}: z"))
    with np.errstate(all='ignore'):
        part.add("radius at its height", sampling.ks(np.where(z > 0, rr / (z * z), 0.0), uniform01,
                                                     title=f"{name}: (x^2 + y^2) / z^2"))
    part.add("angle", sampling.ks(np.arctan2(y, x) / (2 * math.pi) + 0.5, uniform01,
                                  title=f"{name}: angle / 2 pi"), plot=False)
    part.add("joint", sampling.projected_ks(p, reference(h, REFERENCE, parts.SEED), seed=parts.SEED,
                                            title=f"{name} against the cone"), plot=False)
    return points.settle_direct(part, program, entry, draws)


def check(ctx):
    source = ctx.input('parse')
    try:
        program, runnable = points.prepare(source)
    except pseudocode.Unsupported as e:
        return parts.unparsed(LABEL, source, e)
    return parts.evidence(check_program(program, runnable, ctx.variant), source, runnable.source)
