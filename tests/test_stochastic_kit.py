"""examinator_kits.stochastic: random primitives and sampler-check scaffolding. Synthetic programs only."""
import dataclasses
import re
from pathlib import Path

import numpy as np
import pytest

from examinator.core.evidence import body_of
from examinator.std import pseudocode
from examinator_kits.stochastic import checks, primitives
from examinator_kits.stochastic.fit import Fit

ROOT = Path(__file__).resolve().parents[1]
RANDOM = {**primitives.UNIF, 'coin': primitives.COIN, 'die': primitives.die()}
LANGUAGE = pseudocode.WITH_COLLECTIONS.with_builtins(**RANDOM)
VECTORS = dataclasses.replace(pseudocode.WITH_COLLECTIONS, vectors=True).with_builtins(**RANDOM)


def sample(source, n=1, language=LANGUAGE, **kw):
    return pseudocode.parse(source, language).sample('f', n=n, seed=3, **kw)


def test_primitives():
    us = sample("def f():\n    return [unif(), unif(2, 5), coin(0.3), die(4)]\n", n=3000)
    a, b, c, d = (np.array(col, dtype=float) for col in zip(*us))
    assert 0 <= a.min() and a.max() < 1 and 2 <= b.min() and b.max() < 5
    assert abs(c.mean() - 0.3) < 0.04 and set(d) == {1, 2, 3, 4}
    zero = pseudocode.BASE.with_builtins(die=primitives.die(start=0))
    assert set(sample("def f():\n    return die(3)\n", n=300, language=zero)) == {0, 1, 2}
    assert "from 0 to n - 1" in zero.description and "from 1 to n" in LANGUAGE.description
    assert "from 5 to n + 4" in primitives.die(5).doc
    assert "- Randomness: `unif()`" in LANGUAGE.description and "`coin(p)`" in LANGUAGE.description
    for call, kind in (("coin(2)", 'domain'), ("die(0)", 'domain'), ("die(1.5)", 'domain'),
                       ("unif(1)", 'arity'), ("unif(None, 1)", 'type')):
        with pytest.raises(pseudocode.Failure) as e:
            sample(f"def f():\n    return {call}\n")
        assert e.value.kind == kind and e.value.line == 2, call
    assert "probability 2 not in [0, 1]" in str(pytest.raises(pseudocode.Failure, sample,
                                                                "def f():\n    return coin(2)\n").value)
    with pytest.raises(TypeError):
        primitives.die(1.0)


def test_primitives_draw_only_through_random():
    calls = []

    class Gen:
        def random(self):
            calls.append(1)
            return 0.5
    assert sample("def f():\n    return [unif(), coin(0.6), die(4)]\n", rng=lambda seed, i: Gen()) == \
        [[0.5, True, 3]]
    assert len(calls) == 3


def test_draw_policy():
    prog = pseudocode.parse("def f(x):\n    return g(x)\n", LANGUAGE)
    with pytest.raises(checks.Stop) as stop:
        checks.draw(prog, 'f', (1,), n=1, seconds=5)
    assert stop.value.outcome is None and "'g' is not defined" in stop.value.note
    assert isinstance(stop.value.error, pseudocode.Failure)
    with pytest.raises(checks.Stop) as stop:
        checks.draw(prog, 'f', (1,), n=1, seconds=5, undecided=())
    assert stop.value.outcome == 'incorrect'
    with pytest.raises(checks.Stop) as stop:
        checks.draw(pseudocode.parse("def f():\n    while True:\n        pass\n", LANGUAGE), 'f', n=1, max_steps=1000)
    assert stop.value.outcome is None and isinstance(stop.value.error, pseudocode.Budget)
    unif = pseudocode.parse("def f():\n    return unif()\n", LANGUAGE)
    assert checks.draw(unif, 'f', n=4, seed=7) == unif.sample('f', n=4, seed=7)     # seeded as `sample` is


def test_vector_arithmetic_and_accept_reject():
    for body, expected in (("[1, 2] + [1, 2, 3]", True), ("[1, 2] + 1", True), ("1 / [1, 2]", True),
                           ("-[None]", True), ("None + 1", False), ("log(-1)", False)):
        with pytest.raises(pseudocode.Failure) as e:
            sample(f"def f():\n    return {body}\n", language=VECTORS)
        assert checks.vector_arithmetic(e.value) is expected, body
    prog = pseudocode.parse("def f():\n    return g()\ndef g():\n    while coin(0.5):\n        pass\n    return 1\n"
                            "def h():\n    return h() if coin(0.5) else 1\n", LANGUAGE)
    assert checks.accept_reject(prog, 'f') == "while loop" and checks.accept_reject(prog, 'h') == "recursion"
    assert checks.accept_reject(pseudocode.parse("def f():\n    return unif()\n", LANGUAGE), 'f') == ""


def test_support_then_fit_helpers():
    rng = np.random.default_rng(1)
    xs = list(rng.exponential(size=2000))
    assert checks.fit_cdf(xs, lambda t: 1 - np.exp(-np.maximum(t, 0)), lo=0).outcome == 'correct'
    assert checks.fit_cdf([-1.0] + xs, lambda t: 1 - np.exp(-np.maximum(t, 0)), lo=0).outcome == 'incorrect'
    ks = list(rng.integers(0, 3, size=3000))
    pmf = checks.table_pmf([1 / 3] * 3)
    assert checks.fit_pmf(ks, pmf).outcome == 'correct' and checks.fit_pmf([0.5] + ks, pmf).outcome == 'incorrect'
    assert list(pmf(np.array([-1, 0, 2, 3]))) == [0, 1 / 3, 1 / 3, 0]
    p = checks.matrix([[1, 2], (3, 4)], 2)
    assert p.shape == (2, 2)
    for bad in ([[1, 2, 3]], [[1, True]], [[1, float('inf')]], [5]):
        with pytest.raises(checks.Stop) as stop:
            checks.matrix(bad, 2)
        assert stop.value.outcome == 'incorrect'
    fit = checks.inside(np.array([True, False]), p)
    assert fit.outcome == 'incorrect' and "1 of 2 outside" in fit.note and "(3, 4)" in fit.note
    assert checks.inside(np.array([True, True]), p).outcome == 'correct'
    assert checks.preview([0.123456, [1.0, 2], 'x'] * 4, 3) == "0.1235, [1, 2], x, ... (12)"


def test_parts_and_evidence():
    part = checks.Part('1')
    part.add("first", Fit('correct', 0.5, 1.0, "ok", None), [1.0, 2.0])
    part.add("second", Fit(None, None, None, "too few", None))
    assert part.combine().outcome is None and part.note == "second: too few"
    part.settle('correct')
    assert part.note == ""
    other = checks.Part('2').stop("g(1)", checks.Stop('incorrect', "line 2: boom"))
    assert (other.outcome, other.note) == ('incorrect', "g(1): line 2: boom")
    body = body_of(checks.evidence([part, other], "def f():\n    pass\n"))
    assert body['data'] == {'fields': {'1': {'outcome': 'correct', 'note': ''},
                                       '2': {'outcome': 'incorrect', 'note': "g(1): line 2: boom"}}}
    shown = str(body['blocks'])
    assert "1 tests" in shown and "2 tests" in shown and "Samples" in shown and "Source" in shown
    one = str(body_of(checks.evidence(part, "src", ran="run"))['blocks'])
    assert "'Tests'" in one and "As run" in one


def test_check_source():
    def judge(program):
        part = checks.Part('a')
        return part.settle('blank') if program.is_blank('f') else part.settle('correct')
    data = body_of(checks.check_source("def f():\n    pass\n", LANGUAGE, 'a', judge))['data']
    assert data['fields']['a']['outcome'] == 'blank'
    data = body_of(checks.check_source("def f():\n    import os\n", LANGUAGE, ['a', 'b'], judge))['data']
    assert [f['outcome'] for f in data['fields'].values()] == [None, None] and 'line 2' in data['fields']['a']['note']
    # `ran` is what runs; the evidence shows the source as written too.
    body = body_of(checks.check_source("def f():\n    return 2 ^ 3\n", LANGUAGE, 'a', judge,
                                       ran="def f():\n    return 2 ** 3\n"))
    assert body['data']['fields']['a']['outcome'] == 'correct' and "As run" in str(body['blocks'])


def test_kit_knows_no_exam_and_the_core_never_imports_it():
    pattern = re.compile(r'\bq\d+\b|weibull|poisson|huge|shiny|exam_0|snow|spire|cone', re.I)
    kit = ROOT / 'src' / 'examinator_kits'
    hits = [f"{f.name}:{n}" for f in kit.rglob('*.py')
            for n, line in enumerate(f.read_text().splitlines(), 1) if pattern.search(line)]
    assert not hits, hits
    assert not [f for f in (ROOT / 'src' / 'examinator').rglob('*.py') if 'examinator_kits' in f.read_text()]
    assert not hasattr(pseudocode, 'UNIF')
