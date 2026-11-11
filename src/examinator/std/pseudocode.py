"""A bounded interpreter for a Python-syntax teaching language.

Students write samplers in informal pseudocode; a model converts that to this
language; a check then runs the student's functions many times with seeded
randomness and tests the output distribution.

Student-derived text is untrusted and is never executed as host Python. The
source is parsed with `ast` (parsing only), every node is validated against a
whitelist, and the validated tree is compiled into Python closures that this
module interprets itself: names resolve to list slots or to values supplied by
the caller, never to host namespaces. Work is bounded by step, draw, depth,
size and time budgets.

    program = parse(source)                       # Unsupported
    program.sample('f', (2, 4), n=1000, seed=1)   # Failure, Budget

Not thread-safe while sampling: `sample` raises the host recursion limit for
its duration and restores it afterwards.
"""
from __future__ import annotations

import ast
import math
import operator
import sys
import time
import warnings
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np

MAX_SOURCE = 20_000        # characters
MAX_NODES = 5_000          # statements and expressions in one program
MAX_NESTING = 60           # nesting of statements and expressions
MAX_INT_BITS = 1 << 16     # bit length of any integer value (about 19,700 digits)
MAX_ITEMS = 100_000        # elements in one collection
MAX_CELLS = 20_000_000     # collection cells allocated by one sample
MAX_DEPTH = 200            # nested calls of student functions

_CHUNK = 4096              # steps between deadline checks
_SMALL = 1 << 62           # integers below this need no size check
_EXACT = 1 << 53           # largest n for die(n): floats are exact up to here
_INF = math.inf

# Names the language itself provides. A caller checking that everything a
# program needs is supplied subtracts these from `Program.free_names`.
BUILTINS = frozenset({
    'unif', 'coin', 'die', 'log', 'ln', 'exp', 'sqrt', 'sin', 'cos', 'tan', 'floor', 'ceil',
    'abs', 'int', 'float', 'round', 'min', 'max', 'range', 'pi', 'e', 'inf',
})
COLLECTION_BUILTINS = frozenset({'len', 'sum', 'sorted', 'list', 'set'})   # added by WITH_COLLECTIONS

_UNSET = object()
_BREAK = object()
_CONTINUE = object()
_RETURN = object()


class Unsupported(Exception):
    """The source uses syntax or a construct outside the language."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message if line is None else f"line {line}: {message}")
        self.line = line
        self.detail = message


class Failure(Exception):
    """The student's program failed at run time.

    `kind` says why: 'undefined' (a name or function that is not defined),
    'arity' (wrong number of arguments), 'domain' (a math-domain error or an
    invalid probability), 'type' (an operation on the wrong kind of value),
    or 'other'.
    """

    def __init__(self, message: str, line: int | None = None, expr: str | None = None,
                 kind: str = 'other'):
        parts = []
        if line is not None:
            parts.append(f"line {line}")
        if expr:
            parts.append(expr)
        parts.append(message)
        super().__init__(": ".join(parts))
        self.line = line
        self.expr = expr
        self.detail = message
        self.kind = kind


class Budget(Exception):
    """A budget ran out. `kind` is 'steps', 'draws', 'depth', 'size' or 'time'."""

    def __init__(self, kind: str, message: str | None = None):
        super().__init__(message or f"{kind} budget exhausted")
        self.kind = kind


# --------------------------------------------------------------------------
# The language, as a value
# --------------------------------------------------------------------------

_HEAD = """\
The language is a small subset of Python. A program is a sequence of function definitions and nothing else.

- Definitions: `def name(a, b):` with plain positional parameters. No defaults, decorators, annotations, nested functions, classes, imports or top-level statements. A docstring is allowed.
- Statements: assignment (`x = ...`, `x, y = ...`), augmented assignment (`+=` and so on), `if`/`elif`/`else`, `while`, `for i in range(...)`, `return`, `break`, `continue`, `pass`, and a function call on its own line.
- Expressions: `+ - * / // % **`, unary `+` and `-`, comparisons (may be chained), `is None`, `and`/`or`/`not`, `a if c else b`, and function calls with positional arguments.
- Randomness: `unif()` is uniform on [0, 1); `unif(a, b)` is uniform on [a, b); `coin(p)` is True with probability p; `die(n)` is a uniform whole number from 1 to n.
- Math: `log` (natural; also `ln`), `exp`, `sqrt`, `abs`, `min`, `max`, `floor`, `ceil`, `round`, `sin`, `cos`, `tan`, `int`, `float`; constants `pi`, `e`, `inf`.
- Functions may call each other and themselves. Any other name is available only if the task says so."""

_BASE_TAIL = """
- Values: integers of any size, floats, `True`, `False`, `None`, and tuples used only to return or assign several values at once (`return a, b`).
- Not available: strings, lists, sets, dictionaries, indexing, `in`, comprehensions, attributes and methods, lambda, keyword or starred arguments, `print`, `try`, `with`, `global`, `assert`, `del`."""

_COLLECTIONS_TAIL = """
- Values: integers of any size, floats, `True`, `False`, `None`, tuples, lists and sets.
- Collections: list and set literals (`[]`, `[a, b]`, `{a, b}`), `set()`, `list()`, `list(xs)`, `set(xs)`; list and set comprehensions with `for ... in ...` clauses and `if` filters; `for x in xs`; `len`, `sum`, `sorted`, `min`, `max`; `x in xs`, `x not in xs`; indexing and slicing of lists (`xs[i]`, `xs[a:b]`) and `xs[i] = v`; `+` to concatenate and `*` to repeat lists; `|` for the union of sets; `range(...)` as a list.
- Methods, and no others: `xs.append(v)`, `xs.extend(ys)`, `xs.pop()`, `s.add(v)`.
- Not available: strings, dictionaries, other attributes and methods, lambda, keyword or starred arguments, `print`, `try`, `with`, `global`, `assert`, `del`."""


@dataclass(frozen=True)
class Language:
    name: str
    description: str          # handed to the parse model in a prompt
    collections: bool = False


BASE = Language('base', _HEAD + _BASE_TAIL)
WITH_COLLECTIONS = Language('collections', _HEAD + _COLLECTIONS_TAIL, collections=True)


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------

_BINOPS = MappingProxyType({
    ast.Add: '+', ast.Sub: '-', ast.Mult: '*', ast.Div: '/',
    ast.FloorDiv: '//', ast.Mod: '%', ast.Pow: '**',
})
_CMPOPS = MappingProxyType({
    ast.Eq: '==', ast.NotEq: '!=', ast.Lt: '<', ast.LtE: '<=', ast.Gt: '>', ast.GtE: '>=',
    ast.Is: 'is', ast.IsNot: 'is not',
})
_METHODS = MappingProxyType({'append': (1, 1), 'add': (1, 1), 'extend': (1, 1), 'pop': (0, 1)})

# Host names that are never part of the language, whatever a caller registers.
# Nothing depends on this list for safety (names are only ever looked up in the
# interpreter's own tables); it turns a confusing run-time error into Unsupported.
_RESERVED = frozenset({
    'eval', 'exec', 'compile', 'open', 'input', 'print', 'getattr', 'setattr', 'delattr',
    'globals', 'locals', 'vars', 'dir', 'type', 'breakpoint', 'exit', 'quit', 'help',
})

_KINDS = MappingProxyType({
    'Import': "import", 'ImportFrom': "import", 'Attribute': "attribute access",
    'Subscript': "indexing", 'Lambda': "lambda", 'ClassDef': "class", 'Dict': "dictionary",
    'DictComp': "dictionary", 'ListComp': "comprehension", 'SetComp': "comprehension",
    'GeneratorExp': "comprehension", 'List': "list", 'Set': "set", 'Try': "try",
    'TryStar': "try", 'With': "with", 'Global': "global", 'Nonlocal': "nonlocal",
    'Delete': "del", 'Assert': "assert", 'Yield': "yield", 'YieldFrom': "yield",
    'Await': "await", 'AsyncFunctionDef': "async", 'AsyncFor': "async", 'AsyncWith': "async",
    'Starred': "starred expression", 'JoinedStr': "f-string", 'NamedExpr': "walrus assignment",
    'FunctionDef': "nested function", 'AnnAssign': "annotation", 'Raise': "raise",
    'Match': "match", 'Slice': "slice",
})


@dataclass(frozen=True)
class _Def:
    name: str
    node: ast.FunctionDef = field(compare=False, repr=False)
    params: tuple
    locals: tuple             # params first, then every other assigned name
    free: frozenset           # names read or called that are not local
    blank: bool


def _what(node) -> str:
    kind = type(node).__name__
    return _KINDS.get(kind, kind)


class _Checker:
    """Validates a parsed tree against the whitelist. Raises Unsupported."""

    def __init__(self, language: Language):
        self.collections = language.collections
        self.nodes = 0

    def no(self, node, message=None):
        return Unsupported(message or f"{_what(node)} is not supported", getattr(node, 'lineno', None))

    def enter(self, node, depth):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise self.no(node, "program is too large")
        if depth > MAX_NESTING:
            raise self.no(node, "code is nested too deeply")

    def name(self, node, ident):
        if ident.startswith('__'):
            raise self.no(node, f"name '{ident}' is not supported")
        if ident in _RESERVED:
            raise self.no(node, f"'{ident}' is not part of the language")

    def module(self, tree) -> dict:
        defs = {}
        for node in tree.body:
            if type(node) is not ast.FunctionDef:
                kind = _what(node)
                raise self.no(node, f"only function definitions are allowed at top level, not {kind}")
            defs[node.name] = self.function(node)
        return defs

    def function(self, node) -> _Def:
        self.enter(node, 1)
        self.name(node, node.name)
        a = node.args
        if node.decorator_list:
            raise self.no(node, "decorators are not supported")
        if a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg or a.defaults or a.kw_defaults:
            raise self.no(node, "only plain positional parameters are supported")
        if node.returns is not None or any(p.annotation is not None for p in a.args):
            raise self.no(node, "annotations are not supported")
        if getattr(node, 'type_params', None):
            raise self.no(node, "type parameters are not supported")
        params = tuple(p.arg for p in a.args)
        for p in a.args:
            self.name(p, p.arg)
        body = node.body
        if body and _is_docstring(body[0]):
            self.nodes += 1
            body = body[1:]
        for stmt in body:
            self.stmt(stmt, 2, False)
        assigned, loaded = [], set()
        for stmt in body:
            for sub in ast.walk(stmt):
                if type(sub) is ast.Name:
                    if type(sub.ctx) is ast.Store:
                        assigned.append(sub.id)
                    else:
                        loaded.add(sub.id)
        local = tuple(dict.fromkeys(params + tuple(assigned)))
        return _Def(node.name, node, params, local, frozenset(loaded - set(local)),
                    all(_is_filler(s) for s in body))

    def block(self, stmts, depth, in_loop):
        for stmt in stmts:
            self.stmt(stmt, depth, in_loop)

    def stmt(self, node, depth, in_loop):
        self.enter(node, depth)
        kind = type(node)
        d = depth + 1
        if kind is ast.Assign:
            for target in node.targets:
                self.target(target, d)
            self.expr(node.value, d)
        elif kind is ast.AugAssign:
            if type(node.op) not in _BINOPS and not (self.collections and type(node.op) is ast.BitOr):
                raise self.no(node, "this operator is not supported")
            if type(node.target) not in (ast.Name, ast.Subscript):
                raise self.no(node, "this assignment target is not supported")
            self.target(node.target, d)
            self.expr(node.value, d)
        elif kind is ast.If:
            self.expr(node.test, d)
            self.block(node.body, d, in_loop)
            self.block(node.orelse, d, in_loop)
        elif kind is ast.While:
            if node.orelse:
                raise self.no(node, "else on a loop is not supported")
            self.expr(node.test, d)
            self.block(node.body, d, True)
        elif kind is ast.For:
            if node.orelse:
                raise self.no(node, "else on a loop is not supported")
            self.loop_target(node.target, d)
            self.iterable(node.iter, d)
            self.block(node.body, d, True)
        elif kind is ast.Return:
            if node.value is not None:
                self.expr(node.value, d)
        elif kind is ast.Pass:
            pass
        elif kind in (ast.Break, ast.Continue):
            if not in_loop:
                raise self.no(node, "break or continue outside a loop")
        elif kind is ast.Expr:
            if _is_ellipsis(node):
                return
            if type(node.value) is ast.Constant and isinstance(node.value.value, str):
                raise self.no(node, "string literals are not supported")
            if type(node.value) is not ast.Call:
                raise self.no(node, "an expression on its own line must be a call")
            self.expr(node.value, d)
        else:
            raise self.no(node)

    def target(self, node, depth):
        self.enter(node, depth)
        kind = type(node)
        if kind is ast.Name:
            self.name(node, node.id)
        elif kind in (ast.Tuple, ast.List):
            for elt in node.elts:
                self.target(elt, depth + 1)
        elif kind is ast.Subscript and self.collections:
            if type(node.slice) is ast.Slice:
                raise self.no(node, "slice assignment is not supported")
            self.expr(node.value, depth + 1)
            self.expr(node.slice, depth + 1)
        else:
            raise self.no(node)

    def loop_target(self, node, depth):
        self.enter(node, depth)
        if type(node) is ast.Name:
            self.name(node, node.id)
        elif type(node) in (ast.Tuple, ast.List):
            for elt in node.elts:
                self.loop_target(elt, depth + 1)
        else:
            raise self.no(node, "this loop variable is not supported")

    def iterable(self, node, depth):
        if _is_range(node):
            self.enter(node, depth)
            self.call_args(node, depth + 1)
        elif self.collections:
            self.expr(node, depth)
        else:
            raise self.no(node, "for loops must run over range(...)")

    def call_args(self, node, depth):
        if node.keywords:
            raise self.no(node, "keyword arguments are not supported")
        for arg in node.args:
            self.expr(arg, depth)

    def expr(self, node, depth):
        self.enter(node, depth)
        kind = type(node)
        d = depth + 1
        if kind is ast.Constant:
            v = node.value
            if isinstance(v, str):
                raise self.no(node, "string literals are not supported")
            if v is not None and type(v) not in (int, float, bool):
                raise self.no(node, "this literal is not supported")
            if type(v) is int and v.bit_length() > MAX_INT_BITS:
                raise self.no(node, "integer literal is too large")
        elif kind is ast.Name:
            self.name(node, node.id)
        elif kind is ast.BinOp:
            if type(node.op) not in _BINOPS and not (self.collections and type(node.op) is ast.BitOr):
                raise self.no(node, "this operator is not supported")
            self.expr(node.left, d)
            self.expr(node.right, d)
        elif kind is ast.UnaryOp:
            if type(node.op) not in (ast.UAdd, ast.USub, ast.Not):
                raise self.no(node, "this operator is not supported")
            self.expr(node.operand, d)
        elif kind is ast.BoolOp:
            for value in node.values:
                self.expr(value, d)
        elif kind is ast.Compare:
            for op in node.ops:
                if type(op) in (ast.In, ast.NotIn):
                    if not self.collections:
                        raise self.no(node, "'in' is not supported")
                elif type(op) not in _CMPOPS:
                    raise self.no(node, "this comparison is not supported")
            self.expr(node.left, d)
            for value in node.comparators:
                self.expr(value, d)
        elif kind is ast.IfExp:
            self.expr(node.test, d)
            self.expr(node.body, d)
            self.expr(node.orelse, d)
        elif kind is ast.Call:
            self.call(node, d)
        elif kind is ast.Tuple:
            for elt in node.elts:
                self.expr(elt, d)
        elif not self.collections:
            raise self.no(node)
        elif kind in (ast.List, ast.Set):
            for elt in node.elts:
                self.expr(elt, d)
        elif kind in (ast.ListComp, ast.SetComp, ast.GeneratorExp):
            for gen in node.generators:
                if gen.is_async:
                    raise self.no(node, "async is not supported")
                self.loop_target(gen.target, d)
                self.iterable(gen.iter, d)
                for cond in gen.ifs:
                    self.expr(cond, d)
            self.expr(node.elt, d)
        elif kind is ast.Subscript:
            self.expr(node.value, d)
            if type(node.slice) is ast.Slice:
                self.nodes += 1
                for part in (node.slice.lower, node.slice.upper, node.slice.step):
                    if part is not None:
                        self.expr(part, d + 1)
            else:
                self.expr(node.slice, d)
        else:
            raise self.no(node)

    def call(self, node, depth):
        func = node.func
        if type(func) is ast.Name:
            self.name(func, func.id)
            if func.id == 'range' and not self.collections:
                raise self.no(node, "range is only supported in a for loop")
        elif type(func) is ast.Attribute:
            if not self.collections:
                raise self.no(func)
            if func.attr not in _METHODS:
                raise self.no(func, f"method '{func.attr}' is not supported")
            lo, hi = _METHODS[func.attr]
            if not lo <= len(node.args) <= hi:
                raise self.no(node, f"wrong number of arguments for {func.attr}")
            self.expr(func.value, depth)
        else:
            raise self.no(node, "only named functions can be called")
        self.call_args(node, depth)


def _is_docstring(stmt) -> bool:
    return (type(stmt) is ast.Expr and type(stmt.value) is ast.Constant
            and isinstance(stmt.value.value, str))


def _is_ellipsis(stmt) -> bool:
    return (type(stmt) is ast.Expr and type(stmt.value) is ast.Constant
            and stmt.value.value is Ellipsis)


def _is_filler(stmt) -> bool:
    return type(stmt) is ast.Pass or _is_ellipsis(stmt)


def _is_range(node) -> bool:
    return type(node) is ast.Call and type(node.func) is ast.Name and node.func.id == 'range'


def parse(source: str, language: Language = BASE) -> 'Program':
    """Parse and validate `source`. Raises Unsupported; never runs anything."""
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    if len(source) > MAX_SOURCE:
        raise Unsupported("program is too long")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            tree = ast.parse(source, mode='exec')
    except SyntaxError as ex:
        message = str(ex.msg)
        if 'integer string conversion' in message:
            message = "integer literal is too large"
        elif len(message) > 60:
            message = message[:57] + "..."
        raise Unsupported(f"not valid syntax: {message}", ex.lineno) from None
    except (ValueError, RecursionError, MemoryError, OverflowError):
        raise Unsupported("not valid syntax") from None
    try:
        defs = _Checker(language).module(tree)
    except RecursionError:
        raise Unsupported("code is nested too deeply") from None
    return Program(source=source, language=language, names=tuple(defs), _defs=MappingProxyType(defs))


@dataclass(frozen=True)
class Program:
    """A validated program. Immutable; `select` returns a new Program."""
    source: str
    language: Language
    names: tuple                                   # top-level function names, in source order
    _defs: MappingProxyType = field(repr=False, compare=False)

    def has(self, name: str) -> bool:
        return name in self._defs

    def is_blank(self, name: str) -> bool:
        """Missing, or a body that is only `pass` (or only a docstring or comments)."""
        d = self._defs.get(name)
        return d is None or d.blank

    def _reach(self, entry: str) -> list:
        if entry not in self._defs:
            return []
        seen, todo = {entry}, [entry]
        while todo:
            for name in self._defs[todo.pop()].free:
                if name in self._defs and name not in seen:
                    seen.add(name)
                    todo.append(name)
        return [name for name in self.names if name in seen]

    def select(self, entry: str) -> 'Program':
        """The entry plus every student function it references, transitively.

        A missing entry gives an empty program. Line numbers in the result
        refer to the result's own `source`.
        """
        parts = [ast.get_source_segment(self.source, self._defs[name].node) or ast.unparse(self._defs[name].node)
                 for name in self._reach(entry)]
        return parse("\n\n".join(parts) + ("\n" if parts else ""), self.language)

    def references(self, entry: str, name: str) -> bool:
        """Does `entry`, or a student function it reaches, mention the free name `name`?"""
        return any(name in self._defs[fn].free for fn in self._reach(entry))

    def free_names(self, entry: str) -> frozenset:
        """Every name `entry` needs from outside the program.

        The free names (called functions, primitives, constants) of `entry`
        and of the student functions it reaches, less the functions the
        program defines. Built-ins of the language are included: subtract
        `BUILTINS` (and `COLLECTION_BUILTINS`) to get what must be supplied.
        Empty for a missing entry.
        """
        found = set()
        for fn in self._reach(entry):
            found |= self._defs[fn].free
        return frozenset(found - set(self._defs))

    def sample(self, entry: str, args=(), *, n: int, seed: int = 0, primitives=None, constants=None,
               max_steps: int | None = 2_000_000, max_draws: int | None = None,
               deadline: float | None = None, max_depth: int = MAX_DEPTH) -> list:
        """Call `entry(*args)` n times and return the n results as plain Python values.

        Sample i has its own numpy Generator, seeded by
        `SeedSequence(seed, spawn_key=(i,))`: results are reproducible and do
        not depend on n. `primitives` maps names to trusted `fn(rng, *args)`
        and overrides student functions of the same name; `constants` maps
        names to numbers. Budgets are per sample except `deadline`, a
        `time.monotonic()` value. Raises Failure or Budget.
        """
        primitives = dict(primitives or {})
        consts = {}
        for key, value in (constants or {}).items():
            value = _adopt(value)
            if type(value) not in (int, float, bool):
                raise TypeError(f"constant {key!r} must be a number")
            consts[key] = value
        args = tuple(_adopt(a) for a in args)
        rt = _Run(max_steps, max_draws, deadline, max_depth)
        call = _Compiler(self, rt, primitives, consts).entry(entry, len(args))
        ss, pcg, gen = np.random.SeedSequence, np.random.PCG64, np.random.Generator
        out = []
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, min(max_depth, 1000) * (MAX_NESTING + 20) + 2000))
        try:
            for i in range(n):
                rt.start(gen(pcg(ss(seed, spawn_key=(i,)))))
                try:
                    out.append(_export(call([_fresh(a) for a in args])))
                except (Failure, Budget, Unsupported):
                    raise
                except RecursionError:
                    raise Budget('depth', "nesting too deep") from None
                except MemoryError:
                    raise Budget('size', "out of memory") from None
                except Exception as ex:      # last resort: no host exception type escapes
                    raise Failure(f"interpreter error: {type(ex).__name__}") from ex
        finally:
            sys.setrecursionlimit(old)
        return out


# --------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------

class _Bag(list):
    """A set that keeps duplicates: remembers every element ever added."""
    __slots__ = ()


def _adopt(v):
    """A trusted host value (argument, constant, primitive result) as an interpreter value."""
    t = type(v)
    if t is float or t is int or t is bool or v is None:
        return v
    if isinstance(v, np.generic):
        return _adopt(v.item())
    if isinstance(v, np.ndarray):
        return _adopt(v.tolist())
    if t is list:
        return [_adopt(x) for x in v]
    if t is tuple:
        return tuple(_adopt(x) for x in v)
    if t is set or t is frozenset:
        return _Bag(_adopt(x) for x in v)
    raise TypeError(f"unsupported value of type {t.__name__}")


def _fresh(v):
    """A per-sample copy, so one sample cannot see another's mutations."""
    if isinstance(v, list):
        return type(v)(_fresh(x) for x in v)
    if type(v) is tuple:
        return tuple(_fresh(x) for x in v)
    return v


def _export(v, depth=0):
    t = type(v)
    if t is float or t is int or t is bool or v is None:
        return v
    if depth > 50:
        raise Failure("result is nested too deeply")
    if t is list or t is _Bag:
        return [_export(x, depth + 1) for x in v]
    if t is tuple:
        return tuple(_export(x, depth + 1) for x in v)
    raise Failure(f"result has unsupported type {t.__name__}")


def _show(v, depth=0) -> str:
    """A short rendering of a value for an error message."""
    t = type(v)
    if t is float:
        return format(v, '.6g')
    if t is int:
        bits = v.bit_length()
        if bits <= 64:
            return str(v)
        return f"{'-' if v < 0 else ''}(about 1e{int(bits * 0.30103)})"
    if t is bool or v is None:
        return repr(v)
    if t is list or t is tuple or t is _Bag:
        open_, close = ('(', ')') if t is tuple else ('{', '}') if t is _Bag else ('[', ']')
        if depth >= 2:
            return f"{open_}...{close}"
        shown = ", ".join(_show(x, depth + 1) for x in v[:5])
        if len(v) > 5:
            shown += f", ... {len(v)} items"
        return f"{open_}{shown}{close}"
    return f"<{t.__name__}>"


def _shows(values) -> str:
    return ", ".join(_show(v) for v in values)


def _sat(v):
    """An integer too large for a float becomes an infinity."""
    if type(v) is int:
        try:
            return float(v)
        except OverflowError:
            return _INF if v > 0 else -_INF
    return v


def _is_num(t) -> bool:
    return t is float or t is int or t is bool


def _is_seq(t) -> bool:
    return t is list or t is _Bag or t is tuple


# --------------------------------------------------------------------------
# Run-time state for one call of Program.sample
# --------------------------------------------------------------------------

class _Run:
    __slots__ = ('left', 'reserve', 'max_steps', 'deadline', 'dl', 'max_draws', 'depth',
                 'max_depth', 'cells', 'rng', 'random')

    def __init__(self, max_steps, max_draws, deadline, max_depth):
        self.max_steps = sys.maxsize if max_steps is None else max_steps
        self.max_draws = sys.maxsize if max_draws is None else max_draws
        self.deadline = deadline
        self.max_depth = max_depth

    def start(self, rng):
        self.left = 0
        self.reserve = self.max_steps
        self.dl = self.max_draws
        self.depth = 0
        self.cells = MAX_CELLS
        self.rng = rng
        self.random = rng.random

    def refill(self):
        """Called when the current chunk of steps is used up."""
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise Budget('time', "time limit reached")
        if self.reserve <= 0:
            raise Budget('steps', f"more than {self.max_steps} steps")
        take = min(_CHUNK, self.reserve)
        self.reserve -= take
        self.left = take - 1

    def charge(self, n):
        if n > MAX_ITEMS:
            raise Budget('size', f"collection with more than {MAX_ITEMS} elements")
        self.cells -= n
        if self.cells < 0:
            raise Budget('size', "too much collection data")

    def grow(self, total, added):
        """An existing collection grows in place to `total` elements."""
        if total > MAX_ITEMS:
            raise Budget('size', f"collection with more than {MAX_ITEMS} elements")
        self.cells -= added
        if self.cells < 0:
            raise Budget('size', "too much collection data")

    def draws_out(self):
        return Budget('draws', f"more than {self.max_draws} random draws")


class _Fn:
    __slots__ = ('name', 'nparams', 'proto', 'body', 'line')


# --------------------------------------------------------------------------
# Compiling a validated tree into closures
# --------------------------------------------------------------------------
#
# Every expression becomes `run(frame) -> value`; every statement becomes
# `run(frame) -> None | _BREAK | _CONTINUE | _RETURN`. A frame is a list: slot 0
# holds the return value, the rest hold the function's local variables.
# Children are always evaluated outside any try block, so a Failure or Budget
# raised below is never swallowed.

class _Compiler:
    def __init__(self, program: Program, rt: _Run, primitives: dict, constants: dict):
        self.program = program
        self.collections = program.language.collections
        self.rt = rt
        self.primitives = primitives
        self.constants = constants
        self.builtins, self.values = self.make_builtins()
        self.fns = {}
        self.queue = []
        self.slots = {}
        self.params = 0
        self.cell = [None]

    # ---- errors ----------------------------------------------------------

    def text(self, node) -> str:
        try:
            text = ast.get_source_segment(self.program.source, node) or ast.unparse(node)
        except Exception:
            return ""
        text = " ".join(text.split())
        return text if len(text) <= 80 else text[:77] + "..."

    def fail(self, node, message, kind='other') -> Failure:
        return Failure(message, line=getattr(node, 'lineno', None), expr=self.text(node), kind=kind)

    def raiser(self, node, message, kind='other'):
        fail = self.fail

        def run(f):
            raise fail(node, message, kind)
        return run

    # ---- functions -------------------------------------------------------

    def fn(self, name) -> _Fn:
        fn = self.fns.get(name)
        if fn is None:
            d = self.program._defs[name]
            fn = self.fns[name] = _Fn()
            fn.name = name
            fn.nparams = len(d.params)
            fn.proto = [None] + [_UNSET] * len(d.locals)
            fn.body = None
            fn.line = d.node.lineno
            self.queue.append(d)
        return fn

    def drain(self):
        while self.queue:
            d = self.queue.pop()
            self.slots = {name: i + 1 for i, name in enumerate(d.locals)}
            self.params = len(d.params)
            self.fns[d.name].body = self.block(d.node.body)

    def entry(self, name, nargs):
        """`call(args) -> value` for the entry point; problems surface as Failure when called."""
        rt = self.rt
        if name in self.primitives:
            prim = self.primitives[name]

            def call(args):
                rt.dl -= 1
                if rt.dl < 0:
                    raise rt.draws_out()
                try:
                    return _adopt(prim(rt.rng, *args))
                except (Failure, Budget):
                    raise
                except Exception as ex:
                    raise Failure(f"{name}({_shows(args)}) raised {type(ex).__name__}: {ex}") from ex
            return call
        d = self.program._defs.get(name)
        problem = None
        if d is None:
            problem = Failure(f"function '{name}' is not defined", kind='undefined')
        elif d.blank:
            problem = Failure(f"function '{name}' is blank", line=d.node.lineno)
        elif len(d.params) != nargs:
            problem = Failure(f"{name}() takes {len(d.params)} arguments, got {nargs}", line=d.node.lineno, kind='arity')
        if problem is not None:
            def call(args):
                raise problem
            return call
        fn = self.fn(name)
        self.drain()
        proto, body = fn.proto, fn.body

        def call(args):
            rt.left -= 1
            if rt.left < 0:
                rt.refill()
            rt.depth = 1
            nf = proto[:]
            nf[1:1 + nargs] = args
            body(nf)
            return nf[0]
        return call

    # ---- statements ------------------------------------------------------

    def block(self, stmts):
        cs = [self.stmt(s) for s in stmts if not (_is_filler(s) or _is_docstring(s))]
        if not cs:
            return lambda f: None
        if len(cs) == 1:
            return cs[0]
        if len(cs) == 2:
            a, b = cs

            def run(f):
                s = a(f)
                if s is not None:
                    return s
                return b(f)
            return run
        if len(cs) == 3:
            a, b, c = cs

            def run(f):
                s = a(f)
                if s is not None:
                    return s
                s = b(f)
                if s is not None:
                    return s
                return c(f)
            return run
        cs = tuple(cs)

        def run(f):
            for c in cs:
                s = c(f)
                if s is not None:
                    return s
        return run

    def stmt(self, node):
        method = getattr(self, 's_' + type(node).__name__, None)
        if method is None:
            raise Unsupported(f"{_what(node)} is not supported", getattr(node, 'lineno', None))
        return method(node)

    def s_Assign(self, node):
        val = self.expr(node.value)
        if len(node.targets) == 1 and type(node.targets[0]) is ast.Name:
            i = self.slots[node.targets[0].id]

            def run(f):
                f[i] = val(f)
            return run
        setters = tuple(self.store(t) for t in node.targets)

        def run(f):
            v = val(f)
            for s in setters:
                s(f, v)
        return run

    def store(self, node):
        """`set(frame, value)` for an assignment target."""
        fail = self.fail
        kind = type(node)
        if kind is ast.Name:
            i = self.slots[node.id]

            def set_(f, v):
                f[i] = v
            return set_
        if kind in (ast.Tuple, ast.List):
            setters = tuple(self.store(t) for t in node.elts)
            n = len(setters)

            def set_(f, v):
                if not _is_seq(type(v)):
                    raise fail(node, f"cannot unpack {_show(v)} into {n} names", 'type')
                if len(v) != n:
                    raise fail(node, f"cannot unpack {len(v)} values into {n} names: {_show(v)}")
                for s, x in zip(setters, list(v)):
                    s(f, x)
            return set_
        if kind is ast.Subscript and self.collections:
            obj, idx = self.expr(node.value), self.expr(node.slice)

            def set_(f, v):
                o = obj(f)
                i = idx(f)
                if type(o) is not list or type(i) is not int:
                    raise fail(node, f"cannot assign to index {_show(i)} of {_show(o)}", 'type')
                try:
                    o[i] = v
                except IndexError:
                    raise fail(node, f"index {i} out of range for a list of length {len(o)}") from None
            return set_
        raise Unsupported(f"{_what(node)} is not supported", getattr(node, 'lineno', None))

    def s_AugAssign(self, node):
        sym = '|' if type(node.op) is ast.BitOr else _BINOPS[type(node.op)]
        val = self.expr(node.value)
        target = node.target
        if type(target) is ast.Name:
            i = self.slots[target.id]
            op = self.binop(node, sym, self.load(target), val, inplace=True)

            def run(f):
                f[i] = op(f)
            return run
        if type(target) is not ast.Subscript or not self.collections:
            raise Unsupported("this assignment target is not supported", node.lineno)
        fail = self.fail
        obj, idx = self.expr(target.value), self.expr(target.slice)
        cell = self.cell     # the current element, read by `op` before it evaluates `val`
        op = self.binop(node, sym, lambda f: cell[0], val, inplace=True)

        def run(f):
            o = obj(f)
            i = idx(f)
            if type(o) is not list or type(i) is not int:
                raise fail(target, f"cannot assign to index {_show(i)} of {_show(o)}", 'type')
            try:
                cell[0] = o[i]
                v = op(f)
                cell[0] = None
                o[i] = v
            except IndexError:
                raise fail(target, f"index {i} out of range for a list of length {len(o)}") from None
        return run

    def s_If(self, node):
        test = self.expr(node.test)
        body = self.block(node.body)
        if not node.orelse:
            def run(f):
                if test(f):
                    return body(f)
            return run
        orelse = self.block(node.orelse)

        def run(f):
            if test(f):
                return body(f)
            return orelse(f)
        return run

    def s_While(self, node):
        rt = self.rt
        body = self.block(node.body)
        if type(node.test) is ast.Constant and node.test.value is True:
            def run(f):
                while True:
                    rt.left -= 1
                    if rt.left < 0:
                        rt.refill()
                    s = body(f)
                    if s is not None:
                        if s is _BREAK:
                            return None
                        if s is _RETURN:
                            return s
            return run
        test = self.expr(node.test)

        def run(f):
            while test(f):
                rt.left -= 1
                if rt.left < 0:
                    rt.refill()
                s = body(f)
                if s is not None:
                    if s is _BREAK:
                        return None
                    if s is _RETURN:
                        return s
        return run

    def s_For(self, node):
        rt = self.rt
        it = self.iterable(node.iter)
        body = self.block(node.body)
        if type(node.target) is ast.Name:
            i = self.slots[node.target.id]

            def run(f):
                for v in it(f):
                    rt.left -= 1
                    if rt.left < 0:
                        rt.refill()
                    f[i] = v
                    s = body(f)
                    if s is not None:
                        if s is _BREAK:
                            return None
                        if s is _RETURN:
                            return s
            return run
        set_ = self.store(node.target)

        def run(f):
            for v in it(f):
                rt.left -= 1
                if rt.left < 0:
                    rt.refill()
                set_(f, v)
                s = body(f)
                if s is not None:
                    if s is _BREAK:
                        return None
                    if s is _RETURN:
                        return s
        return run

    def s_Return(self, node):
        if node.value is None:
            def run(f):
                f[0] = None
                return _RETURN
            return run
        val = self.expr(node.value)

        def run(f):
            f[0] = val(f)
            return _RETURN
        return run

    def s_Break(self, node):
        return lambda f: _BREAK

    def s_Continue(self, node):
        return lambda f: _CONTINUE

    def s_Expr(self, node):
        if type(node.value) is not ast.Call:
            raise Unsupported("an expression on its own line must be a call", node.lineno)
        call = self.expr(node.value)

        def run(f):
            call(f)
        return run

    # ---- iteration -------------------------------------------------------

    def iterable(self, node):
        """`it(frame) -> iterable` for a for loop or a comprehension clause."""
        fail = self.fail
        if _is_range(node) and 'range' not in self.slots:
            if node.keywords:
                raise Unsupported("keyword arguments are not supported", node.lineno)
            if not 1 <= len(node.args) <= 3:
                return self.raiser(node, f"range() takes 1 to 3 arguments, got {len(node.args)}", 'arity')
            argc = tuple(self.expr(a) for a in node.args)
            make = self.make_range
            if len(argc) == 1:
                a0 = argc[0]

                def it(f):
                    v = a0(f)
                    if type(v) is int:
                        return range(v)
                    return make(node, (v,))
                return it

            def it(f):
                return make(node, [a(f) for a in argc])
            return it
        if not self.collections:
            raise Unsupported("for loops must run over range(...)", node.lineno)
        src = self.expr(node)

        def it(f):
            v = src(f)
            if _is_seq(type(v)):
                return v
            raise fail(node, f"cannot loop over {_show(v)}", 'type')
        return it

    def make_range(self, node, values):
        ints = []
        for v in values:
            t = type(v)
            if t is int or t is bool:
                ints.append(int(v))
            elif t is float and v - v == 0.0 and v == int(v):
                ints.append(int(v))      # a whole-number float counts as a whole number
            else:
                raise self.fail(node, f"range needs whole numbers, got {_show(v)}", 'type')
        if len(ints) == 3 and ints[2] == 0:
            raise self.fail(node, "range with step 0", 'domain')
        return range(*ints)

    # ---- expressions -----------------------------------------------------

    def expr(self, node):
        method = getattr(self, 'e_' + type(node).__name__, None)
        if method is None:
            raise Unsupported(f"{_what(node)} is not supported", getattr(node, 'lineno', None))
        return method(node)

    def e_Constant(self, node):
        v = node.value
        if v is not None and type(v) not in (int, float, bool):
            raise Unsupported("this literal is not supported", node.lineno)
        return lambda f: v

    def load(self, node):
        name = node.id
        fail = self.fail
        i = self.slots.get(name)
        if i is not None:
            if i <= self.params:
                return lambda f: f[i]

            def run(f):
                v = f[i]
                if v is _UNSET:
                    raise fail(node, f"variable '{name}' is used before it is assigned")
                return v
            return run
        if name in self.constants:
            v = self.constants[name]
            return lambda f: v
        if name in self.primitives or name in self.program._defs or name in self.builtins or name == 'range':
            if name in self.values and name not in self.primitives and name not in self.program._defs:
                v = self.values[name]
                return lambda f: v
            return self.raiser(node, f"'{name}' is a function, not a value", 'type')
        if name in self.values:
            v = self.values[name]
            return lambda f: v
        return self.raiser(node, f"name '{name}' is not defined", 'undefined')

    e_Name = load

    def e_UnaryOp(self, node):
        fail = self.fail
        x = self.expr(node.operand)
        kind = type(node.op)
        if kind is ast.Not:
            return lambda f: not x(f)
        if kind is ast.USub:
            def run(f):
                v = x(f)
                if type(v) is float:
                    return -v
                if _is_num(type(v)):
                    return -v
                raise fail(node, f"cannot negate {_show(v)}", 'type')
            return run

        def run(f):
            v = x(f)
            if _is_num(type(v)):
                return +v
            raise fail(node, f"cannot apply + to {_show(v)}", 'type')
        return run

    def e_BinOp(self, node):
        sym = '|' if type(node.op) is ast.BitOr else _BINOPS[type(node.op)]
        return self.binop(node, sym, self.expr(node.left), self.expr(node.right))

    def binop(self, node, sym, l, r, inplace=False):
        slow = self.slow_binop
        if sym == '+':
            def run(f):
                a = l(f)
                b = r(f)
                ta = type(a)
                if ta is float or ta is int:
                    try:
                        v = a + b
                    except Exception:
                        return slow(node, sym, a, b, inplace)
                    if type(v) is float or -_SMALL < v < _SMALL:
                        return v
                return slow(node, sym, a, b, inplace)
        elif sym == '-':
            def run(f):
                a = l(f)
                b = r(f)
                ta = type(a)
                if ta is float or ta is int:
                    try:
                        v = a - b
                    except Exception:
                        return slow(node, sym, a, b, inplace)
                    if type(v) is float or -_SMALL < v < _SMALL:
                        return v
                return slow(node, sym, a, b, inplace)
        elif sym == '*':
            def run(f):
                a = l(f)
                b = r(f)
                ta = type(a)
                tb = type(b)
                if (ta is float or ta is int) and (tb is float or tb is int):
                    try:
                        v = a * b
                    except Exception:
                        return slow(node, sym, a, b, inplace)
                    if type(v) is float or -_SMALL < v < _SMALL:
                        return v
                return slow(node, sym, a, b, inplace)
        elif sym == '/':
            def run(f):
                a = l(f)
                b = r(f)
                try:
                    return a / b
                except Exception:
                    return slow(node, sym, a, b, inplace)
        elif sym == '**':
            def run(f):
                a = l(f)
                b = r(f)
                if type(a) is float:
                    tb = type(b)
                    if tb is float or tb is int:
                        try:
                            v = a ** b
                        except Exception:
                            return slow(node, sym, a, b, inplace)
                        if type(v) is float:
                            return v
                return slow(node, sym, a, b, inplace)
        else:
            def run(f):
                return slow(node, sym, l(f), r(f), inplace)
        return run

    def slow_binop(self, node, sym, a, b, inplace=False):
        """Every case the fast paths do not take: checks, big numbers, collections, errors."""
        ta, tb = type(a), type(b)
        if _is_num(ta) and _is_num(tb) and sym != '|':
            if sym == '**':
                return self.power(node, a, b)
            op = _OPERATORS[sym]
            try:
                v = op(a, b)
            except ZeroDivisionError:
                raise self.fail(node, f"division by zero: {_show(a)} {sym} {_show(b)}", 'domain') from None
            except OverflowError:
                try:
                    v = op(_sat(a), _sat(b))
                except ZeroDivisionError:
                    raise self.fail(node, f"division by zero: {_show(a)} {sym} {_show(b)}", 'domain') from None
                except OverflowError:
                    raise self.fail(node, f"overflow: {_show(a)} {sym} {_show(b)}", 'domain') from None
            if type(v) is int and v.bit_length() > MAX_INT_BITS:
                raise Budget('size', "integer is too large")
            return v
        sa, sb = _is_seq(ta), _is_seq(tb)
        if sym in ('+', '|') and sa and sb and (ta is tuple) == (tb is tuple) \
                and (sym == '+' or (ta is _Bag and tb is _Bag)):
            if ta is tuple:
                self.rt.charge(len(a) + len(b))
                return a + b
            if inplace:
                self.rt.grow(len(a) + len(b), len(b))
                a.extend(b)
                return a
            self.rt.charge(len(a) + len(b))
            v = ta(a)
            v.extend(b)
            return v
        if sym == '*' and ((sa and (tb is int or tb is bool)) or (sb and (ta is int or ta is bool))):
            seq, k = (a, b) if sa else (b, a)
            k = max(int(k), 0)
            if not k or not seq:
                return type(seq)()
            self.rt.charge(len(seq) * k)
            return type(seq)(list(seq) * k)
        raise self.fail(node, f"cannot compute {_show(a)} {sym} {_show(b)}", 'type')

    def power(self, node, a, b):
        ta, tb = type(a), type(b)
        if ta is not float and tb is not float and b >= 0:
            a, b = int(a), int(b)
            if not -1 <= a <= 1 and a.bit_length() * b > MAX_INT_BITS:
                raise Budget('size', "integer power is too large")
            return a ** b
        try:
            v = a ** b
        except ZeroDivisionError:
            raise self.fail(node, f"zero to a negative power: {_show(a)} ** {_show(b)}", 'domain') from None
        except OverflowError:
            try:
                v = _sat(a) ** _sat(b)
            except ZeroDivisionError:
                raise self.fail(node, f"zero to a negative power: {_show(a)} ** {_show(b)}", 'domain') from None
            except OverflowError:      # overflow gives an infinity, as multiplication does
                v = -_INF if a < 0 and b == int(b) and int(b) % 2 else _INF
        if type(v) is complex:
            raise self.fail(node, f"negative number to a fractional power: {_show(a)} ** {_show(b)}", 'domain')
        return v

    def e_BoolOp(self, node):
        values = tuple(self.expr(v) for v in node.values)
        is_and = type(node.op) is ast.And
        if len(values) == 2:
            a, b = values
            if is_and:
                def run(f):
                    v = a(f)
                    return b(f) if v else v
            else:
                def run(f):
                    v = a(f)
                    return v if v else b(f)
            return run
        last = values[-1]
        first = values[:-1]
        if is_and:
            def run(f):
                for x in first:
                    v = x(f)
                    if not v:
                        return v
                return last(f)
        else:
            def run(f):
                for x in first:
                    v = x(f)
                    if v:
                        return v
                return last(f)
        return run

    def e_IfExp(self, node):
        test, body, orelse = self.expr(node.test), self.expr(node.body), self.expr(node.orelse)
        return lambda f: body(f) if test(f) else orelse(f)

    def e_Compare(self, node):
        fail = self.fail
        ops = []
        for op in node.ops:
            if type(op) in (ast.In, ast.NotIn):
                if not self.collections:
                    raise Unsupported("'in' is not supported", node.lineno)
                ops.append('in' if type(op) is ast.In else 'not in')
            else:
                ops.append(_CMPOPS[type(op)])
        l = self.expr(node.left)
        rs = [self.expr(c) for c in node.comparators]

        def bad(sym, a, b):
            return fail(node, f"cannot compare {_show(a)} {sym} {_show(b)}", 'type')

        if len(ops) == 1:
            sym, r = ops[0], rs[0]
            if sym == '<':
                def run(f):
                    a = l(f)
                    b = r(f)
                    try:
                        return a < b
                    except Exception:
                        raise bad(sym, a, b) from None
            elif sym == '<=':
                def run(f):
                    a = l(f)
                    b = r(f)
                    try:
                        return a <= b
                    except Exception:
                        raise bad(sym, a, b) from None
            elif sym == '>':
                def run(f):
                    a = l(f)
                    b = r(f)
                    try:
                        return a > b
                    except Exception:
                        raise bad(sym, a, b) from None
            elif sym == '>=':
                def run(f):
                    a = l(f)
                    b = r(f)
                    try:
                        return a >= b
                    except Exception:
                        raise bad(sym, a, b) from None
            else:
                op = _COMPARATORS[sym]

                def run(f):
                    a = l(f)
                    b = r(f)
                    try:
                        return op(a, b)
                    except Exception:
                        raise bad(sym, a, b) from None
            return run
        steps = tuple((sym, _COMPARATORS[sym], r) for sym, r in zip(ops, rs))

        def run(f):
            a = l(f)
            v = True
            for sym, op, r in steps:
                b = r(f)
                try:
                    v = op(a, b)
                except Exception:
                    raise bad(sym, a, b) from None
                if not v:
                    return v
                a = b
            return v
        return run

    def e_Tuple(self, node):
        elts = tuple(self.expr(e) for e in node.elts)
        return lambda f: tuple([e(f) for e in elts])

    def e_List(self, node, kind=list):
        if not self.collections:
            raise Unsupported(f"{_what(node)} is not supported", node.lineno)
        rt = self.rt
        elts = tuple(self.expr(e) for e in node.elts)
        n = len(elts)
        if n == 0:
            return lambda f: kind()

        def run(f):
            rt.charge(n)
            return kind([e(f) for e in elts])
        return run

    def e_Set(self, node):
        return self.e_List(node, _Bag)

    def e_Subscript(self, node):
        if not self.collections:
            raise Unsupported("indexing is not supported", node.lineno)
        fail = self.fail
        rt = self.rt
        obj = self.expr(node.value)
        if type(node.slice) is ast.Slice:
            parts = tuple(None if p is None else self.expr(p)
                          for p in (node.slice.lower, node.slice.upper, node.slice.step))

            def run(f):
                o = obj(f)
                vals = [None if p is None else p(f) for p in parts]
                if type(o) is not list and type(o) is not tuple:
                    raise fail(node, f"cannot slice {_show(o)}", 'type')
                for v in vals:
                    if v is not None and type(v) is not int:
                        raise fail(node, f"slice bounds must be whole numbers, got {_show(v)}", 'type')
                if vals[2] == 0:
                    raise fail(node, "slice with step 0", 'domain')
                v = o[slice(*vals)]
                rt.charge(len(v))
                return v
            return run
        idx = self.expr(node.slice)

        def run(f):
            o = obj(f)
            i = idx(f)
            if (type(o) is list or type(o) is tuple) and type(i) is int:
                try:
                    return o[i]
                except IndexError:
                    raise fail(node, f"index {_show(i)} out of range for length {len(o)}") from None
            raise fail(node, f"cannot take index {_show(i)} of {_show(o)}", 'type')
        return run

    def comprehension(self, node, kind):
        if not self.collections:
            raise Unsupported("comprehension is not supported", node.lineno)
        rt = self.rt
        elt = self.expr(node.elt)

        def inner(f, out):
            out.append(elt(f))
            if len(out) > MAX_ITEMS:
                raise Budget('size', f"collection with more than {MAX_ITEMS} elements")

        for gen in reversed(node.generators):
            inner = self.clause(self.iterable(gen.iter), self.store(gen.target),
                                tuple(self.expr(c) for c in gen.ifs), inner)
        loop = inner

        def run(f):
            out = kind()
            loop(f, out)
            rt.charge(len(out))
            return out
        return run

    def clause(self, it, set_, conds, inner):
        rt = self.rt

        def loop(f, out):
            for v in it(f):
                rt.left -= 1
                if rt.left < 0:
                    rt.refill()
                set_(f, v)
                for c in conds:
                    if not c(f):
                        break
                else:
                    inner(f, out)
        return loop

    def e_ListComp(self, node):
        return self.comprehension(node, list)

    e_GeneratorExp = e_ListComp

    def e_SetComp(self, node):
        return self.comprehension(node, _Bag)

    # ---- calls -----------------------------------------------------------

    def e_Call(self, node):
        if node.keywords:
            raise Unsupported("keyword arguments are not supported", node.lineno)
        func = node.func
        if type(func) is ast.Attribute:
            return self.method(node)
        if type(func) is not ast.Name:
            raise Unsupported("only named functions can be called", node.lineno)
        name = func.id
        nargs = len(node.args)
        if name in self.slots:
            return self.raiser(node, f"'{name}' is a variable, not a function", 'type')
        if name in self.primitives:
            return self.call_primitive(node, name)
        if name in self.constants:
            return self.raiser(node, f"'{name}' is a number, not a function", 'type')
        d = self.program._defs.get(name)
        if d is not None:
            if d.blank:
                return self.raiser(node, f"function '{name}' is blank")
            if len(d.params) != nargs:
                return self.raiser(node, f"{name}() takes {len(d.params)} arguments, got {nargs}", 'arity')
            return self.call_student(node, self.fn(name))
        spec = self.builtins.get(name)
        if spec is None:
            return self.raiser(node, f"function '{name}' is not defined", 'undefined')
        impl, lo, hi, fast = spec
        if not lo <= nargs <= hi:
            takes = str(lo) if lo == hi else f"{lo} to {hi}" if hi < 99 else f"at least {lo}"
            return self.raiser(node, f"{name}() takes {takes} arguments, got {nargs}", 'arity')
        argc = tuple(self.expr(a) for a in node.args)
        if fast is not None:
            made = fast(node, argc)
            if made is not None:
                return made
        failed = self.builtin_failed

        if nargs == 1:
            a0 = argc[0]

            def run(f):
                x = a0(f)
                try:
                    return impl(x)
                except (Failure, Budget):
                    raise
                except Exception as ex:
                    return failed(node, name, impl, (x,), ex)
            return run

        def run(f):
            args = [a(f) for a in argc]
            try:
                return impl(*args)
            except (Failure, Budget):
                raise
            except Exception as ex:
                return failed(node, name, impl, args, ex)
        return run

    def builtin_failed(self, node, name, impl, args, ex):
        if isinstance(ex, OverflowError):
            # an integer too large for a float, or a result too large: use infinities
            try:
                return impl(*[_sat(a) for a in args])
            except OverflowError:
                if name == 'exp':
                    return _INF
            except (Failure, Budget):
                raise
            except Exception:
                pass
        if isinstance(ex, _Oops):
            raise self.fail(node, *ex.args) from None
        shown = _show(args[0]) if len(args) == 1 else f"({_shows(args)})"
        kind = 'type' if isinstance(ex, TypeError) else 'domain'
        raise self.fail(node, f"{name} of {shown}", kind) from None

    def call_student(self, node, fn):
        rt = self.rt
        maxd = rt.max_depth
        proto = fn.proto
        argc = tuple(self.expr(a) for a in node.args)
        n = len(argc)

        def deep():
            return Budget('depth', f"more than {maxd} nested calls")

        if n == 0:
            def run(f):
                rt.left -= 1
                if rt.left < 0:
                    rt.refill()
                d = rt.depth + 1
                if d > maxd:
                    raise deep()
                rt.depth = d
                nf = proto[:]
                fn.body(nf)
                rt.depth = d - 1
                return nf[0]
        elif n == 1:
            a0 = argc[0]

            def run(f):
                x = a0(f)
                rt.left -= 1
                if rt.left < 0:
                    rt.refill()
                d = rt.depth + 1
                if d > maxd:
                    raise deep()
                rt.depth = d
                nf = proto[:]
                nf[1] = x
                fn.body(nf)
                rt.depth = d - 1
                return nf[0]
        elif n == 2:
            a0, a1 = argc

            def run(f):
                x = a0(f)
                y = a1(f)
                rt.left -= 1
                if rt.left < 0:
                    rt.refill()
                d = rt.depth + 1
                if d > maxd:
                    raise deep()
                rt.depth = d
                nf = proto[:]
                nf[1] = x
                nf[2] = y
                fn.body(nf)
                rt.depth = d - 1
                return nf[0]
        else:
            def run(f):
                args = [a(f) for a in argc]
                rt.left -= 1
                if rt.left < 0:
                    rt.refill()
                d = rt.depth + 1
                if d > maxd:
                    raise deep()
                rt.depth = d
                nf = proto[:]
                nf[1:1 + n] = args
                fn.body(nf)
                rt.depth = d - 1
                return nf[0]
        return run

    def call_primitive(self, node, name):
        rt = self.rt
        fail = self.fail
        prim = self.primitives[name]
        argc = tuple(self.expr(a) for a in node.args)

        def run(f):
            args = [a(f) for a in argc]
            rt.dl -= 1
            if rt.dl < 0:
                raise rt.draws_out()
            try:
                v = prim(rt.rng, *args)
                if type(v) is float:
                    return v
                return _adopt(v)
            except (Failure, Budget):
                raise
            except Exception as ex:
                raise fail(node, f"{name}({_shows(args)}) raised {type(ex).__name__}: {ex}") from ex
        return run

    def method(self, node):
        if not self.collections:
            raise Unsupported("attribute access is not supported", node.lineno)
        name = node.func.attr
        if name not in _METHODS:
            raise Unsupported(f"method '{name}' is not supported", node.lineno)
        fail = self.fail
        rt = self.rt
        recv = self.expr(node.func.value)
        argc = tuple(self.expr(a) for a in node.args)
        if name in ('append', 'add'):       # either name works on a list or a set
            if len(argc) != 1:
                raise Unsupported(f"wrong number of arguments for {name}", node.lineno)
            a0 = argc[0]

            def run(f):
                xs = recv(f)
                v = a0(f)
                if type(xs) is list or type(xs) is _Bag:
                    xs.append(v)
                    if len(xs) > MAX_ITEMS:
                        raise Budget('size', f"collection with more than {MAX_ITEMS} elements")
                    return None
                raise fail(node, f"cannot {name} to {_show(xs)}", 'type')
            return run
        if name == 'extend':
            a0 = argc[0]

            def run(f):
                xs = recv(f)
                v = a0(f)
                if (type(xs) is list or type(xs) is _Bag) and _is_seq(type(v)):
                    rt.grow(len(xs) + len(v), len(v))
                    xs.extend(v)
                    return None
                raise fail(node, f"cannot extend {_show(xs)} with {_show(v)}", 'type')
            return run
        a0 = argc[0] if argc else None

        def run(f):
            xs = recv(f)
            i = -1 if a0 is None else a0(f)
            if (type(xs) is list or type(xs) is _Bag) and type(i) is int:
                try:
                    return xs.pop(i)
                except IndexError:
                    raise fail(node, f"pop index {i} out of range for length {len(xs)}") from None
            raise fail(node, f"cannot pop from {_show(xs)}", 'type')
        return run

    # ---- built-ins -------------------------------------------------------

    def make_builtins(self):
        """({name: (impl, min args, max args, fast-path factory or None)}, {name: value})."""
        rt = self.rt
        fail = self.fail
        failed = self.builtin_failed

        def math1(name, fn):
            def factory(node, argc):
                a0 = argc[0]

                def run(f):
                    x = a0(f)
                    try:
                        return fn(x)
                    except Exception as ex:
                        return failed(node, name, fn, (x,), ex)
                return run
            return factory

        def unif_fast(node, argc):
            if argc:
                return None

            def run(f):
                rt.dl -= 1
                if rt.dl < 0:
                    raise rt.draws_out()
                return rt.random()
            return run

        def unif(a, b):
            if not (_is_num(type(a)) and _is_num(type(b))):
                raise _Oops(f"unif needs two numbers, got ({_shows((a, b))})", 'type')
            rt.dl -= 1
            if rt.dl < 0:
                raise rt.draws_out()
            return a + (b - a) * rt.random()

        def coin_fast(node, argc):
            p0 = argc[0]

            def run(f):
                p = p0(f)
                try:
                    ok = 0.0 <= p <= 1.0
                except Exception:
                    ok = False
                if not ok or not _is_num(type(p)):
                    raise fail(node, f"probability {_show(p)} not in [0, 1]",
                               'domain' if _is_num(type(p)) else 'type')
                rt.dl -= 1
                if rt.dl < 0:
                    raise rt.draws_out()
                return rt.random() < p
            return run

        def die_fast(node, argc):
            n0 = argc[0]

            def run(f):
                n = n0(f)
                t = type(n)
                if t is float and n - n == 0.0 and n == int(n):
                    n = int(n)           # a whole-number float counts as a whole number
                elif t is not int:
                    raise fail(node, f"die needs a positive whole number, got {_show(n)}",
                               'domain' if t is float else 'type')
                if not 1 <= n <= _EXACT:
                    raise fail(node, f"die needs a positive whole number, got {_show(n)}", 'domain')
                rt.dl -= 1
                if rt.dl < 0:
                    raise rt.draws_out()
                return 1 + int(rt.random() * n)
            return run

        def round_(x, digits=None):
            if not _is_num(type(x)):
                raise TypeError
            if digits is None:
                return round(x)
            if type(digits) is not int or not -400 <= digits <= 400:
                raise _Oops(f"cannot round to {_show(digits)} digits", 'type')
            return round(x, digits)

        def number(fn):
            def impl(x):
                if not _is_num(type(x)):
                    raise TypeError
                return fn(x)
            return impl

        def extreme(fn):
            def impl(*args):
                if len(args) == 1 and not _is_seq(type(args[0])):
                    raise TypeError
                return fn(*args)
            return impl

        table = {
            'unif': (unif, 0, 2, unif_fast),
            'coin': (None, 1, 1, coin_fast),
            'die': (None, 1, 1, die_fast),
            'log': (math.log, 1, 1, math1('log', math.log)),
            'ln': (math.log, 1, 1, math1('ln', math.log)),
            'exp': (math.exp, 1, 1, math1('exp', math.exp)),
            'sqrt': (math.sqrt, 1, 1, math1('sqrt', math.sqrt)),
            'sin': (math.sin, 1, 1, None),
            'cos': (math.cos, 1, 1, None),
            'tan': (math.tan, 1, 1, None),
            'floor': (math.floor, 1, 1, None),
            'ceil': (math.ceil, 1, 1, None),
            'abs': (number(abs), 1, 1, None),
            'int': (number(int), 1, 1, None),
            'float': (number(float), 1, 1, None),
            'round': (round_, 1, 2, None),
            'min': (extreme(min), 1, 99, None),
            'max': (extreme(max), 1, 99, None),
        }
        if self.collections:
            def seq(x):
                if not _is_seq(type(x)):
                    raise TypeError
                return x

            def len_(x):
                return len(seq(x))

            def sum_(x):
                v = sum(seq(x))
                if type(v) is int and v.bit_length() > MAX_INT_BITS:
                    raise Budget('size', "integer is too large")
                return v

            def sorted_(x):
                rt.charge(len(seq(x)))
                return sorted(x)

            def list_(x=()):
                rt.charge(len(seq(x)))
                return list(x)

            def set_(x=()):
                rt.charge(len(seq(x)))
                return _Bag(x)

            def range_(*args):
                r = self.make_range(None, args)
                try:
                    n = len(r)
                except OverflowError:
                    n = MAX_ITEMS + 1
                rt.charge(n)
                return list(r)

            table.update({
                'len': (len_, 1, 1, None), 'sum': (sum_, 1, 1, None), 'sorted': (sorted_, 1, 1, None),
                'list': (list_, 0, 1, None), 'set': (set_, 0, 1, None), 'range': (range_, 1, 3, None),
            })
        return table, {'pi': math.pi, 'e': math.e, 'inf': _INF}


class _Oops(Exception):
    """A built-in's own error (message, kind), turned into a Failure at the call site."""


_OPERATORS = MappingProxyType({
    '+': operator.add, '-': operator.sub, '*': operator.mul, '/': operator.truediv,
    '//': operator.floordiv, '%': operator.mod,
})
_COMPARATORS = MappingProxyType({
    '==': operator.eq, '!=': operator.ne, '<': operator.lt, '<=': operator.le,
    '>': operator.gt, '>=': operator.ge, 'is': operator.is_, 'is not': operator.is_not,
    'in': lambda a, b: _contains(b, a), 'not in': lambda a, b: not _contains(b, a),
})


def _contains(collection, item) -> bool:
    if not _is_seq(type(collection)):
        raise TypeError
    return item in collection
