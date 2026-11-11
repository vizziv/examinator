"""The `huge_constant` convention, as a deterministic rewrite of parsed pseudocode.

A rejection sampler on an idealized computer may use an absurdly large envelope
constant (`10**1000`). Run literally it never accepts. `rewrite` replaces one
clearly identified envelope scale with the name `huge_constant`, so the check
can substitute small values for it.

A scale is recognized only in an acceptance-probability role: as a
multiplicative factor of the argument of `coin(...)`, or of either side of a
comparison with `unif()`. It must shrink the acceptance probability by a factor
of at least 100: a literal >= 100 dividing it, or a reciprocal <= 0.01
multiplying it, possibly through a variable assigned once. Every scale found
must have the same value; otherwise nothing is rewritten and a person decides.
Constants anywhere else (arguments of samplers, exponents, sums) are target or
proposal parameters and are never touched.

The source is only parsed (`ast`), never executed.
"""
import ast
import math

NAME = 'huge_constant'
THRESHOLD = 2.0         # log10 of the smallest scale that counts as an envelope


def _number(node) -> float | None:
    """A numeric literal as a float, or None."""
    if type(node) is ast.Constant and type(node.value) in (int, float):
        try:
            return float(node.value)
        except OverflowError:
            return math.inf
    return None


def _exponent(node) -> float | None:
    """The value of a constant expression used as an exponent."""
    if type(node) is ast.UnaryOp and type(node.op) in (ast.USub, ast.UAdd):
        v = _exponent(node.operand)
        return None if v is None else (-v if type(node.op) is ast.USub else v)
    size = log10(node)
    if size is None:
        return None
    return 10.0 ** size if size < 300 else math.inf


def log10(node) -> float | None:
    """log10 of a positive constant expression built from literals with * / **, or None."""
    if type(node) is ast.Constant:
        if type(node.value) is int and node.value > 0:
            return math.log10(node.value)       # exact enough for integers of any size
        v = _number(node)
        return math.log10(v) if v is not None and 0 < v < math.inf else (math.inf if v == math.inf else None)
    if type(node) is not ast.BinOp:
        return None
    if type(node.op) is ast.Pow:
        base, power = log10(node.left), _exponent(node.right)
        if base is None or power is None:
            return None
        size = 0.0 if base == 0 or power == 0 else base * power
    elif type(node.op) in (ast.Mult, ast.Div):
        left, right = log10(node.left), log10(node.right)
        if left is None or right is None:
            return None
        size = left + right if type(node.op) is ast.Mult else left - right
    else:
        return None
    return None if math.isnan(size) else size


def _is_unif(node) -> bool:
    return (type(node) is ast.Call and type(node.func) is ast.Name and node.func.id == 'unif'
            and not node.args and not node.keywords)


class Ambiguous(Exception):
    pass


class _Function:
    """Scales in acceptance-probability roles within one function definition."""

    def __init__(self, fn: ast.FunctionDef):
        self.fn = fn
        self.values: dict[str, list] = {}       # name -> assigned value nodes (None: not a plain value)
        for a in fn.args.args:
            self.values.setdefault(a.arg, []).append(None)
        for node in ast.walk(fn):
            if type(node) is ast.Assign and len(node.targets) == 1 and type(node.targets[0]) is ast.Name:
                self.values.setdefault(node.targets[0].id, []).append(node.value)
            elif type(node) is ast.Name and type(node.ctx) is ast.Store:
                self.values.setdefault(node.id, []).append(None)
        # A plain assignment is seen twice (once above, once as a stored name): drop the echo.
        for name, values in self.values.items():
            plain = [v for v in values if v is not None]
            self.values[name] = plain if len(values) == 2 * len(plain) else values
        self.found: dict[tuple, tuple[ast.AST, float, int]] = {}    # position -> (node, log10, sign)

    def value(self, name: str):
        """The single value a name is assigned, if it is assigned exactly once."""
        values = self.values.get(name, [])
        return values[0] if len(values) == 1 else None

    def uniform(self, node) -> bool:
        """`unif()`, a variable holding one, or one scaled by other factors."""
        if _is_unif(node):
            return True
        if type(node) is ast.Name:
            v = self.value(node.id)
            return v is not None and _is_unif(v)
        if type(node) is ast.BinOp and type(node.op) is ast.Mult:
            return self.uniform(node.left) != self.uniform(node.right)
        if type(node) is ast.BinOp and type(node.op) is ast.Div:
            return self.uniform(node.left)
        return False

    def scan(self) -> None:
        for node in ast.walk(self.fn):
            if (type(node) is ast.Call and type(node.func) is ast.Name and node.func.id == 'coin'
                    and len(node.args) == 1):
                self.factor(node.args[0], +1, ())
            elif (type(node) is ast.Compare and len(node.ops) == 1
                  and type(node.ops[0]) in (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                left, right = node.left, node.comparators[0]
                if self.uniform(left) != self.uniform(right):
                    u, p = (left, right) if self.uniform(left) else (right, left)
                    self.factor(u, -1, ())      # scaling the uniform up scales the probability down
                    self.factor(p, +1, ())

    def factor(self, node, sign: int, via: tuple) -> None:
        """Visit one multiplicative factor of an acceptance probability (`sign` is
        +1 for a factor, -1 for a divisor)."""
        size = log10(node)
        if size is not None:
            if sign * size <= -THRESHOLD:
                where = (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
                if where in self.found and self.found[where][2] != sign:
                    raise Ambiguous()
                self.found[where] = (node, size, sign)
        elif type(node) is ast.BinOp and type(node.op) is ast.Mult:
            self.factor(node.left, sign, via)
            self.factor(node.right, sign, via)
        elif type(node) is ast.BinOp and type(node.op) is ast.Div:
            self.factor(node.left, sign, via)
            self.factor(node.right, -sign, via)
        elif type(node) is ast.Name and node.id not in via:
            v = self.value(node.id)
            if v is not None:
                self.factor(v, sign, via + (node.id,))


def rewrite(source: str) -> str:
    """Replace a single, clearly identified envelope scale with `huge_constant`.

    Returns `source` unchanged when there is no such scale, when scales of
    different sizes appear, or when the name is already used.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return source
    if any(type(n) is ast.Name and n.id == NAME for n in ast.walk(tree)):
        return source
    found: dict[tuple, tuple[ast.AST, float, int]] = {}
    try:
        for fn in tree.body:
            if type(fn) is ast.FunctionDef:
                scanner = _Function(fn)
                scanner.scan()
                found.update(scanner.found)
    except (Ambiguous, RecursionError):
        return source
    scales = {round(abs(size), 6) for _, size, _ in found.values()}
    if len(scales) != 1:
        return source
    lines = [line.encode() for line in source.split('\n')]      # ast columns count UTF-8 bytes
    for (line, col, end_line, end_col), (_, size, _) in sorted(found.items(), reverse=True):
        if line != end_line:
            return source
        text = NAME if size > 0 else f'(1 / {NAME})'
        row = lines[line - 1]
        lines[line - 1] = row[:col] + text.encode() + row[end_col:]
    out = b'\n'.join(lines).decode()
    try:
        ast.parse(out)
    except SyntaxError:
        return source
    return out
