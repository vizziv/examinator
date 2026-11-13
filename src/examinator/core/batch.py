"""Batch reading.

`plan` lists exactly which questions a selection would read, what it skips as
protected or already present, and what is blocked: per question, what
`runner.to_read` says, the computation each read then executes. The app shows
it live as the options change, and a run plans again when it starts. Only
questions graders work through are selected (`Exam.questions`).

**The batch is its reads.** Its progress counts reads, so it matches the plan,
and cancelling stops the reads not yet started (running ones finish). Checks
recompute by themselves (`Runner.refresh`): once the reads stop, finished or
cancelled, every submission something was read for is refreshed, all its
fields in place, so a check that reads another question's fields finds them.
That is not part of what is counted or cancelled.

A batch never replaces what a person set or verified unless told to
(`include_protected`), and a read that finds a value changed meanwhile is
discarded (`Runner.read`); it never touches a decision.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .runner import PROTECTED, Outcome, Runner, to_read
from .status import Sheet
from .workspace import Workspace

ACTIONS = ('read', 'skip_protected', 'skip_present', 'blocked')


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
    fields: int = 0         # how many fields it reads


@dataclass(frozen=True)
class Plan:
    selection: Selection
    items: tuple[Item, ...]

    def count(self, action: str) -> int:
        return sum(1 for i in self.items if i.action == action)


def plan(ws: Workspace, selection: Selection, *, model_available: bool) -> Plan:
    """What reading `selection` in this workspace would do now: per question with a
    field that has a reader, what `runner.to_read` says. A question that reads a
    field is 'read' (it may also have fields blocked: those are not counted);
    one that reads none is 'blocked' if a field asked for cannot be read now,
    else skipped ('skip_protected' or 'skip_present')."""
    exam, store = ws.exam, ws.store
    for qid in selection.questions:
        if qid not in exam.questions:
            raise KeyError(qid)
    items: list[Item] = []
    for sub in selection.submissions:
        values = Sheet.of(store.all(submission=sub, kinds=('value',))).values
        gone, lay = ws.missing(sub), ws.layers(sub)
        for qid in selection.questions:
            r = to_read(lay.question(qid), values, gone, missing_only=selection.missing_only,
                        include_protected=selection.include_protected, model=model_available)
            if r.count:
                items.append(Item(sub, qid, 'read', f"{r.count} field{'s' if r.count != 1 else ''}", r.count))
            elif r.blocked:
                items.append(Item(sub, qid, 'blocked', ", ".join(sorted(set(r.blocked.values())))))
            elif r.why:
                items.append(Item(sub, qid, 'skip_protected' if PROTECTED in r.why.values() else 'skip_present'))
    return Plan(selection, tuple(items))


@dataclass
class BatchRun:
    runner: Runner
    plan: Plan
    parallel: int = 2
    outcomes: list[Outcome] = field(default_factory=list)
    total: int = 0                   # reads
    done: int = 0                    # reads finished
    asked: int = 0                   # fields the finished reads asked for ...
    got: int = 0                     # ... and those that got a value (the rest stay not read)
    state: str = 'ready'             # 'ready' | 'running' | 'done' | 'cancelled'
    current: str = ''                # in progress: 'reading', then 'recomputing'

    def __post_init__(self):
        self.total = self.plan.count('read')
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> list[Outcome]:
        """Execute the plan. Blocks until the reads are finished or cancelled and the
        submissions read for are refreshed."""
        self.state = 'running'
        sel = self.plan.selection
        read_for: set[str] = set()

        def read(item):
            if self._cancel.is_set():
                return
            outs = self.runner.read(item.submission, item.question, missing_only=sel.missing_only,
                                    include_protected=sel.include_protected)
            bad = [o for o in outs if not o.ok]
            with self._lock:
                self.asked += sum(o.asked for o in outs)
                self.got += sum(o.read for o in outs)
                read_for.add(item.submission)
                short = [o for o in outs if o.read < o.asked]       # done, with fields left not read
                self.outcomes.append(bad[0] if bad else short[0] if short else
                                     Outcome(item.submission, item.question, 'read', 'done'))
                self.done += 1

        workers = max(1, self.parallel)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            self.current = 'reading'
            list(pool.map(read, [i for i in self.plan.items if i.action == 'read']))
            self.current = 'recomputing'
            for outs in pool.map(self.runner.refresh, [s for s in sel.submissions if s in read_for]):
                self.outcomes.extend(outs)
        self.current = ''
        self.state = 'cancelled' if self._cancel.is_set() else 'done'
        return self.outcomes
