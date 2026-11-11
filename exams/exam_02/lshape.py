"""Question 3: inverse-CDF sampling from the L piece, six placeholders.

    compute_X(u):     if u < ??_1: return ??_2      else: return ??_3
    compute_Y(x, v):  if x < ??_4: return ??_5      else: return ??_6

Each function is one joint key entry. Several fillings are right (either
branch may take either part of the L, and a uniform may run either way), so a
unit is compared with every such form and scored against the closest. Any
other filling is right if the function it makes has the right law, which is
tested on a grid, with no randomness.
"""
from dataclasses import dataclass
from itertools import product

import numpy as np
import sympy

from examinator.std import math

from . import keys

X_SYMBOLS = ('u',)
Y_SYMBOLS = ('x', 'v')
GRID = 4000             # points of u, and of v for each x
X_PER_PART = 6          # values of x tried in each part of the L
TOLERANCE = 2e-3        # largest gap between the grid's distribution and the target CDF


@dataclass(frozen=True)
class L:
    """Columns [left, left + 3] of height 1, with one end column of height 2."""
    left: int
    tall_left: bool

    @property
    def tall(self) -> tuple[int, int]:
        return (self.left, self.left + 1) if self.tall_left else (self.left + 2, self.left + 3)

    @property
    def short(self) -> tuple[int, int]:
        return (self.left + 1, self.left + 3) if self.tall_left else (self.left, self.left + 2)

    @property
    def boundary(self) -> int:
        return self.left + 1 if self.tall_left else self.left + 2

    def height(self, x):
        lo, hi = self.tall
        return np.where((x >= lo) & (x <= hi), 2.0, 1.0)

    def x_cdf(self, x):
        """CDF of the x coordinate of a uniform point: each part holds half the area."""
        (a, b), (c, d) = self.short, self.tall
        return 0.5 * np.clip((x - a) / (b - a), 0, 1) + 0.5 * np.clip((x - c) / (d - c), 0, 1)


SHAPES = {'right': L(0, False), 'left': L(0, True), 'right+1': L(1, False), 'left+1': L(1, True)}


# --- the accepted forms --------------------------------------------------------------

def _runs(interval, t):
    """`t` in [0, 1] mapped onto the interval, upward and downward."""
    lo, hi = interval
    return (lo + (hi - lo) * t, hi - (hi - lo) * t)


def x_forms(shape: L) -> list[tuple[str, str, str]]:
    u = sympy.Symbol('u')
    forms = []
    for first, second in ((shape.short, shape.tall), (shape.tall, shape.short)):
        for below, above in product(_runs(first, 2 * u), _runs(second, 2 * u - 1)):
            forms.append(('1/2', str(sympy.expand(below)), str(sympy.expand(above))))
    return forms


def y_forms(shape: L) -> list[tuple[str, str, str]]:
    v = sympy.Symbol('v')
    low, high = (2, 1) if shape.tall_left else (1, 2)       # heights for x below and above the boundary
    return [(str(shape.boundary), str(sympy.expand(below)), str(sympy.expand(above)))
            for below, above in product(_runs((0, low), v), _runs((0, high), v))]


# --- evaluating a parsed expression on arrays -----------------------------------------

_CALLS = {sympy.exp: np.exp, sympy.log: np.log, sympy.Abs: np.abs, sympy.sin: np.sin,
          sympy.cos: np.cos, sympy.tan: np.tan}


def evaluate(expr: sympy.Expr, values: dict) -> np.ndarray:
    """`expr` at arrays of values for its symbols, by walking the tree.
    Raises `math.Unsupported` for anything it does not know."""
    if expr.is_Symbol:
        if expr.name not in values:
            raise math.Unsupported(f"unknown symbol: {expr.name}")
        return values[expr.name]
    if expr.is_number and not expr.args:
        return np.float64(float(expr)) if expr.is_finite and expr.is_real else np.float64('nan')
    args = [evaluate(a, values) for a in expr.args]
    if expr.is_Add:
        return sum(args[1:], args[0])
    if expr.is_Mul:
        return np.prod(np.broadcast_arrays(*args), axis=0)
    if expr.is_Pow:
        return np.float_power(args[0], args[1])
    if isinstance(expr, (sympy.Min, sympy.Max)):
        return (np.minimum if isinstance(expr, sympy.Min) else np.maximum).reduce(np.broadcast_arrays(*args))
    fn = _CALLS.get(expr.func)
    if fn is None:
        raise math.Unsupported(f"cannot evaluate {expr.func.__name__}")
    return fn(*args)


def _gap(samples: np.ndarray, cdf) -> float:
    """Kolmogorov distance between evenly weighted samples and a CDF."""
    if not np.isfinite(samples).all():
        return np.inf
    s = np.sort(samples)
    return float(np.max(np.abs(cdf(s) - (np.arange(len(s)) + 0.5) / len(s))))


def _piecewise(texts, symbols, values, test: str):
    cut, below, above = (evaluate(math.parse_expr(t, symbols=symbols), values) for t in texts)
    with np.errstate(all='ignore'):
        return np.where(values[test] < cut, below, above)


def x_valid(shape: L, texts) -> bool:
    """Does `compute_X(u)` with this filling give the x coordinate's law?"""
    u = (np.arange(GRID) + 0.5) / GRID
    with np.errstate(all='ignore'):
        return _gap(_piecewise(texts, X_SYMBOLS, {'u': u}, 'u'), shape.x_cdf) <= TOLERANCE


def y_valid(shape: L, texts) -> bool:
    """Is `compute_Y(x, v)` uniform on [0, height(x)] for x in either part?"""
    v = (np.arange(GRID) + 0.5) / GRID
    for lo, hi in (shape.short, shape.tall):
        for x in lo + (hi - lo) * (np.arange(X_PER_PART) + 0.5) / X_PER_PART:
            h = float(shape.height(x))
            with np.errstate(all='ignore'):
                y = _piecewise(texts, Y_SYMBOLS, {'x': np.full(GRID, x), 'v': v}, 'x')
            if _gap(y, lambda s: np.clip(s / h, 0, 1)) > TOLERANCE:
                return False
    return True


# --- the joint entry -------------------------------------------------------------------

@dataclass(frozen=True)
class Unit:
    """One of the two functions: three fields scored together."""
    variant: str
    axis: str               # 'x' or 'y'

    @property
    def forms(self):
        return (x_forms if self.axis == 'x' else y_forms)(SHAPES[self.variant])

    @property
    def symbols(self):
        return X_SYMBOLS if self.axis == 'x' else Y_SYMBOLS

    @property
    def label(self):
        return " | ".join(", ".join(form) for form in self.forms) + " | any filling with the right law"

    def __call__(self, answers):
        parsed = []
        for a in answers:
            try:
                parsed.append(None if a is None else math.parse_expr(a, symbols=self.symbols))
            except math.Unsupported as e:
                parsed.append(e)

        def against(form):
            out = []
            for got, want in zip(parsed, form):
                if got is None:
                    out.append(('blank', ""))
                elif isinstance(got, math.Unsupported):
                    out.append((None, str(got)))
                else:
                    out.append(keys.same(got, math.parse_expr(want, symbols=self.symbols)))
            return out

        best = max((against(form) for form in self.forms),
                   key=lambda rs: sum(outcome == 'correct' for outcome, _ in rs))
        if all(outcome == 'correct' for outcome, _ in best):
            return best
        if all(isinstance(p, sympy.Expr) for p in parsed):
            valid = x_valid if self.axis == 'x' else y_valid
            try:
                if valid(SHAPES[self.variant], answers):
                    return [('correct', "right law")] * 3
            except math.Unsupported:
                pass
        return best


def key(variant: str) -> dict:
    return {('??_1', '??_2', '??_3'): Unit(variant, 'x'), ('??_4', '??_5', '??_6'): Unit(variant, 'y')}
