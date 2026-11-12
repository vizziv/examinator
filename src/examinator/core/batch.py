"""Batch runs: reads, then computes; always planned and previewed first.

`plan` lists exactly what will run, what is skipped as protected or already
present, and what is blocked. `BatchRun.run` refuses a plan whose `token` no
longer matches the store. Every selected read finishes before any check runs,
so a check that reads another question's fields finds them. A failed read
stops that question's checks for that submission only. Cancelling stops queued
work and lets running tasks finish.

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
from .status import Sheet, current
from .store import Store

ACTIONS = ('run', 'skip_protected', 'skip_present', 'blocked')
STEPS = ('read', 'check')


class StalePlan(Exception):
    """Something relevant changed since the preview."""


@dataclass(frozen=True)
class Selection:
    submissions: tuple[str, ...]
    questions: tuple[str, ...]
    steps: tuple[str, ...] = STEPS
    missing_only: bool = False        # read only fields with no value; run only checks not current
    include_protected: bool = False   # read again what a person set or verified (kept in history)


@dataclass(frozen=True)
class Item:
    submission: str
    question: str
    step: str                         # 'read', or a check's name
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
    seen: list = [[selection.submissions, selection.questions, selection.steps,
                   selection.missing_only, selection.include_protected, model_available]]
    for sub in selection.submissions:
        sheet = Sheet.of(store.all(submission=sub, kinds=('value', 'result'), drop='$.blocks'))
        seen.append([sub, sorted((k, v.rev, v.settled) for k, v in sheet.values.items()),
                     sorted((k, r.rev) for k, r in sheet.results.items())])
        for qid in selection.questions:
            q = exam.questions[qid]

            def add(step, action, reason=""):
                items.append(Item(sub, qid, step, action, reason))

            if 'read' in selection.steps:
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
                        add('read', 'skip_protected' if protected and not selection.missing_only else 'skip_present')
                    elif not placed:
                        add('read', 'blocked', "no region")
                    elif not model_available and any(getattr(q.reader_of(f.label), 'uses_model', True)
                                                     for f in placed):
                        add('read', 'blocked', "model unavailable")
                    else:
                        add('read', 'run', f"{len(placed)} field{'s' if len(placed) != 1 else ''}")
            if 'check' in selection.steps:
                for c in q.checks:
                    if c.fn is None:
                        continue
                    stored = sheet.results.get(key(qid, c.name))
                    if selection.missing_only and stored is not None and current(
                            stored, sheet.values, defn=exam.check_hash(q, c), code=exam.code_hash):
                        add(c.name, 'skip_present')
                    else:
                        add(c.name, 'run')
    token = hashlib.sha256(json.dumps(seen, sort_keys=True).encode()).hexdigest()[:16]
    return Plan(selection, tuple(items), token)


@dataclass
class BatchRun:
    runner: Runner
    plan: Plan
    parallel: int = 2
    reasoning: bool = False
    outcomes: list[Outcome] = field(default_factory=list)
    total: int = 0
    state: str = 'ready'             # 'ready' | 'running' | 'done' | 'cancelled'
    current: str = ''                # the step in progress: 'read' or 'check'

    def __post_init__(self):
        self.total = self.plan.count('run')
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    @property
    def done(self) -> int:
        return len(self.outcomes)

    def cancel(self) -> None:
        self._cancel.set()

    def fresh(self) -> Plan:
        r = self.runner
        return plan(r.exam, r.store, self.plan.selection, model_available=r.model is not None,
                    regions=r.ws.fields)

    def run(self) -> list[Outcome]:
        """Execute the plan. Blocks until finished or cancelled."""
        if self.fresh().token != self.plan.token:
            raise StalePlan("the preview is out of date")
        self.state = 'running'
        sel = self.plan.selection
        todo = [i for i in self.plan.items if i.action == 'run']
        stopped: set[tuple[str, str]] = set()

        def task(item):
            if self._cancel.is_set() or (item.submission, item.question) in stopped:
                return None
            if item.step == 'read':
                outs = self.runner.read(item.submission, item.question, missing_only=sel.missing_only,
                                        include_protected=sel.include_protected, reasoning=self.reasoning)
                bad = [o for o in outs if not o.ok]
                return bad[0] if bad else Outcome(item.submission, item.question, 'read', 'done')
            return self.runner.compute(item.submission, item.question, item.step)

        with ThreadPoolExecutor(max_workers=max(1, self.parallel)) as pool:
            for step in ('read', 'check'):
                batch = [i for i in todo if (i.step == 'read') == (step == 'read')]
                if not batch:
                    continue
                self.current = step
                for item, out in zip(batch, pool.map(task, batch)):
                    if out is None:
                        continue
                    with self._lock:
                        self.outcomes.append(out)
                    if not out.ok and item.step == 'read' and out.status in ('failed', 'blocked'):
                        stopped.add((item.submission, item.question))
                if self._cancel.is_set():
                    break
        self.current = ''
        self.state = 'cancelled' if self._cancel.is_set() else 'done'
        return self.outcomes
