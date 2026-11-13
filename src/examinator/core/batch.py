"""Batch reading.

`plan` lists exactly which questions a selection would read, what it skips as
protected or already present, and what is blocked; the app shows it live as
the options change, and a run plans again when it starts. A batch has no
check step: checks recompute by themselves (`Runner.refresh`), and once every
read has finished the batch refreshes each selected submission, so a check
that reads another question's fields finds them. Cancelling stops queued work
and lets running tasks finish.

A batch never replaces what a person set or verified unless told to
(`include_protected`), and a read that finds a value changed meanwhile is
discarded (`Runner.read`); it never touches a decision.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .defs import key
from .runner import Outcome, Runner, unreadable
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


@dataclass(frozen=True)
class Plan:
    selection: Selection
    items: tuple[Item, ...]

    def count(self, action: str) -> int:
        return sum(1 for i in self.items if i.action == action)


def plan(ws: Workspace, selection: Selection, *, model_available: bool) -> Plan:
    """What reading `selection` in this workspace would do now. A field its reader
    cannot read (`runner.unreadable`: no region, a page missing) is not counted."""
    exam, store = ws.exam, ws.store
    items: list[Item] = []
    for sub in selection.submissions:
        sheet = Sheet.of(store.all(submission=sub, kinds=('value',)))
        gone = ws.missing(sub)
        for qid in selection.questions:
            q = exam.questions[qid]

            def add(action, reason=""):
                items.append(Item(sub, qid, action, reason))

            readable = [f for f in q.fields if q.reader_of(f.label) is not None]
            if readable:
                why = unreadable(ws, sub, q, gone)
                vals = [sheet.values.get(key(qid, f.label)) for f in readable]
                todo = [f for f, v in zip(readable, vals)
                        if not (selection.missing_only and v is not None)
                        and not (v is not None and v.verified == 'person' and not selection.include_protected)]
                placed = [f for f in todo if f.label not in why]
                if not todo:
                    protected = any(v is not None and v.verified == 'person' for v in vals)
                    add('skip_protected' if protected and not selection.missing_only else 'skip_present')
                elif not placed:
                    add('blocked', ", ".join(sorted({why[f.label] for f in todo})))
                elif not model_available and any(getattr(q.reader_of(f.label), 'uses_model', True)
                                                 for f in placed):
                    add('blocked', "model unavailable")
                else:
                    add('read', f"{len(placed)} field{'s' if len(placed) != 1 else ''}")
    return Plan(selection, tuple(items))


@dataclass
class BatchRun:
    runner: Runner
    plan: Plan
    parallel: int = 2
    outcomes: list[Outcome] = field(default_factory=list)
    total: int = 0                   # reads, plus one refresh per submission
    done: int = 0
    state: str = 'ready'             # 'ready' | 'running' | 'done' | 'cancelled'
    current: str = ''                # in progress: 'reading', then 'recomputing'

    def __post_init__(self):
        self.total = self.plan.count('read') + len(self.plan.selection.submissions)
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> list[Outcome]:
        """Execute the plan. Blocks until finished or cancelled."""
        self.state = 'running'
        sel = self.plan.selection

        def read(item):
            if self._cancel.is_set():
                return None
            outs = self.runner.read(item.submission, item.question, missing_only=sel.missing_only,
                                    include_protected=sel.include_protected)
            bad = [o for o in outs if not o.ok]
            return [bad[0] if bad else Outcome(item.submission, item.question, 'read', 'done')]

        def refresh(sub):
            return None if self._cancel.is_set() else self.runner.refresh(sub, questions=sel.questions)

        with ThreadPoolExecutor(max_workers=max(1, self.parallel)) as pool:
            for step, fn, todo in (('reading', read, [i for i in self.plan.items if i.action == 'read']),
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
