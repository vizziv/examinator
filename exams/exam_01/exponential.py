"""The check for question 1: `exponential(m)`, where the printing says whether
`m` is the mean or the rate."""
import time

import numpy as np

from examinator.std import pseudocode, sampling

from . import parts

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


def check_program(program: pseudocode.Program, meaning: str) -> parts.Part:
    part = parts.Part(LABEL)
    if program.is_blank(ENTRY):
        return part.settle('blank')
    deadline = time.monotonic() + ATTEMPT
    for m in SETTINGS:
        name = f"exponential({m:g})"
        try:
            xs = parts.draw(program, ENTRY, (m,), n=N, deadline=deadline)
        except parts.Stop as stop:
            return part.stop(name, stop)
        fit = sampling.support(xs, lo=0, title=name)
        if fit.outcome == 'correct':
            fit = sampling.ks(xs, cdf(meaning, m), title=f"{name}, {meaning} m")
        part.add(name, fit, xs)
    return part.combine()


def check(ctx):
    source = ctx.input('parse')
    try:
        program = pseudocode.parse(source)
    except pseudocode.Unsupported as e:
        return parts.unparsed(LABEL, source, e)
    return parts.evidence(check_program(program, MEANING[ctx.variant]), source)
