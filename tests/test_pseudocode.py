"""std.pseudocode: the bounded interpreter. Synthetic, instructor-written programs only."""
import dataclasses
import math
import statistics
import sys
import time

import numpy as np
import pytest

from examinator.std import pseudocode
from examinator.std.pseudocode import (BASE, WITH_COLLECTIONS, Budget, Constant, Failure, Language, Primitive,
                                       Program, Unsupported)


# A course's random primitives: the core language has none, not even `unif`.

def coin_fn(rng, p):
    if type(p) not in (int, float, bool) or not 0 <= p <= 1:
        raise ValueError(f"probability {p!r} not in [0, 1]")
    return rng.random() < p


def die_fn(rng, n):
    if type(n) is float and n == int(n):
        n = int(n)
    if type(n) is not int or n < 1:
        raise ValueError(f"die needs a positive whole number, got {n!r}")
    return 1 + int(rng.random() * n)


def unif_fn(rng, *bounds):
    u = rng.random()
    if not bounds:
        return u
    if len(bounds) != 2:
        raise Failure(f"unif() takes 0 or 2 arguments, got {len(bounds)}", kind='arity')
    a, b = bounds
    if not all(type(v) in (int, float, bool) for v in bounds):
        raise TypeError
    return a + (b - a) * u


UNIF = {'unif': Primitive(unif_fn, "`unif()` is uniform on [0, 1); `unif(a, b)` is uniform on [a, b)",
                          group='Randomness', random=True, numeric=True, arity=(0, 2))}
COIN = Primitive(coin_fn, "`coin(p)` is True with probability p", group='Randomness', random=True)
DIE = Primitive(die_fn, "`die(n)` is a uniform whole number from 1 to n", group='Randomness', random=True)
COURSE = {**UNIF, 'coin': COIN, 'die': DIE}
TBASE = BASE.with_builtins(**COURSE)
TCOLL = WITH_COLLECTIONS.with_builtins(**COURSE)


def parse(source, language=None):
    """The test course's language unless another is given."""
    return pseudocode.parse(source, TBASE if language is None else language)


SCALAR = '''\
def weibull(a, b):
    return (-log(1 - unif()) / b) ** (1 / a)

def weibullish():
    while True:
        t = weibull(2, 4)
        if t >= 1 and coin(t**2 / huge_constant):
            return t

def poisson_dist(m):
    n = 0
    total = -log(1 - unif())
    while total < m:
        n += 1
        total += -log(1 - unif())
    return n
'''

PROCESS = '''
def poisson_process():
    events = []
    for i in range(poisson_dist(25)):
        u = unif()
        if u < 0.5:
            events.append(-sqrt(25 - 50*u))
        else:
            events.append(sqrt(50*u - 25))
    return events
'''


def reference_weibull(rng, a, b):
    return (rng.exponential() / b) ** (1 / a)


def run(source, entry='f', args=(), language=TBASE, **kw):
    kw.setdefault('n', 1)
    return parse(source, language).sample(entry, args, **kw)


def one(source, *args, **kw):
    return run(source, 'f', args, **kw)[0]


def coll(source, *args, **kw):
    return one(source, *args, language=TCOLL, **kw)


# ---- the four fixture functions ------------------------------------------

def test_fixtures_parse():
    program = parse(SCALAR, TBASE)
    assert isinstance(program, Program)
    assert program.names == ('weibull', 'weibullish', 'poisson_dist')
    assert program.source == SCALAR
    with pytest.raises(Unsupported):
        parse(SCALAR + PROCESS)            # lists and methods need TCOLL
    both = parse(SCALAR + PROCESS, TCOLL)
    assert both.names[-1] == 'poisson_process'


def test_scalar_samplers_have_the_right_means():
    program = parse(SCALAR, TBASE)
    xs = program.sample('weibull', (1, 0.5), n=4000, seed=1)
    assert all(type(x) is float and x >= 0 and math.isfinite(x) for x in xs)
    assert abs(statistics.mean(xs) - 2) < 0.15
    ks = program.sample('poisson_dist', (3,), n=4000, seed=2)
    assert all(type(k) is int and k >= 0 for k in ks)
    assert abs(statistics.mean(ks) - 3) < 0.15
    assert abs(statistics.variance(ks) - 3) < 0.4


def test_collection_sampler():
    program = parse(SCALAR + PROCESS, TCOLL)
    runs = program.sample('poisson_process', n=600, seed=3)
    assert all(type(events) is list for events in runs)
    assert abs(statistics.mean(len(events) for events in runs) - 25) < 1
    assert all(-5 <= t <= 5 for events in runs for t in events)
    # a trusted primitive replaces the student's count helper
    fixed = program.sample('poisson_process', n=5, primitives={'poisson_dist': lambda rng, m: 7})
    assert [len(events) for events in fixed] == [7] * 5


def test_invalid_acceptance_probability_is_a_failure():
    program = parse(SCALAR, TBASE)
    with pytest.raises(Failure) as info:
        program.sample('weibullish', n=200, constants={'huge_constant': 1})
    message = str(info.value)
    assert 'coin(t**2 / huge_constant)' in message
    assert info.value.expr == 'coin(t**2 / huge_constant)' and info.value.line == 7
    value = float(message.split('probability ')[1].split(' not in')[0])
    assert value > 1


def test_rejection_sampler_speed():
    program = parse(SCALAR, TBASE)
    start = time.perf_counter()
    xs = program.sample('weibullish', n=1200, seed=4, constants={'huge_constant': 10},
                        primitives={'weibull': reference_weibull})
    elapsed = time.perf_counter() - start
    print(f"\n1200 rejection samples: {elapsed:.2f} s")
    assert len(xs) == 1200 and all(x >= 1 and math.isfinite(x) for x in xs)
    assert 1.1 < statistics.mean(xs) < 1.2
    assert elapsed < 30
    # the student's own helper gives the same law
    ys = program.sample('weibullish', n=300, seed=4, constants={'huge_constant': 10})
    assert all(y >= 1 for y in ys) and 1.08 < statistics.mean(ys) < 1.22


# ---- program queries -------------------------------------------------------

def test_primitive_overrides_student_function():
    source = "def helper():\n    return 1\n\ndef f():\n    return helper() + 1\n"
    program = parse(source)
    assert program.sample('f', n=2) == [2, 2]
    assert program.sample('f', n=2, primitives={'helper': lambda rng: 10}) == [11, 11]
    assert program.sample('helper', n=1, primitives={'helper': lambda rng: 10}) == [10]
    # primitives get the sample's generator and their numpy results become plain values
    got = program.sample('f', n=3, primitives={'helper': lambda rng: rng.integers(5, 6)})
    assert got == [6, 6, 6] and all(type(v) is int for v in got)
    # a primitive hides even a blank or wrong-arity student helper
    blank = parse("def helper(a, b, c):\n    pass\n\ndef f():\n    return helper()\n")
    assert blank.sample('f', n=1, primitives={'helper': lambda rng: 5}) == [5]


def test_select_keeps_only_reachable_helpers():
    source = '''\
def a():
    return b() + 1

def unused():
    return a()

def b():
    return c(2)

def c(x):
    return x * shared
'''
    program = parse(source)
    picked = program.select('a')
    assert picked.names == ('a', 'b', 'c')
    assert 'unused' not in picked.source
    assert picked.sample('a', n=1, constants={'shared': 3}) == [7]
    assert program.select('c').names == ('c',)
    assert program.select('unused').names == ('a', 'unused', 'b', 'c')
    assert program.select('missing').names == ()
    assert program.names == ('a', 'unused', 'b', 'c')          # the original is unchanged


def test_references():
    program = parse(SCALAR, TBASE)
    assert program.references('weibullish', 'huge_constant')
    assert program.references('weibullish', 'weibull')
    assert program.references('weibullish', 'coin')
    assert program.references('weibullish', 'log')              # through the helper
    assert not program.references('weibull', 'huge_constant')
    assert not program.references('weibullish', 't')            # a local, not a free name
    assert not program.references('missing', 'log')
    shadow = parse("def f(log):\n    return log\n")
    assert not shadow.references('f', 'log')


def test_is_blank_and_has():
    program = parse('''\
def a():
    pass

def b():
    """Only a docstring."""

def c():
    # only a comment
    pass

def d():
    """Docstring."""
    return 1

def e():
    ...
''')
    assert [program.is_blank(n) for n in 'abcde'] == [True, True, True, False, True]
    assert program.is_blank('missing')
    assert program.has('a') and not program.has('missing')
    for name in ('a', 'missing'):
        with pytest.raises(Failure, match=name):
            program.sample(name, n=1)
    with pytest.raises(Failure, match="blank"):
        run("def g():\n    pass\n\ndef f():\n    return g()\n")
    assert program.sample('d', n=1) == [1]


# ---- reproducibility --------------------------------------------------------

def test_reproducible_and_independent_of_n():
    program = parse(SCALAR, TBASE)
    a = program.sample('weibull', (2, 4), n=50, seed=7)
    assert a == program.sample('weibull', (2, 4), n=50, seed=7)
    assert a[:10] == program.sample('weibull', (2, 4), n=10, seed=7)
    assert a != program.sample('weibull', (2, 4), n=50, seed=8)
    assert len(set(a)) == 50
    counts = program.sample('poisson_dist', (10,), n=30, seed=7)     # variable draws per sample
    assert counts[:5] == program.sample('poisson_dist', (10,), n=5, seed=7)
    assert program.sample('weibull', (2, 4), n=0) == []


def test_recursion_limit_is_restored():
    before = sys.getrecursionlimit()
    run("def f():\n    return 1\n")
    with pytest.raises(Budget):
        run("def f():\n    return f()\n")
    assert sys.getrecursionlimit() == before


# ---- budgets ----------------------------------------------------------------

def test_step_budget():
    with pytest.raises(Budget) as info:
        run("def f():\n    while True:\n        pass\n", max_steps=10_000)
    assert info.value.kind == 'steps'
    with pytest.raises(Budget) as info:
        run("def f():\n    for i in range(10**9):\n        pass\n", max_steps=10_000)
    assert info.value.kind == 'steps'
    with pytest.raises(Budget) as info:      # steps are per sample and exact
        run("def f():\n    for i in range(100):\n        pass\n", max_steps=100)
    assert info.value.kind == 'steps'
    assert run("def f():\n    for i in range(100):\n        pass\n", max_steps=101, n=3) == [None] * 3
    with pytest.raises(Budget) as info:      # calls count as steps: no loop needed to run forever
        run("def f():\n    return g(40)\ndef g(n):\n    return 1 if n == 0 else g(n - 1) + g(n - 1)\n",
            max_steps=50_000)
    assert info.value.kind == 'steps'


class Counting:
    """A test's own draw counter: wraps the sample's generator and caps its draws."""

    def __init__(self, cap, error=None):
        self.cap, self.error, self.counts = cap, error, []

    def __call__(self, seed, i):
        rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(i,))))
        self.counts.append(0)
        outer = self

        class Wrapped:
            def random(self):
                outer.counts[-1] += 1
                if outer.counts[-1] > outer.cap:
                    raise outer.error or Budget('draws', f"more than {outer.cap} draws")
                return rng.random()
        return Wrapped()


def test_rng_factory_counts_and_caps_draws():
    source = ("def f():\n    n = 0\n    while not coin(0.4):\n        n += 1\n"
              "    return n + 0 * unif(2, 3) + 0 * unif()\n")
    counter = Counting(cap=10**6)
    values = run(source, n=100, seed=3, rng=counter)
    assert values == run(source, n=100, seed=3)             # the same generator underneath
    assert counter.counts == [v + 1 + 2 for v in values]     # coins, then two unif
    assert len(set(counter.counts)) > 3                      # per sample
    with pytest.raises(Budget) as info:                      # a cap raised by the generator passes through
        run(source, n=100, seed=3, rng=Counting(cap=max(counter.counts) - 1))
    assert info.value.kind == 'draws'
    for draw, line in (("unif()", 2), ("unif(0, 1)", 2)):
        prog = f"def f():\n    return {draw}\n"
        with pytest.raises(Failure) as info:                 # a limitation, at the unif call
            run(prog, rng=Counting(0, pseudocode.Limitation("out of draws for this check")))
        assert info.value.kind == 'unsupported' and info.value.line == line and info.value.expr == draw
        assert "out of draws" in str(info.value)
        with pytest.raises(Failure) as info:                 # anything else is 'other'
            run(prog, rng=Counting(0, RuntimeError("generator broke")))
        assert info.value.kind == 'other' and info.value.line == line and "RuntimeError" in str(info.value)
    # numpy scalars from a custom generator are plain floats inside the program
    assert run("def f():\n    return unif() * 2\n", n=2,
               rng=lambda seed, i: type('G', (), {'random': lambda self: np.float64(0.25)})()) == [0.5, 0.5]
    with pytest.raises(TypeError):
        run("def f():\n    return 1\n", rng=lambda seed, i: object())


def test_depth_budget():
    with pytest.raises(Budget) as info:
        run("def f():\n    return f()\n")
    assert info.value.kind == 'depth'
    with pytest.raises(Budget) as info:
        run("def f():\n    return 1 + (2 * g(1))\ndef g(n):\n    if n > 0:\n        return 1 + (g(n + 1) + 0) * 1\n")
    assert info.value.kind == 'depth'
    ok = "def f(n):\n    if n == 0:\n        return 0\n    return 1 + f(n - 1)\n"
    assert run(ok, args=(150,)) == [150]
    with pytest.raises(Budget) as info:
        run(ok, args=(150,), max_depth=100)
    assert info.value.kind == 'depth'


def test_size_budget():
    for body in ("return 10**10**10", "return 2**(2**40)", "x = 3\n    while True:\n        x = x * x",
                 "x = 10**1000\n    while True:\n        x *= x",
                 "x = 7\n    for i in range(10):\n        x = x ** x\n    return x"):
        with pytest.raises(Budget) as info:
            run(f"def f():\n    {body}\n")
        assert info.value.kind == 'size', body
    for body in ("return [0] * 10**9", "xs = [1]\n    while True:\n        xs = xs + xs",
                 "xs = []\n    while True:\n        xs.append(1)", "return [i for i in range(10**6)]",
                 "xs = [0] * 50000\n    ys = []\n    while True:\n        ys.append(xs + xs)",
                 "return list(range(10**12))", "xs = [1]\n    while True:\n        xs.extend(xs)",
                 "return (1, 2) * 10**9"):
        with pytest.raises(Budget) as info:
            coll(f"def f():\n    {body}\n")
        assert info.value.kind == 'size', body
    with pytest.raises(Budget) as info:      # tuples exist in the scalar language too
        run("def f():\n    return (1, 2) * 10**9\n")
    assert info.value.kind == 'size'


def test_deadline():
    start = time.monotonic()
    with pytest.raises(Budget) as info:
        run("def f():\n    while True:\n        pass\n", max_steps=None, deadline=start + 0.2)
    assert info.value.kind == 'time'
    assert time.monotonic() - start < 5
    with pytest.raises(Budget) as info:      # already past: the first sample stops
        run("def f():\n    return 1\n", deadline=time.monotonic() - 1)
    assert info.value.kind == 'time'


def test_big_integers_never_crash_the_host():
    assert one("def f():\n    return 10**1000\n") == 10**1000
    assert one("def f():\n    return 10**1000 * 10**1000 // 10**1999\n") == 10
    assert one("def f():\n    return 0.5 / 10**1000\n") == 0.0
    assert one("def f():\n    return 10**1000 / 10**999\n") == 10.0
    assert one("def f():\n    return 1 / 10**1000\n") == 0.0
    assert one("def f():\n    return 10**1000 + 0.5\n") == math.inf
    assert one("def f():\n    return coin(1 / 10**1000)\n") is False
    assert one("def f():\n    return 10**1000 > 1.5\n") is True
    assert one("def f():\n    return float(10**1000), sqrt(10**1000), exp(1000), 10.0**1000, exp(10**1000)\n") \
        == (math.inf,) * 5
    assert one("def f():\n    return log(10**1000)\n") == pytest.approx(1000 * math.log(10))
    assert one("def f():\n    return 2 ** -2, (-2) ** 3, 0 ** 0, True + True\n") == (0.25, -8, 1, 2)
    assert one("def f():\n    return (10**1000) ** -1\n") == 0.0


# ---- unsupported -------------------------------------------------------------

UNSUPPORTED = [
    "import os",
    "def f():\n    import os\n",
    "def f():\n    from os import path\n",
    "def f():\n    return ().__class__\n",
    "def f(x):\n    return x.real\n",
    "def f():\n    return open('/etc/passwd')\n",
    "def f():\n    return open(1)\n",
    "def f():\n    return __import__\n",
    "def f():\n    return __import__(1)\n",
    "def f():\n    return eval(1)\n",
    "def f():\n    g = lambda x: x\n    return g(1)\n",
    "def f():\n    return 'text'\n",
    "def f():\n    x = 1\n    'not a docstring'\n    return x\n",
    "def f():\n    return f'{1}'\n",
    "def f():\n    return float('x')\n",
    "def f():\n    return b'x'\n",
    "def f():\n    return 1j\n",
    "def f(xs):\n    return xs[0]\n",
    "def f():\n    return [1, 2]\n",
    "def f():\n    return [i for i in range(3)]\n",
    "def f():\n    return {1: 2}\n",
    "def f(xs):\n    for x in xs:\n        return x\n",
    "def f():\n    return 1 in (1, 2)\n",
    "x = 1\n",
    "f()\n",
    "'module docstring'\ndef f():\n    return 1\n",
    "def f():\n    return 1\nprint(f())\n",
    "class A:\n    pass\n",
    "def f():\n    return g(x=1)\n",
    "def f(a, b=2):\n    return a\n",
    "def f(*a):\n    return 1\n",
    "def f(**k):\n    return 1\n",
    "def f(a, /, b):\n    return 1\n",
    "def f(a: int):\n    return a\n",
    "def f() -> int:\n    return 1\n",
    "@decorator\ndef f():\n    return 1\n",
    "async def f():\n    return 1\n",
    "def f(a):\n    return g(*a)\n",
    "def f():\n    def g():\n        return 1\n    return g()\n",
    "def f():\n    try:\n        return 1\n    except Exception:\n        return 2\n",
    "def f():\n    with x:\n        pass\n",
    "def f():\n    global x\n    x = 1\n",
    "def f():\n    x = 1\n    del x\n",
    "def f():\n    assert True\n",
    "def f():\n    yield 1\n",
    "def f():\n    return (x := 1)\n",
    "def f():\n    x: int = 1\n",
    "def f():\n    raise 1\n",
    "def f():\n    return 1 << 3\n",
    "def f():\n    return ~1\n",
    "def f():\n    return 1 @ 2\n",
    "def f():\n    1 + 2\n",
    "def f():\n    return 3(4)\n",
    "def f():\n    return f()()\n",
    "def f():\n    break\n",
    "def f():\n    while True:\n        pass\n    else:\n        pass\n",
    "def f():\n    x = range(3)\n",
    "def f():\n    return 1 +\n",
    "def f():\nreturn 1\n",
    "def f():\n    return __x\n",
    "def __f__():\n    return 1\n",
    "def f():\n    return ...\n",
    "def f():\n    return 10 ** " + "9" * 5000 + "\n",
    "def f():\n    return 0x" + "f" * 18000 + "\n",
    "def f():\n    return " + "(" * 300 + "1" + ")" * 300 + "\n",
    "def f():\n    return " + "-" * 100 + "1\n",
    "def f():\n    return " + " + ".join(["1"] * 3000) + "\n",
    "def f():\n    return 1\n" + "#" * 20_000,
    "def f():\n    return 1\x00\n",
]


@pytest.mark.parametrize('source', UNSUPPORTED)
def test_unsupported(source):
    with pytest.raises(Unsupported) as info:
        parse(source)
    assert info.value.line is None or isinstance(info.value.line, int)
    assert len(str(info.value)) < 120


UNSUPPORTED_WITH_COLLECTIONS = [
    "def f(xs):\n    return xs.sort()\n",
    "def f(xs):\n    return xs.__class__\n",
    "def f(xs):\n    return xs.append\n",
    "def f(xs):\n    xs.append(1, 2)\n",
    "def f(xs):\n    xs[0:1] = [2]\n",
    "def f():\n    return {1: 2}\n",
    "def f():\n    return {i: i for i in range(3)}\n",
    "def f():\n    return 'a' in ['a']\n",
    "def f():\n    return [*range(3)]\n",
    "def f(xs):\n    del xs[0]\n",
    "def f(xs):\n    return xs.pop.__self__\n",
]


@pytest.mark.parametrize('source', UNSUPPORTED_WITH_COLLECTIONS)
def test_unsupported_with_collections(source):
    with pytest.raises(Unsupported):
        parse(source, TCOLL)


def test_unsupported_reports_the_line():
    with pytest.raises(Unsupported) as info:
        parse("def f():\n    x = 1\n    import os\n")
    assert info.value.line == 3 and 'import' in str(info.value)


# ---- failures ---------------------------------------------------------------

FAILURES = [
    ("def f(x, y):\n    z = 1\n    return x / (y - z)\n", (3, 1), 3, 'x / (y - z)', ['division by zero', '3 / 0']),
    ("def f(x, y):\n    return x // y\n", (3.5, 0), 2, 'x // y', ['division by zero', '3.5 // 0']),
    ("def f(x, y):\n    return x % y\n", (3, 0), 2, 'x % y', ['division by zero']),
    ("def f(u):\n    x = 2\n    return log(1 - u)\n", (1.25,), 3, 'log(1 - u)', ['log of -0.25']),
    ("def f(u):\n    return ln(u)\n", (0,), 2, 'ln(u)', ['ln of 0']),
    ("def f(u):\n    return sqrt(u)\n", (-4.0,), 2, 'sqrt(u)', ['sqrt of -4']),
    ("def f(u):\n    return u ** 0.5\n", (-4.0,), 2, 'u ** 0.5', ['fractional power', '-4 ** 0.5']),
    ("def f(u):\n    return 0 ** u\n", (-1,), 2, '0 ** u', ['zero to a negative power']),
    ("def f(u):\n    return floor(u)\n", (math.inf,), 2, 'floor(u)', ['floor of inf']),
    ("def f(p):\n    return coin(p)\n", (1.7,), 2, 'coin(p)', ['probability 1.7 not in [0, 1]']),
    ("def f(p):\n    return coin(p)\n", (-0.1,), 2, 'coin(p)', ['probability -0.1 not in [0, 1]']),
    ("def f(p):\n    return coin(p)\n", (math.nan,), 2, 'coin(p)', ['probability nan']),
    ("def f(p):\n    return coin(p)\n", (None,), 2, 'coin(p)', ['probability None']),
    ("def f():\n    y = 1\n    return y + zed\n", (), 3, 'zed', ["name 'zed' is not defined"]),
    ("def f():\n    return mystery(2)\n", (), 2, 'mystery(2)', ["function 'mystery' is not defined"]),
    ("def f(c):\n    if c:\n        y = 1\n    return y\n", (False,), 4, 'y', ["'y'", 'before']),
    ("def g(a, b):\n    return a\n\ndef f():\n    return g(1)\n", (), 5, 'g(1)', ['g() takes 2 arguments, got 1']),
    ("def f():\n    return log(1, 2, 3)\n", (), 2, 'log(1, 2, 3)', ['log() takes 1 arguments, got 3']),
    ("def f():\n    return coin()\n", (), 2, 'coin()', ['takes 1 arguments, got 0']),
    ("def f():\n    return 1 + None\n", (), 2, '1 + None', ['1 + None']),
    ("def f():\n    return -None\n", (), 2, '-None', ['None']),
    ("def f():\n    return None < 1\n", (), 2, 'None < 1', ['compare', 'None < 1']),
    ("def f():\n    x = 3\n    return x(1)\n", (), 3, 'x(1)', ['variable, not a function']),
    ("def f():\n    return log + 1\n", (), 2, 'log', ['function, not a value']),
    ("def f():\n    a, b = 1, 2, 3\n", (), 2, 'a, b', ['3 values', '2 names']),
    ("def f():\n    a, b = 5\n", (), 2, 'a, b', ['unpack 5']),
    ("def f():\n    for i in range(2.5):\n        pass\n", (), 2, 'range(2.5)', ['whole numbers', '2.5']),
    ("def f():\n    return round(1.5, 10**9)\n", (), 2, 'round(1.5, 10**9)', ['round']),
    ("def f():\n    return k(1)\n", (), 2, 'k(1)', ['number, not a function']),
    ("def f():\n    return len(3)\n", (), 2, 'len(3)', ["function 'len' is not defined"]),
]


@pytest.mark.parametrize('source,args,line,expr,needles', FAILURES)
def test_failure_names_expression_and_values(source, args, line, expr, needles):
    with pytest.raises(Failure) as info:
        run(source, args=args, constants={'k': 2})
    failure = info.value
    assert failure.line == line
    assert failure.expr == expr
    assert str(failure).startswith(f"line {line}: {expr}: ")
    for needle in needles:
        assert needle in str(failure)


def test_entry_arity_and_primitive_errors_are_failures():
    program = parse("def f(a):\n    return helper(a, 0)\n")
    with pytest.raises(Failure, match="takes 1 arguments, got 2"):
        program.sample('f', (1, 2), n=1)
    with pytest.raises(Failure) as info:
        program.sample('f', (1,), n=1, primitives={'helper': lambda rng, a, b: a / b})
    assert 'helper(1, 0)' in str(info.value) and 'ZeroDivisionError' in str(info.value)
    with pytest.raises(Failure, match=r"helper\(\) takes 1 arguments, got 2") as info:     # arity from the signature
        program.sample('f', (1,), n=1, primitives={'helper': lambda rng, a: a})
    assert info.value.kind == 'arity' and info.value.line == 2
    with pytest.raises(Failure, match="IndexError"):                  # what the signature cannot say
        program.sample('f', (1,), n=1, primitives={'helper': Primitive(lambda rng, *a: a[2], "x", random=True)})
    with pytest.raises(Failure, match="unsupported value"):
        program.sample('f', (1,), n=1, primitives={'helper': lambda rng, a, b: 'text'})


def test_failure_stops_at_the_failing_sample_only():
    source = "def f():\n    u = unif()\n    return 1 / (0 if u < 0.01 else 1)\n"
    assert run(source, n=20, seed=0) == [1.0] * 20
    with pytest.raises(Failure, match="division by zero"):
        run(source, n=3000, seed=0)


# ---- language semantics ------------------------------------------------------

def test_statements_and_expressions():
    assert one('''\
def f(n):
    """Sum of the odd numbers below n that are not multiples of 5."""
    total = 0
    i = 0
    while True:
        i += 1
        if i >= n:
            break
        if i % 2 == 0:
            continue
        elif i % 5 == 0:
            continue
        else:
            total += i
    return total
''', 12) == 1 + 3 + 7 + 9 + 11
    assert one("def f():\n    a, b = 1, 2\n    a, b = b, a\n    x = y = a * 10\n    return a, b, x, y\n") == (2, 1, 20, 20)
    assert one("def g():\n    return 3, 4\n\ndef f():\n    p, q = g()\n    return p * q\n") == 12
    assert one("def f():\n    s = 0\n    for i in range(2, 11, 3):\n        s += i\n    return s\n") == 15
    assert one("def f():\n    s = 0\n    for i in range(5, 0, -2):\n        s = s * 10 + i\n    return s\n") == 531
    assert one("def f():\n    for i in range(3.0):\n        pass\n    return i\n") == 2
    assert one("def f(x):\n    return 1 < x <= 3 and not x == 2\n", 3) is True
    assert one("def f(x):\n    return 1 < x < 3\n", 5) is False
    assert one("def f(x):\n    return 'neg' if False else (x if x > 0 else -x)\n".replace("'neg'", "0"), -4) == 4
    assert one("def f(x):\n    return x or 7, x and 7, 0 or 0.0 or None, 1 and 2 and 3\n", 0) == (7, 0, None, 3)
    assert one("def f(x):\n    return x is None, x is not None, x == None\n", None) == (True, False, True)
    assert one("def f():\n    return 7 // 2, -7 // 2, 7 % 3, 7.5 % 2, 2 ** 10, 7 / 2, +3, -(-3)\n") \
        == (3, -4, 1, 1.5, 1024, 3.5, 3, 3)
    assert one("def f():\n    return\n") is None
    assert one("def f():\n    x = 1\n") is None
    assert one("def f():\n    g()\n    return 5\ndef g():\n    return 1\n") == 5
    assert one("def f(n):\n    return 1 if n <= 1 else n * f(n - 1)\n", 20) == math.factorial(20)
    assert one("def f(n):\n    while n > 0:\n        for i in range(10):\n            if i == n:\n"
               "                return i * 100\n        n -= 1\n    return -1\n", 4) == 400


def test_builtin_math():
    got = one("def f():\n    return (log(e), ln(1), exp(0), sqrt(16), abs(-2.5), min(3, 1, 2), max(1, 2.5),\n"
              "            floor(2.7), ceil(2.1), round(2.6), round(2.567, 2), sin(0), cos(0), tan(0),\n"
              "            int(3.9), float(2), pi, inf, abs(-3))\n")
    assert got == (1.0, 0.0, 1.0, 4.0, 2.5, 1, 2.5, 2, 3, 3, 2.57, 0.0, 1.0, 0.0, 3, 2.0, math.pi, math.inf, 3)
    assert [type(v) for v in got[7:10]] == [int, int, int]
    us = run("def f():\n    return unif(2, 5)\n", n=500)
    assert all(2 <= u < 5 for u in us) and abs(statistics.mean(us) - 3.5) < 0.2
    flips = run("def f():\n    return coin(0.25)\n", n=2000)
    assert set(flips) == {True, False} and abs(sum(flips) / 2000 - 0.25) < 0.04
    assert run("def f():\n    return coin(0), coin(1), coin(True)\n", n=20) == [(False, True, True)] * 20


def test_names_resolve_in_order():
    # a local shadows a constant and a built-in; a student function shadows a built-in;
    # a constant shadows a student function; a primitive shadows everything global
    assert one("def f(e):\n    log = e + 1\n    return log\n", 1) == 2
    assert one("def exp(x):\n    return 42\n\ndef f():\n    return exp(0)\n") == 42
    assert one("def f():\n    return e\n", constants={'e': 5}) == 5
    assert one("def f():\n    return sqrt(4)\n", primitives={'sqrt': lambda rng, x: -1}) == -1
    assert one("def f():\n    return k + n\n", constants={'k': np.float64(0.5), 'n': np.int64(2)}) == 2.5
    with pytest.raises(TypeError):
        run("def f():\n    return k\n", constants={'k': 'text'})


def test_arguments_are_fresh_for_every_sample():
    program = parse("def f(xs):\n    xs.append(len(xs))\n    return xs\n", TCOLL)
    mine = [1]
    assert program.sample('f', (mine,), n=3) == [[1, 1]] * 3
    assert mine == [1]
    assert program.sample('f', (np.array([1.5, 2.5]),), n=1) == [[1.5, 2.5, 2]]


# ---- collections ----------------------------------------------------------------

def test_comprehensions():
    assert coll("def f():\n    return [i * i for i in range(10) if i % 2 == 0 if i > 0]\n") == [4, 16, 36, 64]
    assert coll("def f():\n    return [(i, j) for i in range(3) for j in range(i)]\n") == [(1, 0), (2, 0), (2, 1)]
    assert coll("def f(xs):\n    return [x + 1 for x in xs if x not in [2]]\n", [1, 2, 3]) == [2, 4]
    assert coll("def f(xs):\n    return sum(x * x for x in xs)\n", [1, 2, 3]) == 14
    assert coll("def f(ps):\n    return [a - b for a, b in ps]\n", [(5, 1), (7, 2)]) == [4, 5]
    assert sorted(coll("def f():\n    return {i % 3 for i in range(7)}\n")) == [0, 0, 0, 1, 1, 2, 2]


def test_sets_keep_duplicates():
    got = coll("def f():\n    s = set()\n    s.add(1.5)\n    s.add(2.5)\n    s.add(1.5)\n    return s\n")
    assert type(got) is list and sorted(got) == [1.5, 1.5, 2.5]
    assert sorted(coll("def f():\n    return {1, 1, 2}\n")) == [1, 1, 2]
    assert sorted(coll("def f():\n    a = {1, 2}\n    b = {2, 3}\n    return a | b\n")) == [1, 2, 2, 3]
    assert sorted(coll("def f():\n    a = {1, 2}\n    a |= {2}\n    a += [9]\n    return a\n")) == [1, 2, 2, 9]
    assert sorted(coll("def f():\n    return set([4, 4, 5])\n")) == [4, 4, 5]
    assert coll("def f():\n    s = {1, 1}\n    return len(s), 1 in s, 3 in s, 3 not in s\n") == (2, True, False, True)
    assert coll("def f():\n    return [{1, 1}, [2]], ({3}, 4)\n") == ([[1, 1], [2]], ([3], 4))
    assert coll("def f():\n    s = set()\n    s.append(1)\n    xs = []\n    xs.add(2)\n    return s + xs\n") == [1, 2]


def test_lists():
    assert coll("def f():\n    return [1, 2] + [3], [0] * 3, 2 * [1, 2], [] + []\n") == ([1, 2, 3], [0, 0, 0], [1, 2, 1, 2], [])
    assert coll("def f(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t, len(xs), sum(xs)\n", [1, 2, 3.5]) \
        == (6.5, 3, 6.5)
    assert coll("def f(xs):\n    return xs[0], xs[-1], xs[1:3], xs[::-1], xs[:2], sorted(xs), min(xs), max(xs)\n",
                [3, 1, 2, 5]) == (3, 5, [1, 2], [5, 2, 1, 3], [3, 1], [1, 2, 3, 5], 1, 5)
    assert coll("def f():\n    xs = [1, 2, 3]\n    xs[0] = 9\n    xs[1] += 5\n    xs[-1] *= 2\n    return xs\n") == [9, 7, 6]
    assert coll("def f():\n    xs = [1]\n    xs.extend([2, 3])\n    last = xs.pop()\n    first = xs.pop(0)\n"
                "    return xs, last, first\n") == ([2], 3, 1)
    assert coll("def f():\n    xs = [1]\n    ys = xs\n    ys += [2]\n    zs = xs + [3]\n    return xs, zs\n") == ([1, 2], [1, 2, 3])
    assert coll("def f():\n    return list(), list(range(3)), range(1, 4), list((1, 2)), [[]] * 2\n") \
        == ([], [0, 1, 2], [1, 2, 3], [1, 2], [[], []])
    assert coll("def f():\n    xs = []\n    for i in range(4):\n        xs.append(i)\n        if xs:\n            xs[0] += 1\n"
                "    return xs if xs else None\n") == [4, 1, 2, 3]
    assert coll("def f():\n    return [1, 2] == [1, 2], [1] != [2], [1, 2] < [1, 3]\n") == (True, True, True)
    big = coll("def f():\n    xs = []\n    for i in range(20000):\n        xs += [i]\n    return xs\n")
    assert big == list(range(20000))


COLLECTION_FAILURES = [
    ("def f(xs):\n    return xs[3]\n", ([1, 2],), ['xs[3]', 'index 3', 'length 2']),
    ("def f(xs):\n    return xs[0.5]\n", ([1, 2],), ['index 0.5']),
    ("def f(xs):\n    xs[5] = 1\n", ([1, 2],), ['xs[5]', 'index 5']),
    ("def f(xs):\n    return xs.pop()\n", ([],), ['pop']),
    ("def f(x):\n    x.append(1)\n", (3,), ['x.append(1)', 'append to 3']),
    ("def f(x):\n    for y in x:\n        pass\n", (3,), ['loop over 3']),
    ("def f(x):\n    return [y for y in x]\n", (None,), ['loop over None']),
    ("def f(x):\n    return 1 in x\n", (3,), ['1 in 3']),
    ("def f(x):\n    return len(x)\n", (3,), ['len of 3']),
    ("def f(x):\n    return sum(x)\n", ([[1], [2]],), ['sum of']),
    ("def f(x):\n    return sorted(x)\n", ([1, None],), ['sorted of [1, None]']),
    ("def f(x):\n    return x + 1\n", ([1],), ['[1] + 1']),
    ("def f(x):\n    return x - x\n", ([1],), ['[1] - [1]']),
    ("def f(x):\n    return x * 1.5\n", ([1],), ['[1] * 1.5']),
    ("def f(x):\n    return log(x)\n", ([1],), ['log of [1]']),
    ("def f(x):\n    return coin(x)\n", ([1],), ['probability [1]']),
    ("def f(x):\n    return min(x)\n", ([],), ['min of []']),
    ("def f(x):\n    return x[1:2:0]\n", ([1],), ['step 0']),
    ("def f(x):\n    return x | x\n", ([1],), ['[1] | [1]']),
    ("def f(x):\n    return x | 1\n", (1,), ['1 | 1']),
]


@pytest.mark.parametrize('source,args,needles', COLLECTION_FAILURES)
def test_collection_failures(source, args, needles):
    with pytest.raises(Failure) as info:
        run(source, args=args, language=TCOLL)
    for needle in needles:
        assert needle in str(info.value)


# ---- nothing but Failure / Budget / Unsupported escapes ---------------------------

ODD = [
    "def f():\n    return 1 + None\n",
    "def f():\n    x = 3\n    return x(2)\n",
    "def f():\n    for i in range(10**9):\n        pass\n",
    "def f():\n    return float('x')\n",
    "def f():\n    return None()\n",
    "def f():\n    return True + True * False - None\n",
    "def f():\n    return 1e308 * 1e308 - 1e308 * 1e308\n",
    "def f():\n    return int(1e308 * 10)\n",
    "def f():\n    return int(inf - inf)\n",
    "def f():\n    return round(inf)\n",
    "def f():\n    return round(1, -10**9)\n",
    "def f():\n    return ceil(-inf)\n",
    "def f():\n    return sin(inf)\n",
    "def f():\n    return 10**1000 % 0.0\n",
    "def f():\n    return 10**1000 // 1e-300\n",
    "def f():\n    return (-10.0) ** 1001 + 2.0 ** 10**1000\n",
    "def f():\n    return (-10**1000) ** 0.5\n",
    "def f():\n    return 0.0 ** -1\n",
    "def f():\n    return 2 ** -(10**1000)\n",
    "def f():\n    return (-1) ** 10**1000, 1 ** 10**1000, 0 ** 10**1000\n",
    "def f():\n    return unif(0, 10**1000)\n",
    "def f():\n    return unif(None, 1)\n",
    "def f():\n    return min(1, None)\n",
    "def f():\n    return min(1)\n",
    "def f():\n    return max()\n",
    "def f():\n    return abs(None), int(None)\n",
    "def f():\n    return f\n",
    "def f():\n    return f(1)\n",
    "def f():\n    f = 2\n    return f\n",
    "def f():\n    return (1, 2) < (1, None)\n",
    "def f():\n    return (1, 2) + 3\n",
    "def f():\n    return -(1, 2)\n",
    "def f():\n    x = (1, 2)\n    x += (3,)\n    return x\n",
    "def f():\n    x, (y, z) = 1, (2, 3)\n    return x + y + z\n",
    "def f():\n    x, y = f2()\n    return x\ndef f2():\n    return\n",
    "def f():\n    return g(g(g(1)))\ndef g(x):\n    return g(x + 1) if x < 10**6 else x\n",
    "def f():\n    while unif() < 2:\n        coin(0.5)\n",
    "def f(pass_):\n    return 1\n",
    "def f():\n    return range\n",
    "def f():\n    range = 3\n    for i in range(2):\n        pass\n",
    "def f():\n    for i in range():\n        pass\n",
    "def f():\n    for i in range(1, 2, 0):\n        pass\n",
    "def f():\n    for i in range(None):\n        pass\n",
    "def f():\n    for i, j in range(3):\n        pass\n",
    "def f():\n    i = 0\n    for i in range(3):\n        i += 10\n    return i\n",
    "def f():\n    return coin(coin(0.5))\n",
    "def f():\n    return log(log(log(unif())))\n",
    "def f():\n    return exp(exp(exp(exp(3))))\n",
    "def f():\n    x = 1.0\n    while x > 0:\n        x = x * 1e10\n",
    "def f():\n    x = 1\n    while x:\n        x = x * 10 + 1\n",
    "def f():\n    x = 1\n    for i in range(10**6):\n        x += x\n",
    "",
    "\n\n# nothing\n",
    "def g():\n    pass\n",
]

ODD_COLLECTIONS = [
    "def f():\n    xs = []\n    xs.append(xs)\n    return xs\n",
    "def f():\n    xs = []\n    xs.append(xs)\n    return xs == xs, xs < xs\n",
    "def f():\n    xs = []\n    xs.append(xs)\n    ys = []\n    ys.append(ys)\n    return xs == ys\n",
    "def f():\n    xs = [1]\n    for x in xs:\n        xs.append(x)\n",
    "def f():\n    xs = [3, 1]\n    for x in xs:\n        xs.pop()\n    return xs\n",
    "def f():\n    return sorted([[1], 2])\n",
    "def f():\n    return sum([10**1000] * 1000) ** 20\n",
    "def f():\n    return [1][10**1000]\n",
    "def f():\n    return [1][:10**1000]\n",
    "def f():\n    return [1] * -5, [1] * True, [] * 10**1000\n",
    "def f():\n    return [1] * 10**1000\n",
    "def f():\n    return [[i] * 1000 for i in range(1000) for j in range(1000)]\n",
    "def f():\n    s = {1}\n    return s[0]\n",
    "def f():\n    s = {1}\n    s[0] = 2\n",
    "def f():\n    return len(), len(1, 2), sorted(3), list(3), set(None)\n",
    "def f():\n    return [].pop(0.5)\n",
    "def f():\n    x = 5\n    x[0] += 1\n",
    "def f():\n    xs = [1]\n    xs[0] += None\n",
    "def f():\n    xs = [[1]]\n    xs[0][0] = xs\n    return xs\n",
    "def f():\n    return [x for x in range(3) if x / 0]\n",
    "def f():\n    return [y for x in range(3)]\n",
    "def f():\n    return {(1, [2]), 3} | {None}\n",
    "def f():\n    return (1, 2)[5], (1, 2)[0]\n",
    "def f():\n    a = [1]\n    b = a * 60000\n    return len(b + b)\n",
    "def f():\n    return range(5)[1:3], range(10**7)\n",
    "def f():\n    return max([1, 2], [1, 3]), min([[2], [1]]), max([None, 1])\n",
    "def f():\n    xs = [1, 2]\n    a, b, c = xs\n",
    "def f():\n    xs = [0] * 100000\n    xs.append(1)\n",
    "def f():\n    xs = [0] * 100\n    ys = [xs] * 100000\n    return [list(y) for y in ys for z in ys]\n",
]


@pytest.mark.parametrize('language', [TBASE, TCOLL])
@pytest.mark.parametrize('source', ODD + ODD_COLLECTIONS)
def test_only_our_exceptions_escape(source, language):
    try:
        results = parse(source, language).sample('f', n=3, max_steps=200_000)
    except (Failure, Budget, Unsupported) as ex:
        assert str(ex)
    else:
        assert len(results) == 3


def test_odd_programs_do_what_they_should():
    assert coll("def f():\n    xs = [3, 1]\n    for x in xs:\n        xs.pop()\n    return xs\n") == [3]
    assert coll("def f():\n    return [1] * -5, [1] * True, [] * 10**1000\n") == ([], [1], [])
    assert one("def f():\n    return (-1) ** 10**1000, 1 ** 10**1000, 0 ** 10**1000\n") == (1, 1, 0)
    assert one("def f():\n    return 2 ** -(10**1000)\n") == 0.0
    assert one("def f():\n    x = (1, 2)\n    x += (3,)\n    return x\n") == (1, 2, 3)
    assert one("def f():\n    x, (y, z) = 1, (2, 3)\n    return x + y + z\n") == 6
    assert one("def f():\n    i = 0\n    for i in range(3):\n        i += 10\n    return i\n") == 12
    assert one("def f():\n    return 1e308 * 1e308\n") == math.inf
    for source in ("def f():\n    xs = []\n    xs.append(xs)\n    return xs\n",
                   "def f():\n    xs = []\n    xs.append(xs)\n    ys = []\n    ys.append(ys)\n    return xs == ys\n"):
        with pytest.raises((Failure, Budget)):
            coll(source)


# ---- the language as a value ---------------------------------------------------

def test_language_descriptions():
    assert isinstance(BASE, Language) and not BASE.collections and WITH_COLLECTIONS.collections
    assert BASE.name != WITH_COLLECTIONS.name
    for language in (BASE, WITH_COLLECTIONS, TBASE, TCOLL):
        text = language.description
        for word in ('def name', 'log', 'sqrt', 'range', 'while', 'pi'):
            assert word in text
        assert len(text) < 3000
    for language in (BASE, WITH_COLLECTIONS):                 # no randomness unless a course adds it
        assert 'unif' not in language.description and 'coin' not in language.description
    for language in (TBASE, TCOLL):
        assert 'unif()' in language.description and 'coin(p)' in language.description
    assert 'append' in WITH_COLLECTIONS.description and 'append' not in BASE.description
    with pytest.raises(Exception):
        BASE.name = 'other'
    program = parse("def f():\n    return 1\n")
    with pytest.raises(Exception):
        program.source = "changed"


def test_library_has_no_exam_words():
    text = open(pseudocode.__file__, encoding='utf-8').read().lower()
    for word in ('q2', 'weibull', 'poisson', 'huge', 'shiny'):
        assert word not in text


def test_student_source_is_never_handed_to_the_host():
    text = open(pseudocode.__file__, encoding='utf-8').read()
    for call in ('eval(', 'exec(', 'compile(', '__import__('):
        assert call not in text.replace("'eval', 'exec', 'compile'", "")


# ---- die, Failure.kind, free names ------------------------------------------------

def test_die_and_coin_are_not_built_in():
    for name in ('die(6)', 'coin(0.5)'):
        with pytest.raises(Failure) as info:
            run(f"def f():\n    return {name}\n", language=BASE)
        assert info.value.kind == 'undefined'
    rolls = run("def f():\n    return die(6)\n", n=3000, seed=5)         # the test course's die
    assert set(rolls) == {1, 2, 3, 4, 5, 6} and all(type(r) is int for r in rolls)
    assert rolls[:7] == run("def f():\n    return die(6)\n", n=7, seed=5)
    zero = Primitive(lambda rng, n: die_fn(rng, n) - 1, "`die(n)` is a uniform whole number from 0 to n - 1",
                     random=True)
    course = BASE.with_builtins(**UNIF, die=zero)
    assert [r + 1 for r in run("def f():\n    return die(6)\n", n=3000, seed=5, language=course)] == rolls
    assert "from 0 to n - 1" in course.description and "from 1 to n" not in course.description


KINDS = [
    ("def f():\n    return zed + 1\n", (), 'undefined'),
    ("def f():\n    return mystery(2)\n", (), 'undefined'),
    ("def f():\n    return helper(1) + helper(2)\n", (), 'undefined'),
    ("def f():\n    return len(3)\n", (), 'undefined'),                 # not in the scalar language
    ("def g(a, b):\n    return a\n\ndef f():\n    return g(1)\n", (), 'arity'),
    ("def f():\n    return log(1, 2)\n", (), 'arity'),
    ("def f():\n    return coin()\n", (), 'arity'),
    ("def f():\n    for i in range():\n        pass\n", (), 'arity'),
    ("def f(x):\n    return 1 / x\n", (0,), 'domain'),
    ("def f(x):\n    return 1 % x\n", (0,), 'domain'),
    ("def f(x):\n    return log(x)\n", (-1,), 'domain'),
    ("def f(x):\n    return sqrt(x)\n", (-1.0,), 'domain'),
    ("def f(x):\n    return x ** 0.5\n", (-1.0,), 'domain'),
    ("def f(x):\n    return 0 ** x\n", (-1,), 'domain'),
    ("def f(x):\n    return floor(x)\n", (math.inf,), 'domain'),
    ("def f(x):\n    return sin(x)\n", (math.inf,), 'domain'),
    ("def f(x):\n    return coin(x)\n", (1.7,), 'other'),
    ("def f(x):\n    return coin(x)\n", (math.nan,), 'other'),
    ("def f(x):\n    for i in range(1, 5, x):\n        pass\n", (0,), 'domain'),
    ("def f(x):\n    return coin(x)\n", (None,), 'other'),
    ("def f(x):\n    return log(x)\n", (None,), 'type'),
    ("def f(x):\n    return 1 + x\n", (None,), 'type'),
    ("def f(x):\n    return -x\n", (None,), 'type'),
    ("def f(x):\n    return x < 1\n", (None,), 'type'),
    ("def f(x):\n    return x(1)\n", (3,), 'type'),
    ("def f(x):\n    return k(1)\n", (3,), 'type'),
    ("def f(x):\n    return log + x\n", (3,), 'type'),
    ("def f(x):\n    a, b = x\n", (3,), 'type'),
    ("def f(x):\n    for i in range(x):\n        pass\n", (2.5,), 'type'),
    ("def f(x):\n    return unif(x, 1)\n", (None,), 'type'),
    ("def f(x):\n    a, b = x, x, x\n", (3,), 'other'),
    ("def f(x):\n    if x:\n        y = 1\n    return y\n", (0,), 'other'),
    ("def g():\n    pass\n\ndef f():\n    return g()\n", (), 'other'),
    ("def f():\n    return boom()\n", (), 'other'),
]


@pytest.mark.parametrize('source,args,kind', KINDS)
def test_failure_kind(source, args, kind):
    def boom(rng):
        raise RuntimeError("primitive broke")
    with pytest.raises(Failure) as info:
        run(source, args=args, constants={'k': 2}, primitives={'boom': boom})
    assert info.value.kind == kind


def test_failure_kind_for_entry_and_collections():
    program = parse("def f(a):\n    return a\n\ndef blank():\n    pass\n")
    for entry, args, kind in (('missing', (), 'undefined'), ('f', (), 'arity'), ('blank', (), 'other')):
        with pytest.raises(Failure) as info:
            program.sample(entry, args, n=1)
        assert info.value.kind == kind
    assert Failure("plain").kind == 'other'
    for source, arg, kind in (("def f(x):\n    return x[5]\n", [1], 'other'),
                              ("def f(x):\n    return x[0]\n", 3, 'type'),
                              ("def f(x):\n    return len(x)\n", 3, 'type'),
                              ("def f(x):\n    return min(x)\n", [], 'domain'),
                              ("def f(x):\n    x.append(1)\n", 3, 'type'),
                              ("def f(x):\n    return [y for y in x]\n", 3, 'type'),
                              ("def f(x):\n    return x[::0]\n", [1], 'domain'),
                              ("def f(x):\n    return x + 1\n", [1], 'type')):
        with pytest.raises(Failure) as info:
            coll(source, arg)
        assert info.value.kind == kind, source


def test_free_names():
    program = parse(SCALAR, TBASE)
    assert program.free_names('weibull') == {'log', 'unif'}
    assert program.free_names('weibullish') == {'coin', 'huge_constant', 'log', 'unif'}   # helper included
    assert program.free_names('poisson_dist') == {'log', 'unif'}
    assert program.free_names('missing') == frozenset()
    assert isinstance(program.free_names('weibull'), frozenset)
    needed = program.free_names('weibullish') - program.language.names
    assert needed == {'huge_constant'}
    assert program.free_names('weibullish') - BASE.names == {'coin', 'unif', 'huge_constant'}   # no randomness in BASE
    both = parse(SCALAR + PROCESS, TCOLL)
    assert both.free_names('poisson_process') == {'range', 'unif', 'sqrt', 'log'}
    source = '''\
def f(a):
    b = a + offset
    for i in range(3):
        b += helper(i) + pi
    return other(b, f(a))

def other(x, y):
    scale = 2
    return x * scale + lookup(y)

def unused():
    return never_needed
'''
    program = parse(source)
    assert program.free_names('f') == {'offset', 'range', 'helper', 'pi', 'lookup'}
    assert program.free_names('f') - program.language.names == {'offset', 'helper', 'lookup'}
    assert program.free_names('other') == {'lookup'}
    assert program.free_names('unused') == {'never_needed'}
    assert program.select('f').free_names('f') == program.free_names('f')
    lists = parse("def f(xs):\n    return sorted([x for x in xs if x > cut]) + [len(xs)]\n", TCOLL)
    assert lists.free_names('f') == {'sorted', 'len', 'cut'}
    assert lists.free_names('f') - lists.language.names == {'cut'}


def test_standard_builtins_match_the_interpreter():
    math_names = {'log', 'ln', 'exp', 'sqrt', 'abs', 'min', 'max', 'floor', 'ceil', 'round', 'sin', 'cos', 'tan',
                  'int', 'float', 'pi', 'e', 'inf'}
    assert set(pseudocode.MATH) == math_names
    assert set(pseudocode.COLLECTION_FUNCTIONS) == {'len', 'sum'} and not hasattr(pseudocode, 'UNIF')
    assert pseudocode.CORE_NAMES == {'range'} and pseudocode.CORE_COLLECTION_NAMES == {'sorted', 'list', 'set'}
    assert not hasattr(pseudocode, 'BUILTINS')
    for language in (BASE, WITH_COLLECTIONS, TBASE, TCOLL):
        for name in language.names - {'range'}:
            call = type(language.builtins.get(name)) is not Constant
            source = f"def f():\n    return {name}(1, 2, 3, 4, 5)\n" if call else f"def f():\n    return {name}\n"
            try:                 # defined: whatever happens, it is not "undefined"
                parse(source, language).sample('f', n=1)
            except Failure as failure:
                assert failure.kind != 'undefined', name
    for name in ('len', 'sum', 'sorted', 'list', 'set', 'unif', 'coin'):    # not in the scalar base language
        with pytest.raises(Failure) as info:
            run(f"def f():\n    return {name}(1)\n", language=BASE)
        assert info.value.kind == 'undefined'
    for name in ('log', 'pi'):                                              # nothing at all in a bare language
        with pytest.raises(Failure) as info:
            run(f"def f():\n    return {name}\n", language=Language())
        assert info.value.kind == 'undefined'
    with pytest.raises(Failure) as info:
        run("def f():\n    return dice(6)\n")
    assert info.value.kind == 'undefined'


# ---- language options ---------------------------------------------------------------

VEC = dataclasses.replace(TCOLL, vectors=True)


def vec(source, *args, language=VEC, **kw):
    return one(source, *args, language=language, **kw)


def test_language_options_and_names():
    assert BASE.name == 'base' and WITH_COLLECTIONS.name == 'collections'
    assert (BASE.collections, BASE.vectors, BASE.extra) == (False, False, "")
    assert dict(BASE.builtins) == dict(pseudocode.MATH)
    assert dict(WITH_COLLECTIONS.builtins) == {**pseudocode.MATH, **pseudocode.COLLECTION_FUNCTIONS}
    vec = dataclasses.replace(WITH_COLLECTIONS, vectors=True)
    assert vec.name == 'collections+vectors'
    assert dataclasses.replace(vec, vectors=False).name == 'collections'             # re-derived
    mine = dataclasses.replace(vec, name='course')
    assert mine.name == 'course' and dataclasses.replace(mine, vectors=False).name == 'course'   # kept
    assert dataclasses.replace(BASE, extra="- More.").name.startswith('base+extra-')
    assert TCOLL.name == 'collections+with-coin,die,unif' and TBASE.name == 'base+with-coin,die,unif'
    assert Language().name.startswith('base+names-')                                  # no builtins at all
    # deterministic: names and equality do not depend on function identity or order
    other = {'die': Primitive(lambda rng, n: 1, "rolls", random=True),
             'coin': Primitive(lambda rng, p: 0, "flips", random=True), 'unif': COIN}
    assert WITH_COLLECTIONS.with_builtins(**other).name == TCOLL.name
    assert WITH_COLLECTIONS.with_builtins(die=DIE, coin=COIN, **UNIF) == TCOLL
    assert list(TCOLL.builtins)[-3:] == ['unif', 'coin', 'die']                       # order kept
    assert Language(collections=True, builtins=WITH_COLLECTIONS.builtins) == WITH_COLLECTIONS
    assert hash(TCOLL) == hash(dataclasses.replace(TCOLL))
    for bad in (dict(vectors=True), dict(extra=3), dict(builtins={'coin': coin_fn}), dict(builtins={'pi': 3.14}),
                dict(builtins={'range': COIN}), dict(builtins={'sorted': COIN}), dict(builtins={'print': COIN}),
                dict(builtins={'two words': COIN}), dict(builtins={'__x': COIN})):
        with pytest.raises((ValueError, TypeError)):
            Language(**bad)
    for args, kw in (((3, "doc"), {}), ((coin_fn, ""), {}), ((coin_fn, "two\nlines"), {}),
                     ((math.sqrt, "x"), {'arity': (2, 1)}), ((lambda: 1, "x"), {'random': True}),
                     ((coin_fn, "x"), {'group': ""})):
        with pytest.raises((ValueError, TypeError)):
            Primitive(*args, **kw)
    with pytest.raises(TypeError):
        Constant("3", "three")
    with pytest.raises(dataclasses.FrozenInstanceError):
        vec.vectors = False
    with pytest.raises(TypeError):
        TCOLL.builtins['coin'] = DIE                     # frozen
    assert TBASE.names == {'range'} | set(pseudocode.MATH) | {'unif', 'coin', 'die'}
    assert TCOLL.names == TBASE.names | {'sorted', 'list', 'set', 'len', 'sum'}
    assert pseudocode.parse("def f():\n    return 1\n").language is BASE              # the default


def test_primitive_arity():
    assert COIN.arity == (1, 1) and DIE.arity == (1, 1)
    assert Primitive(lambda a, b=1: a, "x").arity == (1, 2)
    assert Primitive(lambda rng, *xs: 0, "x", random=True).arity == (0, None)
    assert Primitive(math.sqrt, "x", arity=1).arity == (1, 1)
    assert UNIF['unif'].arity == (0, 2) and pseudocode.MATH['max'].arity == (1, None)
    course = BASE.with_builtins(two=Primitive(lambda a, b: a + b, "`two(a, b)` adds"))
    assert one("def f():\n    return two(1, 2)\n", language=course) == 3
    for call, message in (("two(1)", "two() takes 2 arguments, got 1"),
                          ("max()", "max() takes at least 1 arguments, got 0"),
                          ("round(1, 2, 3)", "round() takes 1 to 2 arguments, got 3")):
        with pytest.raises(Failure) as info:
            one(f"def f():\n    return {call}\n", language=course)
        assert info.value.kind == 'arity' and message in str(info.value) and info.value.line == 2
    with pytest.raises(Failure) as info:                 # the entry point too
        parse("def g():\n    return 1\n", course).sample('two', (1,), n=1)
    assert info.value.kind == 'arity'


def test_descriptions_follow_the_options():
    plain = WITH_COLLECTIONS.description
    assert "`+` to concatenate" in plain and "`*` to repeat" in plain and "Vectors" not in plain
    assert "`xs[a:b]`" in plain
    v = VEC.description
    assert "Vectors:" in v and "`p + q`" in v and "`c * p`" in v and "`-p`" in v
    assert "`+` to concatenate" not in v and "`+` does not join lists" in v and "`xs.extend(ys)`" in v
    extra = "- Course: `turn(p, a)` turns a point."
    assert dataclasses.replace(VEC, extra=extra).description == v + "\n" + extra
    assert dataclasses.replace(VEC, extra="\n" + extra).description == v + "\n" + extra
    assert len(v) < 3000
    bare = Language().description                        # syntax only
    for word in ('unif', 'log', 'Math', 'pi', 'Randomness'):
        assert word not in bare
    assert "- Math: `log` (natural); `ln`" in BASE.description and "- Constants: `pi`; `e`; `inf`." in BASE.description
    assert "unif" not in BASE.description and "Collection functions" not in BASE.description
    assert "- Collection functions: `len(xs)`; `sum(xs)`." in WITH_COLLECTIONS.description


def test_language_builtins_are_described_and_callable():
    text = TBASE.description
    assert ("- Randomness: `unif()` is uniform on [0, 1); `unif(a, b)` is uniform on [a, b); "
            "`coin(p)` is True with probability p; `die(n)` is a uniform whole number from 1 to n.\n") in text
    assert text.index("- Math") < text.index("- Randomness") < text.index("- Functions may call")
    alone = BASE.with_builtins(turn=Primitive(lambda p: p, "`turn(p)` turns p a quarter"),
                               k=Constant(7, "`k` is seven"))
    assert "\n- `turn(p)` turns p a quarter.\n- `k` is seven.\n" in alone.description    # ungrouped: own lines
    assert one("def f():\n    return turn(k) + k\n", language=alone) == 14
    flips = run("def f():\n    return coin(0.25)\n", n=2000)
    assert abs(sum(flips) / 2000 - 0.25) < 0.04
    program = parse("def f():\n    return coin(1), die(1)\n")
    assert program.sample('f', n=2) == [(True, 1), (True, 1)]
    assert program.free_names('f') - program.language.names == frozenset()
    for name in ('k', 'pi'):                                 # constants are values, not functions
        with pytest.raises(Failure) as info:
            one(f"def f():\n    return {name}(1)\n", language=alone)
        assert info.value.kind == 'type'
    with pytest.raises(Failure) as info:
        one("def f():\n    return turn + 1\n", language=alone)
    assert info.value.kind == 'type'


def test_builtin_override_order():
    two = Primitive(lambda: 2, "`two()` is 2")
    course = BASE.with_builtins(two=two)
    program = parse("def two():\n    return 0\n\ndef f():\n    return two()\n", course)
    assert program.sample('f', n=1) == [0]                                   # the student's beats the language's
    assert parse("def f():\n    return two()\n", course).sample('f', n=1) == [2]
    assert program.sample('f', n=1, primitives={'two': lambda rng: 3}) == [3]          # the call's beats both
    assert program.sample('f', n=1, primitives={'two': Primitive(lambda: 4, "x")}) == [4]
    assert parse("def g():\n    return 1\n", course).sample('two', n=1) == [2]       # a builtin as the entry
    assert program.sample('f', n=1, primitives={'other': lambda rng: 9}) == [0]        # adds, keeps the rest
    assert parse("def log(x):\n    return 5\n\ndef f():\n    return log(1)\n").sample('f', n=1) == [5]
    with pytest.raises(TypeError):
        program.sample('f', n=1, primitives={'two': 5})


def test_primitive_errors_keep_the_call_site():
    def strict(x):
        if x < 0:
            raise Failure("needs a nonnegative number", kind='domain')
        if x > 10:
            raise ValueError("too big")
        return x
    course = BASE.with_builtins(strict=Primitive(strict, "`strict(x)`"),
                                safe=Primitive(strict, "`safe(x)`", numeric=True))
    with pytest.raises(Failure) as info:
        one("def f():\n    y = 1\n    return strict(-y)\n", language=course)
    assert (info.value.kind, info.value.line, info.value.expr) == ('domain', 3, 'strict(-y)')
    assert str(info.value) == "line 3: strict(-y): needs a nonnegative number"
    with pytest.raises(Failure) as info:
        one("def f():\n    return strict(11)\n", language=course)
    assert info.value.kind == 'other' and "strict(11) raised ValueError: too big" in str(info.value)
    with pytest.raises(Failure) as info:                 # numeric: the math wording
        one("def f():\n    return safe(11)\n", language=course)
    assert info.value.kind == 'domain' and str(info.value) == "line 2: safe(11): safe of 11"


def test_vector_arithmetic():
    assert vec("def f():\n    return [1, 2] + [3, 4.5]\n") == [4, 6.5]
    assert vec("def f():\n    return [1, 2] - [3, 4]\n") == [-2, -2]
    assert vec("def f():\n    return [1, 2] * [3, 4]\n") == [3, 8]
    assert vec("def f():\n    return [1, 2] / [4, 8]\n") == [0.25, 0.25]
    assert vec("def f():\n    return 2 * [1, 2], [1, 2] * 3, [1, 2] / 2\n") == ([2, 4], [3, 6], [0.5, 1.0])
    assert vec("def f():\n    p = [1, 2]\n    return -p, p\n") == ([-1, -2], [1, 2])
    assert vec("def f():\n    p = [1, 2]\n    q = p\n    p += [10, 10]\n    p *= 2\n    return p, q\n") \
        == ([22, 24], [1, 2])
    assert vec("def f():\n    xs = [[0, 0], [1, 1]]\n    xs[1] += [1, 2]\n    return xs\n") == [[0, 0], [2, 3]]
    assert vec("def f():\n    a, b = 1, 2\n    return [a, b] + (3, 4)\n") == [4, 6]       # a tuple of numbers too
    assert vec("def f():\n    xs = [1]\n    xs.extend([2, 3])\n    xs.append(4)\n    return xs\n") == [1, 2, 3, 4]
    assert vec("def f():\n    return 2 + 3 * 4 - 6 / 3\n") == 12.0          # numbers as before
    assert vec("def f():\n    return [1, 2] == [1, 2]\n") is True
    xs = parse("def f():\n    return [unif(), unif()] * 2 - [1, 1]\n", VEC).sample('f', n=50)
    assert all(-1 <= x < 1 for p in xs for x in p)


@pytest.mark.parametrize('source, needle', [
    ("def f():\n    return [1, 2] + [1, 2, 3]\n", "different lengths (2 and 3)"),
    ("def f():\n    return [1, 2] - []\n", "different lengths"),
    ("def f():\n    return [1, [2]] + [1, 2]\n", "vectors must hold numbers"),
    ("def f():\n    return -[1, None]\n", "vectors must hold numbers"),
    ("def f():\n    return [1, 2] + 1\n", "cannot compute"),
    ("def f():\n    return 1 - [1, 2]\n", "cannot compute"),
    ("def f():\n    return 1 / [1, 2]\n", "cannot compute"),
    ("def f():\n    return [1, 2] // 2\n", "cannot compute"),
    ("def f():\n    return [1, 2] ** 2\n", "cannot compute"),
    ("def f():\n    return {1, 2} + {3, 4}\n", "cannot compute"),
])
def test_vector_mismatches_are_type_failures(source, needle):
    with pytest.raises(Failure) as info:
        vec(source)
    assert info.value.kind == 'type' and needle in str(info.value) and info.value.line == 2


def test_vectors_turn_off_concatenation_and_repetition():
    assert coll("def f():\n    return [1, 2] + [3], [0] * 3\n") == ([1, 2, 3], [0, 0, 0])
    with pytest.raises(Failure) as info:
        vec("def f():\n    return [1, 2] + [3]\n")
    assert info.value.kind == 'type'
    assert vec("def f():\n    return [0] * 3\n") == [0]           # scales instead of repeating
    with pytest.raises(Failure) as info:
        vec("def f():\n    return [1, 2] / [1, 0]\n")
    assert info.value.kind == 'domain'


# ---- primitives: limitations ---------------------------------------------------------

@pytest.mark.parametrize('error', [pseudocode.Limitation("points must be 2-D"), Unsupported("points must be 2-D")])
def test_primitive_limitation_is_unsupported_with_line(error):
    def turn(rng, p):
        raise error
    source = "def f():\n    p = [1, 2, 3]\n    return turn(p)\n"
    for prim in (turn, Primitive(turn, "`turn(p)` turns p", random=True)):
        with pytest.raises(Failure) as info:
            parse(source, TCOLL).sample('f', n=1, primitives={'turn': prim})
        assert info.value.kind == 'unsupported' and info.value.line == 3
        assert info.value.expr == 'turn(p)' and "points must be 2-D" in str(info.value)
    with pytest.raises(Failure) as info:                 # the entry point itself
        parse(source, TCOLL).sample('turn', ([1],), n=1, primitives={'turn': turn})
    assert info.value.kind == 'unsupported'

    def broken(rng, p):
        raise ValueError("bad")
    with pytest.raises(Failure) as info:                 # anything else stays 'other'
        parse(source, TCOLL).sample('f', n=1, primitives={'turn': broken})
    assert info.value.kind == 'other' and info.value.line == 3


# ---- program structure ----------------------------------------------------------------

STRUCTURE = '''\
def accept():
    while True:
        x = unif()
        if coin(x):
            return x

def counted(n):
    total = 0
    for i in range(n):
        total += unif()
    return total

def via_helper():
    return accept() + 1

def plain():
    return counted(3)

def ping(n):
    return 0 if n == 0 else pong(n - 1)

def pong(n):
    return ping(n)

def bounce():
    return ping(4) + plain()

def fact(n):
    return 1 if n < 2 else n * fact(n - 1)
'''


def test_has_loop_and_is_recursive():
    program = parse(STRUCTURE)
    assert program.has_loop('accept') and program.has_loop('via_helper')
    assert not program.has_loop('plain') and not program.has_loop('counted')
    assert program.has_loop('plain', kinds=('while', 'for')) and program.has_loop('counted', kinds=('for',))
    assert not program.has_loop('via_helper', kinds=('for',)) and program.has_loop('via_helper', 'while')
    assert not program.has_loop('missing')
    with pytest.raises(ValueError):
        program.has_loop('accept', kinds=('until',))
    assert parse("def f(xs):\n    return [x for x in xs]\n", TCOLL).has_loop('f', ('for',))
    assert program.is_recursive('fact') and program.is_recursive('ping') and program.is_recursive('bounce')
    assert not any(program.is_recursive(name) for name in ('accept', 'counted', 'via_helper', 'plain', 'missing'))
    assert not parse("def g():\n    return 1\n\ndef f():\n    return g() + g()\n").is_recursive('f')
