"""The check for question 1: `exponential(m)`, where the printing says whether
`m` is the mean or the rate."""
import time

import numpy as np

from examinator.std import pseudocode
from examinator_kits.stochastic import checks
from examinator_kits.stochastic import fit as sampling

from .language import SCALAR, draw
from .versions import PRINTING

LABEL = '1'
ENTRY = 'exponential'
MEANING = {'original': 'mean', 'alternate': 'rate'}
SETTINGS = (0.5, 2, 5)      # m; none is 1, where mean and rate coincide
N = 2000
ATTEMPT = 15.0              # seconds of sampling


def cdf(meaning: str, m: float):
    rate = 1 / m if meaning == 'mean' else m

    def F(x):
        return -np.expm1(-rate * np.maximum(np.asarray(x, dtype=float), 0.0))
    return F


def check_program(program: pseudocode.Program, meaning: str) -> checks.Part:
    part = checks.Part(LABEL)
    if program.is_blank(ENTRY):
        return part.settle('blank')
    deadline = time.monotonic() + ATTEMPT
    for m in SETTINGS:
        name = f"exponential({m:g})"
        try:
            xs = draw(program, ENTRY, (m,), n=N, deadline=deadline)
        except checks.Stop as stop:
            return part.stop(name, stop)
        fit = sampling.support(xs, lo=0, title=name)
        if fit.outcome == 'correct':
            fit = sampling.ks(xs, cdf(meaning, m), title=f"{name}, {meaning} m")
        part.add(name, fit, xs)
    return part.combine()


def check(ctx):
    meaning = MEANING[PRINTING['q1'](ctx)]       # undecided until the printing agrees
    return checks.check_source(ctx.read(LABEL), SCALAR, LABEL, lambda program: check_program(program, meaning))
