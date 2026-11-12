"""The grid's read model: every submission's status for every result part.

Two queries, no page rendering and no per-row lookups: revisions for all
artifacts, and bodies only for the small kinds that statuses read (grades and
variants).
"""
from __future__ import annotations

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


@dataclass(frozen=True)
class Row:
    submission: Submission
    cells: Mapping[str, Cell]         # question id -> cell
    points: float
    complete: bool


def load(exam: Exam, store: Store) -> list[Row]:
    small = {'variant'} | {grade_kind(p) for q in exam.questions.values() for p in q.result}
    arts: dict[tuple[str, str], dict[str, Artifact]] = {}
    bodies = {(a.submission, a.question, a.kind): a for a in store.all(kinds=small)}
    for a in store.all(bodies=False):
        if a.kind in small:      # the two queries are not one snapshot: keep only rows seen whole
            a = bodies.get((a.submission, a.question, a.kind))
            if a is None:
                continue
        arts.setdefault((a.submission, a.question), {})[a.kind] = a
    rows = []
    for sub in store.submissions():
        cells = row_cells(exam, {qid: arts.get((sub.id, qid), {}) for qid in exam.questions})
        points, complete = total(exam, {qid: c.status for qid, c in cells.items()})
        rows.append(Row(sub, cells, points, complete))
    return rows


def row_cells(exam: Exam, arts: Mapping[str, Mapping[str, Artifact]]) -> dict[str, Cell]:
    """One submission's cells from its artifacts by question (bodies needed only
    for grades and the variant)."""
    cells = {}
    for qid, q in exam.questions.items():
        mine = arts.get(qid, {})
        v = mine.get('variant')
        cells[qid] = Cell(question_status(q, mine, code_hash=exam.code_hash), resolved(v.body) if v else None)
    return cells
