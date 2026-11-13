"""Exam definition types.

An exam package builds an `Exam` and adds questions to it. A question groups
**fields** (named slots for one value read from paper) and **checks**
(functions from field values to one proposed grade and evidence). Once built,
every definition is frozen data: it can be described as JSON (`describe`),
hashed (`digest`) and displayed. Nothing here knows about any particular exam.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import importlib.util
import inspect
import json
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# --- review markers ---------------------------------------------------------

REVIEW = re.compile(r'\[REVIEW\b', re.I)   # also catches a marker cut off before its ']'


def one_line(text: str) -> str:
    """A value on one line: line breaks become single spaces."""
    if '\n' not in text and '\r' not in text:
        return text
    return ' '.join(part.strip() for part in text.splitlines() if part.strip())


def has_marker(body: Any) -> bool:
    """True if any string inside a JSON-like body carries a `[REVIEW: ...]` marker."""
    if isinstance(body, str):
        return bool(REVIEW.search(body))
    if isinstance(body, Mapping):
        return any(has_marker(k) or has_marker(v) for k, v in body.items())
    if isinstance(body, (list, tuple)):
        return any(has_marker(v) for v in body)
    return False


# --- geometry and fields ----------------------------------------------------

@dataclass(frozen=True)
class Region:
    """A rectangle on a printed exam page, in per-mille of that page."""
    page: int
    left: int
    top: int
    right: int
    bottom: int


def check_region(region: Region, pages: int, what: str) -> None:
    """`ValueError` unless `region` lies on a printed page 1..pages, inside 0..1000."""
    if not 1 <= region.page <= pages:
        raise ValueError(f"{what}: page {region.page} is not a printed page (1..{pages})")
    if not (0 <= region.left < region.right <= 1000 and 0 <= region.top < region.bottom <= 1000):
        raise ValueError(f"{what}: a region is per-mille of the printed page and needs "
                         f"0 <= left < right <= 1000 and 0 <= top < bottom <= 1000, got {region}")


@dataclass(frozen=True)
class Field:
    """A named slot for one value read from paper.

    `region` is where to look (it may be left unset and given in the app).
    `read` is a reader for this field alone (`Printed`, say); None, the
    default: the question's reader reads it with the question's other fields,
    and without one a person types it. A value is always text: one line,
    unless the field is `multiline` (None, the default: as its reader says,
    which is one line for all but code).
    """
    label: str
    region: Region | None = None
    read: Any = None
    multiline: bool | None = None


@dataclass(frozen=True)
class Landmark:
    """Printed evidence on a scanned sheet of which printed page it is, used to
    split a stack of scans into submissions (`core.split`).

    The model is asked `ask` about the printed text in `region` (printed-page
    coordinates, on whatever sheet is being looked at). With `reads`, a reading
    maps (normalised, as for a printed field) to a printed page number, e.g. the title
    that only page 1 carries: `reads={'Midterm 2': 1}`. Without `reads`, the
    reading is the printed page number itself (a page-number footer).
    `region.page` is the first printed page that carries the landmark.
    """
    name: str
    region: Region
    ask: str
    reads: Mapping[str, int] | None = None


ROSTER_FIELDS = ('name', 'id', 'email')     # what a roster row may hold (`core.identity`)


@dataclass(frozen=True)
class Identity:
    """A box where the student writes who they are (a name, an ID, ...), read by
    the model to propose a roster entry (`core.identity`). Like a printed
    field: a `region` on a printed page and what to `ask` about it, here about the
    handwriting. `roster` names the roster field the reading is compared with
    ('name', 'id' or 'email'); None shows the reading without comparing it.
    Several boxes may share a roster field (first and last name): their
    readings are joined."""
    name: str
    region: Region
    ask: str
    roster: str | None = None


# --- grades -------------------------------------------------------------------

@dataclass(frozen=True)
class Grade:
    """A grade value (`None` is undecided) plus display flags such as 'blank',
    and the reason it is undecided."""
    value: bool | int | float | None = None
    flags: tuple[str, ...] = ()
    reason: str | None = None

    def to_json(self) -> dict:
        return {'value': self.value, 'flags': list(self.flags), 'reason': self.reason}

    @staticmethod
    def from_json(body: Mapping | None) -> 'Grade':
        body = body or {}
        return Grade(body.get('value'), tuple(body.get('flags') or ()), body.get('reason'))


@dataclass(frozen=True)
class Bool:
    """A true/false grade, worth `worth` points when true. Accepts the standard
    outcomes directly: 'correct', 'incorrect', 'blank' (false, flagged) and None."""
    counts: bool = True
    worth: float = 1.0

    def coerce(self, x: Any) -> Grade:
        if isinstance(x, Grade):
            if x.value is not None and not isinstance(x.value, bool):
                raise ValueError(f"expected a bool, got {x.value!r}")
            return x
        if x is None:
            return Grade(None)
        if x == 'correct' or x is True:
            return Grade(True)
        if x == 'incorrect' or x is False:
            return Grade(False)
        if x == 'blank':
            return Grade(False, ('blank',))
        raise ValueError(f"cannot read {x!r} as a bool grade")

    def points(self, value: Any) -> float:
        return self.worth if value else 0.0

    def parse(self, text: str) -> bool:
        """Read a grader's typed value."""
        t = text.strip().lower()
        if t in ('1', 'true', 'yes', 'y', 'correct'):
            return True
        if t in ('0', 'false', 'no', 'n', 'incorrect'):
            return False
        raise ValueError(f"expected true or false, got {text!r}")


@dataclass(frozen=True)
class Number:
    """A numeric grade, `int` or `float`, optionally limited to a range; the
    value is the points."""
    type: type = float
    min: float | None = None
    max: float | None = None
    counts: bool = True

    def coerce(self, x: Any) -> Grade:
        if isinstance(x, Grade):
            if x.value is None:
                return x
            return dataclasses.replace(x, value=self._check(x.value))
        if x is None:
            return Grade(None)
        if x == 'blank':
            return Grade(self._check(self.min or 0), ('blank',))
        return Grade(self._check(x))

    def _check(self, v: Any) -> int | float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"expected a number, got {v!r}")
        if self.type is int:
            if v != int(v):
                raise ValueError(f"expected an integer, got {v!r}")
            v = int(v)
        else:
            v = float(v)
        if (self.min is not None and v < self.min) or (self.max is not None and v > self.max):
            raise ValueError(f"{v} is outside [{self.min}, {self.max}]")
        return v

    def points(self, value: Any) -> float:
        return float(value or 0)

    def parse(self, text: str) -> int | float:
        return self._check(float(text.strip()))


Points = Bool | Number


# --- checks and questions -----------------------------------------------------

@dataclass(frozen=True)
class Check:
    """A function from field values to one proposed grade and evidence.

    `fn(ctx) -> Result` (or a bare grade: a value, an outcome, a `Grade`); it
    reads fields with `ctx.read(label)` or `ctx.read(label, question=...)`,
    and what it read is recorded. None: a person decides every grade.
    `points` is the grade's spec (`Bool()`, `Number(...)`) and what it is worth.
    A check runs in a worker process with a wall-clock limit (`timeout`
    seconds, default 60) unless `isolate` is False. If `fn` has a method
    `validate(question, exam)`, `Exam.question` calls it, and it raises
    `ValueError` for a configuration that does not fit.
    """
    fn: Callable[[Any], Any] | None = None
    points: Points = Bool()
    timeout: float | None = None
    isolate: bool = True
    name: str = field(default='check', metadata={'describe': False})


@dataclass(frozen=True)
class Question:
    """A group: some fields and any number of checks. It has no state."""
    id: str
    title: str
    pages: tuple[int, ...]                 # printed pages holding the answer
    fields: tuple[Field, ...]
    read: Any = None                       # reads the fields that have no reader of their own
    checks: tuple[Check, ...] = ()
    facets: tuple[Any, ...] = field(default=(), metadata={'describe': False})

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(f.label for f in self.fields)

    def field(self, label: str) -> Field | None:
        return next((f for f in self.fields if f.label == label), None)

    def check(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    @property
    def written(self) -> tuple[Field, ...]:
        """The fields read by the question's reader (or typed, without one)."""
        return tuple(f for f in self.fields if f.read is None)

    def groups(self) -> list[tuple[Any, tuple[Field, ...]]]:
        """What reads what: (reader, fields), one per field with its own reader,
        then the question's reader with every other field."""
        out = [(f.read, (f,)) for f in self.fields if f.read is not None]
        if self.read is not None and self.written:
            out.append((self.read, self.written))
        return out

    def reader_of(self, label: str) -> Any:
        f = self.field(label)
        return None if f is None else (f.read if f.read is not None else self.read)

    def multiline(self, label: str) -> bool:
        """Whether a field's value may span lines: the field's own setting, else its reader's."""
        f = self.field(label)
        if f is None or f.multiline is not None:
            return bool(f and f.multiline)
        return bool(getattr(self.reader_of(label), 'multiline', False))

    def column(self, check: str) -> str:
        """The grid column of one check: the question id when it is the only one."""
        return self.id if len(self.checks) <= 1 else f'{self.id}.{check}'

    def columns(self) -> list[str]:
        return [self.column(c.name) for c in self.checks] or [self.id]


def key(question: str, label: str) -> str:
    """How a field (or a check) is addressed in storage: 'question/label'."""
    return f'{question}/{label}'


def split_key(k: str) -> tuple[str, str]:
    question, _, label = k.partition('/')
    return question, label


class Exam:
    """One printed exam: its pages and its questions."""

    def __init__(self, id: str, *, title: str = "", pages: int,
                 scan_window: Sequence[int] = (0, 0, 1000, 1000), cover_pages: int = 0,
                 crop_pad: int = 12, landmarks: Sequence[Landmark] = (),
                 identity: Sequence[Identity] = ()):
        self.id = id
        self.title = title or id
        self.pages = pages
        self.scan_window = tuple(scan_window)   # initial default; the workspace may override
        self.cover_pages = cover_pages          # likewise
        self.crop_pad = crop_pad                # per-mille of the printed page around every crop
        self.landmarks = tuple(landmarks)       # how to tell which printed page a scanned sheet is (intake)
        self.identity = tuple(identity)         # where students write who they are (intake: who is it)
        for what, items in (('landmark', self.landmarks), ('identity', self.identity)):
            names = [x.name for x in items]
            if len(set(names)) != len(names):
                raise ValueError(f"duplicate {what} names {names}")
        for f in self.identity:
            if f.roster not in (None, *ROSTER_FIELDS):
                raise ValueError(f"identity {f.name!r}: roster must be one of {ROSTER_FIELDS} or None")
        for what, items in (('landmark', self.landmarks), ('identity', self.identity)):
            for x in items:
                check_region(x.region, pages, f"{what} {x.name!r}")
        self.questions: dict[str, Question] = {}   # live: base definitions plus overrides
        self.base: dict[str, Question] = {}        # as built by the package
        self.overrides: dict[str, Any] = {}        # applied overrides {path: JSON value}; see `overrides`
        self.path: Path | None = None           # set by `load_exam`
        self.code_hash: str = ''
        self._hashes: dict = {}

    def question(self, id: str, *, title: str = "", pages: Sequence[int],
                 fields: Sequence[Field] = (), read: Any = None,
                 check: Any = None, checks: Mapping[str, Any] | None = None,
                 facets: Sequence[Any] = ()) -> Question:
        """Add a question: its fields, the reader of the fields that have none
        of their own (`read`), and its checks: one (`check`, named 'check'),
        or several by name (`checks`). A check is a `Check` or a bare function
        (a `Check` worth one `Bool` point). Without checks the question only
        holds fields (the exam's printing, say, which other questions read)."""
        if id in self.questions:
            raise ValueError(f"duplicate question {id!r}")
        if not id or '/' in id:
            raise ValueError(f"question id {id!r}: no '/' and not empty")
        if check is not None and checks is not None:
            raise ValueError(f"{id}: give `check` or `checks`, not both")
        named = {'check': check} if check is not None else dict(checks or {})
        built = []
        for name, c in named.items():
            if not name or '/' in name or '.' in name:
                raise ValueError(f"{id}: check name {name!r}: no '/' or '.' and not empty")
            c = c if isinstance(c, Check) else Check(c)
            built.append(dataclasses.replace(c, name=name))
        q = Question(id=id, title=title or id, pages=tuple(pages), fields=tuple(fields), read=read,
                     checks=tuple(built), facets=tuple(facets))
        self._check(q)
        self.questions[id] = self.base[id] = q
        for c in q.checks:              # a check may check its configuration against the question
            validate = getattr(c.fn, 'validate', None)
            if callable(validate):
                try:
                    validate(q, self)
                except ValueError:
                    del self.questions[id], self.base[id]
                    raise
        from .focus import facets as all_facets
        all_facets(q)                    # checks names: built-in, reader, check and question facets together
        return q

    def _check(self, q: Question) -> None:
        """Mistakes in a question's data that are mistakes whatever the exam: `ValueError`."""
        dupes = sorted({n for n in q.labels if q.labels.count(n) > 1})
        if dupes:
            raise ValueError(f"{q.id}: duplicate field labels {dupes}")
        if any('/' in label or not label for label in q.labels):
            raise ValueError(f"{q.id}: a field label is not empty and has no '/'")
        for p in q.pages:
            if not 1 <= p <= self.pages:
                raise ValueError(f"{q.id}: page {p} is not a printed page (1..{self.pages})")
        for f in q.fields:
            if f.region is not None:
                check_region(f.region, self.pages, f"{q.id}: field {f.label!r}")
            validate = getattr(f.read, 'validate', None)
            if callable(validate):
                validate(f"{q.id}: field {f.label!r}")

    def check_hash(self, q: Question, c: Check) -> str:
        """Digest of a check's definition as it computes: its function and that
        function's configuration (keys, notes; exam code it calls). Not its
        points (totals follow at once) nor the question's other fields."""
        hit = self._hashes.get(id(c))
        if hit is None or hit[0] is not c:      # the entry keeps `c` alive, so its id is not reused
            hit = self._hashes[id(c)] = (c, digest(c.fn))
        return hit[1]


# --- describing and hashing definitions --------------------------------------

def _is_exam_code(fn) -> bool:
    """A plain function defined outside the library and the standard library:
    its source is part of the exam's definition."""
    root = (getattr(fn, '__module__', '') or '').split('.')[0]
    return bool(root) and root != __name__.split('.')[0] and root not in sys.stdlib_module_names \
        and root not in ('numpy', 'scipy', 'sympy', 'PIL')


def _code_names(code) -> set[str]:
    names = set(code.co_names)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            names |= _code_names(c)
    return names


def describe(x: Any, _seen: frozenset = frozenset()) -> Any:
    """A JSON-serializable description of a definition value.

    Dataclasses become dicts of their fields. A function written in an exam
    package becomes a hash of its source together with the values it closes
    over and the module-level data and functions it names, so that editing a
    key held in code changes the description of exactly the checks that use
    it. Library functions are described by name. What this cannot see (data
    reached indirectly) is covered by the exam-wide code hash.
    """
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        d = {f.name: describe(getattr(x, f.name), _seen) for f in dataclasses.fields(x)
             if f.init and f.metadata.get('describe') is not False}
        d['__type__'] = type(x).__qualname__
        return d
    if isinstance(x, Mapping):
        return {str(k): describe(v, _seen) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple)):
        return [describe(v, _seen) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted((describe(v, _seen) for v in x), key=repr)
    if isinstance(x, functools.partial):
        return {'__partial__': describe(x.func, _seen), 'args': describe(x.args, _seen),
                'kw': describe(x.keywords, _seen)}
    if isinstance(x, types.ModuleType):
        return f'<module {x.__name__}>'
    if isinstance(x, types.FunctionType) and _is_exam_code(x):
        name = f'{x.__module__}:{x.__qualname__}'
        if id(x) in _seen:
            return name
        seen = _seen | {id(x)}
        try:
            src = inspect.getsource(x)
        except (OSError, TypeError):
            src = repr(x.__code__.co_code) + repr(x.__code__.co_consts)
        cells = [describe(c.cell_contents, seen) for c in (x.__closure__ or ()) if _has_contents(c)]
        used = {n: describe(x.__globals__[n], seen) for n in sorted(_code_names(x.__code__))
                if n in x.__globals__}
        return {'__fn__': name, 'src': hashlib.sha256(src.encode()).hexdigest()[:16],
                'closure': cells, 'globals': used}
    if isinstance(x, type) or callable(x):
        name = getattr(x, '__qualname__', None)
        if name:
            return f"{getattr(x, '__module__', '')}:{name}"
    return f'<{type(x).__module__}:{type(x).__qualname__}>'


def _has_contents(cell) -> bool:
    try:
        cell.cell_contents
        return True
    except ValueError:
        return False


def digest(x: Any) -> str:
    return hashlib.sha256(json.dumps(describe(x), sort_keys=True).encode()).hexdigest()[:16]


# --- loading an exam package --------------------------------------------------

def load_exam(path: str | Path) -> Exam:
    """Import an exam package from its directory and return its `exam`.

    The package is `path/`, containing `exam.py` (or an `__init__.py`) that
    exposes `exam`. It is imported under the directory's name.
    """
    path = Path(path).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"exam package not found: {path}")
    name = path.name
    for cached in [m for m in sys.modules if m == name or m.startswith(name + '.')]:
        del sys.modules[cached]         # every load builds a fresh exam; nothing is shared between loads
    init = path / '__init__.py'
    spec = importlib.util.spec_from_file_location(
        name, init if init.exists() else None, submodule_search_locations=[str(path)])
    if init.exists():
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[name] = pkg
        spec.loader.exec_module(pkg)
    else:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(path)]
        sys.modules[name] = pkg
    exam = getattr(pkg, 'exam', None)
    if not isinstance(exam, Exam):
        mod = importlib.import_module(f'{name}.exam')
        exam = getattr(mod, 'exam', None)
    if not isinstance(exam, Exam):
        raise ValueError(f"{path} does not expose an `exam`")
    exam.path = path
    h = hashlib.sha256()
    for f in sorted(path.rglob('*.py')):
        h.update(f.relative_to(path).as_posix().encode())
        h.update(f.read_bytes())
    exam.code_hash = h.hexdigest()[:16]
    return exam
