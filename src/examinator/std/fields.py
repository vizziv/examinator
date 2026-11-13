"""Fields in a check: one field's value, of the check's own question or of
another question's, and fields that should agree.

    fields.value('side')                       # the question's own field
    fields.value('bit1', question='info')      # another question's
    fields.agree(fields.value('conf', question='info'), fields.value('obs', question='info'))
    fields.table(fields.value('conf', question='info'), {'95': 'original', '90': 'alternate'})

Each is a small frozen dataclass called as `fn(ctx) -> str`, so it is plain data
in a check's definition (`math.keyed(key, by=...)` picks a key with one) and a
custom check can call it too. Every read goes through `ctx.read`, so it is
recorded. A value that is not read, empty, or carries a review marker raises
`Undecided`; so do fields that disagree. Values are compared normalised
(`core.reading.normalize`: case, spaces, one layer of brackets).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..core.reading import normalize
from ..core.result import Undecided


@dataclass(frozen=True)
class Value:
    """One field's value: of the check's own question, or of `question`'s."""
    label: str
    question: str | None = None

    @property
    def name(self) -> str:
        return self.label if self.question is None else f"{self.question} {self.label}"

    def refs(self) -> tuple['Value', ...]:
        return (self,)

    def __call__(self, ctx) -> str:
        v = ctx.read(self.label, question=self.question)
        if not v.strip():
            raise Undecided(f"{self.name} is empty")
        return v.strip()


@dataclass(frozen=True)
class Table:
    """A field's value looked up in a table ({value: name}, values compared
    normalised): the printing a printed text names, say. A value not in the
    table is undecided, naming the field and the value."""
    value: Value
    names: Mapping[str, str]

    @property
    def name(self) -> str:
        return self.value.name

    def refs(self) -> tuple[Value, ...]:
        return self.value.refs()

    def __call__(self, ctx) -> str:
        v = self.value(ctx)
        hit = {normalize(k): n for k, n in self.names.items()}.get(normalize(v))
        if hit is None:
            raise Undecided(f"{self.name} = {v}: not one of {', '.join(self.names)}"[:160])
        return hit


@dataclass(frozen=True)
class Agree:
    """The common value of several fields (or tables), which must agree (else undecided)."""
    values: tuple[Value | Table, ...]

    @property
    def name(self) -> str:
        return " and ".join(v.name for v in self.values)

    def refs(self) -> tuple[Value, ...]:
        return tuple(r for v in self.values for r in v.refs())

    def __call__(self, ctx) -> str:
        got = [v(ctx) for v in self.values]
        if len({normalize(g) for g in got}) > 1:
            raise Undecided(f"{', '.join(v.name for v in self.values)} disagree")
        return got[0]


def value(label: str, *, question: str | None = None) -> Value:
    return Value(label, question)


def table(value: Value, names: Mapping[str, str]) -> Table:
    return Table(value, dict(names))


def agree(*values: Value | Table) -> Agree:
    if not values:
        raise ValueError("agree() needs at least one field")
    return Agree(tuple(values))


Chooser = Callable[[Any], str]      # a `Value`, an `Agree`, or the exam's own function


def as_chooser(by: str | Chooser) -> Chooser:
    """`by` as given to a check: a label is the question's own field."""
    return Value(by) if isinstance(by, str) else by


def check_refs(exam: Any, question: Any, by: Chooser, what: str) -> None:
    """`ValueError` for a field `by` names that does not exist: in the question
    itself, or in a question declared before it."""
    refs = getattr(by, 'refs', None)
    for ref in refs() if callable(refs) else ():
        q = question if ref.question is None else exam.questions.get(ref.question)
        if q is None:
            raise ValueError(f"{what}: no question {ref.question!r} (declare it before the questions that read it)")
        if q.field(ref.label) is None:
            raise ValueError(f"{what}: {q.id} has no field {ref.label!r}")
