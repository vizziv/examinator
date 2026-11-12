"""Key entries for the standard math check, with this exam's notation.

A single-field entry is a callable `answer -> (outcome, note)`. A joint entry
sits under a tuple of labels and is a callable `(answers) -> [(outcome, note), ...]`,
one result per label, where a blank answer arrives as `None`:

    {'??_5': '1/2', ('??_6', '??_7'): EitherOrder(point('-2', '1'), point('2', '1'))}
"""
from dataclasses import dataclass
from typing import Any

import sympy

from examinator.std import math

FUNCTIONS = ('sqrt',)       # answers are numbers: arithmetic, sqrt and pi


def parse(text: str, symbols=()) -> sympy.Expr:
    return math.parse_expr(text, symbols=symbols, functions=FUNCTIONS)


def is_none(answer: str) -> bool:
    """The student wrote that no value works."""
    return math.normalize_text(answer) in ('none', 'none works', 'no value', 'no value works')


def same(got: sympy.Expr, want: sympy.Expr) -> tuple[str | None, str]:
    result = math.equal(got, want)
    return {True: ('correct', ""), False: ('incorrect', ""), None: (None, "cannot decide")}[result]


# --- single-field entries ---------------------------------------------------------

@dataclass(frozen=True)
class Number:
    """A number written as arithmetic. "None" is a wrong answer, not notation."""
    expr: str

    @property
    def label(self):
        return self.expr

    def __call__(self, answer: str) -> tuple[str | None, str]:
        if is_none(answer):
            return 'incorrect', ""
        return same(parse(answer), parse(self.expr))


def number(expr: str) -> Number:
    parse(expr)         # a key that does not parse is a programming error
    return Number(expr)


def split(text: str) -> list[str]:
    """The comma-separated parts of `[a, b]`, `(a, b)` or `a, b` (top-level commas only)."""
    text = text.strip()
    if len(text) >= 2 and (text[0], text[-1]) in (('[', ']'), ('(', ')')) and _wraps(text):
        text = text[1:-1]
    parts, depth, start = [], 0, 0
    for i, c in enumerate(text):
        depth += (c in '([') - (c in ')]')
        if c == ',' and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts]


def _wraps(text: str) -> bool:
    """The first bracket closes at the very end: `(1, 2)`, but not `(1)+(2)`."""
    depth = 0
    for i, c in enumerate(text):
        depth += (c in '([') - (c in ')]')
        if depth == 0:
            return i == len(text) - 1
    return False


@dataclass(frozen=True)
class Point:
    """A list or tuple of numbers, compared coordinate by coordinate."""
    coords: tuple[str, ...]

    @property
    def label(self):
        return f"[{', '.join(self.coords)}]"

    def __call__(self, answer: str) -> tuple[str | None, str]:
        if is_none(answer):
            return 'incorrect', ""
        got = split(answer)
        if len(got) != len(self.coords):
            return 'incorrect', f"expected {len(self.coords)} numbers"
        results = [same(parse(g), parse(want)) for g, want in zip(got, self.coords)]
        outcomes = [outcome for outcome, _ in results]
        if 'incorrect' in outcomes:
            return 'incorrect', ""
        return (None, "cannot decide") if None in outcomes else ('correct', "")


def point(*coords: str) -> Point:
    for c in coords:
        parse(c)
    return Point(tuple(coords))


_FOLD = str.maketrans({'≤': '<=', '≥': '>=', 'δ': 'delta', '−': '-', '–': '-', '’': "'", '*': None, ' ': None})


def fold(text: str) -> str:
    """Option text as compared: ASCII comparison signs, no spaces, no leading "it's"."""
    s = math.normalize_text(text).translate(_FOLD)
    for lead in ("it's", "its"):
        s = s.removeprefix(lead)
    return s


@dataclass(frozen=True)
class Option:
    """The n-th printed option: its text, its number, 'option n' or its letter."""
    n: int
    texts: tuple[str, ...]

    @property
    def label(self):
        return f"{self.n}: {self.texts[0]}"

    def __call__(self, answer: str) -> str:
        accepted = {fold(t) for t in (*self.texts, str(self.n), f'option {self.n}', 'ABCDEFGHI'[self.n - 1])}
        return 'correct' if fold(answer) in accepted else 'incorrect'


# --- joint entries ------------------------------------------------------------------

@dataclass(frozen=True)
class EitherOrder:
    """Two fields that must hold these two entries, in either order."""
    first: Any
    second: Any

    @property
    def label(self):
        return f"{math.show_entry(self.first)} and {math.show_entry(self.second)}, either order"

    def __call__(self, answers):
        def results(entries):
            return [('blank', "") if a is None else math.check_field(a, e, functions=FUNCTIONS)
                    for a, e in zip(answers, entries)]
        straight, swapped = results((self.first, self.second)), results((self.second, self.first))
        score = lambda rs: sum(outcome == 'correct' for outcome, _ in rs)       # noqa: E731
        return swapped if score(swapped) > score(straight) else straight


def value(text: str) -> float:
    """A number as a float; anything with a variable in it is unsupported."""
    v = parse(text).evalf(30)
    if not (v.is_Float or v.is_Rational):
        raise math.Unsupported("not a number")
    return float(v)


@dataclass(frozen=True)
class Interval:
    """Fields a and b of `[X + a, X + b]`: correct when b - a is `width` and
    -b lies in [lo, hi] (every such interval is equally narrow)."""
    width: str
    lo: str
    hi: str

    @property
    def label(self):
        w, lo, hi = (sympy.nsimplify(parse(t)) for t in (self.width, self.lo, self.hi))
        return f"a = {-(lo + w)}, b = {-lo}" if lo == hi else f"b - a = {w}, {-hi} <= b <= {-lo}"

    def __call__(self, answers):
        w, lo, hi = value(self.width), value(self.lo), value(self.hi)
        eps = 1e-9
        limits = ((-(hi + w), -(lo + w)), (-hi, -lo))       # where a and b may each lie
        got, out = [], []
        for answer, (low, high) in zip(answers, limits):
            if answer is None:
                got.append(None)
                out.append(('blank', ""))
                continue
            try:
                v = None if is_none(answer) else value(answer)
            except math.Unsupported as e:
                got.append(None)
                out.append((None, str(e)))
                continue
            got.append(v)
            out.append(('correct', "") if v is not None and low - eps <= v <= high + eps else ('incorrect', ""))
        if all(outcome == 'correct' for outcome, _ in out) and abs(got[1] - got[0] - w) > eps:
            out = [('incorrect', f"b - a is not {self.width}")] * 2
        return out
