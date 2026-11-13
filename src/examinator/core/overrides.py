"""Live edits of exam data: overrides layered on the definitions a package builds.

An override replaces one plain-data value of a question, addressed by a path:

    <question>/pages                           printed pages holding the answer
    <question>/fields/<label>/region           where a field is (may be unset: then it is not read)
    <question>/fields/<label>/read/<field>     a field's own reader (a printed field: ask, reads, verify)
    <question>/read/<field>[/<key>...]         the question's reader (notes, prompts)
    <question>/checks/<name>/<field>[/<key>...]    a check's configuration: a field of its
                                               function's dataclass, then mapping keys (a key)

and the exam's own lists of named items (`Exam.LISTS`, e.g. the identity boxes),
under '@' and the list's name:

    @<list>/<name>/<field>                     a declared item's field (a region set to
                                               none, where the item allows it: not used)
    @<list>/<name>                             an item the package does not declare, whole:
                                               {field: value}; clearing it takes it away

Segments are separated by '/'; inside a segment '%' is written '%25' and '/'
is written '%2F'. Labels with dots ('4.1.a') need no escaping.

What can be edited is what is plain data: text, numbers, booleans, a `Region`,
page lists, a printed field's `reads` map, and key entries that are text or a
choice (an object with an `accepted` tuple of texts). Functions are never
editable, and neither are names used as addresses, result specs or stage
wiring. A configuration field can opt out with
`dataclasses.field(metadata={'override': False})`.

The `Exam` keeps the questions as built (`exam.base`); `apply` recomputes
each live question from its base plus the overrides, replacing frozen objects
along the path, never mutating them. Because a check's hash describes the live
check, an edited key outdates exactly the checks that used it. Changing how a
field is read changes no value; `Change.fields` lists the fields to offer to
read again. (A field's region for one submission is not an override here:
`Workspace.set_region`.)
"""
from __future__ import annotations

import copy
import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .defs import Check, Exam, Field, Question, Region, digest
from .store import ANY, Store

KINDS = ('region', 'text', 'int', 'number', 'bool', 'pages', 'map', 'choice', 'item')
LIST = '@'          # the first segment of a path into one of the exam's lists: '@identity'

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


def _item_fields(cls: type) -> dict[str, tuple[str, bool, bool]]:
    """{field: (kind, nullable, required)} for the plain data of a list item's class."""
    out = {}
    for f in dataclasses.fields(cls):
        if not f.init or f.name.startswith('_') or f.metadata.get('override') is False:
            continue
        kind, nullable = _annotation(f.type)
        if kind is not None:
            missing = f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
            out[f.name] = (kind, nullable, missing)
    return out


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
    out: dict[tuple[str, ...], tuple[str, bool]] = {
        ('pages',): ('pages', False)}

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

def _named(x: Any) -> bool:
    return dataclasses.is_dataclass(x) and not isinstance(x, type) and isinstance(getattr(x, 'name', None), str)


def _child(node: Any, seg: str):
    """(child, rebuild): the value under one segment, and a function returning a
    copy of `node` with that value replaced."""
    if isinstance(node, Check):
        child, rebuild = _child(node.fn, seg)
        return child, lambda v: dataclasses.replace(node, fn=rebuild(v))
    if isinstance(node, tuple) and node and all(isinstance(x, (Field, Check)) or _named(x) for x in node):
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
    if kind == 'pages':
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
    if kind == 'pages':
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
    """Where a path points: `root` (a question id, or '@<list>'), its object as the
    package built it (`base`: a question, or the list's tuple), the segments under it,
    and what kind of value is there. `cls`: a list's item class."""
    root: str
    base: Any
    segs: tuple[str, ...]
    kind: str
    nullable: bool
    cls: type | None = None


def _locate(exam: Exam, path: str) -> _Loc:
    segs = split(path)
    if segs[0].startswith(LIST):
        name = segs[0][len(LIST):]
        cls = exam.LISTS.get(name)
        if cls is None:
            raise ValueError(f"no exam list {name!r}")
        base = exam.base_lists.get(name, ())
        if len(segs) < 2 or not segs[1]:
            raise ValueError(f"{path} is not an editable value")
        declared = any(x.name == segs[1] for x in base)
        if not declared and len(segs) == 2:
            return _Loc(segs[0], base, segs[1:], 'item', False, cls)
        info = _item_fields(cls).get(segs[2]) if declared and len(segs) == 3 else None
        if info is None:
            raise ValueError(f"{path} is not an editable value")
        return _Loc(segs[0], base, segs[1:], info[0], info[1], cls)
    q = exam.base.get(segs[0])
    if q is None:
        raise ValueError(f"no question {segs[0]!r}")
    info = _leaves(q).get(segs[1:])
    if info is None:
        raise ValueError(f"{path} is not an editable value")
    return _Loc(segs[0], q, segs[1:], *info)


def _check_item(exam: Exam, loc: _Loc, value: Any) -> dict:
    """A whole item the package does not declare, in normal JSON form, or `ValueError`."""
    fields = _item_fields(loc.cls)
    if not isinstance(value, Mapping) or any(k not in fields for k in value):
        raise ValueError(f"an item is an object with {', '.join(fields)}")
    out = {}
    for k, (kind, nullable, required) in fields.items():
        if k not in value:
            if required:
                raise ValueError(f"{k} is required")
            continue
        out[k] = _check(exam, None, (k,), kind, nullable, value[k])
    _make(loc, out)            # what the class itself checks
    return out


def _make(loc: _Loc, value: Mapping) -> Any:
    fields = _item_fields(loc.cls)
    return loc.cls(name=loc.segs[0], **{k: _from_json(fields[k][0], v, None) for k, v in value.items()})


def _value(exam: Exam, loc: _Loc, value: Any) -> Any:
    """`value` checked for where `loc` points, in normal JSON form; `ValueError` if it does not fit."""
    if loc.kind == 'item':
        return _check_item(exam, loc, value)
    value = _check(exam, loc.base, loc.segs, loc.kind, loc.nullable, value)
    if loc.cls is not None:                 # a declared item's field: what the class itself checks
        try:
            _set(loc.base, loc.segs, _from_json(loc.kind, value, _get(loc.base, loc.segs)))
        except TypeError as e:
            raise ValueError(str(e)) from None
    return value


def validate(exam: Exam, path: str, value: Any) -> Any:
    """The value in normal JSON form if `path` is editable and `value` fits it; else `ValueError`."""
    return _value(exam, _locate(exam, path), value)


# --- listing and applying ------------------------------------------------------------

def _base_of(exam: Exam) -> dict[str, Question]:
    for qid, q in exam.questions.items():       # questions added without `Exam.question`
        exam.base.setdefault(qid, q)
    return exam.base


def _item_json(cls: type, x: Any) -> dict:
    return {k: _to_json(kind, getattr(x, k)) for k, (kind, _, _) in _item_fields(cls).items()}


def editable(exam: Exam, question: str | None = None) -> list[Entry]:
    """Every editable value (of one question or exam list ('@<list>'), or of all),
    with base and current values. An item the package does not declare is listed
    while an override adds it (its base is None)."""
    out = []
    for qid, base in _base_of(exam).items():
        if question is not None and qid != question:
            continue
        live = exam.questions.get(qid, base)
        for segs, (kind, nullable) in _leaves(base).items():
            path = join(qid, *segs)
            try:
                current = _get(live, segs)
            except KeyError:
                current = _get(base, segs)
            out.append(Entry(path, kind, _to_json(kind, _get(base, segs)), _to_json(kind, current),
                             path in exam.overrides, nullable))
    for name, cls in exam.LISTS.items():
        root = LIST + name
        if question is not None and root != question:
            continue
        live = {x.name: x for x in getattr(exam, name)}
        for x in exam.base_lists.get(name, ()):
            now = live.get(x.name, x)
            for k, (kind, nullable, _) in _item_fields(cls).items():
                path = join(root, x.name, k)
                out.append(Entry(path, kind, _to_json(kind, getattr(x, k)), _to_json(kind, getattr(now, k)),
                                 path in exam.overrides, nullable))
        for path, value in exam.overrides.items():
            segs = split(path)
            if segs[0] == root and len(segs) == 2:
                out.append(Entry(path, 'item', None, value, True))
    return out


def apply(exam: Exam, mapping: Mapping[str, Any]) -> list[Skipped]:
    """Make the live questions the base definitions plus `mapping` ({path: JSON value}).

    Questions without overrides go back to their base objects. An override whose
    path no longer exists or whose value no longer fits is skipped and reported."""
    base = _base_of(exam)
    skipped, applied = [], {}
    live: dict[str, Any] = dict(base)
    live.update({LIST + name: tuple(exam.base_lists.get(name, ())) for name in exam.LISTS})
    for path in sorted(mapping):
        try:
            loc = _locate(exam, path)
            value = _value(exam, loc, mapping[path])
            if loc.kind == 'item':          # added after the declared ones, by name
                live[loc.root] = live[loc.root] + (_make(loc, value),)
            else:
                live[loc.root] = _set(live[loc.root], loc.segs, _from_json(loc.kind, value, _get(loc.base, loc.segs)))
            applied[path] = value
        except (ValueError, KeyError, TypeError) as e:
            skipped.append(Skipped(path, str(e)))
    exam.overrides = applied     # first: a worker never runs older data than a stage's recorded hash
    for qid, q in live.items():
        if qid.startswith(LIST):
            setattr(exam, qid[len(LIST):], q)
        elif exam.questions.get(qid) is not q:
            exam.questions[qid] = q
    return skipped


def refresh(exam: Exam, store: Store) -> list[Skipped]:
    """Apply every override stored in the workspace. Call on opening a workspace."""
    return apply(exam, {path: o.value for path, o in store.overrides().items()})


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
    fields = {(qid, f.label): digest({'region': f.region, 'read': q.reader_of(f.label), 'pages': q.pages})
              for qid, q in exam.questions.items() for f in q.fields}
    return checks, fields


def _changed(exam: Exam, store: Store) -> Change:
    (c0, f0), skipped = _hashes(exam), refresh(exam, store)
    c1, f1 = _hashes(exam)
    return Change(tuple(k for k in c1 if c1[k] != c0.get(k)), tuple(k for k in f1 if f1[k] != f0.get(k)),
                  tuple(skipped))


def set(exam: Exam, store: Store, path: str, value: Any, *, author: str | None,
        expect_rev: Any = ANY) -> Change:
    """Validate, store and apply one override. Raises `ValueError` or `store.Conflict`."""
    value = validate(exam, path, value)
    store.set_override(path, value, author=author, expect_rev=expect_rev)
    return _changed(exam, store)


def clear(exam: Exam, store: Store, path: str, *, expect_rev: Any = ANY) -> Change:
    """Remove one override, returning to the package's value."""
    store.clear_override(path, expect_rev=expect_rev)
    return _changed(exam, store)


# --- showing changes as Python ----------------------------------------------------------

def _python(kind: str, value: Any, cls: type | None = None, name: str = '') -> str:
    if value is None:
        return 'None'
    if kind == 'item':
        fields = _item_fields(cls)
        args = [repr(name)] + [f'{k}={_python(fields[k][0], v)}' for k, v in value.items()]
        return f"{cls.__name__}({', '.join(args)})"
    if kind == 'region':
        return f"Region({', '.join(str(v) for v in value)})"
    if kind == 'pages':
        return repr(list(value))
    if kind == 'choice':
        return f"Choice({', '.join(repr(v) for v in value)})"
    return repr(value)


def describe_changes(exam: Exam, store: Store) -> list[str]:
    """One line per stored override, old and new value as Python, for whoever
    maintains the exam package."""
    lines = []
    for path, o in store.overrides().items():
        loc = None
        try:
            loc = _locate(exam, path)
            kind = loc.kind
            old = "(added)" if kind == 'item' else _python(kind, _to_json(kind, _get(loc.base, loc.segs)))
        except (ValueError, KeyError):
            kind, old = '', "(no longer in the package)"
        new = _python(kind, o.value, loc.cls, loc.segs[0]) if loc is not None else repr(o.value)
        lines.append(f"{path}: {old} -> {new}  ({o.author or '?'}, {o.updated_at[:10]})")
    return lines
