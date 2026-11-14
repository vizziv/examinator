"""What is stored per submission, and whether a grade counts (MODEL.md).

Three kinds of stored thing, each a store row with a revision that changes
only when its content changes:

- a **field value** (`Value`): text, who verified it (nobody, the reader
  that vouched for it, or a person), and whether it needs review (`review`: a
  note, possibly empty, from the reader that was unsure or a person who
  flagged it: `flagged`). Its states: not read (no row), review, unverified,
  verified. The small changes to it are `Value`'s methods.
- a **result** (`Stored`): a check's proposed grade, its evidence, which
  fields it read at which revisions, and which definition computed it. It is
  current while every field it read is at that revision and the definition
  is unchanged; otherwise outdated (and recomputed automatically). A field
  read at its default is recorded at `defs.DEFAULT`: that read is current
  while the field has no stored value, and verified.
- a **decision** (`Decision`): a person's grade for a check, recorded against
  the revisions of the fields on the page; outdated once one of them changes.

The grade of a check (`status`), the first that applies:

| state              | when                                                        | counts |
| ------------------ | ----------------------------------------------------------- | ------ |
| 'decided'          | a decision exists and its fields are unchanged              | yes    |
| 'decided_outdated' | a decision exists, a field changed since                    | no     |
| 'to_decide'        | no check function; or the result is current, the fields it  | no     |
|                    | read are verified, and it proposes no grade (or read        |        |
|                    | nothing)                                                    |        |
| 'not_run'          | no result                                                   | no     |
| 'outdated'         | the result is outdated                                      | no     |
| 'review'           | current, and some field it read needs review                | no     |
| 'unverified'       | current, and some field it read is not verified             | no     |
| 'graded'           | current, every field it read verified, a grade proposed     | yes    |

Pure functions over stored rows; nothing here is derived from evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .defs import DEFAULT, Check, Exam, Grade, Question, key
from .reading import Reading
from .store import Row

if TYPE_CHECKING:
    from .store import Store

STATES = ('decided', 'decided_outdated', 'to_decide', 'not_run', 'outdated', 'review', 'unverified', 'graded')
COUNTING = ('decided', 'graded')
VERIFIERS = ('reader', 'person')
FIELD_STATES = ('not_read', 'review', 'unverified', 'verified')     # a question with no checks: its fields
# every state as a person reads it: the one table wherever a state is shown (grid, filter, CLI)
LABELS = {
    'decided': "decided", 'decided_outdated': "decided, outdated", 'to_decide': "to decide",
    'not_run': "not run", 'outdated': "outdated", 'review': "review", 'unverified': "unverified", 'graded': "graded",
    'not_read': "not read", 'verified': "verified"}


@dataclass(frozen=True)
class Value:
    """One field's stored value, and the small changes to it (MODEL.md), each a pure
    function to the value that replaces it. The revision is the store's: a change
    that keeps the text keeps it.

    A value that needs review is never verified (the one place that holds:
    `__post_init__`). A person's verifying or flagging of a value is never
    undone by a reader while its text stands: a read of the same text changes nothing a person
    set; a read of another text is a new value, its state the reader's."""
    text: str
    rev: int = 0
    verified: str | None = None       # None, 'reader' or 'person'
    by: str | None = None             # the person who verified it
    author: str | None = None         # who wrote this text (a person), None if a reader
    updated_at: str = ''
    review: str | None = None         # needs review: a note (possibly ''); None if not
    flagged: str | None = None        # who asked for the review: 'reader' or 'person'

    def __post_init__(self):
        if self.review is not None and self.verified is not None:
            object.__setattr__(self, 'verified', None)
            object.__setattr__(self, 'by', None)
        if self.review is None and self.flagged is not None:
            object.__setattr__(self, 'flagged', None)

    @property
    def is_verified(self) -> bool:
        return self.verified in VERIFIERS

    @property
    def protected(self) -> bool:
        """A person verified it: a read leaves it alone unless told otherwise."""
        return self.verified == 'person'

    @property
    def state(self) -> str:
        """'review', 'unverified' or 'verified' (FIELD_STATES; no value is 'not_read')."""
        return 'review' if self.review is not None else 'verified' if self.is_verified else 'unverified'

    @staticmethod
    def read(got: Reading, vouched: bool, cur: 'Value | None' = None) -> 'Value':
        """A reader's reading of a field whose value is `cur`: for review if the
        reader was unsure, else verified by the reader if it `vouched`, else
        unverified. The same text keeps whatever a person said about it."""
        if cur is not None and cur.text == got.text and (cur.protected or cur.flagged == 'person'):
            return cur
        return Value(got.text, verified='reader' if vouched else None, review=got.review,
                     flagged='reader' if got.review is not None else None)

    @staticmethod
    def set(text: str, author: str | None) -> 'Value':
        """A person's value: verified by them."""
        return Value(text, verified='person', by=author, author=author)

    def verify(self, author: str | None) -> 'Value':
        """Verified by a person as it stands (any review cleared)."""
        return replace(self, verified='person', by=author, review=None)

    def unverify(self) -> 'Value':
        """Nobody stands behind it any more (a review stays)."""
        return replace(self, verified=None, by=None)

    def flag(self, note: str | None) -> 'Value':
        """Flagged by a person for review, with a note (possibly ''): unverified. With
        `note=None`, the flag taken off (it stays unverified)."""
        return replace(self, review=note, flagged='person' if note is not None else None)

    @staticmethod
    def of(row: Row) -> 'Value':
        b = row.body or {}
        review = b.get('review')
        return Value(str(b.get('text') or ''), row.rev, b.get('verified'), b.get('by'),
                     row.author if row.origin == 'manual' else None, row.updated_at,
                     str(review) if review is not None else None, b.get('flagged'))

    def body(self) -> dict:
        return {'text': self.text, 'verified': self.verified, 'by': self.by, 'review': self.review,
                'flagged': self.flagged}


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

    @staticmethod
    def of(row: Row) -> 'Stored':
        b = row.body or {}
        return Stored(Grade.from_json(b.get('grade')), dict(b.get('reads') or {}), b.get('defn', ''),
                      b.get('code', ''), row.rev, dict(b.get('fields') or {}), b.get('blocks'))


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
    waiting: tuple[str, ...] = ()         # 'review', 'unverified': the fields read that hold it back

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
        if self.state == 'review':
            return f"{', '.join(k.split('/', 1)[1] for k in self.waiting)} needs review"
        return self.grade.reason


Values = Mapping[str, Value]          # one submission's values: 'question/label' -> Value


def rev_of(values: Values, k: str) -> int | None:
    v = values.get(k)
    return v.rev if v is not None else None


def current(stored: Stored, values: Values, *, defn: str, code: str) -> bool:
    """The result was computed under this definition and code, from the values as they
    are (a field read at its default: one with no stored value)."""
    return (stored.defn == defn and stored.code == code
            and all(rev_of(values, k) == (None if rev == DEFAULT else rev) for k, rev in stored.reads.items()))


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
    flagged = tuple(k for k, rev in stored.reads.items()
                    if rev != DEFAULT and (v := values.get(k)) is not None and v.review is not None)
    if flagged:
        return Status('review', stored.grade, waiting=flagged)
    waiting = tuple(k for k, rev in stored.reads.items()
                    if rev != DEFAULT and not ((v := values.get(k)) is not None and v.is_verified))
    if waiting:
        return Status('unverified', stored.grade, waiting=waiting)
    if not stored.reads:
        return Status('to_decide', stored.grade if stored.grade.value is None
                      else Grade(stored.grade.value, stored.grade.flags, "read no field"))
    if stored.grade.value is None:
        return Status('to_decide', stored.grade)
    return Status('graded', stored.grade)


def fields_state(question: Question, values: Values) -> str:
    """How a question's fields stand, for a question with no checks: 'review'
    (some value needs review), 'unverified' (some value is not verified), 'not_read'
    (some field has no value) or 'verified'. A field at its default is verified."""
    got = [values.get(key(question.id, f.label)) for f in question.fields if f.default is None]
    if any(v is not None and v.review is not None for v in got):
        return 'review'
    if any(v is not None and not v.is_verified for v in got):
        return 'unverified'
    if any(v is None for v in got):
        return 'not_read'
    return 'verified'


def total(exam: Exam, statuses: Mapping[tuple[str, str], Status]) -> tuple[float, bool]:
    """Points that count so far for one submission (statuses by (question, check)),
    and whether every counting grade is verified."""
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


KINDS = ('value', 'result', 'decision')


@dataclass(frozen=True)
class Ledger:
    """Everything stored for one submission, typed: what statuses need. Loaded
    only by `load` (a result without its display blocks)."""
    values: dict[str, Value] = field(default_factory=dict)
    results: dict[str, Stored] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)

    @staticmethod
    def load(store: 'Store', submission: str | None = None, *, kinds: Sequence[str] = KINDS,
             prefix: str | None = None) -> dict[str, 'Ledger']:
        """Every submission's ledger (or one's) that has a row, in one query: rows of
        `kinds` whose name starts with `prefix`."""
        out: dict[str, Ledger] = {}
        for r in store.all(kinds=kinds, drop='$.blocks', submission=submission, prefix=prefix):
            got = out.setdefault(r.submission, Ledger())
            if r.kind == 'value':
                got.values[r.name] = Value.of(r)
            elif r.kind == 'result':
                got.results[r.name] = Stored.of(r)
            else:
                got.decisions[r.name] = Decision.of(r)
        return out

    @staticmethod
    def of(store: 'Store', submission: str, *, kinds: Sequence[str] = KINDS) -> 'Ledger':
        """One submission's ledger (empty if nothing is stored)."""
        return Ledger.load(store, submission, kinds=kinds).get(submission) or Ledger()

    def status(self, exam: Exam, q: Question, c: Check) -> Status:
        k = key(q.id, c.name)
        return status(c, self.values, self.results.get(k), self.decisions.get(k),
                      defn=exam.check_hash(q, c), code=exam.code_hash)

    def statuses(self, exam: Exam) -> dict[tuple[str, str], Status]:
        return {(q.id, c.name): self.status(exam, q, c) for q in exam.questions.values() for c in q.checks}
