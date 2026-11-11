"""Key entries for question 3: expressions in subscripted quantities, and a
distribution's name.

Both are callables `answer -> (outcome, note)`. `s` and `w` are indexed symbols
of the expression parser: `s[i-1]` and `s[i]` are unrelated unknowns, and the
standard expression comparison applies. The other subscript notations
(`s_{i-1}`, `s_(i-1)`, `s_i`) are rewritten to brackets first.
"""
import re
from dataclasses import dataclass

import sympy

from examinator.std import math

SYMBOLS = {'s': 'positive indexed', 'w': 'positive indexed',
           **{name: 'positive' for name in ('i', 'n', 'k', 'j', 'u')}}

_SUB = re.compile(r'(?<![A-Za-z0-9_])([sw])\s*(?:_?\s*\{([^{}]*)\}|_?\s*\(([^()]*)\)|_([A-Za-z0-9]+))', re.I)
_LOOSE = re.compile(r'(?<![A-Za-z0-9_])[sw]_[A-Za-z0-9]+\s*[-+]\s*\d', re.I)


def read(text: str) -> sympy.Expr:
    """An answer as an expression. Raises `math.Unsupported` for other notation."""
    if _LOOSE.search(text):
        raise math.Unsupported("subscript unclear: write s[i-1]")
    brackets = _SUB.sub(lambda m: f"{m[1]}[{next(g for g in m.groups()[1:] if g is not None)}]",
                        text.replace('\\', ''))
    return math.parse_expr(brackets.lower(), symbols=SYMBOLS, functions=())


@dataclass(frozen=True)
class Indexed:
    """An expression key. An answer equal to one of `review` goes to a person."""
    expr: str
    review: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.expr

    def __call__(self, answer: str) -> tuple[str | None, str]:
        got = read(answer)
        same = math.equal(got, read(self.expr))
        if same:
            return 'correct', ""
        for other in self.review:
            if math.equal(got, read(other)):
                return None, f"wrote {other}: accept?"
        return ('incorrect', "") if same is False else (None, "cannot decide")


def indexed(expr: str, *review: str) -> Indexed:
    for text in (expr, *review):
        read(text)          # a key that does not parse is a programming error
    return Indexed(expr, tuple(review))


_UNIFORM = re.compile(r'(the)?(standard|continuous)?(uniform|unif)(distribution)?((on|over)?[(\[]0,1[)\]])?(distribution)?')
_SHORT = re.compile(r'u[(\[]0,1[)\]]')


@dataclass(frozen=True)
class StandardUniform:
    """The uniform distribution on (0, 1), by name. Other uniform-looking answers go to a person."""
    label: str = "Uniform(0, 1)"

    def __call__(self, answer: str) -> tuple[str | None, str]:
        text = re.sub(r'\s+', '', answer.casefold()).rstrip('.')
        if _UNIFORM.fullmatch(text) or _SHORT.fullmatch(text):
            return 'correct', ""
        if 'unif' in text or re.match(r'u\W', text):
            return None, "a uniform, but which?"
        return 'incorrect', ""
