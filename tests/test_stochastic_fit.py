"""examinator_kits.stochastic.fit (formerly std.sampling): seeded synthetic samples only."""
import base64
import json

import numpy as np
import pytest
from scipy import stats

from examinator_kits.stochastic import fit as sampling
from examinator_kits.stochastic.fit import ALPHA, Fit, chi_square, combine, ks, projected_ks, support


def rng(seed=1):
    return np.random.default_rng(seed)


def exp_cdf(rate):
    return lambda x: 1 - np.exp(-rate * np.clip(x, 0, None))


def poisson_pmf(mu):
    return lambda k: stats.poisson.pmf(k, mu)


def check_blocks(fit, tone):
    blocks = [b.to_json() for b in fit.blocks()]
    json.loads(json.dumps(blocks, allow_nan=False))
    assert blocks[-1] == {'type': 'note', 'text': fit.note, 'tone': tone}
    for block in blocks[:-1]:
        assert block['type'] == 'image' and base64.b64decode(block['data']).startswith(b'\x89PNG')
    return blocks


# ---------------------------------------------------------------- ks

def test_ks_accepts_matching_cdf():
    fit = ks(rng().exponential(1.0, 5000), exp_cdf(1.0), title="T")
    assert fit.outcome == 'correct' and fit.p > ALPHA
    assert fit.note.startswith("KS p = ")
    blocks = check_blocks(fit, 'good')
    assert blocks[0]['type'] == 'image' and blocks[0]['title'] == "T"


def test_ks_rejects_wrong_cdf():
    fit = ks(rng().exponential(1.0, 5000).tolist(), exp_cdf(2.0))
    assert fit.outcome == 'incorrect' and fit.p < 1e-20 and fit.statistic > 0.1
    check_blocks(fit, 'bad')


def test_ks_is_deterministic():
    x = rng().exponential(1.0, 500)
    assert ks(x, exp_cdf(1.0)).p == ks(x, exp_cdf(1.0)).p


@pytest.mark.parametrize('samples, word', [
    ([0.1, 0.2, 0.3], "only 3"),
    ([], "only 0"),
    ([0.5] * 30 + [float('nan')], "non-finite"),
    ([0.5] * 30 + [float('inf')], "non-finite"),
    ([0.5] * 30 + [None], "non-numeric"),
    ([0.5] * 30 + ['0.5'], "non-numeric"),
    (None, "non-numeric"),
])
def test_ks_undecided(samples, word):
    fit = ks(samples, exp_cdf(1.0))
    assert fit.outcome is None and fit.p is None and word in fit.note
    check_blocks(fit, 'warn')


def test_ks_huge_values_do_not_raise():
    fit = ks([1e308, -1e308] * 20, exp_cdf(1.0))
    assert fit.outcome == 'incorrect'
    fit.blocks()


# ---------------------------------------------------------------- chi-square

def test_chi_square_accepts_matching_pmf():
    fit = chi_square(rng().poisson(3.0, 3000), poisson_pmf(3.0))
    assert fit.outcome == 'correct' and fit.p > ALPHA
    assert len(check_blocks(fit, 'good')) == 2


def test_chi_square_rejects_shifted_mean():
    fit = chi_square(rng().poisson(3.6, 3000).tolist(), poisson_pmf(3.0))
    assert fit.outcome == 'incorrect' and fit.p < 1e-10
    check_blocks(fit, 'bad')


def test_chi_square_statistic_matches_scipy():
    x = rng().poisson(3.0, 3000)
    fit = chi_square(x, poisson_pmf(3.0))
    _, observed, expected = sampling._cells(x.astype(float), poisson_pmf(3.0), 5.0)
    reference = stats.chisquare(observed, expected)
    assert fit.statistic == pytest.approx(reference.statistic)
    assert fit.p == pytest.approx(reference.pvalue)


@pytest.mark.parametrize('mu, n', [(3.0, 3000), (0.2, 200), (40.0, 500)])
def test_cells_merge_tails(mu, n):
    x = rng().poisson(mu, n).astype(float)
    starts, observed, expected = sampling._cells(x, poisson_pmf(mu), 5.0)
    assert len(starts) == len(observed) == len(expected) >= 2
    assert (expected >= 5.0).all()
    assert observed.sum() == n
    assert expected.sum() == pytest.approx(n)          # the open tail holds the rest
    assert starts[0] == 0 and (np.diff(starts) > 0).all()


def test_cells_sample_beyond_the_cells_lands_in_the_tail():
    x = np.append(rng().poisson(3.0, 500), 1e300)
    _, observed, _ = sampling._cells(x, poisson_pmf(3.0), 5.0)
    assert observed.sum() == 501
    assert chi_square(x, poisson_pmf(3.0)).outcome == 'correct'


def test_merge():
    assert sampling._merge([1, 2, 3, 10, 10, 4, 0.5], 5) == [0, 3, 4]
    assert sampling._merge([1, 1], 5) == [0]


@pytest.mark.parametrize('samples, word', [
    ([0.5] * 50, "non-integer"),
    ([1, 2, 3] * 10 + [2.5], "non-integer"),
    ([1, 2, 3] * 10 + [-1], "negative"),
    ([1, 2, 3] * 10 + [float('nan')], "non-finite"),
    ([1, 2, 3] * 10 + [[1]], "non-numeric"),
    ([1, 2, 3], "only 3"),
    ([], "only 0"),
])
def test_chi_square_undecided(samples, word):
    fit = chi_square(samples, poisson_pmf(3.0))
    assert fit.outcome is None and word in fit.note
    check_blocks(fit, 'warn')


def test_chi_square_too_few_cells():
    fit = chi_square([0] * 20, poisson_pmf(0.01))
    assert fit.outcome is None and "cells" in fit.note


def test_chi_square_accepts_bools_and_finite_support():
    x = rng().random(2000) < 0.3
    pmf = lambda k: np.where(k == 0, 0.7, np.where(k == 1, 0.3, 0.0))
    assert chi_square(x, pmf).outcome == 'correct'
    assert chi_square(x.tolist(), lambda k: np.where(k <= 1, 0.5, 0.0)).outcome == 'incorrect'


# ---------------------------------------------------------------- support

def test_support_ok():
    fit = support(rng().uniform(0, 1, 1000), lo=0, hi=1)
    assert fit.outcome == 'correct' and fit.p is None
    check_blocks(fit, 'good')
    assert support([0, 1, 2, 3.0, True], lo=0, integer=True).outcome == 'correct'
    assert support(np.arange(5), lo=0, hi=4, integer=True).outcome == 'correct'


@pytest.mark.parametrize('samples, kwargs, note', [
    ([0.5, 1.5, 2.5], {'hi': 2}, "1 of 3 outside support, e.g. 2.5"),
    ([-1, 0.5, -2], {'lo': 0}, "2 of 3 outside support, e.g. -1"),
    ([1, 2.5, 3], {'integer': True}, "1 of 3 outside support, e.g. 2.5"),
    ([1.0, float('nan')], {}, "1 of 2 outside support, e.g. nan"),
    ([1.0, float('inf')], {'integer': True}, "1 of 2 outside support, e.g. inf"),
    ([1.0, None, 'x'], {}, "2 of 3 outside support, e.g. None"),
    ([[1, 2], [3, 4]], {}, "2 of 2 outside support, e.g. [1, 2]"),
    (None, {}, "1 of 1 outside support, e.g. None"),
    ("abc", {}, "1 of 1 outside support, e.g. 'abc'"),
    ([1 + 2j], {}, "1 of 1 outside support, e.g. (1+2j)"),
    ([10 ** 400], {'hi': 5}, "1 of 1 outside support, e.g. inf"),
])
def test_support_incorrect(samples, kwargs, note):
    fit = support(samples, **kwargs)
    assert fit.outcome == 'incorrect' and fit.note == note
    check_blocks(fit, 'bad')


def test_support_empty_is_undecided():
    assert support([]).outcome is None


def test_support_huge_values_do_not_raise():
    assert support([1e308, -1e308, 0.0]).outcome == 'correct'
    assert support(np.array([[1.0, 2.0]])).outcome == 'incorrect'


# ---------------------------------------------------------------- projected KS

def normals(seed, n=4000, rho=0.0):
    return rng(seed).multivariate_normal([0, 0], [[1, rho], [rho, 1]], n)


def test_projected_ks_accepts_same_law():
    fit = projected_ks(normals(1), normals(2), title="T")
    assert fit.outcome == 'correct' and fit.p > ALPHA / 18
    assert len(check_blocks(fit, 'good')) == 2
    assert "18 directions" in fit.note


def test_projected_ks_rejects_wrong_joint_law_with_correct_marginals():
    correlated, independent = normals(1, rho=0.9), normals(2)
    for axis in range(2):   # the marginals alone look fine
        assert stats.ks_2samp(correlated[:, axis], independent[:, axis]).pvalue > 1e-3
    fit = projected_ks(correlated.tolist(), independent)
    assert fit.outcome == 'incorrect' and fit.p < 1e-20
    assert "random direction" in fit.note
    check_blocks(fit, 'bad')


def test_projected_ks_seeded():
    a, b = normals(1, 500), normals(2, 500)
    first, again = projected_ks(a, b, seed=3), projected_ks(a, b, seed=3)
    assert first == again and first.plot.to_json() == again.plot.to_json()
    assert projected_ks(a, b, seed=3).note != projected_ks(a, b, seed=4, projections=5).note


def test_projected_ks_one_dimension():
    assert projected_ks(rng(1).normal(size=500), rng(2).normal(size=500)).outcome == 'correct'
    assert projected_ks(rng(1).normal(size=500), rng(2).normal(3, 1, size=500)).outcome == 'incorrect'


@pytest.mark.parametrize('samples', [
    [[1, 2], [3]] * 20,                    # ragged
    [[1.0, 2.0, 3.0]] * 40,                # wrong dimension
    [1.0] * 40,                            # scalars where pairs are expected
    [[1.0, float('nan')]] * 40,
    [[1.0, 2.0]] * 3,                      # too few
    [['a', 'b']] * 40,
    [[None, 1.0]] * 40,
    [],
    None,
    np.zeros((4, 5, 2)),
    [[1.7e308, 1.7e308], [-1.7e308, 1.7e308]] * 20,   # projections overflow
])
def test_projected_ks_undecided(samples):
    fit = projected_ks(samples, normals(2, 200))
    assert fit.outcome is None and fit.note and fit.plot is None
    check_blocks(fit, 'warn')


def test_projected_ks_huge_values_do_not_raise():
    fit = projected_ks([[1e308, 1e308], [-1e308, 1e308]] * 20, normals(2, 200))
    assert fit.outcome == 'incorrect'
    check_blocks(fit, 'bad')


def test_projected_ks_bad_reference_is_undecided():
    assert projected_ks(normals(1, 200), [[1, 2], [3]]).outcome is None


# ---------------------------------------------------------------- combine, Fit

@pytest.mark.parametrize('outcomes, expected', [
    (['correct'], 'correct'),
    (['correct', 'correct'], 'correct'),
    (['correct', None], None),
    ([None, None], None),
    (['correct', 'incorrect'], 'incorrect'),
    ([None, 'incorrect'], 'incorrect'),
    (['incorrect', 'incorrect', 'correct', None], 'incorrect'),
    ([], None),
])
def test_combine(outcomes, expected):
    assert combine(*outcomes) == expected
    assert combine(*[Fit(o, None, None, "", None) for o in outcomes]) == expected


def test_combine_mixes_fits_and_outcomes():
    assert combine(Fit('correct', 0.5, 0.1, "", None), 'incorrect') == 'incorrect'
    assert combine(support([1, 2], lo=0), 'correct') == 'correct'


def test_blocks_without_plot():
    fit = Fit(None, None, None, "only 3 samples", None)
    assert [b.to_json() for b in fit.blocks()] == [{'type': 'note', 'text': "only 3 samples", 'tone': 'warn'}]


def test_p_formatting():
    assert sampling._p(0.25) == "0.25"
    assert sampling._p(3e-9) == "3e-9"
    assert sampling._p(0.0) == "0"
