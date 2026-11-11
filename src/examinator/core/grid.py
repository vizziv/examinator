"""The grid's read model: every submission's status for every result part.

Two queries, no page rendering and no per-row lookups: revisions for all
artifacts, and bodies only for the small kinds that statuses read (grades and
variants).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Mapping

from .defs import Exam, grade_kind
from .store import Artifact, Store, Submission
from .variants import resolved
from .workflow import Status, question_status, total


@dataclass(frozen=True)
class Cell:
    status: Mapping[str, Status]      # part -> status
    variant: str | None               # resolved variant, if the question has variants
    flags: tuple[str, ...] = ()       # e.g. 'markers' is left to facets later


@dataclass(frozen=True)
class Row:
    submission: Submission
    cells: Mapping[str, Cell]         # question id -> cell
    points: float
    complete: bool


def load(exam: Exam, store: Store) -> list[Row]:
    small = {'variant'} | {grade_kind(p) for q in exam.questions.values() for p in q.result}
    arts: dict[tuple[str, str], dict[str, Artifact]] = {}
    for a in store.all(bodies=False):
        arts.setdefault((a.submission, a.question), {})[a.kind] = a
    for a in store.all(kinds=small):
        arts.setdefault((a.submission, a.question), {})[a.kind] = a
    rows = []
    for sub in store.submissions():
        cells, by_q = {}, {}
        for qid, q in exam.questions.items():
            mine = arts.get((sub.id, qid), {})
            by_q[qid] = question_status(q, mine, code_hash=exam.code_hash)
            v = mine.get('variant')
            cells[qid] = Cell(by_q[qid], resolved(v.body) if v else None)
        points, complete = total(exam, by_q)
        rows.append(Row(sub, cells, points, complete))
    return rows
