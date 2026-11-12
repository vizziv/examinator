"""The checks for question 2: `weibull(a, b)` (2.1) and `weibullish()` (2.2), one per field.

Each reads only its own field: 2.2 runs with a trusted `weibull`, so it does not
depend on the student's 2.1."""
import math
import time

import numpy as np

from examinator.std import pseudocode
from examinator_kits.stochastic import checks

from . import huge_constants
from .language import SCALAR, draw

SETTINGS = ((0.5, 2), (1, 0.5), (2, 4))     # (a, b) for 2.1
N_WEIBULL = 2000
N_WEIBULLISH = 1200
ATTEMPT = 20.0          # seconds for 2.1, and for each attempt at 2.2
MAX_STEPS = 20_000_000  # per sample: rejection loops are long, so the time limit governs
TRIALS = (1, 10)        # values tried for huge_constant
MAX_CUSTOM = 1e6
PARAM = huge_constants.NAME     # a grader may rerun with one custom value


def weibull_cdf(a: float, b: float):
    def cdf(t):
        t = np.maximum(np.asarray(t, dtype=float), 0.0)
        return -np.expm1(-b * t ** a)
    return cdf


def weibullish_cdf(t):
    t = np.maximum(np.asarray(t, dtype=float), 1.0)
    return 1.0 - (4 * t ** 2 + 1) / 5 * np.exp(4 - 4 * t ** 2)


def reference_weibull(rng, a, b):
    """Trusted `weibull(a, b)`, replacing the student's own inside 2.2."""
    if not (a > 0 and b > 0):
        raise ValueError("weibull needs a > 0 and b > 0")
    return (rng.standard_exponential() / b) ** (1 / a)


def check_weibull(program: pseudocode.Program) -> checks.Part:
    part = checks.Part('2.1', shown=12)
    if program.is_blank('weibull'):
        return part.settle('blank')
    deadline = time.monotonic() + ATTEMPT
    for a, b in SETTINGS:
        name = f"weibull({a:g}, {b:g})"
        try:
            xs = draw(program, 'weibull', (a, b), n=N_WEIBULL, deadline=deadline)
        except checks.Stop as stop:
            return part.stop(name, stop)
        part.add(name, checks.fit_cdf(xs, weibull_cdf(a, b), lo=0, title=name), xs)
    return part.combine()


def custom_value(params) -> tuple[float | None, str]:
    """The grader's custom huge_constant, if any: `(value, problem)`."""
    raw = params.get(PARAM)
    if raw is None or raw == "":
        return None, ""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not 0 < value <= MAX_CUSTOM:
        return None, f"{PARAM} must be positive, at most {MAX_CUSTOM:g}"
    return value, ""


def check_weibullish(program: pseudocode.Program, params) -> checks.Part:
    part = checks.Part('2.2', shown=12)
    if program.is_blank('weibullish'):
        return part.settle('blank')
    custom, problem = custom_value(params)
    if problem:
        return part.settle(None, problem)
    idealized = program.references('weibullish', PARAM)
    values = (custom,) if custom is not None else TRIALS if idealized else (None,)
    outcome = None
    for value in values:
        name = "as written" if value is None else f"{PARAM} = {value:g}"
        try:
            xs = draw(program, 'weibullish', n=N_WEIBULLISH, deadline=time.monotonic() + ATTEMPT,
                            primitives={'weibull': reference_weibull}, max_steps=MAX_STEPS,
                            constants=None if value is None else {PARAM: value})
        except checks.Stop as stop:
            part.stop(name, stop)
            outcome = stop.outcome
            continue
        outcome = part.add(name, checks.fit_cdf(xs, weibullish_cdf, lo=1, title=f"weibullish(), {name}"), xs).outcome
        if outcome == 'correct':
            return part.settle('correct', name if value is not None else "")
    if idealized:
        # A finite substitute that fails proves nothing about the idealized algorithm.
        return part.settle(None, f"no pass with {PARAM} = {', '.join(f'{v:g}' for v in values)}")
    return part.settle(outcome)


def check_21(ctx):
    return checks.check_source(ctx.read('2.1'), SCALAR, '2.1', check_weibull)


def check_22(ctx):
    return checks.check_source(ctx.read('2.2'), SCALAR, '2.2', lambda program: check_weibullish(program, ctx.params))
