"""Math for checks: a restricted expression parser, equality, choices, key comparison.

Student-derived text is data. `parse_expr` reads a small arithmetic grammar with
its own tokenizer and builds SymPy objects explicitly; nothing is evaluated as
Python and SymPy never sees raw text. `equal` compares two expressions under the
assumptions their symbols carry. `check_answers` compares answers with a key,
field by field (or a group of fields at once). `keyed(key, grade=...)` is the
standard check built from these: one judgement per field, one grade from the
fields' outcomes (`all_correct`, `at_least(k)`, or the exam's own rule).

Outcomes are 'correct', 'incorrect', 'blank' or None. None means "could not
decide" (unsupported notation, an unknown symbol, an inconclusive comparison)
and is never used for a demonstrated mismatch.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

import sympy

from ..core.reading import normalize
from ..core.result import OUTCOMES, Judgement, Result, Undecided
from .blocks import Note

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

    def __repr__(self) -> str:        # as an exam package writes it
        return f"Choice({', '.join(map(repr, self.accepted))})"

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
    `entry` says; all blank skips `entry`. Notation
    `entry` cannot read (`Unsupported`) leaves the answered fields undecided.
    """
    given = tuple(None if a is None or not a.strip() else a for a in answers)
    if all(a is None for a in given):
        return [('blank', "")] * len(given)
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
                  functions: Sequence[str] | None = None) -> dict[str, tuple[str | None, str]]:
    """Compare answers with a key, in key order: `{label: (outcome, note)}`.
    A missing or empty answer is blank.

    A key maps a label to an entry (see `check_field`), or a tuple of labels to
    a callable over those fields' answers (see `check_group`):

        {'a': '1/2', ('b', 'c'): either_order}
    """
    out = {}
    for labels, entry in key.items():
        if isinstance(labels, tuple):
            if not callable(entry):
                raise TypeError(f"the entry for {labels} must be callable")
            out.update(zip(labels, check_group([answers.get(label) for label in labels], entry)))
        else:
            out[labels] = check_field(answers.get(labels), entry, symbols=symbols, functions=functions)
    return out


def labels_of(key: Mapping[str | tuple, Any]) -> list[str]:
    """Every label a key names, in key order."""
    out: list[str] = []
    for k in key:
        out += [x for x in (k if isinstance(k, tuple) else (k,)) if x not in out]
    return out


def show_entry(entry: Any) -> str:
    """A key entry as graders see it. A callable entry may carry a `label`."""
    if isinstance(entry, str):
        return entry
    label = getattr(entry, 'label', None)
    if label:
        return str(label)
    accepted = getattr(entry, 'accepted', None)
    if accepted:
        return " | ".join(accepted)
    if isinstance(entry, Approx):
        return f"{entry.value} ± {entry.tol}" if entry.tol is not None else f"{entry.value} ± {entry.rel:.0%}"
    return getattr(entry, '__name__', type(entry).__name__)


# --- grade rules: from the fields' outcomes to one grade -----------------------------

def _list(outcomes) -> list[str | None]:
    return list(outcomes.values()) if isinstance(outcomes, Mapping) else list(outcomes)


def all_correct(outcomes) -> str | None:
    """Correct only if every field is; all blank is blank. A demonstrated
    mistake outranks an undecided field, which outranks a partly blank answer
    (incorrect). `outcomes` is `{label: outcome}` or a list."""
    outcomes = _list(outcomes)
    if 'incorrect' in outcomes:
        return 'incorrect'
    if not outcomes or None in outcomes:
        return None
    if all(o == 'blank' for o in outcomes):
        return 'blank'
    return 'incorrect' if 'blank' in outcomes else 'correct'


def at_least(k: int, required: Sequence[str] = ()) -> Callable[[Mapping], str | None]:
    """Correct when at least `k` fields are correct, all of `required` among them.
    Incorrect once that is out of reach; undecided while undecided fields could
    still tip it; all blank is blank."""
    def rule(outcomes: Mapping[str, str | None]) -> str | None:
        values = list(outcomes.values())
        if values and all(o == 'blank' for o in values):
            return 'blank'
        correct, open_ = values.count('correct'), values.count(None)
        must = [outcomes.get(label) for label in required]
        if any(o in ('incorrect', 'blank') for o in must) or correct + open_ < k:
            return 'incorrect'
        if correct >= k and all(o == 'correct' for o in must):
            return 'correct'
        return None
    rule.__qualname__ = f'at_least({k})'
    return rule


# --- the keyed check -------------------------------------------------------------

def pick(keys: Mapping[str, Any], value: str, what: str | None = None) -> tuple[str, Any]:
    """The key for a value (names compared normalised), or `Undecided` naming what
    picked it (`what`: a field) and the value."""
    for name, k in keys.items():
        if normalize(name) == normalize(value):
            return name, k
    raise Undecided((f"no key for {what} = {value}" if what else f"no key for {value!r}")[:120])


def keyed_by_value(key: Mapping[Any, Any]) -> bool:
    """A key whose every value is itself a mapping is one key per value of a field."""
    return bool(key) and all(isinstance(v, Mapping) for v in key.values())


@dataclass(frozen=True)
class Keyed:
    """A check comparing each field with its key entry (`keyed`)."""
    key: Mapping[Any, Any]               # {label or (label, ...): entry}, or {value: {...}} with `by`
    symbols: Mapping[str, str] | tuple[str, ...] = ()
    functions: tuple[str, ...] | None = None
    by: Any = None                       # with a key per value: what picks it (`fields.value`, ...)
    grade: Callable[[Mapping], Any] = all_correct

    def validate(self, question, exam) -> None:
        """A key per value comes with `by` (whose fields exist), and its labels are
        the question's fields. A value with no key is undecided when it is read."""
        from . import fields as fields_
        per_value = keyed_by_value(self.key)
        if per_value != (self.by is not None):
            raise ValueError(f"{question.id}: " + ("a key per value needs `by=` to pick one" if per_value
                                                   else "`by=` picks one of several keys: give one key per value"))
        unknown = [label for k in (self.key.values() if per_value else [self.key]) for label in labels_of(k)
                   if question.field(label) is None]
        if unknown:
            raise ValueError(f"{question.id}: the key names {sorted(set(unknown))}, which are not its fields")
        for k in (self.key.values() if per_value else [self.key]):
            for labels, entry in k.items():
                if isinstance(entry, str):
                    try:
                        parse_expr(entry, symbols=self.symbols, functions=self.functions)
                    except Unsupported as e:
                        raise ValueError(f"{question.id}: the key for {labels} does not parse: {e}") from None
                elif isinstance(entry, Choice) and not (entry.accepted and all(
                        isinstance(a, str) and a.strip() for a in entry.accepted)):
                    raise ValueError(f"{question.id}: the key for {labels} is a choice of one or more texts")
        if self.by is not None:
            fields_.check_refs(exam, question, self.by, question.id)

    def __call__(self, ctx) -> Result:
        name, key = pick(self.key, self.by(ctx), getattr(self.by, 'name', None)) if self.by is not None \
            else (None, self.key)
        answers, problems = {}, {}
        for label in labels_of(key):
            try:
                answers[label] = ctx.read(label)
            except Undecided as e:
                problems[label] = str(e)
        fields: dict[str, Judgement] = {}
        for labels, entry in key.items():
            group = labels if isinstance(labels, tuple) else (labels,)
            shown = show_entry(entry)           # a group's key is shown beside its first field
            expected = {label: shown if i == 0 else None for i, label in enumerate(group)}
            if any(label in problems for label in group):
                for label in group:
                    fields[label] = Judgement(None, expected[label],
                                              problems.get(label) or "a field it goes with is not read")
                continue
            for label, (outcome, note) in check_answers({x: answers[x] for x in group}, {labels: entry},
                                                         symbols=self.symbols, functions=self.functions).items():
                fields[label] = Judgement(outcome, expected[label], note or None)
        outcomes = {label: j.outcome for label, j in fields.items()}
        grade = self.grade(outcomes)
        reason = None
        if grade is None:
            why = [j.note if j.note and j.note.startswith(label) else f"{label}: {j.note}" if j.note else label
                   for label, j in fields.items() if j.outcome is None]
            reason = ("undecided: " + "; ".join(why)) if why else "undecided"
        return Result(grade, fields, [Note(f"key: {name}")] if name is not None else [], reason=reason)

    def facets(self, question) -> list:
        """One facet per field: the answer's normal form, so equal answers group."""
        keys = list(self.key.values()) if keyed_by_value(self.key) else [self.key]
        choice = {label for k in keys for labels, e in k.items() if isinstance(e, Choice)
                  for label in (labels if isinstance(labels, tuple) else (labels,))}
        labels = dict.fromkeys(label for k in keys for label in labels_of(k))
        return [answer_facet(label, symbols=self.symbols, functions=self.functions, text=label in choice)
                for label in labels]


def keyed(key: Mapping[Any, Any], *, by: Any = None, symbols: Mapping[str, str] | Sequence[str] = (),
          functions: Sequence[str] | None = None, grade: Callable[[Mapping], Any] = all_correct) -> Keyed:
    """A check that compares each field with its key entry, and grades the
    fields' outcomes with `grade` (`all_correct`, `at_least(k)`, or any
    `fn({label: outcome}) -> grade`).

    A key entry is an expression string, a `Choice`, an `Approx`, or a function
    `answer -> outcome` (see `check_field`); a tuple of labels maps to a function
    over those fields' answers (`check_group`). With `by`, `key` is one key per
    value of a field, and `by` picks it: a label of the question's own field, a
    `fields.value(label, question=...)` of another question's,
    `fields.agree(...)`, `fields.table(...)`, or any `fn(ctx) -> value` (raise
    `Undecided` when it cannot tell); a value with no key is undecided. `symbols` maps names to assumptions (`{'a': 'positive'}`), or
    lists names; `functions=[]` means no function calls, so juxtaposition is
    multiplication. A field that is not read is undecided, and so is a grade it
    could tip.
    """
    from . import fields as fields_
    if not isinstance(symbols, Mapping):
        symbols = tuple(symbols)
    return Keyed(key, symbols, None if functions is None else tuple(functions),
                 None if by is None else fields_.as_chooser(by), grade)


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
    if not text:
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




def answer_facet(label: str, *, symbols: Iterable[str] | Mapping[str, Any] = (),
                 functions: Sequence[str] | None = None, text: bool = False):
    """A facet over one field's answer, in its normal form (`normal_form`), so
    that equal answers usually group. A blank field has the value `focus.BLANK`."""
    from ..core.focus import BLANK, Facet
    symbols = tuple(sorted(symbols.items())) if isinstance(symbols, Mapping) else tuple(symbols)

    def values(view):
        answer = view.value(label)
        if answer is None:
            return []
        if not answer.strip():
            return [BLANK]
        k = ('normal', answer, symbols, functions, text)
        if k not in view.memo:
            view.memo[k] = normal_form(answer, symbols=dict(symbols) if symbols and isinstance(symbols[0], tuple)
                                       else symbols, functions=functions, text=text)
        return [view.memo[k]]

    return Facet(f'answer/{label}', label, values, group='answer')
