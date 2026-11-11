"""Exam definition types.

An exam package builds an `Exam` and adds questions to it. Once built, each
`Question` is frozen data: it can be described as JSON (`describe`), hashed
(`stage_hash`) and displayed. Nothing here knows about any particular exam.
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
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

if TYPE_CHECKING:
    from .context import Context

# --- review markers ---------------------------------------------------------

REVIEW = re.compile(r'\[REVIEW\b', re.I)   # also catches a marker cut off before its ']'


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


@dataclass(frozen=True)
class Field:
    """One answer slot: a printed label and, usually, where its box is."""
    label: str
    region: Region | None = None


@dataclass(frozen=True)
class Cue:
    """Printed evidence that distinguishes variants.

    Either a `region` with a question to `ask` the model about the printed text
    there and a map `reads` from readings to variant names, or a function
    `fn(ctx) -> variant | None` for what a map cannot express.
    """
    name: str
    region: Region | None = None
    ask: str = ""
    reads: Mapping[str, str] = field(default_factory=dict)
    fn: Callable[['Context'], str | None] | None = None


@dataclass(frozen=True)
class Landmark:
    """Printed evidence on a scanned sheet of which printed page it is, used to
    split a stack of scans into submissions (`core.split`).

    The model is asked `ask` about the printed text in `region` (printed-page
    coordinates, on whatever sheet is being looked at). With `reads`, a reading
    maps (normalised, as for a `Cue`) to a printed page number, e.g. the title
    that only page 1 carries: `reads={'Midterm 2': 1}`. Without `reads`, the
    reading is the printed page number itself (a page-number footer).
    `region.page` is the first printed page that carries the landmark.
    """
    name: str
    region: Region
    ask: str
    reads: Mapping[str, int] | None = None


# --- results and grades -----------------------------------------------------

@dataclass(frozen=True)
class Grade:
    """A result value (`None` is undecided) plus display flags such as 'blank'."""
    value: bool | int | float | None = None
    flags: tuple[str, ...] = ()
    reason: str | None = None

    def to_json(self) -> dict:
        return {'value': self.value, 'flags': list(self.flags), 'reason': self.reason}

    @staticmethod
    def from_json(body: Mapping) -> 'Grade':
        return Grade(body.get('value'), tuple(body.get('flags') or ()), body.get('reason'))


@dataclass(frozen=True)
class Bool:
    """A true/false result. Accepts the standard outcomes directly."""
    counts: bool = True

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
        raise ValueError(f"cannot read {x!r} as a bool result")

    def points(self, value: Any) -> float:
        return 1.0 if value else 0.0

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
    """A numeric result, `int` or `float`, optionally limited to a range."""
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


ResultSpec = Bool | Number


def grade_kind(part: str) -> str:
    """The artifact kind holding the grade of one result part ('' is the only part)."""
    return f'grade:{part}' if part else 'grade'


# --- stages -----------------------------------------------------------------

@dataclass(frozen=True)
class Stage:
    """One step for one submission and question.

    `name` is also the kind of the artifact it writes: 'variant', 'transcript',
    'parse', 'evidence' or 'grade'. `fn(ctx)` returns the artifact body (text
    or JSON-serializable data, or an `Evidence`).
    """
    name: str
    fn: Callable[['Context'], Any]
    inputs: tuple[str, ...] = ()
    uses_model: bool = False
    editable: bool = False          # graders may edit the artifact directly
    isolate: bool = False           # run in a subprocess with a wall-clock limit
    timeout: float | None = None    # seconds; None means the runner's default
    uses_variant: bool = False      # result depends on the resolved variant
    accepts_markers: bool = False   # consume inputs that still carry [REVIEW: ...]
    shape: str = 'text'             # how the body is edited: 'text' | 'fields' ({label: answer}) | 'code'
    # Facets over what this stage writes (`core.focus.Facet`): display, not definition,
    # so they are left out of `describe` and never change the stage's hash.
    facets: tuple[Any, ...] = field(default=(), metadata={'describe': False})


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    pages: tuple[int, ...]                 # printed pages holding the answer
    result: Mapping[str, ResultSpec]       # part name -> spec; '' for a single result
    fields: tuple[Field, ...]
    stages: tuple[Stage, ...]
    export_pages: tuple[int, ...] = ()
    variants: tuple[str, ...] = ()
    cues: tuple[Cue, ...] = ()
    require: int | None = None             # cues that must agree to resolve; None = all
    trust: int = 2                         # agreeing cues needed to count without review
    facets: tuple[Any, ...] = field(default=(), metadata={'describe': False})   # question-level facets
    _hashes: dict = field(init=False, default_factory=dict, compare=False, repr=False)

    def stage(self, name: str) -> Stage | None:
        return next((s for s in self.stages if s.name == name), None)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(f.label for f in self.fields)

    def column(self, part: str) -> str:
        """Name of the grid column for one result part."""
        return f'{self.id}.{part}' if part else self.id


class Exam:
    """One printed exam: its pages and its questions."""

    def __init__(self, id: str, *, title: str = "", pages: int,
                 front: Sequence[int] = (), extra: Sequence[int] = (),
                 scan_window: Sequence[int] = (0, 0, 1000, 1000), cover_pages: int = 0,
                 crop_pad: int = 12, landmarks: Sequence[Landmark] = (),
                 name_region: Region | None = None):
        self.id = id
        self.title = title or id
        self.pages = pages
        self.front = tuple(front)
        self.extra = tuple(extra)
        self.scan_window = tuple(scan_window)   # initial default; the workspace may override
        self.cover_pages = cover_pages          # likewise
        self.crop_pad = crop_pad                # per-mille of the printed page around every crop
        self.landmarks = tuple(landmarks)       # how to tell which printed page a scanned sheet is (intake)
        self.name_region = name_region          # where the student writes their name (intake: who is it)
        names = [lm.name for lm in self.landmarks]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate landmark names {names}")
        self.questions: dict[str, Question] = {}   # live: base definitions plus overrides
        self.base: dict[str, Question] = {}        # as built by the package
        self.overrides: dict[str, Any] = {}        # applied overrides {path: JSON value}; see `overrides`
        self.path: Path | None = None           # set by `load_exam`
        self.code_hash: str = ''

    def question(self, id: str, *, title: str = "", pages: Sequence[int],
                 result: ResultSpec | Mapping[str, ResultSpec],
                 fields: Sequence[Field] = (), stages: Sequence[Stage],
                 export_pages: Sequence[int] = (), variants: Sequence[str] = (),
                 cues: Sequence[Cue] = (), require: int | None = None,
                 trust: int = 2, facets: Sequence[Any] = ()) -> Question:
        if id in self.questions:
            raise ValueError(f"duplicate question {id!r}")
        if not isinstance(result, Mapping):
            result = {'': result}
        stages = list(stages)
        names = [s.name for s in stages]
        if len(set(names)) != len(names):
            raise ValueError(f"{id}: duplicate stage names {names}")
        if variants and 'variant' not in names:
            from .variants import read_variant
            stages.insert(0, read_variant())
        if cues and not variants:
            raise ValueError(f"{id}: cues without variants")
        for i, s in enumerate(stages):
            for kind in s.inputs:
                if kind not in [t.name for t in stages[:i]]:
                    raise ValueError(f"{id}: stage {s.name!r} reads {kind!r}, which no earlier stage writes")
        q = Question(id=id, title=title or id, pages=tuple(pages), result=dict(result),
                     fields=tuple(fields), stages=tuple(stages),
                     export_pages=tuple(export_pages) or tuple(pages),
                     variants=tuple(variants), cues=tuple(cues), require=require, trust=trust,
                     facets=tuple(facets))
        from .focus import facets as all_facets
        all_facets(q)                    # checks names: built-in, stage and question facets together
        self.questions[id] = self.base[id] = q
        return q


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
    key held in code changes the description of exactly the stages that use
    it. Library functions are described by name. What this cannot see (data
    reached indirectly) is covered by the coarser exam-wide code hash.
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


def stage_hash(question: Question, stage: Stage) -> str:
    """Hash of the definition as one stage sees it.

    Covers the stage's own configuration (including exam code it calls) and the
    question's labels, result and variants; for the variant stage also the cues.
    Editing a key outdates the checks that used it and nothing else.
    """
    if stage.name not in question._hashes:
        seen = {'stage': stage, 'labels': question.labels,
                'result': question.result, 'variants': question.variants}
        if stage.name == 'variant':
            seen.update(cues=question.cues, require=question.require)
        question._hashes[stage.name] = digest(seen)
    return question._hashes[stage.name]


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
