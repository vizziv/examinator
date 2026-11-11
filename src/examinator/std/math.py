"""Math for checks: a restricted expression parser, equality, choices, key comparison.

Student-derived text is data. `parse_expr` reads a small arithmetic grammar with
its own tokenizer and builds SymPy objects explicitly; nothing is evaluated as
Python and SymPy never sees raw text. `equal` compares two expressions under the
assumptions their symbols carry. `check_answers` compares parsed answers with a
key, field by field (or a group of fields at once), and is what the standard
`check_math` stage calls.

Outcomes are 'correct', 'incorrect', 'blank' or None. None means "could not
decide" (unsupported notation, an unknown symbol, an inconclusive comparison, a
review marker) and is never used for a demonstrated mismatch.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import sympy

from ..core.defs import has_marker
from ..core.evidence import OUTCOMES

MAX_CHARS = 400          # longest accepted expression
MAX_DEPTH = 40           # deepest nesting of parentheses, signs and exponents
MAX_EXPONENT = 2000      # largest |numeric exponent|
MAX_BITS = 70_000        # largest integer a literal power may produce (10**2000 fits)

ASSUMPTIONS = ('positive', 'negative', 'nonnegative', 'nonpositive', 'nonzero',
               'real', 'integer', 'rational')
INDEXED = 'indexed'      # declares `name[expr]`: one unknown per distinct index


class Unsupported(ValueError):
    """Notation the parser does not accept. The message is a few words, for graders."""


# --- text and choices ---------------------------------------------------------

_WRAPPERS = {'(': ')', '[': ']', '{': '}', '"': '"', "'": "'", '“': '”', '‘': '’'}


def normalize_text(s: str) -> str:
    """Fold case, whitespace, one layer of quotes or brackets, and trailing periods."""
    s = ' '.join(s.casefold().split()).rstrip('. ')
    if len(s) >= 2 and _WRAPPERS.get(s[0]) == s[-1]:
        s = s[1:-1].strip().rstrip('. ')
    return s


@dataclass(frozen=True, init=False)
class Choice:
    """A key entry: any of several accepted texts, compared after `normalize_text`."""
    accepted: tuple[str, ...]

    def __init__(self, *accepted: str):
        object.__setattr__(self, 'accepted', tuple(accepted))

    def matches(self, answer: str) -> bool:
        return normalize_text(answer) in {normalize_text(a) for a in self.accepted}


@dataclass(frozen=True)
class Approx:
    """A key entry: a number within an absolute or a relative tolerance (either passes)."""
    value: float
    tol: float = 1e-9
    rel: float = 0.0


# --- the expression parser ----------------------------------------------------

def _log(x, base=None):
    return sympy.log(x) if base is None else sympy.log(x, base)


# name -> (builder, fewest arguments, most arguments or None)
_FUNCTIONS: Mapping[str, tuple[Callable, int, int | None]] = {
    'sqrt': (sympy.sqrt, 1, 1), 'exp': (sympy.exp, 1, 1), 'log': (_log, 1, 2),
    'ln': (sympy.log, 1, 1), 'sin': (sympy.sin, 1, 1), 'cos': (sympy.cos, 1, 1),
    'tan': (sympy.tan, 1, 1), 'abs': (sympy.Abs, 1, 1),
    'min': (sympy.Min, 1, None), 'max': (sympy.Max, 1, None),
}
_CONSTANTS = {'pi': sympy.pi, 'e': sympy.E}
_NAME = re.compile(r'[A-Za-z_][A-Za-z_0-9]*\Z')
_TOKEN = re.compile(r'\s*(?:(\d+(?:\.\d+)?|\.\d+)|([A-Za-z_][A-Za-z_0-9]*)|(\*\*|[-+*/^(),\[\]]))')
_TYPOGRAPHY = str.maketrans({'×': '*', '·': '*', '⋅': '*', '−': '-', '–': '-', '÷': '/', 'π': ' pi '})


def _symbol_table(symbols) -> dict[str, sympy.Symbol | sympy.IndexedBase]:
    """Declared names as SymPy symbols carrying their assumptions; an
    'indexed' name is an `IndexedBase` with the other assumptions."""
    if isinstance(symbols, str):
        symbols = symbols.replace(',', ' ').split()
    items = symbols.items() if isinstance(symbols, Mapping) else ((name, None) for name in symbols)
    table = {}
    for name, assumption in items:
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"bad symbol name: {name!r}")
        words = assumption.replace(',', ' ').split() if isinstance(assumption, str) else list(assumption or ())
        unknown = [w for w in words if w not in ASSUMPTIONS and w != INDEXED]
        if unknown:
            raise ValueError(f"unknown assumption for {name}: {unknown[0]!r}")
        flags = {w: True for w in words if w != INDEXED}
        table[name] = sympy.IndexedBase(name, **flags) if INDEXED in words else sympy.Symbol(name, **flags)
    return table


def _allowed_functions(functions) -> tuple[str, ...]:
    if functions is None:
        return tuple(_FUNCTIONS)
    unknown = [f for f in functions if f not in _FUNCTIONS]
    if unknown:
        raise ValueError(f"unknown function: {unknown[0]!r} (known: {', '.join(_FUNCTIONS)})")
    return tuple(functions)


def _short(text: str) -> str:
    return text if len(text) <= 20 else text[:17] + '...'


def _tokens(text: str, table: Mapping[str, sympy.Symbol], allowed: Sequence[str]) -> list[tuple[str, Any]]:
    """Split text into ('num' | 'val' | 'idx' | 'func' | 'op', value) tokens, resolving names.

    A word that is not a name on its own becomes a product of single-letter
    symbols when every letter is declared. An indexed name must be followed by `[`.
    """
    raw, pos, depth = [], 0, 0
    end = len(text.rstrip())
    while pos < end:
        m = _TOKEN.match(text, pos)
        if not m:
            raise Unsupported(f"unsupported character: {text[pos:].lstrip()[:1]!r}")
        pos = m.end()
        raw.append(('num', m[1]) if m[1] else ('name', m[2]) if m[2] else ('op', m[3]))
        if m[3] in ('(', '['):
            depth += 1
            if depth > MAX_DEPTH:
                raise Unsupported("nested too deeply")
        elif m[3] in (')', ']'):
            depth -= 1
    out: list[tuple[str, Any]] = []
    for i, (kind, word) in enumerate(raw):
        if kind != 'name':
            out.append((kind, word))
            continue
        called = i + 1 < len(raw) and raw[i + 1] == ('op', '(')
        subscripted = i + 1 < len(raw) and raw[i + 1] == ('op', '[')
        if word in allowed and called:
            out.append(('func', word))
        elif word in table:
            out.append(_resolved(word, table[word], subscripted))
        elif word in _CONSTANTS:
            out.append(('val', _CONSTANTS[word]))
        elif word in allowed:
            raise Unsupported(f"function without arguments: {word}")
        elif all(c in table for c in word):
            out.extend(_resolved(c, table[c], subscripted and j == len(word) - 1) for j, c in enumerate(word))
        elif word in _FUNCTIONS:
            raise Unsupported(f"function not allowed: {word}")
        else:
            raise Unsupported(f"unknown symbol: {_short(word)}")
    return out


def _resolved(word: str, value, subscripted: bool) -> tuple[str, Any]:
    if isinstance(value, sympy.IndexedBase):
        if not subscripted:
            raise Unsupported(f"needs an index: {word}[...]")
        return ('idx', value)
    return ('val', value)


def _power(base: sympy.Expr, exponent: sympy.Expr) -> sympy.Expr:
    """`base ** exponent`, refusing work that a short input should not be able to demand."""
    if exponent.is_Rational:
        size = abs(exponent)
        if size > MAX_EXPONENT:
            raise Unsupported("exponent too large")
        if base.is_Rational and max(abs(base.p).bit_length(), base.q.bit_length()) * size > MAX_BITS:
            raise Unsupported("number too large")
    return sympy.Pow(base, exponent)


class _Parser:
    """Recursive descent over resolved tokens.

        expr   := term (('+' | '-') term)*
        term   := factor (('*' | '/') factor | factor)*      juxtaposition multiplies
        factor := ('+' | '-')* power
        power  := atom (('**' | '^') factor)?                 right associative
        atom   := number | symbol | constant | indexed '[' expr ']'
                | func '(' expr (',' expr)* ')' | '(' expr ')'
    """

    def __init__(self, tokens: list[tuple[str, Any]]):
        self.tokens = tokens
        self.pos = 0
        self.depth = 0
        self.exponent_end = -1      # token position just after the latest exponent

    def peek(self) -> tuple[str, Any]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ('end', None)

    def take_op(self, *ops: str) -> str | None:
        kind, value = self.peek()
        if kind == 'op' and value in ops:
            self.pos += 1
            return value
        return None

    def expect(self, op: str) -> None:
        if not self.take_op(op):
            kind, value = self.peek()
            raise Unsupported("unexpected end" if kind == 'end' else f"expected {op!r}")

    def parse(self) -> sympy.Expr:
        if not self.tokens:
            raise Unsupported("empty expression")
        value = self.expr()
        kind, tok = self.peek()
        if kind != 'end':
            raise Unsupported(f"unexpected {tok!r}" if kind == 'op' else "missing operator")
        return value

    def expr(self) -> sympy.Expr:
        value = self.term()
        while op := self.take_op('+', '-'):
            rhs = self.term()
            value = value + rhs if op == '+' else value - rhs
        return value

    def term(self) -> sympy.Expr:
        value = self.factor()
        divided = False
        while True:
            if op := self.take_op('*', '/'):
                rhs = self.factor()
                value = value * rhs if op == '*' else value / rhs
                divided = op == '/'
                continue
            kind, tok = self.peek()
            if kind == 'num':
                raise Unsupported("missing operator")
            if kind not in ('val', 'idx', 'func') and (kind, tok) != ('op', '('):
                return value
            # Juxtaposition. `a/2b` and `2^ab` have two common readings: do not guess.
            if divided:
                raise Unsupported("ambiguous product after '/'")
            if self.exponent_end == self.pos and (self.tokens[self.pos - 1][0] == 'val'
                                                  or self.tokens[self.pos - 1] == ('op', ']')):
                raise Unsupported("ambiguous product after exponent")
            value = value * self.factor()

    def factor(self) -> sympy.Expr:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise Unsupported("nested too deeply")
        negate = False
        while op := self.take_op('+', '-'):
            negate ^= op == '-'
        value = self.power()
        self.depth -= 1
        return -value if negate else value

    def power(self) -> sympy.Expr:
        base = self.atom()
        if self.take_op('**', '^'):
            base = _power(base, self.factor())
            self.exponent_end = self.pos
        return base

    def atom(self) -> sympy.Expr:
        kind, tok = self.peek()
        self.pos += 1
        if kind == 'num':
            whole, _, frac = tok.partition('.')
            return sympy.Rational(int((whole + frac) or '0'), 10 ** len(frac))
        if kind == 'val':
            return tok
        if kind == 'idx':
            self.expect('[')
            index = self.expr()
            self.expect(']')
            return tok[index]
        if kind == 'func':
            build, fewest, most = _FUNCTIONS[tok]
            self.expect('(')
            args = [self.expr()]
            while self.take_op(','):
                args.append(self.expr())
            self.expect(')')
            if len(args) < fewest or (most is not None and len(args) > most):
                raise Unsupported(f"wrong number of arguments: {tok}")
            return build(*args)
        if (kind, tok) == ('op', '('):
            value = self.expr()
            self.expect(')')
            return value
        raise Unsupported("unexpected end" if kind == 'end' else f"unexpected {tok!r}")


def parse_expr(text: str, *, symbols: Iterable[str] | Mapping[str, Any] = (),
               functions: Sequence[str] | None = None) -> sympy.Expr:
    """Parse an arithmetic expression into SymPy; raise `Unsupported` for anything else.

    `symbols` lists the allowed names, or maps each to an assumption
    (`{'a': 'positive'}`). A name declared 'indexed' (`{'s': 'positive indexed'}`)
    is written `s[expr]` only: each distinct index is its own unknown, carrying
    the other assumptions. `functions=None` allows a default set of calls, a list
    allows only those, and `[]` allows none, so `a(1-b)` is a product. Decimals
    are exact. Juxtaposition multiplies where it has one reading.
    """
    if not isinstance(text, str):
        raise Unsupported("not text")
    if len(text) > MAX_CHARS:
        raise Unsupported("expression too long")
    table = _symbol_table(symbols)
    allowed = _allowed_functions(functions)
    try:
        return _Parser(_tokens(text.translate(_TYPOGRAPHY), table, allowed)).parse()
    except (RecursionError, OverflowError, MemoryError):
        raise Unsupported("expression too large") from None


# --- equality -----------------------------------------------------------------

_POINTS = 6
_SEED = 20240229
_DIGITS = 30
_CLEAR = sympy.Float('1e-9')        # relative difference that counts as a mismatch
_PROOF_OPS = 300                    # do not try to simplify anything larger
_FAILURES = (ArithmeticError, ValueError, TypeError, RecursionError, NotImplementedError, AttributeError)


def _sample(symbol: sympy.Symbol, rng: random.Random) -> sympy.Rational:
    """A value for one symbol consistent with its assumptions, away from 0."""
    lo, hi = -3.0, 3.0
    if symbol.is_positive:
        lo = 0.1
    elif symbol.is_negative:
        hi = -0.1
    elif symbol.is_nonnegative:
        lo = 0.0
    elif symbol.is_nonpositive:
        hi = 0.0
    if symbol.is_integer:
        zero_ok = lo <= 0 <= hi and not symbol.is_nonzero
        return sympy.Integer(rng.choice([n for n in range(-6, 7) if lo <= n <= hi and (n or zero_ok)]))
    while True:
        k = rng.randint(int(lo * 1000), int(hi * 1000))
        if k % 1000:                 # skip integers: they hide differences such as x vs x**2
            return sympy.Rational(k, 1000)


def _value(expr: sympy.Expr, point: Mapping) -> sympy.Float | None:
    """`expr` at `point` as a finite real number, or None if it is not one there."""
    try:
        v = expr.evalf(_DIGITS, subs=point) if point else expr.evalf(_DIGITS)
        if v.is_Float or v.is_Rational:
            return sympy.Float(v, _DIGITS)
    except _FAILURES:
        pass
    return None


def _numeric(a: sympy.Expr, b: sympy.Expr) -> bool | None:
    """False on a clear difference at some point, True if all points agree, None if none evaluates.

    Each distinct indexed term (`s[i - 1]`) is an unknown of its own."""
    terms = a.atoms(sympy.Indexed) | b.atoms(sympy.Indexed)
    if terms:
        swap = {t: sympy.Symbol(str(t), **t.base.assumptions0) for t in terms}
        a, b = a.xreplace(swap), b.xreplace(swap)
    symbols = sorted(a.free_symbols | b.free_symbols, key=lambda s: s.name)
    rng = random.Random(_SEED)
    seen = False
    for _ in range(_POINTS if symbols else 1):
        point = {s: _sample(s, rng) for s in symbols}
        va, vb = _value(a, point), _value(b, point)
        if va is None or vb is None:
            continue
        seen = True
        if abs(va - vb) > _CLEAR * max(1, abs(va), abs(vb)):
            return False
    return True if seen else None


def _provable(d: sympy.Expr) -> bool:
    """Whether simplifying `d` is cheap enough to attempt."""
    if sympy.count_ops(d) > _PROOF_OPS:
        return False
    return not any(p.exp.is_Integer and abs(p.exp) > 60 and not p.base.is_Atom for p in d.atoms(sympy.Pow))


def equal(a: sympy.Expr, b: sympy.Expr) -> bool | None:
    """Whether two expressions are equal for all values their symbols' assumptions allow.

    True needs a symbolic proof; False needs a clear numeric difference at a
    sampled point (or a difference proven nonzero). Everything else is None:
    agreeing numerically without a proof, or not evaluable as real numbers.
    """
    if a == b:
        return True
    if a.has(sympy.nan, sympy.zoo, sympy.oo) or b.has(sympy.nan, sympy.zoo, sympy.oo):
        return None
    try:
        d = a - b
        if d == 0:
            return True
        if _numeric(a, b) is False:
            return False
        if not _provable(d):
            return None
        if sympy.expand(d) == 0:
            return True
        zero = sympy.simplify(d).is_zero
        return None if zero is None else bool(zero)
    except _FAILURES:
        return None


# --- comparing answers with a key ---------------------------------------------

def _check_approx(expr: sympy.Expr, entry: Approx) -> tuple[str | None, str]:
    if expr.free_symbols:
        return None, "not a number"
    v = _value(expr, {})
    if v is None:
        return None, "not a number"
    diff = abs(v - sympy.Float(entry.value, _DIGITS))
    ok = diff <= entry.tol or diff <= entry.rel * abs(entry.value)
    return ('correct', "") if ok else ('incorrect', "")


def check_field(answer: str | None, entry: Any, *, symbols: Iterable[str] | Mapping[str, Any] = (),
                functions: Sequence[str] | None = None) -> tuple[str | None, str]:
    """`(outcome, note)` for one field against its key entry.

    `entry` is an expression string, a `Choice`, an `Approx`, or a function
    `answer -> outcome | (outcome, note)` given the raw answer text. A key that
    does not parse is a programming error and raises; student notation that does
    not parse leaves the field undecided.
    """
    if answer is None or not answer.strip():
        return 'blank', ""
    if has_marker(answer):
        return None, "review"
    try:
        if isinstance(entry, Choice):
            return ('correct', "") if entry.matches(answer) else ('incorrect', "")
        if isinstance(entry, Approx):
            return _check_approx(parse_expr(answer, symbols=symbols, functions=functions), entry)
        if isinstance(entry, str):
            try:
                expected = parse_expr(entry, symbols=symbols, functions=functions)
            except Unsupported as e:
                raise ValueError(f"key expression {entry!r} does not parse: {e}") from None
            same = equal(parse_expr(answer, symbols=symbols, functions=functions), expected)
            return {True: ('correct', ""), False: ('incorrect', ""), None: (None, "cannot decide")}[same]
        if callable(entry):
            return _result(entry(answer))
    except Unsupported as e:
        return None, str(e)
    raise TypeError(f"unsupported key entry: {type(entry).__name__}")


def _result(result) -> tuple[str | None, str]:
    outcome, note = result if isinstance(result, tuple) else (result, "")
    if outcome not in OUTCOMES:
        raise ValueError(f"key function returned {outcome!r}, not an outcome")
    return outcome, str(note or "")


def check_group(answers: Sequence[str | None], entry: Callable) -> list[tuple[str | None, str]]:
    """`(outcome, note)` per field for a key entry spanning several fields.

    `entry(answers)` gets the fields' answers as a tuple, a blank one as None,
    and returns one result for all of them (an outcome or `(outcome, note)`),
    or a list with one result per field. A blank field stays blank whatever
    `entry` says; all blank skips `entry`, and a review marker in any field
    leaves the others undecided, as a single-field entry would. Notation
    `entry` cannot read (`Unsupported`) leaves the answered fields undecided.
    """
    given = tuple(None if a is None or not a.strip() else a for a in answers)
    if all(a is None for a in given):
        return [('blank', "")] * len(given)
    if any(a is not None and has_marker(a) for a in given):
        results = [(None, "review")] * len(given)
    else:
        try:
            got = entry(given)
        except Unsupported as e:
            got = (None, str(e))
        if isinstance(got, list):
            if len(got) != len(given):
                raise ValueError(f"key function returned {len(got)} results for {len(given)} fields")
            results = [_result(r) for r in got]
        else:
            results = [_result(got)] * len(given)
    return [('blank', "") if a is None else r for a, r in zip(given, results)]


def check_answers(answers: Mapping[str, str | None], key: Mapping[str | tuple, Any], *,
                  symbols: Iterable[str] | Mapping[str, Any] = (),
                  functions: Sequence[str] | None = None) -> dict:
    """Compare parsed answers with a key, in key order. A missing label is blank.

    A key maps a label to an entry (see `check_field`), or a tuple of labels to
    a callable over those fields' answers (see `check_group`):

        {'a': '1/2', ('b', 'c'): either_order}
    """
    fields = {}
    for labels, entry in key.items():
        if isinstance(labels, tuple):
            if not callable(entry):
                raise TypeError(f"the entry for {labels} must be callable")
            group = [answers.get(label) for label in labels]
            results = check_group(group, entry)
        else:
            group = [answers.get(labels)]
            results = [check_field(group[0], entry, symbols=symbols, functions=functions)]
            labels = (labels,)
        for label, answer, (outcome, note) in zip(labels, group, results):
            normal = None if outcome == 'blank' else normal_form(
                answer, symbols=symbols, functions=functions, text=isinstance(entry, Choice))
            fields[label] = {'answer': None if outcome == 'blank' else answer, 'outcome': outcome,
                             'note': note, 'normal': normal}
    return {'fields': fields}


# --- normal forms and answer facets -----------------------------------------------

NORMAL_OPS = 120         # larger expressions keep their text
NORMAL_POWER = 12        # a symbolic power above this is not expanded
NORMAL_CHARS = 120       # a longer normal form falls back to the text


def _decimal(r: sympy.Rational) -> str | None:
    """An exact decimal for a rational whose denominator has only 2s and 5s."""
    q, twos, fives = int(r.q), 0, 0
    while q % 2 == 0:
        q, twos = q // 2, twos + 1
    while q % 5 == 0:
        q, fives = q // 5, fives + 1
    if q != 1 or max(twos, fives) > 30:
        return None
    digits = max(twos, fives)
    text = str(abs(int(r.p)) * 10 ** digits // int(r.q)).rjust(digits + 1, '0')
    whole, frac = text[:len(text) - digits], text[len(text) - digits:]
    out = f'{whole}.{frac}'.rstrip('0').rstrip('.') if digits else whole
    return '-' + out if r.p < 0 else out


def normal_form(answer: str | None, *, symbols: Iterable[str] | Mapping[str, Any] = (),
                functions: Sequence[str] | None = None, text: bool = False) -> str | None:
    """A cheap canonical string for an answer, so that equal answers usually
    share it: the parsed expression with rational functions cancelled
    (`2x^2`, `x*2*x` -> '2*x**2'; `0.50`, `1/2` -> '0.5'), or else the normalized
    text (`text=True` always uses the text, as for choices). Best effort, never
    used to grade: two equivalent answers may still differ. None when blank."""
    if answer is None or not answer.strip():
        return None
    if not text and not has_marker(answer):
        try:
            expr = parse_expr(answer, symbols=symbols, functions=functions)
            if expr.is_Rational:
                shown = _decimal(expr) or str(expr)
            elif sympy.count_ops(expr) > NORMAL_OPS or any(
                    p.exp.is_Integer and abs(int(p.exp)) > NORMAL_POWER and not p.base.is_number
                    for p in expr.atoms(sympy.Pow)):
                shown = str(expr)
            else:
                shown = str(sympy.cancel(expr))
            if len(shown) <= NORMAL_CHARS:
                return shown
        except (Unsupported, *_FAILURES):
            pass
    return normalize_text(answer)


def answer_facet(label: str, *, kind: str = 'evidence'):
    """A facet over one field's stored normal form (`fields[label]['normal']`
    in evidence data, as `check_answers` writes it). A blank field has the
    value `focus.BLANK`; evidence from before normal forms existed has none."""
    from ..core.focus import BLANK, Facet

    def values(bodies):
        f = (bodies.get(kind) or {}).get(label)
        if not isinstance(f, Mapping):
            return []
        if f.get('outcome') == 'blank':
            return [BLANK]
        return [f['normal']] if f.get('normal') is not None else []

    return Facet(f'answer/{label}', label, values, reads={kind: '$.data.fields'}, group='answer')


def all_correct(evidence) -> str | None:
    """The standard grade rule: correct only if every field is; all blank is blank.

    A demonstrated mistake outranks an undecided field, which outranks a
    partly blank answer (incorrect).
    """
    outcomes = evidence.outcomes()
    if 'incorrect' in outcomes:
        return 'incorrect'
    if not outcomes or None in outcomes:
        return None
    if all(o == 'blank' for o in outcomes):
        return 'blank'
    return 'incorrect' if 'blank' in outcomes else 'correct'
