"""The check for question 11: `standard_normal()`. Accept-reject is allowed, so
the time limit governs; a sampler too slow to finish is undecided.

The likeliest mistake is an envelope that is too low (accepting when
`unif() * exp(-x) < g(x)`), which flattens the peak only slightly: its CDF is
within 0.024 of the normal one, so the sample is large enough to see that."""
import math

from scipy import stats

from examinator.std import pseudocode, sampling

from . import samplers

LABEL = 'standard_normal'
N = 20_000
SECONDS = 30.0
MAX_STEPS = 20_000_000      # per sample: rejection loops are long


def g(rng, t):
    """The printed helper, for a parse that left it out."""
    return math.exp(-t * t / 2)


def check(ctx):
    source = ctx.input('parse')
    try:
        program, lines = samplers.prepare(source)
    except pseudocode.Unsupported as e:
        return samplers.unparsed(LABEL, source, e)
    if program.is_blank(LABEL):
        return samplers.evidence(LABEL, 'blank', "", source)
    try:
        xs = samplers.draw(program, lines, LABEL, n=N, seconds=SECONDS, max_steps=MAX_STEPS,
                           primitives={} if program.has('g') else {'g': g})
    except samplers.Stop as stop:
        return samplers.evidence(LABEL, stop.outcome, stop.note, source)
    fit = sampling.support(xs, title="standard_normal()")
    tests = [samplers.fit_row("support", fit)]
    if fit.outcome == 'correct':
        fit = sampling.ks(xs, stats.norm.cdf, title="standard_normal()")
        tests.append(samplers.fit_row("normal", fit))
    return samplers.evidence(LABEL, fit.outcome, "" if fit.outcome == 'correct' else fit.note, source,
                             fits=[fit], tests=tests, samples=xs)
