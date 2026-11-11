"""The check for question 11: `standard_normal()`. Accept-reject is allowed, so
the time limit governs; a sampler too slow to finish is undecided.

The likeliest mistake is an envelope that is too low (accepting when
`unif() * exp(-x) < g(x)`), which flattens the peak only slightly: its CDF is
within 0.024 of the normal one, so the sample is large enough to see that."""
import math

from scipy import stats

from examinator.std import pseudocode
from examinator_kits.stochastic import checks
from examinator_kits.stochastic import fit as sampling

from . import samplers

LABEL = 'standard_normal'
N = 20_000
SECONDS = 30.0
MAX_STEPS = 20_000_000      # per sample: rejection loops are long


def g(t):
    """The printed helper, for a parse that left it out."""
    return math.exp(-t * t / 2)


G = pseudocode.Primitive(g, "`g(t)`", numeric=True)


def judge(program) -> checks.Part:
    part = checks.Part(LABEL)
    if program.is_blank(LABEL):
        return part.settle('blank')
    try:
        xs = samplers.draw(program, LABEL, n=N, seconds=SECONDS, max_steps=MAX_STEPS,
                           primitives={} if program.has('g') else {'g': G})
    except checks.Stop as stop:
        return samplers.stopped(part, "standard_normal()", stop)
    fit = part.add("support", sampling.support(xs, title="standard_normal()"), xs)
    if fit.outcome == 'correct':
        fit = part.add("normal", sampling.ks(xs, stats.norm.cdf, title="standard_normal()"))
    return part.settle(fit.outcome, "" if fit.outcome == 'correct' else fit.note)


def check(ctx):
    return samplers.check(ctx.input('parse'), LABEL, judge)
