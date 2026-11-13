"""Live edits of exam data: overrides layered on the definition a package builds.

An override replaces one plain-data value of the definition (`Exam.roots`: each
question by id, and the print area), addressed by a path:

    @print_area                                where the printed page sits on a scanned sheet
    <question>/pages                           pages holding the answer
    <question>/fields/<label>/region           where a field is (may be unset: then it is not read)
    <question>/fields/<label>/read/<field>     a field's own reader (a printed field: ask, reads, verify)
    <question>/read/<field>[/<key>...]         the question's reader (notes, prompts)
    <question>/checks/<name>/<field>[/<key>...]    a check's configuration: a field of its
                                               function's dataclass, then mapping keys (a key)

The built-in group of identity fields ('@identity') is a question like any
other here: '@identity/fields/name/region' is where the name is written.
Segments are separated by '/'; inside a segment '%' is written '%25' and '/'
is written '%2F'. Labels with dots ('4.1.a') need no escaping.

A scope is the exam (every submission; stored as ''), a source scan (every
submission split from it: 'source:<id>') or one submission ('submission:<id>').
Which scopes a value may be set at goes with its kind (`SCOPES`). The value in
force for a submission is its own, else its source scan's, else the exam's,
else the package's.

- **An override is a change to the definition**, valid exactly when the
  definition with it applied is valid: `Exam.validate`, the validation the
  package goes through, runs on a copy with the change, in the context of its
  scope (one submission's extra sheets are pages it may name). Here a JSON value
  is only decoded into the kind of object it replaces. A stored override that no
  longer validates is skipped and reported.
- **An open workspace has its overrides applied**: opening one applies the
  exam's (`Workspace.refresh`, which every exam-wide edit repeats), and what
  holds for one submission is `Layers` (`Workspace.layers`), the one resolver.

What can be edited is what is plain data: text, numbers, booleans, a `Region`,
page lists, a printed field's `reads` map, and key entries that are text or a
choice (an object with an `accepted` tuple of texts). Functions are never
editable, and neither are names used as addresses or result specs. A
configuration field can opt out with `dataclasses.field(metadata={'override': False})`.

`Exam.base` keeps the roots as built; `apply` makes the live ones the base plus
the exam's overrides, replacing frozen objects along each path, never mutating
them. Because a check's hash describes the live check, an edited key outdates
exactly the checks that used it. Changing how a field is read changes no value.
"""
from __future__ import annotations

import copy
import dataclasses
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from .defs import PRINT_AREA, Check, Exam, Field, Region
from .store import ANY

if TYPE_CHECKING:
    from .workspace import Workspace

EXAM, SOURCE, SUBMISSION = 'exam', 'source', 'submission'
# Where each kind of value may be set (any other kind: the exam only).
SCOPES = {
    'region': (EXAM, SUBMISSION),
    'pages': (EXAM, SUBMISSION),
    'print_area': (EXAM, SOURCE, SUBMISSION),
}

_SCALARS = {'str': 'text', 'int': 'int', 'float': 'number', 'bool': 'bool', 'Region': 'region'}


@dataclass(frozen=True)
class Entry:
    """One editable value. `base` and `current` are JSON (a region is
    `[page, left, top, right, bottom]`, a choice is its list of texts)."""
    path: str
    kind: str
    base: Any
    current: Any
    overridden: bool
    nullable: bool = False


@dataclass(frozen=True)
class Skipped:
    """A stored override that could not be applied (the package changed)."""
    path: str
    reason: str


# --- paths ---------------------------------------------------------------------

def escape(segment: str) -> str:
    return segment.replace('%', '%25').replace('/', '%2F')


def unescape(segment: str) -> str:
    return re.sub(r'%(25|2F)', lambda m: '%' if m.group(1) == '25' else '/', segment, flags=re.I)


def join(*segments: str) -> str:
    return '/'.join(escape(s) for s in segments)


def split(path: str) -> tuple[str, ...]:
    return tuple(unescape(s) for s in path.split('/'))


def scope(level: str, id: str = '') -> str:
    """A scope as stored: '' for the exam, '<level>:<id>' for a source scan or a submission."""
    if level == EXAM:
        return ''
    if level not in (SOURCE, SUBMISSION) or not id:
        raise ValueError(f"no such scope {level!r}")
    return f'{level}:{id}'


def parse_scope(text: str) -> tuple[str, str]:
    """(level, id) of a stored scope; `ValueError` if it is not one."""
    if text == '':
        return EXAM, ''
    level, sep, id = text.partition(':')
    if not sep or level not in (SOURCE, SUBMISSION) or not id:
        raise ValueError("no such scope")
    return level, id


# --- what is editable ---------------------------------------------------------------

def _is_choice(x: Any) -> bool:
    accepted = getattr(x, 'accepted', None)
    return (dataclasses.is_dataclass(x) and isinstance(accepted, tuple)
            and all(isinstance(a, str) for a in accepted))


def _value_kind(x: Any) -> str | None:
    if isinstance(x, bool):
        return 'bool'
    if isinstance(x, int):
        return 'int'
    if isinstance(x, float):
        return 'number'
    if isinstance(x, str):
        return 'text'
    if isinstance(x, Region):
        return 'region'
    if _is_choice(x):
        return 'choice'
    return None


def _annotation(ann: Any) -> tuple[str | None, bool]:
    """(kind, nullable) from a dataclass field's annotation, for simple unions only."""
    text = ann.__name__ if isinstance(ann, type) else str(ann)
    parts = [p.strip() for p in text.split('|')]
    rest = [p for p in parts if p != 'None']
    return (_SCALARS.get(rest[0]) if len(rest) == 1 else None), len(rest) < len(parts)


def _config_leaves(obj: Any):
    """(segments, kind, nullable) for the plain data in a reader's or check's dataclass."""
    for f in dataclasses.fields(obj):
        if not f.init or f.name.startswith('_') or f.metadata.get('override') is False:
            continue
        v = getattr(obj, f.name)
        if f.metadata.get('override') == 'map':        # edited whole: readings -> values
            yield (f.name,), 'map', False
            continue
        if isinstance(v, Mapping):
            yield from _map_leaves((f.name,), v)
            continue
        kind, nullable = _annotation(f.type)
        if v is not None:
            vk = _value_kind(v)
            if vk is None or (kind is not None and kind != vk and (kind, vk) != ('number', 'int')):
                continue
            kind = kind or vk
        if kind is not None:
            yield (f.name,), kind, nullable


def _map_leaves(prefix: tuple[str, ...], m: Mapping):
    for k, v in m.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, Mapping):
            yield from _map_leaves((*prefix, k), v)
        elif (kind := _value_kind(v)) is not None:
            yield (*prefix, k), kind, False


def _leaves(root: str, node: Any) -> dict[tuple[str, ...], tuple[str, bool]]:
    """Every editable value under one root: {segments after the root: (kind, nullable)}."""
    if root == PRINT_AREA:
        return {(): ('print_area', False)}
    out: dict[tuple[str, ...], tuple[str, bool]] = {} if node.apart else {   # a group apart has no page to show
        ('pages',): ('pages', False)}

    def config(prefix, obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            for segs, kind, nullable in _config_leaves(obj):
                out[(*prefix, *segs)] = (kind, nullable)

    for f in node.fields:
        if f.default is None:           # a field with a default is a setting: nowhere on paper
            out[('fields', f.label, 'region')] = ('region', True)
        config(('fields', f.label, 'read'), f.read)
    config(('read',), node.read)
    for c in node.checks:
        config(('checks', c.name), c.fn)
    return out


# --- getting and replacing along a path ----------------------------------------------

def _child(node: Any, seg: str):
    """(child, rebuild): the value under one segment, and a function returning a
    copy of `node` with that value replaced."""
    if isinstance(node, Check):
        child, rebuild = _child(node.fn, seg)
        return child, lambda v: dataclasses.replace(node, fn=rebuild(v))
    if isinstance(node, tuple) and node and all(isinstance(x, (Field, Check)) for x in node):
        for i, x in enumerate(node):
            if (x.label if isinstance(x, Field) else x.name) == seg:
                return x, lambda v, i=i: node[:i] + (v,) + node[i + 1:]
        raise KeyError(seg)
    if isinstance(node, Mapping):
        if seg not in node:
            raise KeyError(seg)
        return node[seg], lambda v: {**node, seg: v}
    if dataclasses.is_dataclass(node) and seg in {f.name for f in dataclasses.fields(node) if f.init}:
        return getattr(node, seg), lambda v: dataclasses.replace(node, **{seg: v})
    raise KeyError(seg)


def _get(node: Any, segs: tuple[str, ...]) -> Any:
    for seg in segs:
        node, _ = _child(node, seg)
    return node


def _set(node: Any, segs: tuple[str, ...], value: Any) -> Any:
    if not segs:
        return value
    child, rebuild = _child(node, segs[0])
    return rebuild(_set(child, segs[1:], value))


# --- values: JSON to the definition's objects and back -------------------------------

def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _ints(x: Any, n: int | None = None) -> bool:
    return isinstance(x, (list, tuple)) and (n is None or len(x) == n) and all(_int(v) for v in x)


# The shape of each kind's JSON value: (test, what it should be). Whether a value of the
# right shape is valid is the definition's to say (`Exam.validate`).
_SHAPES = {
    'region': (lambda v: _ints(v, 5), "a region is [page, left, top, right, bottom]"),
    'print_area': (lambda v: _ints(v, 4), "a print area is [left, top, right, bottom]"),
    'pages': (_ints, "pages are a list of page numbers"),
    'int': (_int, "expected an integer"),
    'number': (lambda v: _int(v) or isinstance(v, float), "expected a number"),
    'bool': (lambda v: isinstance(v, bool), "expected true or false"),
    'text': (lambda v: isinstance(v, str), "expected text"),
    'map': (lambda v: isinstance(v, Mapping), "expected a map of readings to values"),
    'choice': (lambda v: isinstance(v, (list, tuple)) and all(isinstance(a, str) for a in v),
               "a choice is a list of accepted texts"),
}


def _decode(kind: str, value: Any, base: Any) -> Any:
    """The definition's object for a JSON value of `kind` (`base`: what it replaces);
    `ValueError` if the value has another shape."""
    if value is None:
        return None
    test, want = _SHAPES[kind]
    if not test(value):
        raise ValueError(want)
    if kind == 'region':
        return Region(*value)
    if kind in ('pages', 'print_area'):
        return tuple(value)
    if kind == 'map':
        return dict(value)
    if kind == 'number':
        return float(value)
    if kind == 'choice':        # the base entry with other texts: the core does not import std
        new = copy.copy(base)
        object.__setattr__(new, 'accepted', tuple(value))
        return new
    return value


def _encode(kind: str, x: Any) -> Any:
    if x is None:
        return None
    if kind == 'region':
        return [x.page, x.left, x.top, x.right, x.bottom]
    if kind in ('pages', 'print_area'):
        return list(x)
    if kind == 'map':
        return dict(x)
    if kind == 'choice':
        return list(x.accepted)
    return x


# --- one change to the definition ---------------------------------------------------------

@dataclass(frozen=True)
class _Loc:
    """Where a path points: its root, the segments under it, what kind of value is
    there and the scopes it may be set at."""
    root: str
    segs: tuple[str, ...]
    kind: str
    nullable: bool
    scopes: tuple[str, ...]


def _locate(exam: Exam, path: str) -> _Loc:
    segs = split(path)
    base = exam.base.get(segs[0])
    if base is None:
        raise ValueError(f"no question {segs[0]!r}")
    info = _leaves(segs[0], base).get(segs[1:])
    if info is None:
        raise ValueError(f"{path} is not an editable value")
    kind, nullable = info
    return _Loc(segs[0], segs[1:], kind, nullable, SCOPES.get(kind, (EXAM,)))


def _change(exam: Exam, roots: Mapping[str, Any], path: str, value: Any, level: str,
            pages: int | None = None) -> tuple[Any, Any]:
    """The root `path` is under, in `roots`, with `value` (JSON) put there, and the value
    in normal JSON form; `ValueError` unless the definition with it is valid (`Exam.validate`,
    with `pages` the pages that may be named at this scope)."""
    loc = _locate(exam, path)
    if level not in loc.scopes:
        where = {EXAM: "every submission", SOURCE: "one scan", SUBMISSION: "one submission"}[level]
        raise ValueError(f"{path}: not set for {where}")
    if value is None and (level != EXAM or not loc.nullable):
        raise ValueError("a value is required" + (" (to drop it, clear it)" if level != EXAM else ""))
    obj = _decode(loc.kind, value, _get(exam.base[loc.root], loc.segs))
    node = _set(roots[loc.root], loc.segs, obj)
    exam.with_roots({**roots, loc.root: node}).validate(pages=pages)
    return node, _encode(loc.kind, obj)


def validate(exam: Exam, path: str, value: Any) -> Any:
    """The value in normal JSON form if the live definition with it at `path`, for
    every submission, is valid; else `ValueError`."""
    return _change(exam, exam.roots(), path, value, EXAM)[1]


def kind(exam: Exam, path: str) -> str:
    """The kind of value at `path`; `ValueError` if nothing editable is there."""
    return _locate(exam, path).kind


def package_value(exam: Exam, path: str, value: Any) -> tuple[Any, Any]:
    """(the package's object at `path`, the object `value` (JSON) stands for); `ValueError`
    if nothing editable is there or the value has another shape."""
    loc = _locate(exam, path)
    base = _get(exam.base[loc.root], loc.segs)
    return base, _decode(loc.kind, value, base)


# --- listing and applying ------------------------------------------------------------

def editable(exam: Exam, root: str | None = None) -> list[Entry]:
    """Every value editable for every submission (of one root: a question, '@print_area';
    or of all), with base and current values."""
    out, live = [], exam.roots()
    for name, base in exam.base.items():
        if root is not None and name != root:
            continue
        for segs, (kind, nullable) in _leaves(name, base).items():
            path = join(name, *segs)
            out.append(Entry(path, kind, _encode(kind, _get(base, segs)), _encode(kind, _get(live[name], segs)),
                             path in exam.overrides, nullable))
    return out


def entry(exam: Exam, path: str) -> Entry | None:
    """The one editable value at `path` (for every submission), or None."""
    return next((e for e in editable(exam, split(path)[0]) if e.path == path), None)


def apply(exam: Exam, mapping: Mapping[str, Any]) -> list[Skipped]:
    """Make the live exam its base definition plus `mapping` ({path: JSON value},
    the exam's own overrides), each applied if the definition with it validates.

    Roots without overrides go back to their base objects. An override whose path
    no longer exists or whose value no longer fits is skipped and reported."""
    roots, applied, skipped = dict(exam.base), {}, []
    for path in sorted(mapping):
        try:
            root = split(path)[0]
            roots[root], applied[path] = _change(exam, roots, path, mapping[path], EXAM)
        except (ValueError, KeyError, TypeError) as e:
            skipped.append(Skipped(path, str(e)))
    exam.overrides = applied     # first: a worker never runs older data than a result's recorded hash
    exam.install(roots)
    return skipped


# --- what applies to one submission ------------------------------------------------------

class Layers:
    """The definition as it is for one submission, or for the sheets of one source
    scan: each value is the submission's own, else its source scan's (when it was
    split from one), else the exam's, else the package's. The one resolver: every
    per-submission value is looked up here (`Workspace.layers`)."""

    def __init__(self, ws: 'Workspace', *, submission: str | None = None, source: str | None = None):
        if submission is not None and source is None:
            made = ws.store.made_of(submission)
            source = made.source if made else None
        self.ws, self.exam, self.submission, self.source = ws, ws.exam, submission, source
        self.stored = {level: ws.store.overrides(scope(level, id))
                       for level, id in ((SUBMISSION, submission), (SOURCE, source)) if id}
        self._roots: dict[str, Any] = {}
        self._pages: int | None = None

    def pages(self) -> int:
        """The pages a value may name here: the printed pages, and a submission's extra sheets."""
        if self._pages is None:
            self._pages = self.exam.pages + (self.ws.extras(self.submission) if self.submission else 0)
        return self._pages

    def root(self, name: str) -> Any:
        """A root of the definition here (a question, the print area): the live one with
        the scan's, then the submission's overrides under it, each applied if the
        definition with it validates (as at the exam's scope)."""
        if name not in self._roots:
            roots = self.exam.roots()
            for level in (SOURCE, SUBMISSION):
                for path, o in self.stored.get(level, {}).items():
                    if split(path)[0] != name:
                        continue
                    try:
                        roots[name], _ = _change(self.exam, roots, path, o.value, level, self.pages())
                    except (ValueError, KeyError, TypeError):
                        pass            # no longer fits the package: not applied
            self._roots[name] = roots[name]
        return self._roots[name]

    def value(self, path: str) -> Any:
        """The value in force at `path` (JSON)."""
        loc = _locate(self.exam, path)
        return _encode(loc.kind, _get(self.root(loc.root), loc.segs))

    def candidate(self, path: str, value: Any, level: str) -> Any:
        """`value` (JSON) at `path` for this submission or scan, in normal form, if the
        definition with it here validates; else `ValueError`."""
        roots = {**self.exam.roots(), split(path)[0]: self.root(split(path)[0])}
        return _change(self.exam, roots, path, value, level, self.pages())[1]


@dataclass(frozen=True)
class Change:
    """What one edit did: the checks whose definition changed (their results are
    now outdated and are recomputed), and stored overrides that could not be applied."""
    checks: tuple[tuple[str, str], ...]
    skipped: tuple[Skipped, ...] = ()

    @property
    def questions(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(q for q, _ in self.checks))


def _hashes(exam: Exam) -> dict:
    return {(qid, c.name): exam.check_hash(q, c) for qid, q in exam.questions.items() for c in q.checks}


def _changed(ws: 'Workspace', path: str, scope: str) -> Change:
    if scope != '':         # below the exam: only how fields are read, for some submissions
        return Change(())
    c0, skipped = _hashes(ws.exam), ws.refresh()
    c1 = _hashes(ws.exam)
    return Change(tuple(k for k in c1 if c1[k] != c0.get(k)), tuple(skipped))


def set(ws: 'Workspace', path: str, value: Any, *, scope: str = '', author: str | None,
        expect_rev: Any = ANY) -> Change:
    """Validate, store and apply one override at a scope ('': the exam's).
    Raises `ValueError` or `store.Conflict`."""
    level, id = parse_scope(scope)
    if level == EXAM:
        value = validate(ws.exam, path, value)
    else:
        value = ws.layers(**{level: id}).candidate(path, value, level)
    ws.store.set_override(path, value, scope=scope, author=author, expect_rev=expect_rev)
    return _changed(ws, path, scope)


def clear(ws: 'Workspace', path: str, *, scope: str = '', expect_rev: Any = ANY) -> Change:
    """Remove one override: back to the next scope's value."""
    parse_scope(scope)
    ws.store.clear_override(path, scope=scope, expect_rev=expect_rev)
    return _changed(ws, path, scope)
