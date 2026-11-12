"""The grid's read model: every submission's status for every check.

One query, no page rendering and no per-row lookups: every value, result
(without its display blocks) and decision in the workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .defs import Exam
from .status import Sheet, Status, fields_state, total
from .store import Store, Submission


@dataclass(frozen=True)
class Row:
    submission: Submission
    sheet: Sheet
    status: Mapping[tuple[str, str], Status]     # (question, check) -> status
    fields: Mapping[str, str]                    # question with no checks -> how its fields stand
    points: float
    complete: bool


def sheets(store: Store, submission: str | None = None) -> dict[str, Sheet]:
    rows: dict[str, list] = {}
    for r in store.all(kinds=('value', 'result', 'decision'), drop='$.blocks', submission=submission):
        rows.setdefault(r.submission, []).append(r)
    return {s: Sheet.of(rs) for s, rs in rows.items()}


def row(exam: Exam, sub: Submission, sheet: Sheet) -> Row:
    status = sheet.statuses(exam)
    fields = {qid: fields_state(q, sheet.values) for qid, q in exam.questions.items() if not q.checks}
    points, complete = total(exam, status)
    return Row(sub, sheet, status, fields, points, complete)


def load(exam: Exam, store: Store) -> list[Row]:
    every = sheets(store)
    empty = Sheet({}, {}, {})
    return [row(exam, sub, every.get(sub.id, empty)) for sub in store.submissions()]
