"""What a check returns: one proposed grade, and evidence for people.

    Result(grade, fields={'1.1': 'correct', '1.2': Judgement('incorrect', expected='3/2')},
           blocks=[Plot.cdf(...), Details("Samples", Table(...))])

`grade` is anything the check's points accept: True/False, a number,
'correct'/'incorrect'/'blank', None (undecided), or a `Grade` with flags and a
reason. Evidence has two parts, both optional and for people only (nothing
computes from it):

- `fields`: per field of the check's own question that it judged, an outcome
  ('correct', 'incorrect', 'blank' or None) and optionally the expected answer
  and a short note; shown beside the field.
- `blocks`: an ordered list of things to show (`examinator.std.blocks`).

A check may also return a bare grade, or raise `Undecided("a few words")`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

OUTCOMES = ('correct', 'incorrect', 'blank', None)


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
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {self.outcome!r}")

    def to_json(self) -> dict:
        return {k: v for k, v in (('outcome', self.outcome), ('expected', self.expected),
                                  ('note', self.note)) if v is not None or k == 'outcome'}


def judgement(x: Any) -> Judgement:
    """A `Judgement`, an outcome, or `(outcome, note)`."""
    if isinstance(x, Judgement):
        return x
    if isinstance(x, tuple):
        outcome, note = x
        return Judgement(outcome, note=note or None)
    if isinstance(x, Mapping):
        return Judgement(x.get('outcome'), x.get('expected'), x.get('note'))
    return Judgement(x)


@dataclass
class Result:
    grade: Any = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    blocks: Sequence[Any] = ()      # block objects (with `to_json`) or plain dicts
    reason: str | None = None       # why undecided, when `grade` is a bare None

    def evidence(self) -> dict:
        """The stored evidence: `{'fields': {label: judgement}, 'blocks': [...]}`."""
        return {'fields': {str(k): judgement(v).to_json() for k, v in self.fields.items()},
                'blocks': [b.to_json() if hasattr(b, 'to_json') else b for b in self.blocks]}


def undecided(reason: str, blocks: Sequence[Any] = ()) -> Result:
    """A result for a check that could not reach any verdict."""
    return Result(None, blocks=[{'type': 'note', 'text': reason, 'tone': 'warn'}, *blocks], reason=reason)


def settle(points: Any, out: Any) -> dict:
    """What a check returned, as stored: `{'grade': Grade JSON, 'fields', 'blocks'}`.
    The grade is coerced by the check's points (`ValueError` if it does not fit)."""
    from .defs import Grade
    if not isinstance(out, Result):
        out = Result(out)
    grade = points.coerce(out.grade)
    if grade.value is None and grade.reason is None:
        grade = Grade(None, grade.flags, out.reason or "undecided")
    return {'grade': grade.to_json(), **out.evidence()}
