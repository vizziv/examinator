"""This course's pseudocode conventions that the interpreter's language lacks.

- Arithmetic on points is elementwise: `p + q`, `p * q`, `c * p`, `p / c`, `-p`.
- Arrays are indexed from 1.
- `rotate(p, angle, center)` and `weighted_die(w)` are available.
- "Do not use accept-reject" means at most `LIMIT` calls of the random
  primitives per sample, counted exactly.

The parse keeps the student's notation. `rewrite` turns it, for running only,
into calls of trusted primitives; `Draws` counts the random calls.
"""
import ast
import math

from examinator.std import pseudocode

LANGUAGE = pseudocode.WITH_COLLECTIONS
LIMIT = 1000        # random calls per sample

OPS = {ast.Add: 'vec_add', ast.Sub: 'vec_sub', ast.Mult: 'vec_mul', ast.Div: 'vec_div'}
SCALAR_CALLS = frozenset({'unif', 'coin', 'die', 'weighted_die', 'log', 'ln', 'exp', 'sqrt', 'sin', 'cos',
                          'tan', 'floor', 'ceil', 'int', 'float', 'round', 'len', 'index1'})
SCALAR_NAMES = frozenset({'pi', 'e', 'inf'})


# --- rewriting ------------------------------------------------------------------

def _scalar(node) -> bool:
    """Certainly a number, from syntax alone: its arithmetic can stay native."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float))
    if isinstance(node, ast.Name):
        return node.id in SCALAR_NAMES
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id in SCALAR_CALLS
    if isinstance(node, ast.UnaryOp):
        return _scalar(node.operand)
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, ast.Pow) or (_scalar(node.left) and _scalar(node.right))
    return False


def _call(name: str, *args) -> ast.Call:
    return ast.Call(ast.Name(name, ast.Load()), list(args), [])


class _Rewrite(ast.NodeTransformer):
    def visit_BinOp(self, node):
        self.generic_visit(node)
        name = OPS.get(type(node.op))
        if name is None or (_scalar(node.left) and _scalar(node.right)):
            return node
        return ast.copy_location(_call(name, node.left, node.right), node)

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.USub) and not _scalar(node.operand):
            return ast.copy_location(_call('vec_neg', node.operand), node)
        return node

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        name = OPS.get(type(node.op))
        if name is None or not isinstance(node.target, ast.Name) or _scalar(node.value):
            return node
        value = _call(name, ast.Name(node.target.id, ast.Load()), node.value)
        return ast.copy_location(ast.Assign([node.target], value), node)

    def visit_Subscript(self, node):
        self.generic_visit(node)
        if not isinstance(node.slice, ast.Slice):
            node.slice = _call('index1', node.slice)
        return node


def rewrite(source: str) -> str:
    """The source with point arithmetic and 1-based indexing made explicit.

    Parsing only; the result is still run by the bounded interpreter. Source
    that is not valid syntax is returned unchanged for the interpreter to refuse.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return source
    try:
        return ast.unparse(ast.fix_missing_locations(_Rewrite().visit(tree))) + "\n"
    except RecursionError:
        return source


def loops(program: pseudocode.Program, entry: str) -> str:
    """Why `entry` might not stop after a fixed number of random calls: "" if it
    has no `while` and no recursion among the functions it reaches."""
    tree = ast.parse(program.select(entry).source)
    defs = {d.name: d for d in tree.body if isinstance(d, ast.FunctionDef)}
    calls = {name: {n.func.id for n in ast.walk(d) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id in defs} for name, d in defs.items()}
    if any(isinstance(n, ast.While) for d in defs.values() for n in ast.walk(d)):
        return "while loop"

    def reaches(start, target, seen):
        return any(c == target or (c not in seen and reaches(c, target, seen | {c})) for c in calls[start])

    return "recursion" if any(reaches(name, name, {name}) for name in defs) else ""


# --- primitives -----------------------------------------------------------------

def unsupported(message: str) -> pseudocode.Failure:
    """A run-time stop that proves nothing about the answer (see `parts.draw`)."""
    return pseudocode.Failure(message, kind='unsupported')


def _vec(v) -> bool:
    return isinstance(v, (list, tuple))


def _num(v) -> bool:
    return isinstance(v, (int, float))


def _elementwise(sym: str, op, broadcast: bool):
    def fn(rng, a, b):
        if _vec(a) and _vec(b):
            if len(a) != len(b):
                raise unsupported(f"points of {len(a)} and {len(b)} coordinates in '{sym}'")
            return [fn(rng, x, y) for x, y in zip(a, b)]
        if _vec(a) or _vec(b):
            if not broadcast or (sym == '/' and _vec(b)):
                raise unsupported(f"a number and a point in '{sym}'")
            return [fn(rng, x, b) for x in a] if _vec(a) else [fn(rng, a, y) for y in b]
        if not (_num(a) and _num(b)):
            raise TypeError(f"'{sym}' needs numbers or points")
        return op(a, b)
    return fn


def vec_neg(rng, a):
    return [vec_neg(rng, x) for x in a] if _vec(a) else -a


def index1(rng, i):
    """A 1-based index as the interpreter's 0-based one."""
    if isinstance(i, bool) or not isinstance(i, int):
        raise TypeError("an index must be a whole number")
    if i == 0:
        raise unsupported("index 0: arrays are indexed from 1 here")
    return i - 1 if i > 0 else i


def rotate(rng, p, angle, center=(0, 0)):
    """`p` turned counterclockwise by `angle` radians about `center`."""
    if not (_vec(p) and len(p) == 2 and _vec(center) and len(center) == 2):
        raise unsupported("rotate needs a 2-D point and center")
    x, y, c, s = p[0] - center[0], p[1] - center[1], math.cos(angle), math.sin(angle)
    return [center[0] + c * x - s * y, center[1] + s * x + c * y]


POINT_PRIMITIVES = {
    'vec_add': _elementwise('+', lambda a, b: a + b, broadcast=False),
    'vec_sub': _elementwise('-', lambda a, b: a - b, broadcast=False),
    'vec_mul': _elementwise('*', lambda a, b: a * b, broadcast=True),
    'vec_div': _elementwise('/', lambda a, b: a / b, broadcast=True),
    'vec_neg': vec_neg, 'index1': index1, 'rotate': rotate,
}


class Draws:
    """The random primitives, counting calls per sample.

    The interpreter's own draw budget also charges every trusted primitive
    (the point arithmetic above), so the exam's rule is counted here instead.
    Going over the limit is a demonstrated mistake, not an exhausted budget.
    """

    def __init__(self, limit: int = LIMIT):
        self.limit, self.most = limit, 0
        self._rng, self._count = None, 0

    def _tick(self, rng) -> None:
        if rng is not self._rng:            # each sample has its own generator
            self._rng, self._count = rng, 0
        self._count += 1
        self.most = max(self.most, self._count)
        if self._count > self.limit:
            raise pseudocode.Failure(f"more than {self.limit} random calls in one sample", kind='limit')

    def unif(self, rng, a=0.0, b=1.0):
        self._tick(rng)
        return a + (b - a) * float(rng.random())

    def coin(self, rng, p):
        self._tick(rng)
        if not 0 <= p <= 1:
            raise ValueError("coin needs a probability")
        return bool(rng.random() < p)

    def die(self, rng, n):
        self._tick(rng)
        if isinstance(n, bool) or n != int(n) or n < 1:
            raise ValueError("die needs a positive whole number")
        return int(rng.integers(1, int(n) + 1))

    def weighted_die(self, rng, w):
        """Index i (from 1) with probability w[i] / sum(w)."""
        self._tick(rng)
        if not (_vec(w) and len(w) and all(_num(x) and x >= 0 for x in w) and 0 < sum(w) < math.inf):
            raise ValueError("weighted_die needs nonnegative weights with a positive sum")
        total, u = 0.0, float(rng.random()) * sum(w)
        for i, x in enumerate(w, 1):
            total += x
            if u < total:
                return i
        return max(i for i, x in enumerate(w, 1) if x > 0)

    def primitives(self) -> dict:
        return {**POINT_PRIMITIVES, 'unif': self.unif, 'coin': self.coin, 'die': self.die,
                'weighted_die': self.weighted_die}


# --- the two halves together ------------------------------------------------------

def prepare(source: str) -> tuple[pseudocode.Program, pseudocode.Program]:
    """`(as written, as run)`. Raises `pseudocode.Unsupported`."""
    return pseudocode.parse(source, LANGUAGE), pseudocode.parse(rewrite(source), LANGUAGE)


def settle_direct(part, program: pseudocode.Program, entry: str, draws: Draws):
    """Combine a part's tests, then apply the no-accept-reject rule: a sampler
    that passes but loops without a fixed bound goes to a person."""
    part.tests.append(["random calls per sample", 'correct', f"at most {draws.most}"])
    part.combine()
    why = loops(program, entry)
    if part.outcome == 'correct' and why:
        part.settle(None, f"{why}: accept-reject?")
    return part
