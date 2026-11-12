"""Workflow rules: whether a grade counts (DESIGN §7).

Pure functions over stored rows. `state` says where a result is in the
workflow; `value` and `flags` say what the grade is. The two are never mixed,
and neither is derived from evidence contents.

| state            | meaning                                               | counts |
| ---------------- | ----------------------------------------------------- | ------ |
| 'not_started'    | nothing exists                                        | no     |
| 'in_progress'    | artifacts exist, no grade proposed yet                | no     |
| 'unverified'     | a current proposed grade; `waiting` says what of its  | no     |
|                  | own a person must still verify ('parse'), `waiting_on`|        |
|                  | which other questions' parses it read and waits for   |        |
| 'graded'         | parse verified, evidence current, grade decided       | yes    |
| 'needs_review'   | parse verified, evidence current, grade undecided;    | no     |
|                  | or a question with no grade rule, not yet decided     |        |
| 'outdated'       | evidence or grade computed from an older parse (its   | no     |
|                  | own or one it read) or definition; old value shown    |        |
| 'manual'         | a grader decided; parse unchanged since               | yes    |
| 'manual_outdated'| a grader decided; parse changed since                 | no     |

A check may read other questions' parses (`Context.read`). The evidence
records each one's revision (`Artifact.reads`; None for a parse that did not
exist). So every function that judges evidence takes `parses`, the
submission's parse artifacts by question; it may be left out only for
evidence that read nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .defs import Exam, Question, grade_kind, stage_hash
from .store import Artifact

STATES = ('not_started', 'in_progress', 'unverified', 'graded', 'needs_review',
          'outdated', 'manual', 'manual_outdated')
COUNTING = ('graded', 'manual')

# Artifact kinds that are not grading work: per-submission settings (a question's
# page selection, the scan layout) and intake's records of who wrote it (the
# model's reading of the name and ID boxes, the person picked; `core.identity`).
# They do not start a question, and do not stop intake rewriting a submission.
NOT_WORK = ('pages', 'layout', 'identity', 'who')

Parses = Mapping[str, Artifact]      # one submission's parse artifacts, by question


@dataclass(frozen=True)
class Status:
    state: str
    value: Any = None                 # the grade shown (possibly old), None if undecided
    flags: tuple[str, ...] = ()
    reason: str | None = None         # why undecided
    waiting: tuple[str, ...] = ()     # what of its own must be verified before it counts
    waiting_on: tuple[str, ...] = ()  # other questions whose parse it read and must be verified
    stale_code: bool = False          # checked with older exam code (informational)
    author: str | None = None         # who decided, for manual grades

    @property
    def counts(self) -> bool:
        """The workflow lets this grade count. (Whether the part is included in
        totals at all is the result spec's `counts`.)"""
        return self.state in COUNTING


def started(arts: Mapping[str, Artifact]) -> bool:
    return any(k not in NOT_WORK for k in arts)


def parses_of(arts_by_question: Mapping[str, Mapping[str, Artifact]]) -> dict[str, Artifact]:
    """The parse artifacts among one submission's artifacts by question."""
    return {qid: a['parse'] for qid, a in arts_by_question.items() if 'parse' in a}


def _derived(question: Question, kind: str) -> bool:
    """An artifact people do not edit: it must follow from its own inputs."""
    stage = question.stage(kind)
    return stage is not None and not stage.editable


def _reads_current(a: Artifact, parses: Parses | None) -> bool:
    """Every other question's parse that `a` read is still at the revision it read."""
    if not a.reads:
        return True
    if parses is None:
        raise TypeError("this evidence read other questions: pass the submission's `parses`")
    return all((p.rev if (p := parses.get(qid)) is not None else None) == rev for qid, rev in a.reads.items())


def _follows(question: Question, kind: str, arts: Mapping[str, Artifact], parses: Parses | None) -> bool:
    """The artifact was computed from the current content of its inputs and of the
    other questions' parses it read, under the current definition; and so, in
    turn, was every derived input. The walk stops at editable artifacts: a person
    vouches for those by verifying them."""
    a, stage = arts.get(kind), question.stage(kind)
    if a is None or stage is None:
        return False
    for k in stage.inputs:
        i = arts.get(k)
        if i is None or a.revs.get(k) != i.rev:
            return False
        if _derived(question, k) and not _follows(question, k, arts, parses):
            return False
    if not _reads_current(a, parses):
        return False
    return a.inputs.get('defn') == stage_hash(question, stage)


def verifiable(question: Question) -> tuple[str, ...]:
    """The editable artifacts a check's result rests on: what a person must
    verify, and what a manual grade is recorded against. Usually ('parse',)."""
    out: list[str] = []

    def walk(kind):
        stage = question.stage(kind)
        for k in (stage.inputs if stage else ()):
            if _derived(question, k):
                walk(k)
            elif k not in out:
                out.append(k)

    if question.stage('evidence') is not None:
        walk('evidence')
    elif question.stage('parse') is not None:
        out.append('parse')
    return tuple(out)


def evidence_current(question: Question, arts: Mapping[str, Artifact], parses: Parses | None = None) -> bool:
    """Evidence was computed from the current inputs, parses read and definition."""
    return _follows(question, 'evidence', arts, parses)


def grade_current(question: Question, part: str, arts: Mapping[str, Artifact],
                  parses: Parses | None = None) -> bool:
    """An automatic grade follows from the current evidence and grade rule."""
    g, ev, stage = arts.get(grade_kind(part)), arts.get('evidence'), question.stage('grade')
    if g is None or ev is None or stage is None or not evidence_current(question, arts, parses):
        return False
    return g.revs.get('evidence') == ev.rev and g.inputs.get('defn') == stage_hash(question, stage)


def waiting(question: Question, arts: Mapping[str, Artifact]) -> tuple[str, ...]:
    """What of its own a person must still confirm before an automatic grade counts.
    Fails closed: a result that rests on nothing a person can verify never
    counts on its own ('input'); it needs a manual decision."""
    kinds = verifiable(question)
    out = [k for k in kinds if not ((a := arts.get(k)) is not None and a.verified)]
    if not kinds:
        out.append('input')
    return tuple(out)


def waiting_on(arts: Mapping[str, Artifact], parses: Parses | None) -> tuple[str, ...]:
    """The other questions whose parse the evidence read and that are not verified
    (or do not exist): an automatic grade waits for a person to verify them."""
    ev = arts.get('evidence')
    if ev is None or not ev.reads:
        return ()
    parses = parses or {}
    return tuple(qid for qid in ev.reads if not ((p := parses.get(qid)) is not None and p.verified))


def seen_revs(question: Question, arts: Mapping[str, Artifact]) -> dict[str, int | None]:
    """The revisions a manual grade is recorded against."""
    return {k: (arts[k].rev if k in arts else None) for k in verifiable(question)}


def part_status(question: Question, part: str, arts: Mapping[str, Artifact],
                *, parses: Parses | None = None, code_hash: str = '') -> Status:
    """The status of one result part of one submission's question."""
    g = arts.get(grade_kind(part))
    ev = arts.get('evidence')
    stale = bool(code_hash and ev is not None and ev.inputs.get('code') not in (None, code_hash))

    if g is not None and g.origin == 'manual':
        same = dict(g.revs) == seen_revs(question, arts)
        return Status('manual' if same else 'manual_outdated', g.body.get('value'),
                      tuple(g.body.get('flags') or ()), g.body.get('reason'), author=g.author)

    if question.stage('grade') is None:        # decided by hand only: always a grader's to do
        return Status('needs_review', reason="by hand")
    if not started(arts):
        return Status('not_started')
    if g is None:
        return Status('outdated' if ev is not None and not evidence_current(question, arts, parses)
                      else 'in_progress', stale_code=stale)

    value, flags, reason = g.body.get('value'), tuple(g.body.get('flags') or ()), g.body.get('reason')
    if not grade_current(question, part, arts, parses):
        return Status('outdated', value, flags, reason, stale_code=stale)
    wait, on = waiting(question, arts), waiting_on(arts, parses)
    if wait or on:
        return Status('unverified', value, flags, reason, waiting=wait, waiting_on=on, stale_code=stale)
    if value is None:
        return Status('needs_review', None, flags, reason, stale_code=stale)
    return Status('graded', value, flags, reason, stale_code=stale)


def question_status(question: Question, arts: Mapping[str, Artifact],
                    *, parses: Parses | None = None, code_hash: str = '') -> dict[str, Status]:
    """Every result part's status (none for a question with no result)."""
    return {part: part_status(question, part, arts, parses=parses, code_hash=code_hash)
            for part in question.result}


def total(exam: Exam, by_question: Mapping[str, Mapping[str, Status]]) -> tuple[float, bool]:
    """Points that count so far for one submission, and whether every counting
    part is settled."""
    points, complete = 0.0, True
    for qid, q in exam.questions.items():
        for part, spec in q.result.items():
            if not spec.counts:
                continue
            st = by_question.get(qid, {}).get(part)
            if st is not None and st.counts:
                points += spec.points(st.value)
            else:
                complete = False
    return points, complete


def recheck_wanted(question: Question, arts: Mapping[str, Artifact]) -> bool:
    """Automatic recheck applies if some part's grade is automatic or missing."""
    return any((g := arts.get(grade_kind(p))) is None or g.origin != 'manual'
               for p in question.result)
