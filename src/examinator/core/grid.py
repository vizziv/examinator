"""The grid's read model: every submission's status for every check.

One query, no page rendering and no per-row lookups: every value, result
(without its display blocks) and decision in the workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .defs import Exam
from .status import Ledger, Status, fields_state, total
from .store import Store, Submission


@dataclass(frozen=True)
class Row:
    submission: Submission
    ledger: Ledger
    status: Mapping[tuple[str, str], Status]     # (question, check) -> status
    fields: Mapping[str, str]                    # question with no checks -> how its fields stand
    points: float
    complete: bool


def row(exam: Exam, sub: Submission, ledger: Ledger) -> Row:
    status = ledger.statuses(exam)
    fields = {qid: fields_state(q, ledger.values) for qid, q in exam.questions.items() if not q.checks}
    points, complete = total(exam, status)
    return Row(sub, ledger, status, fields, points, complete)


def load(exam: Exam, store: Store) -> list[Row]:
    every = Ledger.load(store)
    return [row(exam, sub, every.get(sub.id) or Ledger()) for sub in store.submissions()]
