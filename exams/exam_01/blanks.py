"""Key entries for question 3: expressions in subscripted quantities, and a
distribution's name.

Both are callables `answer -> (outcome, note)`. A subscripted quantity such as
`s[i-1]` (also `s_{i-1}`, `s_(i-1)`, `s_i`) becomes one symbol per distinct
subscript, so `s[i-1]` and `s[i]` are unrelated unknowns and the standard
expression comparison applies.
"""
import re
from dataclasses import dataclass

import sympy

from examinator.std import math

LETTERS = 'sw'                          # the subscripted quantities
INDEX = ('i', 'n', 'k', 'j')            # names a subscript may use
PLAIN = ('i', 'n', 'k', 'u')            # names that may also appear on their own

_SUB = re.compile(r'(?<![A-Za-z0-9_])([sw])\s*(?:_?\s*\[([^\[\]]*)\]|_?\s*\{([^{}]*)\}|_?\s*\(([^()]*)\)|_([A-Za-z0-9]+))',
                  re.I)


def name(letter: str, subscript: str) -> str:
    """The symbol for one subscripted quantity; equal subscripts give equal names."""
    index = sympy.expand(math.parse_expr(subscript.lower(), symbols=INDEX, functions=()))
    slug = str(index).replace(' - ', '_minus_').replace(' + ', '_plus_').replace('-', 'minus_')
    return f"{letter.lower()}_{re.sub(r'[^A-Za-z0-9_]', '_', slug)}"


_LOOSE = re.compile(r'(?<![A-Za-z0-9_])[sw]_[A-Za-z0-9]+\s*[-+]\s*\d', re.I)


def read(text: str) -> sympy.Expr:
    """An answer as an expression. Raises `math.Unsupported` for other notation."""
    if _LOOSE.search(text):
        raise math.Unsupported("subscript unclear: write s[i-1]")
    names = set()

    def symbol(m: re.Match) -> str:
        found = name(m[1], next(g for g in m.groups()[1:] if g is not None))
        names.add(found)
        return f' {found} '

    plain = _SUB.sub(symbol, text.replace('\\', ''))
    symbols = {n: 'positive' for n in (*names, *PLAIN)}
    return math.parse_expr(plain.lower(), symbols=symbols, functions=())


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
