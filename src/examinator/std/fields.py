"""Fields in a check: one field's value, of the question's own parse or of
another question's (`ctx.read`), and fields that should agree.

    fields.value('side')                       # the question's own field
    fields.value('bit1', question='info')      # another question's; the read is recorded
    fields.agree(fields.value('conf', question='info'), fields.value('obs', question='info'))

Each is a small frozen dataclass called as `fn(ctx) -> str`, so it is plain data
in a stage's definition (`stages.check_math(key, by=...)` picks a key with one)
and a custom check can call it too. A value that is missing, empty or carries a
review marker raises `Undecided`; so do fields that disagree. Values are compared
normalised (`core.reading.normalize`: case, spaces, one layer of brackets).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..core.context import Context
from ..core.defs import has_marker
from ..core.evidence import Undecided
from ..core.reading import normalize


@dataclass(frozen=True)
class Value:
    """One field's value: of the question's own parse, or of `question`'s."""
    label: str
    question: str | None = None

    @property
    def name(self) -> str:
        return self.label if self.question is None else f"{self.question} {self.label}"

    def refs(self) -> tuple['Value', ...]:
        return (self,)

    def __call__(self, ctx: Context) -> str:
        if self.question is None:
            parse = ctx.input('parse')
            v = parse.get(self.label) if isinstance(parse, Mapping) else None
        else:
            v = ctx.read(self.question, self.label)
        if v is None or not str(v).strip():
            raise Undecided(f"{self.name} is empty")
        if has_marker(v):
            raise Undecided(f"{self.name} has a review marker")
        return str(v).strip()


@dataclass(frozen=True)
class Agree:
    """The common value of several fields, which must agree (else undecided)."""
    values: tuple[Value, ...]

    def refs(self) -> tuple[Value, ...]:
        return self.values

    def __call__(self, ctx: Context) -> str:
        got = [v(ctx) for v in self.values]
        if len({normalize(g) for g in got}) > 1:
            raise Undecided(f"{', '.join(v.name for v in self.values)} disagree")
        return got[0]


def value(label: str, *, question: str | None = None) -> Value:
    return Value(label, question)


def agree(*values: Value) -> Agree:
    if not values:
        raise ValueError("agree() needs at least one field")
    return Agree(tuple(values))


Chooser = Callable[[Context], str]      # a `Value`, an `Agree`, or the exam's own function


def as_chooser(by: str | Chooser) -> Chooser:
    """`by` as given to a stage: a label is the question's own field."""
    return Value(by) if isinstance(by, str) else by


def possible(exam: Any, question: Any, by: Chooser, what: str) -> set[str] | None:
    """The values `by` can take as far as the definitions say (the `reads` of
    the printed fields it names), or None if they do not say. `ValueError`
    for a field that does not exist: in the question itself, or in a question
    declared before it."""
    refs = getattr(by, 'refs', None)
    if not callable(refs):
        return None
    out: set[str] | None = None
    for ref in refs():
        q = question if ref.question is None else exam.questions.get(ref.question)
        if q is None:
            raise ValueError(f"{what}: no question {ref.question!r} (declare it before the questions that read it)")
        f = next((f for f in q.fields if f.label == ref.label), None)
        if f is None:
            raise ValueError(f"{what}: {q.id} has no field {ref.label!r}")
        reads = getattr(f.read, 'reads', None)
        if reads:
            vals = {normalize(v) for v in reads.values()}
            out = vals if out is None else out & vals
    return out
