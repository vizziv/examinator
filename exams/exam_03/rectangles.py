"""Key entries for question 1: scalars in a and b, and a product of two intervals.

Both are callables `answer -> (outcome, note)`, which is what a math key accepts
besides expression strings and choices.
"""
import re
from dataclasses import dataclass

import sympy

from examinator.std import math

SYMBOLS = {'a': 'positive', 'b': 'positive'}
FUNCTIONS = ()      # no function calls, so juxtaposition is multiplication

_TIMES = re.compile(r'([\])])\s*(?:[x×⨯✕*]|\\times)\s*([\[(])', re.I)


def parse(text: str) -> sympy.Expr:
    return math.parse_expr(text, symbols=SYMBOLS, functions=FUNCTIONS)


def defined(expr: sympy.Expr) -> bool:
    """Every denominator is provably nonzero under the symbols' assumptions alone."""
    return all(p.exp.is_nonnegative or p.base.is_nonzero for p in expr.atoms(sympy.Pow))


def compare(answer: str, expected: str) -> tuple[str | None, str]:
    """One scalar against its key. A denominator that might vanish is undecided."""
    got = parse(answer)
    if not defined(got):
        return None, "denominator may be zero"
    same = math.equal(got, parse(expected))
    return {True: ('correct', ""), False: ('incorrect', ""), None: (None, "cannot decide")}[same]


def split(text: str) -> list[str] | None:
    """The four bounds of `[p, q] x [r, s]`, or None if the text is not of that form."""
    cuts = list(_TIMES.finditer(text))
    if len(cuts) != 1:
        return None
    cut = cuts[0]
    bounds = []
    for interval in (text[:cut.end(1)].strip(), text[cut.start(2):].strip()):
        if len(interval) < 2 or interval[0] not in '[(' or interval[-1] not in '])':
            return None
        parts, depth, start = [], 0, 1
        for i, c in enumerate(interval[1:-1], 1):
            depth += (c in '([') - (c in ')]')
            if c == ',' and depth == 0:
                parts.append(interval[start:i])
                start = i + 1
        parts.append(interval[start:-1])
        if len(parts) != 2:
            return None
        bounds += parts
    return bounds


@dataclass(frozen=True)
class Scalar:
    expr: str

    @property
    def label(self) -> str:
        return self.expr

    def __call__(self, answer: str) -> tuple[str | None, str]:
        return compare(answer, self.expr)


@dataclass(frozen=True)
class Rectangle:
    bounds: tuple[str, str, str, str]

    @property
    def label(self) -> str:
        return "[{}, {}] x [{}, {}]".format(*self.bounds)

    def __call__(self, answer: str) -> tuple[str | None, str]:
        got = split(answer)
        if got is None:
            return None, "not a product of two intervals"
        results = [compare(g, want) for g, want in zip(got, self.bounds)]
        outcomes = [outcome for outcome, _ in results]
        if 'incorrect' in outcomes:
            wrong = ", ".join(str(i) for i, o in enumerate(outcomes, 1) if o == 'incorrect')
            return 'incorrect', f"bound {wrong}"
        if None in outcomes:
            return None, next(note for outcome, note in results if outcome is None)
        return 'correct', ""


def scalar(expr: str) -> Scalar:
    parse(expr)         # a key that does not parse is a programming error
    return Scalar(expr)


def key(lower1: str, upper1: str, lower2: str, upper2: str) -> Rectangle:
    """Key entry for `[lower1, upper1] x [lower2, upper2]`, compared bound by bound."""
    for bound in (lower1, upper1, lower2, upper2):
        parse(bound)
    return Rectangle((lower1, upper1, lower2, upper2))
