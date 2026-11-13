"""The focused set: which submissions a grader is looking at, and how a filter narrows it.

A `Facet` is a small function from one submission's view of one question (a
`View`: its field values and the statuses of its checks) to zero or more short
strings ("values"). The core supplies a few built-in facets for every
question: from the status rules (each check's grade and state; how a question
with no checks stands) and the value of each field with a reader of its own (a
printed field: which printing a submission has). Everything else comes from
outside: a reader or a check function may have `facets(question) -> [Facet]`,
and `Exam.question(..., facets=[...])` adds question-level ones.

`values_table` computes every facet's values for the workspace:

    {facet_id: {submission: [value, ...]}}      facet_id = '<question>/<facet name>'

A selection is `{facet_id: [value, ...]}` for a 'choice' facet and
`{facet_id: "search text"}` for a 'text' facet. Values selected for one facet
are alternatives (OR); different facets narrow further (AND). The unit is the
submission: it matches if, for every facet with a selection, at least one of
its values is selected (or, for a text facet, contains the search text).
`web/static/focus.js` mirrors `active`, `matches`, `focused` and `counts`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .defs import Exam, Question, key
from .status import COUNTING, FIELD_STATES, STATES, Sheet, Status

KINDS = ('choice', 'text')
BLANK = '(blank)'        # the value of a blank answer, for facets that show one
GRADES = ('✓', '✗', 'blank', 'undecided')


@dataclass(frozen=True)
class Facet:
    """One way to describe a submission's answer to a question, for filtering.

    `name` is unique within the question ('answer/1.2'); `label` is what the
    page shows. `values(view)` returns zero or more strings. A 'choice' facet
    has few distinct values (a checkbox list); a 'text' facet has free text,
    searched: the first line of each value is what a search matches, any
    further lines are context shown with it. `group` lays related facets
    together; `order` fixes the display order of known values.
    """
    name: str
    label: str
    values: Callable[['View'], Iterable[Any]]
    kind: str = 'choice'
    group: str = ''
    order: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"facet {self.name!r}: kind must be one of {KINDS}")
        if not self.name:
            raise ValueError("a facet needs a name")
        object.__setattr__(self, 'order', tuple(self.order))


class View:
    """What a facet sees of one submission and one question."""

    def __init__(self, question: Question, sheet: Sheet, status: Mapping[tuple[str, str], Status],
                 memo: dict | None = None):
        self.question, self._sheet, self._status = question, sheet, status
        self.memo = memo if memo is not None else {}     # for facets that compute something costly

    def value(self, label: str, *, question: str | None = None) -> str | None:
        """A field's value (text), or None if not read."""
        v = self._sheet.values.get(key(question or self.question.id, label))
        return v.text if v is not None else None

    def status(self, check: str) -> Status:
        return self._status[(self.question.id, check)]

    def fields_state(self) -> str:
        from .status import fields_state
        return fields_state(self.question, self._sheet.values)


# --- built-in facets: from the status rules, never from evidence contents ---------

def grade_text(st: Status, *, blank: bool = True) -> str | None:
    """What a grade is, as text: None (undecided), 'blank' (unless `blank` is
    False), '✓', '✗' or the number. Every other rendering of a grade is built on this."""
    if st.value is None:
        return None
    if blank and 'blank' in st.flags:
        return 'blank'
    if isinstance(st.value, bool):
        return '✓' if st.value else '✗'
    return f'{st.value:g}'


def grade_value(st: Status) -> str:
    """What a grade is, as a facet value: '✓', '✗', 'blank', 'undecided' or the number."""
    text = grade_text(st)
    return 'undecided' if text is None else text


def state_facet(question: Question, check: str | None = None) -> tuple[str, tuple[str, ...]]:
    """The state facet of one grid column (a check, or a question with no checks)
    and its values that do not count yet: what "still to do here" selects."""
    if not question.checks:
        return 'state', tuple(s for s in FIELD_STATES if s != 'verified')
    suffix = f':{check}' if len(question.checks) > 1 else ''
    return 'state' + suffix, tuple(s for s in STATES if s not in COUNTING)


def builtin_facets(question: Question) -> list[Facet]:
    out = []
    several = len(question.checks) > 1
    for c in question.checks:
        suffix, named = (f':{c.name}', f' {c.name}') if several else ('', '')
        out.append(Facet('grade' + suffix, 'grade' + named, lambda v, n=c.name: [grade_value(v.status(n))],
                         group='grade', order=GRADES))
        out.append(Facet(state_facet(question, c.name)[0], 'state' + named,
                         lambda v, n=c.name: [v.status(n).state], group='grade', order=STATES))
    if not question.checks:
        out.append(Facet(state_facet(question)[0], 'state', lambda v: [v.fields_state()],
                         group='grade', order=FIELD_STATES))
    for f in question.fields:       # what was read of the printing, say: few values, filterable
        if f.read is not None:
            out.append(Facet(f'field/{f.label}', f.label, lambda v, label=f.label: [shown(v.value(label))],
                             group='fields'))
    return out


def shown(text: str | None) -> str | None:
    """A value as a facet value: `BLANK` if empty, None if not read."""
    if text is None:
        return None
    return BLANK if not text.strip() else text


def facets(question: Question) -> list[Facet]:
    """A question's facets: built-ins, then its readers', its checks', then its own."""
    out = builtin_facets(question)
    seen: list = []
    for thing in [f.read for f in question.fields] + [question.read] + [c.fn for c in question.checks]:
        if thing is None or any(thing is s for s in seen):
            continue
        seen.append(thing)
        make = getattr(thing, 'facets', None)
        if callable(make):          # two checks over the same fields offer the same facet once
            out += [f for f in make(question) if f.name not in {g.name for g in out}]
    out += list(question.facets)
    for f in out:
        if not isinstance(f, Facet):
            raise TypeError(f"{question.id}: {f!r} is not a Facet")
    names = [f.name for f in out]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"{question.id}: duplicate facet names {dup}")
    return out


def facet_id(question: str, facet: Facet) -> str:
    return f'{question}/{facet.name}'


# --- the values table --------------------------------------------------------------

def _clean(got: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    for v in got or ():
        if v is None:
            continue
        v = v if isinstance(v, str) else str(v)
        if v not in out:
            out.append(v)
    return out


def values_table(exam: Exam, store, *, submission: str | None = None,
                 memo: dict | None = None) -> dict[str, dict[str, list[str]]]:
    """Every facet's values: `{facet_id: {submission: [values]}}`, for the whole
    workspace (or one submission). One query; `memo` keeps what facets compute
    (normal forms of answers) between calls."""
    from .grid import sheets
    every = sheets(store, submission)
    subs = [submission] if submission is not None else [s.id for s in store.submissions()]
    empty = Sheet({}, {}, {})
    memo = {} if memo is None else memo
    out: dict[str, dict[str, list[str]]] = {}
    status = {sid: every.get(sid, empty).statuses(exam) for sid in subs}
    for qid, q in exam.questions.items():
        fs = facets(q)
        cols = {facet_id(qid, f): {} for f in fs}
        out.update(cols)
        for sid in subs:
            view = View(q, every.get(sid, empty), status[sid], memo)
            for f in fs:
                try:
                    vals = _clean(f.values(view))
                except Exception:        # one bad facet must not take the page down
                    vals = ['(error)']
                if vals:
                    cols[facet_id(qid, f)][sid] = vals
    return out


def describe_facets(exam: Exam) -> list[dict]:
    """What the page needs to lay facets out, per question."""
    return [{'id': qid, 'title': q.title,
             'facets': [{'id': facet_id(qid, f), 'name': f.name, 'label': f.label, 'kind': f.kind,
                         'group': f.group, 'order': list(f.order)} for f in facets(q)]}
            for qid, q in exam.questions.items()]


# --- filter semantics (mirrored in web/static/focus.js) ------------------------------

Selection = Mapping[str, Sequence[str] | str]


def _first_line(value: str) -> str:
    return value.split('\n', 1)[0].lower()


def active(table: Mapping[str, Any], selection: Selection) -> dict[str, list[str] | str]:
    """The selections that narrow anything: known facets, non-empty."""
    out: dict[str, list[str] | str] = {}
    for fid, want in (selection or {}).items():
        if fid not in table:
            continue
        if isinstance(want, str):
            if want.strip():
                out[fid] = want.strip().lower()
        elif want:
            out[fid] = [str(v) for v in want]
    return out


def _hit(values: Sequence[str], want: list[str] | str) -> bool:
    if isinstance(want, str):
        return any(want in _first_line(v) for v in values)
    return any(v in want for v in values)


def matches(table: Mapping[str, Mapping[str, Sequence[str]]], selection: Selection, submission: str,
            *, skip: str | None = None) -> bool:
    """Every selected facet (except `skip`) has a selected value among the submission's."""
    for fid, want in active(table, selection).items():
        if fid != skip and not _hit(table[fid].get(submission, ()), want):
            return False
    return True


def focused(table, selection: Selection, submissions: Iterable[str]) -> list[str]:
    """The submissions in focus, in the given order."""
    return [s for s in submissions if matches(table, selection, s)]


def counts(table, selection: Selection, fid: str, submissions: Iterable[str]) -> dict[str, int]:
    """For each value of one facet, how many submissions have it among those
    matching every *other* facet's selection (so alternatives stay visible)."""
    out: dict[str, int] = {}
    col = table.get(fid, {})
    for s in submissions:
        if matches(table, selection, s, skip=fid):
            for v in col.get(s, ()):
                out[v] = out.get(v, 0) + 1
    return out


__all__ = ['BLANK', 'Facet', 'View', 'active', 'builtin_facets', 'counts', 'describe_facets',
           'facet_id', 'facets', 'focused', 'grade_text', 'grade_value', 'matches', 'shown',
           'state_facet', 'values_table']
