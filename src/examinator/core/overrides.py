"""Live edits of exam data: overrides layered on the definitions a package builds.

An override replaces one plain-data value at a scope, addressed by a path:

    <question>/pages                           printed pages holding the answer
    <question>/note                            a note for the question's reader (one submission's only)
    <question>/fields/<label>/region           where a field is (may be unset: then it is not read)
    <question>/fields/<label>/read/<field>     a field's own reader (a printed field: ask, reads, verify)
    <question>/read/<field>[/<key>...]         the question's reader (notes, prompts)
    <question>/checks/<name>/<field>[/<key>...]    a check's configuration: a field of its
                                               function's dataclass, then mapping keys (a key)

and the exam's own values (`Exam.ATTRS`), under '@':

    @print_area                                where the printed page sits on a scanned sheet
    @cover_pages                               scanned pages before printed page 1

The built-in group of identity fields (`defs.IDENTITY`, '@identity') is a
question like any other here: '@identity/fields/name/region' is where the name
is written.

Segments are separated by '/'; inside a segment '%' is written '%25' and '/'
is written '%2F'. Labels with dots ('4.1.a') need no escaping.

A scope is the exam (every submission; stored as ''), a source scan (every
submission split from it: 'source:<id>') or one submission ('submission:<id>').
Which scopes a value may be set at goes with its kind (`SCOPES`). The value in force for a submission is its own, else
its source scan's, else the exam's, else the package's (`Layers`). `validate`
checks a value for every scope alike.

What can be edited is what is plain data: text, numbers, booleans, a `Region`,
page lists, a printed field's `reads` map, and key entries that are text or a
choice (an object with an `accepted` tuple of texts). Functions are never
editable, and neither are names used as addresses or result specs. A configuration field can opt out with
`dataclasses.field(metadata={'override': False})`.

The `Exam` keeps the questions as built (`exam.base`); `apply` recomputes
each live question from its base plus the exam's overrides, replacing frozen
objects along the path, never mutating them. Because a check's hash describes
the live check, an edited key outdates exactly the checks that used it.
Changing how a field is read changes no value; `Change.fields` lists the fields
to offer to read again.
"""
from __future__ import annotations

import copy
import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .defs import Check, Exam, Field, Question, Region, digest
from .store import ANY, Store

KINDS = ('region', 'text', 'int', 'number', 'bool', 'pages', 'map', 'choice',
         'note', 'print_area', 'cover_pages')
AT = '@'            # starts the path of one of the exam's own values: '@print_area'

EXAM, SOURCE, SUBMISSION = 'exam', 'source', 'submission'
# Where each kind of value may be set (any other kind: the exam only).
# Adding a scope to a kind is one line here.
SCOPES = {
    'region': (EXAM, SUBMISSION),
    'pages': (EXAM, SUBMISSION),
    'note': (SUBMISSION,),
    'print_area': (EXAM, SOURCE, SUBMISSION),
    'cover_pages': (EXAM, SOURCE, SUBMISSION),
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


def _leaves(q: Question) -> dict[tuple[str, ...], tuple[str, bool]]:
    """Every editable value of a question: {segments after the id: (kind, nullable)}."""
    out: dict[tuple[str, ...], tuple[str, bool]] = {} if q.apart else {   # a group apart has no page to show
        ('pages',): ('pages', False), ('note',): ('note', False)}

    def config(prefix, obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            for segs, kind, nullable in _config_leaves(obj):
                out[(*prefix, *segs)] = (kind, nullable)

    for f in q.fields:
        out[('fields', f.label, 'region')] = ('region', True)
        config(('fields', f.label, 'read'), f.read)
    config(('read',), q.read)
    for c in q.checks:
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


# --- values -----------------------------------------------------------------------

def _to_json(kind: str, x: Any) -> Any:
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


def _from_json(kind: str, value: Any, base: Any) -> Any:
    if value is None:
        return None
    if kind == 'region':
        return Region(*value)
    if kind in ('pages', 'print_area'):
        return tuple(value)
    if kind == 'choice':        # a fresh copy of the base entry with other texts; core does not import std
        new = copy.copy(base)
        object.__setattr__(new, 'accepted', tuple(value))
        return new
    return value


def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check(exam: Exam, q: Question, segs: tuple[str, ...], kind: str, nullable: bool, value: Any) -> Any:
    """The value in normal JSON form, or `ValueError`."""
    if value is None:
        if not nullable:
            raise ValueError("a value is required")
        return None
    if kind == 'region':
        if not (isinstance(value, (list, tuple)) and len(value) == 5 and all(_int(v) for v in value)):
            raise ValueError("a region is [page, left, top, right, bottom]")
        page, left, top, right, bottom = value
        if not 1 <= page <= exam.pages:
            raise ValueError(f"page {page} is not in 1..{exam.pages}")
        if not (0 <= left < right <= 1000 and 0 <= top < bottom <= 1000):
            raise ValueError("a region needs 0 <= left < right <= 1000 and 0 <= top < bottom <= 1000")
        return list(value)
    if kind == 'pages':
        if not (isinstance(value, (list, tuple)) and value and all(_int(v) for v in value)):
            raise ValueError("pages are a non-empty list of page numbers")
        if any(not 1 <= p <= exam.pages for p in value):
            raise ValueError(f"pages must be in 1..{exam.pages}")
        if len({*value}) != len(value):
            raise ValueError("repeated page")
        return list(value)
    if kind == 'print_area':
        if not (isinstance(value, (list, tuple)) and len(value) == 4 and all(_int(v) for v in value)):
            raise ValueError("a print area is [left, top, right, bottom]")
        left, top, right, bottom = value
        if not (0 <= left < right <= 1000 and 0 <= top < bottom <= 1000):
            raise ValueError("a print area needs 0 <= left < right <= 1000 and 0 <= top < bottom <= 1000")
        return list(value)
    if kind == 'cover_pages':
        if not (_int(value) and 0 <= value < 1000):
            raise ValueError("cover pages: a whole number from 0 to 999")
        return value
    if kind == 'note':
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError("a note is text of at most 2000 characters")
        return value
    if kind == 'int':
        if not _int(value):
            raise ValueError("expected an integer")
        return value
    if kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected a number")
        return float(value)
    if kind == 'bool':
        if not isinstance(value, bool):
            raise ValueError("expected true or false")
        return value
    if kind == 'text':
        if not isinstance(value, str):
            raise ValueError("expected text")
        return value
    if kind == 'map':
        if not (isinstance(value, Mapping) and all(isinstance(k, str) and k.strip() and isinstance(v, str)
                                                   for k, v in value.items())):
            raise ValueError("expected a map of readings to values")
        return dict(value)
    if kind == 'choice':
        if not (isinstance(value, (list, tuple)) and value
                and all(isinstance(v, str) and v.strip() for v in value)):
            raise ValueError("a choice is a non-empty list of accepted texts")
        return list(value)
    raise ValueError(f"unknown kind {kind!r}")


@dataclass(frozen=True)
class _Loc:
    """Where a path points: `root` (a question id or '@<attr>'), its object as the
    package built it (`base`: a question, the attribute's value), the segments under
    it, what kind of value is there and the scopes it may be set at."""
    root: str
    base: Any
    segs: tuple[str, ...]
    kind: str
    nullable: bool
    scopes: tuple[str, ...] = (EXAM,)


def _attr(exam: Exam, root: str) -> str | None:
    """The exam attribute a path's first segment names ('@print_area'), or None."""
    return root[len(AT):] if root.startswith(AT) and root[len(AT):] in exam.ATTRS else None


def _locate(exam: Exam, path: str) -> _Loc:
    segs = split(path)
    name = _attr(exam, segs[0])
    if name is not None:
        if len(segs) != 1:
            raise ValueError(f"{path} is not an editable value")
        return _Loc(segs[0], exam.base_attrs[name], (), name, False, scopes=SCOPES[name])
    q = exam.base.get(segs[0])
    if q is None:
        raise ValueError(f"no question {segs[0]!r}")
    info = _leaves(q).get(segs[1:])
    if info is None:
        raise ValueError(f"{path} is not an editable value")
    kind, nullable = info
    return _Loc(segs[0], q, segs[1:], kind, nullable, scopes=SCOPES.get(kind, (EXAM,)))


def _value(exam: Exam, loc: _Loc, value: Any) -> Any:
    """`value` checked for where `loc` points, in normal JSON form; `ValueError` if it does not fit."""
    return _check(exam, loc.base, loc.segs, loc.kind, loc.nullable, value)


def _at(loc: _Loc, level: str, path: str) -> None:
    if level not in loc.scopes:
        where = {EXAM: "every submission", SOURCE: "one scan", SUBMISSION: "one submission"}[level]
        raise ValueError(f"{path}: not set for {where}")


def validate(exam: Exam, path: str, value: Any, scope: str = '') -> Any:
    """The value in normal JSON form if `path` is editable at `scope` and `value`
    fits it; else `ValueError`. The one check of a value, for every scope."""
    level, _ = parse_scope(scope)
    loc = _locate(exam, path)
    _at(loc, level, path)
    if value is None and level != EXAM:
        raise ValueError("a value is required (to drop it, clear it)")
    return _value(exam, loc, value)


def kind(exam: Exam, path: str) -> str:
    """The kind of value at `path`; `ValueError` if nothing editable is there."""
    return _locate(exam, path).kind


def _live(exam: Exam, loc: _Loc) -> Any:
    """The value at `loc` in the live exam (an exam-wide override, else the package's), as JSON;
    None for a value that is never set for every submission."""
    if EXAM not in loc.scopes:
        return None
    if _attr(exam, loc.root) is not None:
        live = getattr(exam, loc.root[len(AT):])
    else:
        live = exam.questions.get(loc.root, loc.base)
    try:
        return _to_json(loc.kind, _get(live, loc.segs))
    except KeyError:
        return _to_json(loc.kind, _get(loc.base, loc.segs))


# --- listing and applying ------------------------------------------------------------

def _base_of(exam: Exam) -> dict[str, Question]:
    for qid, q in exam.questions.items():       # questions added without `Exam.question`
        exam.base.setdefault(qid, q)
    return exam.base


def editable(exam: Exam, question: str | None = None) -> list[Entry]:
    """Every value editable for every submission (of one question or exam value
    ('@<attr>'), or of all), with base and current values."""
    out = []
    for qid, base in _base_of(exam).items():
        if question is not None and qid != question:
            continue
        live = exam.questions.get(qid, base)
        for segs, (kind, nullable) in _leaves(base).items():
            if EXAM not in SCOPES.get(kind, (EXAM,)):
                continue
            path = join(qid, *segs)
            try:
                current = _get(live, segs)
            except KeyError:
                current = _get(base, segs)
            out.append(Entry(path, kind, _to_json(kind, _get(base, segs)), _to_json(kind, current),
                             path in exam.overrides, nullable))
    for name in exam.ATTRS:
        path = AT + name
        if question is None or path == question:
            out.append(Entry(path, name, _to_json(name, exam.base_attrs[name]), _to_json(name, getattr(exam, name)),
                             path in exam.overrides))
    return out


def entry(exam: Exam, path: str) -> Entry | None:
    """The one editable value at `path` (for every submission), or None."""
    return next((e for e in editable(exam, split(path)[0]) if e.path == path), None)


def apply(exam: Exam, mapping: Mapping[str, Any]) -> list[Skipped]:
    """Make the live exam its base definitions plus `mapping` ({path: JSON value},
    the exam's own overrides).

    Questions without overrides go back to their base objects. An override whose
    path no longer exists or whose value no longer fits is skipped and reported."""
    base = _base_of(exam)
    skipped, applied = [], {}
    live: dict[str, Any] = dict(base)
    live.update({AT + name: exam.base_attrs[name] for name in exam.ATTRS})
    for path in sorted(mapping):
        try:
            loc = _locate(exam, path)
            _at(loc, EXAM, path)
            value = _value(exam, loc, mapping[path])
            live[loc.root] = _set(live[loc.root], loc.segs, _from_json(loc.kind, value, _get(loc.base, loc.segs)))
            applied[path] = value
        except (ValueError, KeyError, TypeError) as e:
            skipped.append(Skipped(path, str(e)))
    exam.overrides = applied     # first: a worker never runs older data than a result's recorded hash
    for qid, q in live.items():
        if _attr(exam, qid) is not None:
            setattr(exam, qid[len(AT):], q)
        elif exam.questions.get(qid) is not q:
            exam.questions[qid] = q
    return skipped


def refresh(exam: Exam, store: Store) -> list[Skipped]:
    """Apply every exam-wide override stored in the workspace. Call on opening a workspace."""
    return apply(exam, {path: o.value for path, o in store.overrides().items()})


# --- what applies to one submission ------------------------------------------------------

class Layers:
    """The overrides over the live exam for one submission, or for the sheets of one
    source scan: the value in force is the submission's own, else its source scan's
    (when it was split from one), else the exam's, else the package's. The one
    resolver: every per-submission value is looked up here."""

    def __init__(self, exam: Exam, store: Store, *, submission: str | None = None,
                 source: str | None = None):
        if submission is not None and source is None:
            made = store.made_of(submission)
            source = made.source if made else None
        self.exam, self.submission, self.source = exam, submission, source
        self.stored = {level: store.overrides(scope(level, id))
                       for level, id in ((SUBMISSION, submission), (SOURCE, source)) if id}

    def own(self, path: str) -> Any:
        """The value at `path` from the most specific scope below the exam that has one
        that fits (JSON), or None."""
        for level in (SUBMISSION, SOURCE):
            o = self.stored.get(level, {}).get(path)
            if o is None:
                continue
            try:
                loc = _locate(self.exam, path)
                if level in loc.scopes and o.value is not None:
                    return _value(self.exam, loc, o.value)
            except (ValueError, KeyError, TypeError):
                pass            # no longer fits the package: not applied, as at the exam's scope
        return None

    def value(self, path: str) -> Any:
        """The value in force at `path` (JSON)."""
        v = self.own(path)
        return v if v is not None else _live(self.exam, _locate(self.exam, path))


@dataclass(frozen=True)
class Change:
    """What one edit did: the checks whose definition changed (their results are
    now outdated and are recomputed), the fields whose reading changed (to offer
    to read again), and stored overrides that could not be applied."""
    checks: tuple[tuple[str, str], ...]
    fields: tuple[tuple[str, str], ...] = ()
    skipped: tuple[Skipped, ...] = ()

    @property
    def questions(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(q for q, _ in self.checks))


def _hashes(exam: Exam) -> tuple[dict, dict]:
    checks = {(qid, c.name): exam.check_hash(q, c) for qid, q in exam.questions.items() for c in q.checks}
    layout = [list(exam.print_area), exam.cover_pages]
    fields = {(qid, f.label): digest({'region': f.region, 'read': q.reader_of(f.label), 'pages': q.pages,
                                      'layout': layout})
              for qid, q in exam.questions.items() for f in q.fields}
    return checks, fields


def _changed(exam: Exam, store: Store, path: str, scope: str) -> Change:
    if scope == '':
        (c0, f0), skipped = _hashes(exam), refresh(exam, store)
        c1, f1 = _hashes(exam)
        return Change(tuple(k for k in c1 if c1[k] != c0.get(k)), tuple(k for k in f1 if f1[k] != f0.get(k)),
                      tuple(skipped))
    # below the exam: only how fields are read, for some submissions (no check reads these values)
    try:
        loc = _locate(exam, path)
    except ValueError:
        return Change(())
    if _attr(exam, loc.root) is not None:       # the print area, the cover pages: every crop
        return Change((), tuple((qid, f.label) for qid, q in exam.questions.items() for f in q.fields))
    q = exam.questions[loc.root]
    labels = [loc.segs[1]] if loc.segs[0] == 'fields' else q.labels
    return Change((), tuple((q.id, label) for label in labels))


def set(exam: Exam, store: Store, path: str, value: Any, *, scope: str = '', author: str | None,
        expect_rev: Any = ANY) -> Change:
    """Validate, store and apply one override at a scope ('': the exam's).
    Raises `ValueError` or `store.Conflict`."""
    value = validate(exam, path, value, scope)
    store.set_override(path, value, scope=scope, author=author, expect_rev=expect_rev)
    return _changed(exam, store, path, scope)


def clear(exam: Exam, store: Store, path: str, *, scope: str = '', expect_rev: Any = ANY) -> Change:
    """Remove one override: back to the next scope's value."""
    parse_scope(scope)
    store.clear_override(path, scope=scope, expect_rev=expect_rev)
    return _changed(exam, store, path, scope)
