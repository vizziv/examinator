"""Live edits of exam data: overrides layered on the definitions a package builds.

An override replaces one plain-data value of a question, addressed by a path:

    <question>/pages                          printed pages holding the answer
    <question>/fields/<label>/region
    <question>/fields/<label>/read/ask | reads    a printed field: what the model is asked; readings to values
    <question>/stages/<name>/<field>[/<key>...]   a stage's configuration: a field of
                                              its function's dataclass, then mapping keys

Segments are separated by '/'; inside a segment '%' is written '%25' and '/'
is written '%2F'. Labels with dots ('4.1.a') need no escaping.

What can be edited is what is plain data: text, numbers, booleans, a `Region`,
page lists, a printed field's `reads` map, and key entries that are text or a
choice (an object with an `accepted` tuple of texts). Functions are never
editable, and neither are names used as addresses, result specs or stage
wiring. A stage-configuration field can opt out with
`dataclasses.field(metadata={'override': False})`.

The `Exam` keeps the questions as built (`exam.base`); `apply` recomputes
each live question from its base plus the overrides, replacing frozen objects
along the path, never mutating them. Because `stage_hash` describes the live
question, an edited key outdates exactly the checks that used it.
"""
from __future__ import annotations

import copy
import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .defs import Exam, Field, Question, Region, Stage, stage_hash
from .printed import Printed
from .store import ANY, Store

KINDS = ('region', 'text', 'int', 'number', 'bool', 'pages', 'map', 'choice')

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
    """(segments, kind, nullable) for the plain data in a stage function's dataclass."""
    for f in dataclasses.fields(obj):
        if not f.init or f.name.startswith('_') or f.metadata.get('override') is False:
            continue
        v = getattr(obj, f.name)
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
    out: dict[tuple[str, ...], tuple[str, bool]] = {
        ('pages',): ('pages', False)}
    for f in q.fields:
        out[('fields', f.label, 'region')] = ('region', f.read is None)    # a reader needs its region
        if isinstance(f.read, Printed):
            out.update({('fields', f.label, 'read', 'ask'): ('text', False),
                        ('fields', f.label, 'read', 'reads'): ('map', False)})
    for s in q.stages:
        if dataclasses.is_dataclass(s.fn) and not isinstance(s.fn, type):
            for segs, kind, nullable in _config_leaves(s.fn):
                out[('stages', s.name, *segs)] = (kind, nullable)
    return out


# --- getting and replacing along a path ----------------------------------------------

def _child(node: Any, seg: str):
    """(child, rebuild): the value under one segment, and a function returning a
    copy of `node` with that value replaced."""
    if isinstance(node, Stage):
        child, rebuild = _child(node.fn, seg)
        return child, lambda v: dataclasses.replace(node, fn=rebuild(v))
    if isinstance(node, tuple) and all(isinstance(x, (Field, Stage)) for x in node):
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


def _locate(exam: Exam, path: str) -> tuple[Question, tuple[str, ...], str, bool]:
    segs = split(path)
    q = exam.base.get(segs[0])
    if q is None:
        raise ValueError(f"no question {segs[0]!r}")
    info = _leaves(q).get(segs[1:])
    if info is None:
        raise ValueError(f"{path} is not an editable value")
    return q, segs[1:], *info


def validate(exam: Exam, path: str, value: Any) -> Any:
    """The value in normal JSON form if `path` is editable and `value` fits it; else `ValueError`."""
    q, segs, kind, nullable = _locate(exam, path)
    return _check(exam, q, segs, kind, nullable, value)


# --- listing and applying ------------------------------------------------------------

def _base_of(exam: Exam) -> dict[str, Question]:
    for qid, q in exam.questions.items():       # questions added without `Exam.question`
        exam.base.setdefault(qid, q)
    return exam.base


def editable(exam: Exam, question: str | None = None) -> list[Entry]:
    """Every editable value (of one question, or of all), with base and current values."""
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
    return out


def apply(exam: Exam, mapping: Mapping[str, Any]) -> list[Skipped]:
    """Make the live questions the base definitions plus `mapping` ({path: JSON value}).

    Questions without overrides go back to their base objects. An override whose
    path no longer exists or whose value no longer fits is skipped and reported."""
    base = _base_of(exam)
    skipped, applied = [], {}
    live = dict(base)
    for path in sorted(mapping):
        try:
            orig, segs, kind, nullable = _locate(exam, path)
            value = _check(exam, orig, segs, kind, nullable, mapping[path])
            live[orig.id] = _set(live[orig.id], segs, _from_json(kind, value, _get(orig, segs)))
            applied[path] = value
        except (ValueError, KeyError, TypeError) as e:
            skipped.append(Skipped(path, str(e)))
    exam.overrides = applied     # first: a worker never runs older data than a stage's recorded hash
    for qid, q in live.items():
        if exam.questions.get(qid) is not q:
            exam.questions[qid] = q
    return skipped


def refresh(exam: Exam, store: Store) -> list[Skipped]:
    """Apply every override stored in the workspace. Call on opening a workspace."""
    return apply(exam, {path: o.value for path, o in store.overrides().items()})


@dataclass(frozen=True)
class Change:
    """What one edit did: the questions whose stage hashes changed (recheck these),
    and stored overrides that could not be applied."""
    questions: tuple[str, ...]
    skipped: tuple[Skipped, ...] = ()


def _hashes(exam: Exam) -> dict[str, dict[str, str]]:
    return {qid: {s.name: stage_hash(q, s) for s in q.stages} for qid, q in exam.questions.items()}


def _changed(exam: Exam, store: Store) -> Change:
    before = _hashes(exam)
    skipped = refresh(exam, store)
    after = _hashes(exam)
    return Change(tuple(q for q in after if after[q] != before.get(q)), tuple(skipped))


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

def _python(kind: str, value: Any) -> str:
    if value is None:
        return 'None'
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
        try:
            q, segs, kind, _ = _locate(exam, path)
            old = _python(kind, _to_json(kind, _get(q, segs)))
        except (ValueError, KeyError):
            kind, old = '', "(no longer in the package)"
        lines.append(f"{path}: {old} -> {_python(kind, o.value)}  ({o.author or '?'}, {o.updated_at[:10]})")
    return lines
