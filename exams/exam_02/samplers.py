"""Shared pieces of the two pseudocode checks.

The course's pseudocode differs from the interpreter's language in three ways,
all handled here rather than by the parse model:

- points are lists, and `+ - * /` act on them as vectors (`p + [2, 1]`, `2 * p`);
- `^` is a power;
- `die(n)` is uniform on {0, ..., n-1}.

`prepare` rewrites the arithmetic into calls of trusted primitives before the
interpreter sees the source; the parse that graders read stays as written.
"""
import ast
import dataclasses
import math
import operator
import time

from examinator import Evidence
from examinator.std import pseudocode, sampling
from examinator.std.blocks import Code, Details, Note, Table

SEED = 4580

_DIE = "`die(n)` is a uniform whole number from 1 to n"
assert _DIE in pseudocode.WITH_COLLECTIONS.description
LANGUAGE = dataclasses.replace(
    pseudocode.WITH_COLLECTIONS, name='exam_02',
    description=pseudocode.WITH_COLLECTIONS.description.replace(
        _DIE, "`die(n)` is a uniform whole number from 0 to n - 1") + """
- Points: a point is a list `[x, y]`. `+`, `-`, `*` and `/` between two points act coordinate by coordinate, `*` or `/` by a number scales a point, and `-p` negates one; keep such arithmetic exactly as written. `rotate(point, angle, center)` turns a point counterclockwise about a center. `^` may be kept as written for a power.""")


# --- trusted primitives -----------------------------------------------------------------

def die(rng, n):
    """The course's `die(n)`: a uniform integer from 0 to n - 1."""
    if isinstance(n, bool) or n != int(n) or n < 1:
        raise ValueError("die needs a positive integer")
    return int(rng.integers(0, int(n)))


def rotate(rng, point, angle, center=(0, 0)):
    x, y = _pair(point)
    cx, cy = _pair(center)
    c, s = math.cos(angle), math.sin(angle)
    return [cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c]


def _pair(p):
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise ValueError("expected a point [x, y]")
    return p


def _vector(op, scales: bool):
    """A binary operator that also works on points: coordinate by coordinate
    between points of one size (as on Exam 1), and scaling by a number for `*` and `/`."""
    def apply(rng, a, b):
        va, vb = isinstance(a, (list, tuple)), isinstance(b, (list, tuple))
        if not va and not vb:
            return op(a, b)
        if va and vb:
            if len(a) != len(b):
                raise ValueError("points of different sizes")
            return [op(x, y) for x, y in zip(a, b)]
        if scales and (va or op is operator.mul):
            return [op(x, b) for x in a] if va else [op(a, y) for y in b]
        raise ValueError("no such operation on points")
    return apply


def _negate(rng, a):
    return [-x for x in a] if isinstance(a, (list, tuple)) else -a


_OPS = {ast.Add: 'vec_add', ast.Sub: 'vec_sub', ast.Mult: 'vec_mul', ast.Div: 'vec_div'}
PRIMITIVES = {'die': die, 'rotate': rotate,
              'vec_add': _vector(operator.add, False), 'vec_sub': _vector(operator.sub, False),
              'vec_mul': _vector(operator.mul, True), 'vec_div': _vector(operator.truediv, True),
              'vec_neg': _negate}
REWRITES = frozenset({*_OPS.values(), 'vec_neg'})             # the names `prepare` writes in
PROVIDED = frozenset(PRIMITIVES) - REWRITES                   # names a student may call


# --- rewriting the arithmetic -----------------------------------------------------------

def _plain(node) -> bool:
    """Certainly a number, so the operator can stay as it is."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float))
    if isinstance(node, ast.UnaryOp):
        return _plain(node.operand)
    if isinstance(node, ast.BinOp):
        return _plain(node.left) and _plain(node.right)
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
        node.func.id in ('unif', 'coin', 'die', 'sqrt', 'log', 'ln', 'exp', 'sin', 'cos', 'tan', 'abs', 'len')


class _Vectors(ast.NodeTransformer):
    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if not isinstance(node.op, ast.USub) or _plain(node.operand):
            return node
        return ast.copy_location(ast.Call(ast.Name('vec_neg', ast.Load()), [node.operand], []), node)

    def visit_BinOp(self, node):
        self.generic_visit(node)
        name = _OPS.get(type(node.op))
        if name is None or (_plain(node.left) and _plain(node.right)):
            return node
        return ast.copy_location(ast.Call(ast.Name(name, ast.Load()), [node.left, node.right], []), node)

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        name = _OPS.get(type(node.op))
        if name is None or not isinstance(node.target, ast.Name) or _plain(node.value):
            return node
        call = ast.Call(ast.Name(name, ast.Load()), [ast.Name(node.target.id, ast.Load()), node.value], [])
        return ast.copy_location(ast.Assign([node.target], call), node)


def prepare(source: str) -> tuple[pseudocode.Program, dict]:
    """The program to run, and {its line: the line of `source`}. Raises `pseudocode.Unsupported`."""
    source = source.replace('^', '**')      # as text: Python's `^` binds more loosely than a power
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return pseudocode.parse(source, LANGUAGE), {}      # let the interpreter report it
    if any(isinstance(n, ast.Name) and n.id in REWRITES for n in ast.walk(tree)):
        raise pseudocode.Unsupported("reserved name")
    tree = ast.fix_missing_locations(_Vectors().visit(tree))
    rewritten = ast.unparse(tree)
    was = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.stmt)]
    now = [n.lineno for n in ast.walk(ast.parse(rewritten)) if isinstance(n, ast.stmt)]
    lines = dict(zip(now, was))
    try:
        return pseudocode.parse(rewritten, LANGUAGE), lines
    except pseudocode.Unsupported as e:
        raise pseudocode.Unsupported(e.detail, lines.get(e.line, e.line)) from None


def loops(source: str) -> bool:
    """Does the source contain a `while` loop?"""
    try:
        return any(isinstance(n, ast.While) for n in ast.walk(ast.parse(source)))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False


# --- running and reporting ---------------------------------------------------------------

class Stop(Exception):
    """Sampling ended early. `outcome` is 'incorrect' or None (undecided)."""

    def __init__(self, outcome: str | None, note: str):
        super().__init__(note)
        self.outcome, self.note = outcome, note


def draw(program: pseudocode.Program, lines: dict, entry: str, *, n: int, seconds: float,
         primitives=None, max_steps: int | None = 2_000_000) -> list:
    """`n` results of `entry()`, or `Stop`. A budget that runs out, a name the
    exam does not provide and point arithmetic this module cannot do are
    undecided; any other failure is a mistake."""
    try:
        given = {**PRIMITIVES, **(primitives or {})}
        if program.has('rotate'):           # the student's own helper wins
            del given['rotate']
        return program.sample(entry, n=n, seed=SEED, deadline=time.monotonic() + seconds, max_steps=max_steps,
                              primitives=given)
    except (pseudocode.Budget, pseudocode.Unsupported) as e:
        raise Stop(None, str(e)) from None
    except pseudocode.Failure as e:
        where = f"line {lines.get(e.line, e.line)}: " if e.line else ""
        ours = e.detail.startswith(tuple(f'{name}(' for name in REWRITES))      # the rewrite's arithmetic failed
        raise Stop(None if e.kind == 'undefined' or ours else 'incorrect', where + e.detail) from None


def evidence(label: str, outcome: str | None, note: str, source: str, *, fits=(), tests=(), samples=None) -> Evidence:
    """Plots first, then the verdict; tests, samples and the source collapsed."""
    blocks = [fit.plot for fit in fits if fit.plot is not None]
    blocks.append(Note(f"{outcome or '?'}{': ' + note if note else ''}",
                       {'correct': 'good', 'incorrect': 'bad'}.get(outcome, 'warn')))
    if tests:
        blocks.append(Details("Tests", Table(["test", '', ''], [list(row) for row in tests])))
    if samples is not None:
        shown = ", ".join(str(v)[:60] for v in samples[:12])
        blocks.append(Details("Samples", Code(shown), audience='grader'))
    blocks.append(Details("Source", Code(source), audience='grader'))
    return Evidence({'fields': {label: {'outcome': outcome, 'note': note}}}, blocks)


def unparsed(label: str, source: str, error: pseudocode.Unsupported) -> Evidence:
    """The source is outside the language: undecided."""
    return Evidence({'fields': {label: {'outcome': None, 'note': str(error)}}},
                    [Note(str(error), 'warn'),
                     Details("Source", Code(source, [error.line] if error.line else []), audience='grader')])


def fit_row(name: str, fit: sampling.Fit) -> list:
    return [name, fit.outcome or '?', fit.note]
