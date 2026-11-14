"""What a check returns: one proposed grade, and evidence for people.

    Result(grade, fields={'1.1': 'correct', '1.2': Judgement('incorrect', expected='3/2')},
           blocks=[Image(png), Details("Samples", Table(...))])

`grade` is anything the check's points accept: True/False, a number,
'correct'/'incorrect'/'blank', None (undecided), or a `Grade` with flags and a
reason. Evidence has two parts, both optional and for people only (nothing
computes from it):

- `fields`: per field of the check's own question that it judged, an outcome
  ('correct', 'incorrect', 'blank' or None) and optionally the expected answer
  and a short note; shown beside the field.
- `blocks`: an ordered list of things to show. The app renders them; exams
  never write layout. A block is one of the classes below (`Note`, `Table`,
  `Code`, `Details`, `Image`, re-exported as `examinator.std.blocks`) or the
  plain dict its `to_json()` gives; `SHAPES` is the grammar, the constants
  below its limits. `settle` checks every block against both and stores it:
  an image's bytes are kept apart, named by their digest. What breaks the
  grammar or the limits is a check error, never a stored block. Top-level
  images are stored first and shown as a row of figures above the rest; an
  image inside `Details` stays there.

A check may also return a bare grade, or raise `Undecided("a few words")`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import numbers
from dataclasses import dataclass, field, fields, replace
from typing import Any, Mapping, Sequence

from .defs import Grade

OUTCOMES = ('correct', 'incorrect', 'blank', None)

# --- evidence blocks -------------------------------------------------------------

# What an <img> shows safely (an SVG there runs no script and loads nothing).
MEDIA_TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml')
TONES = ('info', 'good', 'bad', 'warn')
MAX_IMAGE = 2 << 20         # bytes per image: evidence at a glance, not a data dump
MAX_EVIDENCE = 8 << 20      # bytes per result: all its images and text together
MAX_DEPTH = 8               # details within details


class _Block:
    def to_json(self) -> dict:
        return {'type': type(self).__name__.lower(), **{f.name: getattr(self, f.name) for f in fields(self)}}


@dataclass
class Note(_Block):
    text: str
    tone: str = 'info'      # one of TONES


@dataclass
class Table(_Block):
    columns: Sequence[str]
    rows: Sequence[Sequence[Any]]       # cells: text, a number, a bool or None


@dataclass
class Code(_Block):
    text: str
    highlight: Sequence[int] = ()       # line numbers, 1-based


@dataclass(init=False)
class Details(_Block):
    """Collapsed by default: `Details("Samples", Table(...), Note(...))`."""
    summary: str
    blocks: Sequence[Any]

    def __init__(self, summary: str, *blocks: Any):
        self.summary = summary
        self.blocks = blocks[0] if len(blocks) == 1 and isinstance(blocks[0], (list, tuple)) else blocks


@dataclass(eq=False)
class Image(_Block):
    """An image file's bytes, shown with an <img>: `Image(png)`. Light
    backgrounds are inverted in dark mode, as scans are."""
    data: bytes
    media_type: str = 'image/png'       # one of MEDIA_TYPES
    alt: str = ""
    title: str = ""


# The block grammar, from the classes: each type's keys besides 'type'. A stored
# image block has 'digest' in place of 'data'.
SHAPES = {c.__name__.lower(): {f.name for f in fields(c)} for c in (Note, Table, Code, Details, Image)}


# --- what a check returns -----------------------------------------------------------


class Undecided(Exception):
    """Raised by a check (or by `ctx.read`) that cannot decide: the grade is
    undecided, and the message, a few words of the exam's own, is the reason
    graders see. Never quote student text in it."""


@dataclass(frozen=True)
class Judgement:
    """What a check found about one field."""
    outcome: str | None = None
    expected: str | None = None
    note: str | None = None

    def __post_init__(self):
        if not (self.outcome is None or isinstance(self.outcome, str)) or self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")

    def to_json(self) -> dict:
        return {k: v for k, v in (('outcome', self.outcome), ('expected', self.expected),
                                  ('note', self.note)) if v is not None or k == 'outcome'}


def judgement(x: Any) -> Judgement:
    """A `Judgement`, an outcome, `(outcome, note)`, or a mapping of a
    judgement's parts; `BadResult` for anything else."""
    given = x
    try:
        if isinstance(x, tuple) and len(x) == 2:
            x = Judgement(x[0], note=x[1] or None)
        elif isinstance(x, Mapping) and set(x) <= {'outcome', 'expected', 'note'}:
            x = Judgement(x.get('outcome'), x.get('expected'), x.get('note'))
        elif x is None or isinstance(x, str):
            x = Judgement(x)
    except ValueError:
        raise _bad("a judgement", given) from None
    if not isinstance(x, Judgement) or not all(v is None or isinstance(v, str) for v in (x.expected, x.note)):
        raise _bad("a judgement", given)
    return x


@dataclass
class Result:
    grade: Any = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    blocks: Sequence[Any] = ()      # block objects (with `to_json`) or plain dicts
    reason: str | None = None       # why undecided, when `grade` is a bare None


def undecided(reason: str, blocks: Sequence[Any] = ()) -> Result:
    """A result for a check that could not reach any verdict."""
    return Result(None, blocks=[{'type': 'note', 'text': reason, 'tone': 'warn'}, *blocks], reason=reason)


class BadResult(ValueError):
    """What a check returned does not fit the result grammar or its limits. The
    message is structural ("the check returned ..."): types and sizes, never content."""


@dataclass(frozen=True)
class Record:
    """A check's result as stored: `body` is `{'grade', 'fields', 'blocks'}` with
    each image block naming its bytes by digest; `images` holds those bytes,
    `{digest: (media_type, data)}`. The only shape a result has after `settle`,
    in a worker, across the pipe (`to_json`/`of_json`) and in the store."""
    body: dict
    images: Mapping[str, tuple[str, bytes]] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {'body': self.body, 'images': {d: [t, base64.b64encode(b).decode('ascii')]
                                              for d, (t, b) in self.images.items()}}

    @classmethod
    def of_json(cls, data: dict) -> 'Record':
        images = {}
        for d, (t, b) in data['images'].items():
            raw = base64.b64decode(b, validate=True)
            if image_digest(t, raw) != d:
                raise BadResult("the check returned an image that does not match its digest")
            images[d] = (t, raw)
        return cls(data['body'], images)


def image_digest(media_type: str, data: bytes) -> str:
    return hashlib.sha256(media_type.encode() + b'\0' + data).hexdigest()


def settle(points: Any, out: Any) -> Record:
    """What a check returned, as stored. The one boundary for the whole result
    (`BadResult` for anything that does not fit, with a structural message):
    the grade is one its points accept (a bool, or a finite number in their
    range; `points.coerce`), its flags are text and its reason text or None;
    each judgement has its shape (`judgement`); every block, a block object
    or a plain dict, fits the block grammar and limits. Top-level
    images go first."""
    if not isinstance(out, Result):
        out = Result(out)
    grade = _grade(points, out.grade)
    if out.reason is not None and not isinstance(out.reason, str):
        raise _bad("a reason", out.reason)
    if grade.value is None and grade.reason is None:
        grade = replace(grade, reason=out.reason or "undecided")
    if not isinstance(out.fields, Mapping):
        raise _bad("fields", out.fields)
    if not all(isinstance(k, str) for k in out.fields):
        raise BadResult("the check returned fields not named by text")
    images: dict[str, tuple[str, bytes]] = {}
    shown = _blocks(out.blocks, images, 0)
    shown.sort(key=lambda b: b['type'] != 'image')          # stable: figures first, in order
    fields = {k: judgement(v).to_json() for k, v in out.fields.items()}
    body = {'grade': grade.to_json(), 'fields': fields, 'blocks': shown}
    size = len(json.dumps(body, ensure_ascii=False)) + sum(len(b) for _, b in images.values())
    if size > MAX_EVIDENCE:
        raise BadResult(f"the check returned {size} bytes of evidence; at most {MAX_EVIDENCE}")
    return Record(body, images)


def _grade(points: Any, x: Any):
    """`x` as a grade of `points`, its parts checked."""
    try:
        grade = points.coerce(x)
    except (ValueError, TypeError):
        raise _bad("a grade its points do not accept,", x.value if isinstance(x, Grade) else x) from None
    v = grade.value
    if not (v is None or isinstance(v, bool) or isinstance(v, (int, float)) and math.isfinite(v)):
        raise _bad("a grade", v)
    if not isinstance(grade.flags, (tuple, list)) or not all(isinstance(f, str) for f in grade.flags):
        raise BadResult("the check returned flags that are not text")
    if grade.reason is not None and not isinstance(grade.reason, str):
        raise _bad("a reason", grade.reason)
    return replace(grade, flags=tuple(grade.flags))


def _bad(what: str, x: Any) -> BadResult:
    return BadResult(f"the check returned {what} of type {type(x).__name__}")


def _text(b: dict, k: str, default: str | None = None) -> str:
    x = b.get(k, default)
    if not isinstance(x, str):
        raise _bad(f"a {b['type']} {k}", x)
    return x


def _cell(x: Any) -> Any:
    if type(x).__module__ == 'numpy' and hasattr(x, 'item'):      # a numpy scalar
        x = x.item()
    if x is None or isinstance(x, (str, bool)):
        return x
    if isinstance(x, numbers.Integral):
        return int(x)
    if isinstance(x, numbers.Real):
        x = float(x)
        return x if math.isfinite(x) else str(x)        # JSON has no NaN or infinity
    raise _bad("a table cell", x)


def _list(x: Any, what: str) -> list:
    if not isinstance(x, (list, tuple)):
        raise _bad(what, x)
    return list(x)


def _blocks(blocks: Any, images: dict, depth: int) -> list[dict]:
    if depth > MAX_DEPTH:
        raise BadResult(f"the check returned details nested deeper than {MAX_DEPTH}")
    return [_block(b, images, depth) for b in _list(blocks, "blocks")]



def _block(b: Any, images: dict, depth: int) -> dict:
    if hasattr(b, 'to_json'):
        b = b.to_json()
    if not isinstance(b, Mapping):
        raise _bad("a block", b)
    kind = b.get('type')
    if kind not in SHAPES:
        raise BadResult("the check returned a block of unknown type")
    if not set(b) - {'type'} <= SHAPES[kind]:
        raise BadResult(f"the check returned a {kind} block with unknown keys")
    if kind == 'note':
        tone = b.get('tone', 'info')
        if tone not in TONES:
            raise BadResult("the check returned a note of unknown tone")
        return {'type': 'note', 'text': _text(b, 'text'), 'tone': tone}
    if kind == 'table':
        columns = [_cell(c) for c in _list(b.get('columns', []), "table columns")]
        return {'type': 'table', 'columns': ['' if c is None else str(c) for c in columns],
                'rows': [[_cell(c) for c in _list(r, "a table row")] for r in _list(b.get('rows'), "table rows")]}
    if kind == 'code':
        hl = _list(b.get('highlight', []), "code highlight")
        if not all(isinstance(n, numbers.Integral) and not isinstance(n, bool) for n in hl):
            raise BadResult("the check returned code highlights that are not line numbers")
        return {'type': 'code', 'text': _text(b, 'text'), 'highlight': [int(n) for n in hl]}
    if kind == 'details':
        return {'type': 'details', 'summary': _text(b, 'summary'),
                'blocks': _blocks(b.get('blocks', []), images, depth + 1)}
    media_type, data = b.get('media_type', 'image/png'), b.get('data')
    if media_type not in MEDIA_TYPES:
        raise BadResult(f"the check returned an image type not one of {', '.join(MEDIA_TYPES)}")
    if not isinstance(data, (bytes, bytearray)):
        raise _bad("image data", data)
    if len(data) > MAX_IMAGE:
        raise BadResult(f"the check returned an image of {len(data)} bytes; at most {MAX_IMAGE}")
    digest = image_digest(media_type, bytes(data))
    images[digest] = (media_type, bytes(data))
    return {'type': 'image', 'digest': digest, 'media_type': media_type,
            'alt': _text(b, 'alt', ""), 'title': _text(b, 'title', "")}
