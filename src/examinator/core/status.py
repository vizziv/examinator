"""What is stored per submission, and whether a grade counts (MODEL.md).

Three kinds of stored thing, each a store row with a revision that changes
only when its content changes:

- a **field value** (`Value`): text, and who settled it: nobody, the reader
  that vouched for it, or a person. Its states: not read (no row), unsettled,
  settled. A value with a review marker is never settled.
- a **result** (`Stored`): a check's proposed grade, its evidence, which
  fields it read at which revisions, and which definition computed it. It is
  current while every field it read is at that revision and the definition
  is unchanged; otherwise outdated (and recomputed automatically).
- a **decision** (`Decision`): a person's grade for a check, recorded against
  the revisions of the fields on the page; outdated once one of them changes.

The grade of a check (`status`), the first that applies:

| state              | when                                                        | counts |
| ------------------ | ----------------------------------------------------------- | ------ |
| 'decided'          | a decision exists and its fields are unchanged              | yes    |
| 'decided_outdated' | a decision exists, a field changed since                    | no     |
| 'to_decide'        | no check function; or the result is current, the fields it  | no     |
|                    | read are settled, and it proposes no grade (or read nothing)|        |
| 'not_run'          | no result                                                   | no     |
| 'outdated'         | the result is outdated                                      | no     |
| 'unverified'       | current, and some field it read is not settled              | no     |
| 'graded'           | current, every field it read settled, a grade proposed      | yes    |

Pure functions over stored rows; nothing here is derived from evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .defs import Check, Exam, Grade, Question, has_marker, key
from .store import Row

STATES = ('decided', 'decided_outdated', 'to_decide', 'not_run', 'outdated', 'unverified', 'graded')
COUNTING = ('decided', 'graded')
SETTLERS = ('reader', 'person')
FIELD_STATES = ('not_read', 'unverified', 'verified')     # a question with no checks: its fields


@dataclass(frozen=True)
class Value:
    """One field's stored value."""
    text: str
    rev: int
    settled: str | None = None        # None, 'reader' or 'person'
    by: str | None = None             # the person who settled it
    author: str | None = None         # who wrote this text (a person), None if a reader
    updated_at: str = ''

    @property
    def is_settled(self) -> bool:
        return self.settled in SETTLERS

    @property
    def marker(self) -> bool:
        return has_marker(self.text)

    @staticmethod
    def of(row: Row) -> 'Value':
        b = row.body or {}
        return Value(str(b.get('text') or ''), row.rev, b.get('settled'), b.get('by'),
                     row.author if row.origin == 'manual' else None, row.updated_at)

    def body(self) -> dict:
        return {'text': self.text, 'settled': self.settled, 'by': self.by}


@dataclass(frozen=True)
class Stored:
    """One check's stored result."""
    grade: Grade
    reads: Mapping[str, int | None]       # 'question/label' -> revision read (None: not read)
    defn: str                             # the check's definition hash
    code: str                             # the exam package's code hash
    rev: int
    fields: Mapping[str, Mapping] = field(default_factory=dict)    # label -> judgement
    blocks: list | None = None            # None when not loaded
    params: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def of(row: Row) -> 'Stored':
        b = row.body or {}
        return Stored(Grade.from_json(b.get('grade')), dict(b.get('reads') or {}), b.get('defn', ''),
                      b.get('code', ''), row.rev, dict(b.get('fields') or {}), b.get('blocks'),
                      dict(b.get('params') or {}))


@dataclass(frozen=True)
class Decision:
    """A person's grade for one check."""
    grade: Grade
    seen: Mapping[str, int | None]        # 'question/label' -> revision on the page
    author: str | None
    rev: int
    updated_at: str = ''

    @staticmethod
    def of(row: Row) -> 'Decision':
        b = row.body or {}
        return Decision(Grade.from_json(b.get('grade')), dict(b.get('seen') or {}), row.author, row.rev,
                        row.updated_at)


@dataclass(frozen=True)
class Status:
    state: str
    grade: Grade = Grade()                # shown: the decision's, or the proposal (possibly old)
    author: str | None = None             # who decided
    waiting: tuple[str, ...] = ()         # 'unverified': the fields read that are not settled

    @property
    def counts(self) -> bool:
        return self.state in COUNTING

    @property
    def value(self) -> Any:
        return self.grade.value

    @property
    def flags(self) -> tuple[str, ...]:
        return self.grade.flags

    @property
    def reason(self) -> str | None:
        return self.grade.reason


Values = Mapping[str, Value]          # one submission's values: 'question/label' -> Value


def rev_of(values: Values, k: str) -> int | None:
    v = values.get(k)
    return v.rev if v is not None else None


def current(stored: Stored, values: Values, *, defn: str, code: str) -> bool:
    """The result was computed under this definition and code, from the values as they are."""
    return (stored.defn == defn and stored.code == code
            and all(rev_of(values, k) == rev for k, rev in stored.reads.items()))


def decision_current(d: Decision, values: Values) -> bool:
    return all(rev_of(values, k) == rev for k, rev in d.seen.items())


def status(check: Check, values: Values, stored: Stored | None, decision: Decision | None,
           *, defn: str, code: str) -> Status:
    """The grade of one check of one submission (the table above)."""
    if decision is not None:
        state = 'decided' if decision_current(decision, values) else 'decided_outdated'
        return Status(state, decision.grade, decision.author)
    if check.fn is None:
        return Status('to_decide', Grade(None, reason="decided by a person"))
    if stored is None:
        return Status('not_run')
    if not current(stored, values, defn=defn, code=code):
        return Status('outdated', stored.grade)
    waiting = tuple(k for k in stored.reads if not ((v := values.get(k)) is not None and v.is_settled))
    if waiting:
        return Status('unverified', stored.grade, waiting=waiting)
    if not stored.reads:
        return Status('to_decide', stored.grade if stored.grade.value is None
                      else Grade(stored.grade.value, stored.grade.flags, "read no field"))
    if stored.grade.value is None:
        return Status('to_decide', stored.grade)
    return Status('graded', stored.grade)


def fields_state(question: Question, values: Values) -> str:
    """How a question's fields stand, for a question with no checks: 'not_read'
    (some field has no value), 'unverified' (some value is not settled) or 'verified'."""
    got = [values.get(key(question.id, f.label)) for f in question.fields]
    if any(v is not None and not v.is_settled for v in got):
        return 'unverified'
    if any(v is None for v in got):
        return 'not_read'
    return 'verified'


def total(exam: Exam, statuses: Mapping[tuple[str, str], Status]) -> tuple[float, bool]:
    """Points that count so far for one submission (statuses by (question, check)),
    and whether every counting grade is settled."""
    points, complete = 0.0, True
    for qid, q in exam.questions.items():
        for c in q.checks:
            if not c.points.counts:
                continue
            st = statuses.get((qid, c.name))
            if st is not None and st.counts:
                points += c.points.points(st.value)
            else:
                complete = False
    return points, complete


@dataclass(frozen=True)
class Sheet:
    """Everything stored for one submission, typed: what statuses need."""
    values: dict[str, Value]
    results: dict[str, Stored]
    decisions: dict[str, Decision]

    @staticmethod
    def of(rows: list[Row]) -> 'Sheet':
        values, results, decisions = {}, {}, {}
        for r in rows:
            if r.kind == 'value':
                values[r.name] = Value.of(r)
            elif r.kind == 'result':
                results[r.name] = Stored.of(r)
            elif r.kind == 'decision':
                decisions[r.name] = Decision.of(r)
        return Sheet(values, results, decisions)

    def status(self, exam: Exam, q: Question, c: Check) -> Status:
        k = key(q.id, c.name)
        return status(c, self.values, self.results.get(k), self.decisions.get(k),
                      defn=exam.check_hash(q, c), code=exam.code_hash)

    def statuses(self, exam: Exam) -> dict[tuple[str, str], Status]:
        return {(q.id, c.name): self.status(exam, q, c) for q in exam.questions.values() for c in q.checks}
