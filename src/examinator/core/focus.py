"""The focused set: which submissions a grader is looking at, and how a filter narrows it.

A `Facet` is a small function from one submission's stored rows to zero or
more short strings ("values"). The core supplies a few built-in facets for
every question, from the workflow rules only (grade value, workflow state,
variant); everything else comes from outside: a `Stage` declares facets over
what it writes, and `Exam.question(..., facets=[...])` adds question-level ones.

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

from .defs import Exam, Question
from .grid import load, row_cells
from .store import Store
from .workflow import STATES

STATUS = '@status'      # what a facet reads to see the workflow's view (a `grid.Cell`)
KINDS = ('choice', 'text')
BLANK = '(blank)'        # the value of a blank answer, for facets that show one
GRADES = ('✓', '✗', 'blank', 'undecided')


@dataclass(frozen=True)
class Facet:
    """One way to describe a submission's answer to a question, for filtering.

    `name` is unique within the question ('answer/1.2'); `label` is what the
    page shows. `reads` lists the artifact kinds `values` needs, each optionally
    with a JSON path into the body ({'evidence': '$.data.fields'}) so that only
    that part is loaded; `STATUS` gives the workflow's view instead. `values`
    gets `{kind: body or part}` for the kinds that exist and returns zero or
    more strings. A 'choice' facet has few distinct values (a checkbox list); a
    'text' facet has free text, searched: the first line of each value is what
    a search matches, any further lines are context shown with it. `group` lays
    related facets together; `order` fixes the display order of known values.
    """
    name: str
    label: str
    values: Callable[[Mapping[str, Any]], Iterable[Any]]
    reads: Any = ()                    # kinds, or {kind: JSON path | None}; stored as pairs
    kind: str = 'choice'
    group: str = ''
    order: tuple[str, ...] = ()

    def __post_init__(self):
        pairs = tuple(self.reads.items()) if isinstance(self.reads, Mapping) else \
            tuple((r, None) if isinstance(r, str) else tuple(r) for r in self.reads)
        kinds = [k for k, _ in pairs]
        if len(set(kinds)) != len(kinds):
            raise ValueError(f"facet {self.name!r} reads a kind twice")
        for _, path in pairs:
            if path is not None and not str(path).startswith('$'):
                raise ValueError(f"facet {self.name!r}: a JSON path starts with '$'")
        if self.kind not in KINDS:
            raise ValueError(f"facet {self.name!r}: kind must be one of {KINDS}")
        if not self.name:
            raise ValueError("a facet needs a name")
        object.__setattr__(self, 'reads', pairs)
        object.__setattr__(self, 'order', tuple(self.order))


# --- built-in facets: from the workflow rules, never from evidence contents ---------

def grade_text(st, *, blank: bool = True) -> str | None:
    """What a grade is, as text: None (undecided), 'blank' (unless `blank` is
    False), '✓', '✗' or the number. Every other rendering of a grade is built on this."""
    if st.value is None:
        return None
    if blank and 'blank' in st.flags:
        return 'blank'
    if isinstance(st.value, bool):
        return '✓' if st.value else '✗'
    return f'{st.value:g}'


def grade_value(st) -> str:
    """What a grade is, as a facet value: '✓', '✗', 'blank', 'undecided' or the number."""
    text = grade_text(st)
    return 'undecided' if text is None else text


def builtin_facets(question: Question) -> list[Facet]:
    out = []
    for part in question.result:
        suffix, shown = (f':{part}', f' {part}') if part else ('', '')
        out.append(Facet('grade' + suffix, 'grade' + shown,
                         lambda b, p=part: [grade_value(b[STATUS].status[p])],
                         reads=(STATUS,), group='result', order=GRADES))
        out.append(Facet('state' + suffix, 'state' + shown,
                         lambda b, p=part: [b[STATUS].status[p].state],
                         reads=(STATUS,), group='result', order=STATES))
    if question.variants:
        out.append(Facet('variant', 'variant', lambda b: [b[STATUS].variant or 'undecided'],
                         reads=(STATUS,), group='result', order=(*question.variants, 'undecided')))
    return out


def facets(question: Question) -> list[Facet]:
    """A question's facets: built-ins, then its stages', then its own."""
    out = builtin_facets(question)
    for s in question.stages:
        out += list(s.facets)
    out += list(question.facets)
    names = [f.name for f in out]
    for f in out:
        if not isinstance(f, Facet):
            raise TypeError(f"{question.id}: {f!r} is not a Facet")
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


def values_table(exam: Exam, store: Store, *, submission: str | None = None
                 ) -> dict[str, dict[str, list[str]]]:
    """Every facet's values: `{facet_id: {submission: [values]}}`, for the whole
    workspace (or one submission). One query per (question, kind, path) that
    facets read, plus the grid's two; no per-row lookups."""
    if submission is None:
        cells = {r.submission.id: r.cells for r in load(exam, store)}
    else:
        cells = {submission: row_cells(exam, {qid: store.for_question(submission, qid)
                                               for qid in exam.questions})}
    out: dict[str, dict[str, list[str]]] = {}
    for qid, q in exam.questions.items():
        fs = facets(q)
        needed = {(k, p) for f in fs for k, p in f.reads if k != STATUS}
        data = {kp: store.bodies(qid, kp[0], kp[1], submission=submission) for kp in needed}
        for f in fs:
            col = out[facet_id(qid, f)] = {}
            for sid, row in cells.items():
                got: dict[str, Any] = {}
                for k, p in f.reads:
                    if k == STATUS:
                        got[k] = row[qid]
                    elif sid in data[(k, p)]:
                        got[k] = data[(k, p)][sid]
                if not got:
                    continue
                try:
                    vals = _clean(f.values(got))
                except Exception:        # one bad facet must not take the page down
                    vals = ['(error)']
                if vals:
                    col[sid] = vals
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


__all__ = ['BLANK', 'Facet', 'STATUS', 'active', 'builtin_facets', 'counts', 'describe_facets',
           'facet_id', 'facets', 'focused', 'grade_text', 'grade_value', 'matches', 'values_table']
