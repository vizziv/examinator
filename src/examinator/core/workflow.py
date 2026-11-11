"""Workflow rules: whether a grade counts (DESIGN §7).

Pure functions over stored rows. `state` says where a result is in the
workflow; `value` and `flags` say what the grade is. The two are never mixed,
and neither is derived from evidence contents.

| state            | meaning                                               | counts |
| ---------------- | ----------------------------------------------------- | ------ |
| 'not_started'    | nothing exists                                        | no     |
| 'in_progress'    | artifacts exist, no grade proposed yet                | no     |
| 'unverified'     | a current proposed grade; `waiting` says what a       | no     |
|                  | person must still confirm ('parse', 'variant')        |        |
| 'graded'         | parse verified, evidence current, grade decided       | yes    |
| 'needs_review'   | parse verified, evidence current, grade undecided     | no     |
| 'outdated'       | evidence or grade computed from an older parse,       | no     |
|                  | variant or definition; old value still shown          |        |
| 'manual'         | a grader decided; parse unchanged since               | yes    |
| 'manual_outdated'| a grader decided; parse changed since                 | no     |
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .defs import Exam, Question, grade_kind, stage_hash
from .store import Artifact

STATES = ('not_started', 'in_progress', 'unverified', 'graded', 'needs_review',
          'outdated', 'manual', 'manual_outdated')
COUNTING = ('graded', 'manual')

# Per-submission overrides stored beside the artifacts; they are not work on the answer.
OVERRIDE_KINDS = ('pages',)


@dataclass(frozen=True)
class Status:
    state: str
    value: Any = None                 # the grade shown (possibly old), None if undecided
    flags: tuple[str, ...] = ()
    reason: str | None = None         # why undecided
    waiting: tuple[str, ...] = ()     # what must be verified before it counts
    stale_code: bool = False          # checked with older exam code (informational)
    author: str | None = None         # who decided, for manual grades

    @property
    def counts(self) -> bool:
        """The workflow lets this grade count. (Whether the part is included in
        totals at all is the result spec's `counts`.)"""
        return self.state in COUNTING


def started(arts: Mapping[str, Artifact]) -> bool:
    return any(k not in OVERRIDE_KINDS for k in arts)


def variant_trusted(question: Question, arts: Mapping[str, Artifact]) -> bool:
    """A resolved variant counts without review if a person chose or verified it,
    or if at least `question.trust` independent cues agreed."""
    v = arts.get('variant')
    if v is None or not (v.body or {}).get('resolved'):
        return False
    return v.origin == 'manual' or v.verified or (v.body.get('agree') or 0) >= question.trust


def evidence_current(question: Question, arts: Mapping[str, Artifact]) -> bool:
    """Evidence was computed from the current inputs, variant and definition."""
    ev, stage = arts.get('evidence'), question.stage('evidence')
    if ev is None or stage is None:
        return False
    for kind in stage.inputs:
        a = arts.get(kind)
        if a is None or ev.revs.get(kind) != a.rev:
            return False
    if stage.uses_variant and question.variants:
        v = arts.get('variant')
        if ev.revs.get('variant') != (v.rev if v else None):
            return False
    return ev.inputs.get('defn') == stage_hash(question, stage)


def grade_current(question: Question, part: str, arts: Mapping[str, Artifact]) -> bool:
    """An automatic grade follows from the current evidence and grade rule."""
    g, ev, stage = arts.get(grade_kind(part)), arts.get('evidence'), question.stage('grade')
    if g is None or ev is None or stage is None or not evidence_current(question, arts):
        return False
    return g.revs.get('evidence') == ev.rev and g.inputs.get('defn') == stage_hash(question, stage)


def waiting(question: Question, arts: Mapping[str, Artifact]) -> tuple[str, ...]:
    """What a person must still confirm before an automatic grade counts."""
    out = []
    check = question.stage('evidence')
    for kind in (check.inputs if check else ()):
        stage = question.stage(kind)
        a = arts.get(kind)
        if stage is not None and stage.editable and not (a is not None and a.verified):
            out.append(kind)
    if check and check.uses_variant and question.variants and not variant_trusted(question, arts):
        out.append('variant')
    return tuple(out)


def part_status(question: Question, part: str, arts: Mapping[str, Artifact],
                *, code_hash: str = '') -> Status:
    """The status of one result part of one submission's question."""
    g = arts.get(grade_kind(part))
    ev = arts.get('evidence')
    stale = bool(code_hash and ev is not None and ev.inputs.get('code') not in (None, code_hash))

    if g is not None and g.origin == 'manual':
        parse = arts.get('parse')
        same = g.revs.get('parse') == (parse.rev if parse else None)
        return Status('manual' if same else 'manual_outdated', g.body.get('value'),
                      tuple(g.body.get('flags') or ()), g.body.get('reason'), author=g.author)

    if not started(arts):
        return Status('not_started')
    if g is None:
        return Status('outdated' if ev is not None and not evidence_current(question, arts)
                      else 'in_progress', stale_code=stale)

    value, flags, reason = g.body.get('value'), tuple(g.body.get('flags') or ()), g.body.get('reason')
    if not grade_current(question, part, arts):
        return Status('outdated', value, flags, reason, stale_code=stale)
    wait = waiting(question, arts)
    if wait:
        return Status('unverified', value, flags, reason, waiting=wait, stale_code=stale)
    if value is None:
        return Status('needs_review', None, flags, reason, stale_code=stale)
    return Status('graded', value, flags, reason, stale_code=stale)


def question_status(question: Question, arts: Mapping[str, Artifact],
                    *, code_hash: str = '') -> dict[str, Status]:
    return {part: part_status(question, part, arts, code_hash=code_hash)
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
