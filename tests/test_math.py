"""`examinator.std.math`: parser, equality, key comparison. Synthetic inputs only."""
import re
import time
from pathlib import Path

import pytest
import sympy

from examinator.core.evidence import EvidenceView
from examinator.std import math as m
from examinator.std.math import Approx, Choice, Unsupported, check_answers, check_field, equal, parse_expr

POS = {'a': 'positive', 'b': 'positive'}


def eq(x, y, symbols=POS, functions=None):
    return equal(parse_expr(x, symbols=symbols, functions=functions),
                 parse_expr(y, symbols=symbols, functions=functions))


# --- equality -----------------------------------------------------------------

@pytest.mark.parametrize('x, y, symbols, functions', [
    ('2*b*(1-a)', '2b(1-a)', POS, []),
    ('2b(1-a)', '2*b - 2*a*b', POS, []),
    ('6/25**3', '6/15625', (), None),
    ('1/2', '0.5', (), None),
    ('.5', '2/4', (), None),
    ('sqrt(a**2)', 'a', POS, None),
    ('log(exp(a))', 'a', {'a': 'real'}, None),
    ('ln(e^a)', 'a', {'a': 'real'}, None),
    ('sin(a)**2 + cos(a)**2', '1', POS, None),
    ('1/(1+1/a)', 'a/(a+1)', POS, None),
    ('(1-a)^10', '(a-1)**10', POS, None),
    ('2 × a − b', '2*a - b', POS, None),
    ('2π', '2*pi', (), None),
])
def test_equivalent_forms(x, y, symbols, functions):
    assert eq(x, y, symbols, functions) is True


@pytest.mark.parametrize('x, y', [
    ('b*(1-a)', 'a*(1-b)'),
    ('12', '16'),
    ('a', 'a**2'),
    ('1/3', '0.3333333333333'),
    ('10**1000', '10**1000 + 1'),
    ('1/(a-b)', '1/(b-a)'),
])
def test_mismatches(x, y):
    assert eq(x, y) is False


def test_assumptions_matter():
    assert eq('sqrt(a**2)', 'a', {'a': 'positive'}) is True
    assert eq('sqrt(a**2)', 'a', {'a': 'real'}) is False        # differs for negative a
    assert eq('sqrt(a**2)', 'abs(a)', {'a': 'real'}) is True


def test_undecided_comparisons():
    # Agrees wherever it is real, but SymPy has no proof without assumptions.
    assert eq('log(a*b)', 'log(a) + log(b)', ['a', 'b']) is None
    # Division by something that is zero: no crash, no verdict.
    assert eq('1/(a-a)', '1') is None
    assert eq('0/0', '1', ()) is None
    # Never real under the assumptions and not provable.
    assert eq('log(-a)', 'log(-a) + sin(a)**2 + cos(a)**2 - 1 + 0*b') in (True, None)


def test_equal_is_deterministic_and_fast():
    t = time.perf_counter()
    results = [eq('min(a,b) + max(a,b)', 'a + b') for _ in range(2)]
    assert results[0] == results[1] and results[0] is not False
    assert eq('(a+b+1)**2000', '(a+b+1)**1999 * (a+b+1)') is True
    assert eq('(a+b+1)**500', '(a+b)**500') is False
    assert time.perf_counter() - t < 5


# --- parser -------------------------------------------------------------------

def test_numbers_are_exact():
    assert parse_expr('0.1') == sympy.Rational(1, 10)
    assert parse_expr('1.50') == sympy.Rational(3, 2)
    assert parse_expr('2^3^2') == 512
    assert parse_expr('-2**2') == -4
    assert parse_expr('2**-1') == sympy.Rational(1, 2)
    assert parse_expr('--3 + +1') == 4
    assert parse_expr('e') == sympy.E and parse_expr('pi') == sympy.pi


def test_symbol_assumptions():
    a = parse_expr('a', symbols={'a': 'positive'})
    assert a.is_positive
    assert parse_expr('a', symbols=['a']).is_positive is None
    n = parse_expr('n', symbols={'n': 'positive integer'})
    assert n.is_integer and n.is_positive
    with pytest.raises(ValueError):
        parse_expr('a', symbols={'a': 'sparkly'})
    assert not isinstance(pytest.raises(ValueError, parse_expr, 'a', symbols={'a': 'odd'}).value, Unsupported)
    # A declared symbol shadows a constant.
    assert parse_expr('e', symbols=['e']) == sympy.Symbol('e')


def test_juxtaposition():
    a, b = sympy.symbols('a b', positive=True)
    p = lambda s, **k: parse_expr(s, symbols=POS, **k)
    assert p('2a') == 2 * a
    assert p('2(a+1)') == 2 * (a + 1)
    assert p('(a)(b)') == a * b
    assert p('a(1-b)') == a * (1 - b)
    assert p('a(1-b)', functions=[]) == a * (1 - b)
    assert p('ab') == a * b
    assert p('2ab') == 2 * a * b
    assert p('ab^2') == a * b**2
    assert p('3a^2(1-b)') == 3 * a**2 * (1 - b)
    assert p('a^2b') == a**2 * b
    assert p('pi a') == sympy.pi * a
    assert p('2 sqrt(a)') == 2 * sympy.sqrt(a)


def test_calls_and_names():
    a = sympy.Symbol('a', positive=True)
    exp_syms = {'e': 'positive', 'x': 'positive', 'p': 'positive', 'a': 'positive'}
    # A whitelisted function stays a call, even when its letters are all symbols.
    assert parse_expr('exp(a)', symbols=exp_syms) == sympy.exp(a)
    assert parse_expr('exp(a)', symbols=POS) == sympy.exp(a)
    # With no functions allowed, the same word is a product of declared letters.
    e, x, p = (sympy.Symbol(n, positive=True) for n in 'exp')
    assert parse_expr('exp(a)', symbols=exp_syms, functions=[]) == e * x * p * a
    # A whole-word symbol is not split.
    assert parse_expr('ab', symbols=['a', 'b', 'ab']) == sympy.Symbol('ab')
    # `pi` is the constant even when p and i are symbols.
    assert parse_expr('pi', symbols=['p', 'i']) == sympy.pi
    assert parse_expr('max(a, 2, 3)', symbols=POS) == sympy.Max(a, 3)
    assert parse_expr('log(8, 2)') == 3
    assert parse_expr('abs(-3)') == 3
    with pytest.raises(Unsupported, match="not allowed"):
        parse_expr('exp(a)', symbols=POS, functions=['sqrt'])
    with pytest.raises(Unsupported):
        parse_expr('sqrt(a, b)', symbols=POS)
    with pytest.raises(Unsupported):
        parse_expr('sin a', symbols=POS)
    with pytest.raises(ValueError):
        parse_expr('a', symbols=POS, functions=['gamma'])


@pytest.mark.parametrize('text', [
    'c', 'a + c', 'abc',                       # unknown symbols
    'a.b', 'a[0]', "__import__('os')", 'lambda a: a', 'a.__class__', '().__class__',
    '__builtins__', 'open(a)', 'a if b else a', 'a == b', 'a < b', 'a = 2', '"a"', 'a; b',
    'a,b', '[a]', '{a}', 'a!', '50%', 'a_1', 'a1', '1e5', '0x10', '1_000', 'a//b', 'a % b',
    '', '   ', '(', 'a +', '* a', 'a b)', '()', 'sqrt()', 'a 2', '2 3', '1.', 'a**', '\\frac{a}{b}',
    'a/2b', '1/2 a', 'a/b(1-a)', '2^ab', 'a**b(1-a)',     # juxtaposition with two readings
])
def test_rejected(text):
    with pytest.raises(Unsupported) as e:
        parse_expr(text, symbols=POS)
    assert len(str(e.value)) < 60


def test_limits():
    t = time.perf_counter()
    for text in ['9**9**9', '9^9^9', '2**(-9**9)', 'a**(10**10)', '((10**2000)**2000)**2000',
                 '(1/10**2000)**2000', '2**2**2**2**2**2', '1+' * 5000 + '1', 'a' * 10_000,
                 '(' * 200 + 'a' + ')' * 200, '(' * 5000 + 'a' + ')' * 5000,
                 'a' + '**a' * 100, 'sqrt(' * 60 + 'a' + ')' * 60]:
        with pytest.raises(Unsupported):
            parse_expr(text, symbols=POS)
    assert parse_expr('10**1000') == sympy.Integer(10) ** 1000
    assert parse_expr('10**2000 * 10**2000') == sympy.Integer(10) ** 4000
    assert parse_expr('(' * 30 + 'a' + ')' * 30, symbols=POS) == sympy.Symbol('a', positive=True)
    assert parse_expr('-' * 301 + 'a', symbols=POS) == -sympy.Symbol('a', positive=True)
    assert parse_expr('1/0') == sympy.zoo                   # parses; `equal` will not decide it
    assert eq('exp(exp(exp(exp(10))))', '1') is None        # not evaluable, not a crash
    assert time.perf_counter() - t < 3


def test_no_eval_in_module():
    source = Path(m.__file__).read_text()
    assert not re.search(r'(?<![\w.])(eval|exec|compile)\s*\(|sympify|__import__|import ast|sympy\.pars', source)


# --- text and choices ---------------------------------------------------------

def test_normalize_text():
    for s in ['C', 'c', '(C)', 'c.', ' C ', '[c]', '"C"', "'c'", '(c).', '( C )']:
        assert m.normalize_text(s) == 'c', s
    assert m.normalize_text('  Option   3 ') == 'option 3'
    assert m.normalize_text('((c))') == '(c)'               # one layer only
    assert m.normalize_text('') == ''


def test_choice():
    c = Choice('C', '3', 'option 3')
    assert c.accepted == ('C', '3', 'option 3')
    for s in ['c', '(C)', 'Option  3', '3.', ' 3 ']:
        assert c.matches(s), s
    for s in ['D', '33', 'option 4', '']:
        assert not c.matches(s), s
    assert c == Choice('C', '3', 'option 3') and hash(c) == hash(Choice('C', '3', 'option 3'))
    assert check_field('(c)', c) == ('correct', '')
    assert check_field('D', c) == ('incorrect', '')


# --- fields -------------------------------------------------------------------

def test_check_field_expressions():
    assert check_field('2b(1-a)', '2*b - 2*a*b', symbols=POS, functions=[]) == ('correct', '')
    assert check_field('a(1-b)', 'b*(1-a)', symbols=POS, functions=[])[0] == 'incorrect'
    assert check_field('12', '16')[0] == 'incorrect'
    for blank in [None, '', '   ', '\n']:
        assert check_field(blank, 'a', symbols=POS) == ('blank', '')


@pytest.mark.parametrize('answer', [
    'c*(1-a)', 'a.b', 'a[0]', "__import__('os')", 'lambda: a', 'a/2b', '9**9**9',
    'a [REVIEW: a or 9?]', '[REVIEW]', 'x' * 10_000,
])
def test_check_field_undecided(answer):
    outcome, note = check_field(answer, 'a*b', symbols=POS)
    assert outcome is None
    assert note and len(note) < 60


def test_check_field_notes():
    assert check_field('a [REVIEW: unclear]', 'a', symbols=POS) == (None, 'review')
    assert check_field('[REVIEW: unclear] C', Choice('C')) == (None, 'review')
    assert check_field('c', 'a', symbols=POS) == (None, "unknown symbol: c")
    assert check_field('1/(a-a)', 'a', symbols=POS)[0] is None
    assert check_field('log(a*b)', 'log(a) + log(b)', symbols=['a', 'b'])[0] is None


def test_bad_key_raises():
    with pytest.raises(ValueError) as e:
        check_field('a', 'a +* q', symbols=POS)
    assert not isinstance(e.value, Unsupported)
    with pytest.raises(TypeError):
        check_field('a', 12, symbols=POS)
    with pytest.raises(ValueError):
        check_field('a', lambda s: 'maybe')


def test_approx():
    assert check_field('3.14', Approx(3.14159, tol=0.01))[0] == 'correct'
    assert check_field('3.2', Approx(3.14159, tol=0.01))[0] == 'incorrect'
    assert check_field('22/7', Approx(3.14159, rel=0.001))[0] == 'correct'
    assert check_field('3.1', Approx(3.14159, rel=0.001))[0] == 'incorrect'
    assert check_field('pi', Approx(3.14159, tol=1e-5))[0] == 'correct'
    assert check_field('sqrt(2)/2', Approx(0.7071, tol=1e-4))[0] == 'correct'
    assert check_field('0.5', Approx(0.5))[0] == 'correct'
    assert check_field('1/3', Approx(1 / 3))[0] == 'correct'
    assert check_field('0.33', Approx(1 / 3))[0] == 'incorrect'
    assert check_field('10**1000', Approx(1.0))[0] == 'incorrect'
    assert check_field('2a', Approx(2.0), symbols=POS) == (None, "not a number")
    assert check_field('1/0', Approx(2.0)) == (None, "not a number")
    assert check_field('sqrt(-1)', Approx(2.0))[0] is None
    assert check_field('two', Approx(2.0))[0] is None
    assert check_field('', Approx(2.0))[0] == 'blank'
    assert Approx(2.0) == Approx(2.0, 1e-9, 0.0)


def test_callable_entries():
    def pair(answer):                                    # an exam-defined compound field
        parts = [parse_expr(p, symbols=POS) for p in answer.split(';')]
        if len(parts) != 2:
            return None, "need two values"
        ok = equal(parts[0] + parts[1], parse_expr('a + b', symbols=POS))
        return 'correct' if ok else 'incorrect'

    assert check_field('a; b', pair) == ('correct', '')
    assert check_field('b; a', pair) == ('correct', '')
    assert check_field('a; a', pair) == ('incorrect', '')
    assert check_field('a', pair) == (None, "need two values")
    assert check_field('a; zz', pair) == (None, "unknown symbol: zz")       # Unsupported inside
    assert check_field(None, pair) == ('blank', '')
    assert check_field('a [REVIEW: ?]; b', pair) == (None, 'review')
    assert check_field('x', lambda s: 'blank') == ('blank', '')
    assert check_field('x', lambda s: None) == (None, '')


# --- check_answers and the grade rule -----------------------------------------

def test_check_answers_shape():
    key = {'1': 'a*b', '2': Choice('B', '2'), '3': Approx(0.25, tol=1e-6),
           '4': lambda s: ('correct', "ok") if s == 'yes' else 'incorrect', '5': 'a', '6': 'b'}
    answers = {'6': '', '2': '(b)', '1': 'ba', '3': '1/4', '4': 'yes', 'extra': 'ignored'}
    out = check_answers(answers, key, symbols=POS)
    assert list(out) == ['fields']
    assert list(out['fields']) == ['1', '2', '3', '4', '5', '6']
    assert out['fields']['1'] == {'answer': 'ba', 'outcome': 'correct', 'note': ''}
    assert out['fields']['4'] == {'answer': 'yes', 'outcome': 'correct', 'note': 'ok'}
    assert out['fields']['5'] == {'answer': None, 'outcome': 'blank', 'note': ''}
    assert out['fields']['6'] == {'answer': None, 'outcome': 'blank', 'note': ''}
    assert [f['outcome'] for f in out['fields'].values()] == ['correct'] * 4 + ['blank'] * 2
    assert m.all_correct(EvidenceView({'data': out})) == 'incorrect'

    out = check_answers({'1': 'a b [REVIEW: b or 6]', '2': 'D'}, {'1': 'a*b', '2': Choice('B')}, symbols=POS)
    assert out['fields']['1'] == {'answer': 'a b [REVIEW: b or 6]', 'outcome': None, 'note': 'review'}
    assert out['fields']['2']['outcome'] == 'incorrect'


def test_check_answers_through_the_stage_types():
    import json
    out = check_answers({'1': None}, {'1': 'a'}, symbols=('a',), functions=('sqrt',))
    assert json.loads(json.dumps(out)) == out


@pytest.mark.parametrize('outcomes, expected', [
    (['correct', 'correct'], 'correct'),
    (['correct'], 'correct'),
    (['correct', 'incorrect'], 'incorrect'),
    (['incorrect', None], 'incorrect'),
    (['incorrect', 'blank'], 'incorrect'),
    (['correct', None], None),
    (['blank', None], None),
    ([None], None),
    (['blank', 'blank'], 'blank'),
    (['blank'], 'blank'),
    (['correct', 'blank'], 'incorrect'),
    ([], None),
])
def test_all_correct(outcomes, expected):
    data = {'fields': {str(i): {'answer': 'x', 'outcome': o} for i, o in enumerate(outcomes)}}
    assert m.all_correct(EvidenceView({'data': data})) == expected


def test_all_correct_on_a_check_that_did_not_run():
    assert m.all_correct(EvidenceView({'data': {'undecided': "timeout"}})) is None
