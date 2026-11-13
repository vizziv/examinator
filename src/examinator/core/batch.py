"""Batch reading: always planned and previewed first.

`plan` lists exactly which questions will be read, what is skipped as
protected or already present, and what is blocked. Its `token` covers the
selection, the values and the items themselves (so a region drawn or exam data
changed since the preview counts too); `BatchRun.run` refuses a plan whose
token no longer matches (`BatchRun.check`, which a caller may run first). A batch has no check step:
checks recompute by themselves (`Runner.refresh`), and once every read has
finished the batch refreshes each selected submission, so a check that reads
another question's fields finds them. Cancelling stops queued work and lets
running tasks finish.

A batch never replaces what a person set or verified unless told to
(`include_protected`); it never touches a decision.
"""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .defs import Exam, key
from .runner import Outcome, Runner
from .status import Sheet
from .store import Store

ACTIONS = ('run', 'skip_protected', 'skip_present', 'blocked')


class StalePlan(Exception):
    """Something relevant changed since the preview."""


@dataclass(frozen=True)
class Selection:
    submissions: tuple[str, ...]
    questions: tuple[str, ...]
    missing_only: bool = False        # read only fields with no value
    include_protected: bool = False   # read again what a person set or verified (kept in history)


@dataclass(frozen=True)
class Item:
    """Reading one question of one submission."""
    submission: str
    question: str
    action: str
    reason: str = ""


@dataclass(frozen=True)
class Plan:
    selection: Selection
    items: tuple[Item, ...]
    token: str

    def count(self, action: str) -> int:
        return sum(1 for i in self.items if i.action == action)


def plan(exam: Exam, store: Store, selection: Selection, *, model_available: bool,
         regions=None) -> Plan:
    """`regions(submission, question) -> fields with this submission's regions`
    (`Workspace.fields`); without it, the exam's regions."""
    items: list[Item] = []
    seen: list = [[selection.submissions, selection.questions,
                   selection.missing_only, selection.include_protected, model_available]]
    for sub in selection.submissions:
        sheet = Sheet.of(store.all(submission=sub, kinds=('value',)))
        seen.append([sub, sorted((k, v.rev, v.settled) for k, v in sheet.values.items())])
        for qid in selection.questions:
            q = exam.questions[qid]

            def add(action, reason=""):
                items.append(Item(sub, qid, action, reason))

            fields = regions(sub, q) if regions is not None else q.fields
            readable = [f for f in fields if q.reader_of(f.label) is not None]
            if readable:
                vals = [sheet.values.get(key(qid, f.label)) for f in readable]
                todo = [f for f, v in zip(readable, vals)
                        if not (selection.missing_only and v is not None)
                        and not (v is not None and v.settled == 'person' and not selection.include_protected)]
                placed = [f for f in todo if f.region is not None
                          or not getattr(q.reader_of(f.label), 'needs_region', False)]
                if not todo:
                    protected = any(v is not None and v.settled == 'person' for v in vals)
                    add('skip_protected' if protected and not selection.missing_only else 'skip_present')
                elif not placed:
                    add('blocked', "no region")
                elif not model_available and any(getattr(q.reader_of(f.label), 'uses_model', True)
                                                 for f in placed):
                    add('blocked', "model unavailable")
                else:
                    add('run', f"{len(placed)} field{'s' if len(placed) != 1 else ''}")
    seen.append([[i.submission, i.question, i.action, i.reason] for i in items])
    token = hashlib.sha256(json.dumps(seen, sort_keys=True).encode()).hexdigest()[:16]
    return Plan(selection, tuple(items), token)


@dataclass
class BatchRun:
    runner: Runner
    plan: Plan
    parallel: int = 2
    reasoning: bool = False
    outcomes: list[Outcome] = field(default_factory=list)
    total: int = 0                   # reads, plus one refresh per submission
    done: int = 0
    state: str = 'ready'             # 'ready' | 'running' | 'done' | 'cancelled'
    current: str = ''                # in progress: 'reading', then 'recomputing'

    def __post_init__(self):
        self.total = self.plan.count('run') + len(self.plan.selection.submissions)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._checked = False

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        """Raise `StalePlan` if planning again would give another plan. `run` checks
        unless this was called just before (as the app does, to answer at once)."""
        r = self.runner
        fresh = plan(r.exam, r.store, self.plan.selection, model_available=r.model is not None,
                     regions=r.ws.fields)
        if fresh.token != self.plan.token:
            raise StalePlan("the preview is out of date")
        self._checked = True

    def run(self) -> list[Outcome]:
        """Execute the plan. Blocks until finished or cancelled."""
        if not self._checked:
            self.check()
        self._checked = False
        self.state = 'running'
        sel = self.plan.selection

        def read(item):
            if self._cancel.is_set():
                return None
            outs = self.runner.read(item.submission, item.question, missing_only=sel.missing_only,
                                    include_protected=sel.include_protected, reasoning=self.reasoning)
            bad = [o for o in outs if not o.ok]
            return [bad[0] if bad else Outcome(item.submission, item.question, 'read', 'done')]

        def refresh(sub):
            return None if self._cancel.is_set() else self.runner.refresh(sub, questions=sel.questions)

        with ThreadPoolExecutor(max_workers=max(1, self.parallel)) as pool:
            for step, fn, todo in (('reading', read, [i for i in self.plan.items if i.action == 'run']),
                                   ('recomputing', refresh, list(sel.submissions))):
                self.current = step
                for outs in pool.map(fn, todo):
                    if outs is None:
                        continue
                    with self._lock:
                        self.outcomes.extend(outs)
                        self.done += 1
                if self._cancel.is_set():
                    break
        self.current = ''
        self.state = 'cancelled' if self._cancel.is_set() else 'done'
        return self.outcomes
