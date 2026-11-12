"""Batch runs: always planned and previewed first.

`plan` lists exactly what will run, what is skipped as protected or already
present, and what is blocked by a missing input. `BatchRun.run` refuses a plan
whose `token` no longer matches the store. Each stage finishes across the whole
batch before the next begins; a failure stops later stages for that submission
and question only. Cancelling stops queued work and lets running tasks finish.
"""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .defs import Exam, grade_kind
from .runner import Outcome, Runner
from .store import Store

ACTIONS = ('run', 'skip_protected', 'skip_present', 'blocked')


class StalePlan(Exception):
    """Something relevant changed since the preview."""


@dataclass(frozen=True)
class Selection:
    submissions: tuple[str, ...]
    questions: tuple[str, ...]
    stages: tuple[str, ...]
    missing_only: bool = False        # fill gaps without touching anything that exists
    include_protected: bool = False   # overwrite what graders wrote or verified (kept in history);
                                      # manual grades too, but only if 'grade' is among the stages


@dataclass(frozen=True)
class Item:
    submission: str
    question: str
    stage: str
    action: str
    reason: str = ""


@dataclass(frozen=True)
class Plan:
    selection: Selection
    items: tuple[Item, ...]
    token: str

    def count(self, action: str) -> int:
        return sum(1 for i in self.items if i.action == action)


def stage_order(exam: Exam) -> list[str]:
    """Stage names in pipeline order across all questions: a name comes after every
    name that precedes it in some question's own list. Ties, and orders that
    contradict each other, fall back to first appearance. So every question's
    parse is written before any check runs, and a check that reads another
    question's parse finds it."""
    seen: list[str] = []
    before: dict[str, set[str]] = {}
    for q in exam.questions.values():
        names = [s.name for s in q.stages]
        for i, name in enumerate(names):
            if name not in seen:
                seen.append(name)
            before.setdefault(name, set()).update(names[:i])
    order: list[str] = []
    while len(order) < len(seen):
        left = [n for n in seen if n not in order]
        order.append(next((n for n in left if before[n] <= set(order)), left[0]))
    return order


def plan(exam: Exam, store: Store, selection: Selection, *, model_available: bool) -> Plan:
    items: list[Item] = []
    seen: list = [[selection.submissions, selection.questions, selection.stages,
                   selection.missing_only, selection.include_protected, model_available]]
    for sub in selection.submissions:
        for qid in selection.questions:
            q = exam.questions[qid]
            arts = store.for_question(sub, qid)
            seen.append([sub, qid, sorted((k, a.rev, a.verified_rev) for k, a in arts.items())])
            available = set(arts)        # kinds that exist or will have been produced
            scheduled: set[str] = set()
            for stage in q.stages:
                name = stage.name
                if name not in selection.stages:
                    continue
                if name == 'grade' and 'evidence' in scheduled:
                    continue             # the grade follows its check
                if name == 'grade':      # one row per result part
                    rows = [arts.get(grade_kind(part)) for part in q.result]
                    present = all(r is not None for r in rows)
                    protected = any(r is not None and r.origin == 'manual' for r in rows)
                else:
                    present = name in arts
                    protected = present and arts[name].protected

                def add(action, reason=""):
                    items.append(Item(sub, qid, name, action, reason))

                missing = [k for k in stage.inputs if k not in available]
                if present and selection.missing_only:
                    add('skip_present')
                elif protected and not selection.include_protected:
                    add('skip_protected')
                elif stage.uses_model and not model_available:
                    add('blocked', "model unavailable")
                elif missing:
                    add('blocked', f"no {missing[0]}")
                else:
                    add('run')
                    scheduled.add(name)
                    available.add(name)
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
    current: str = ''                # the stage in progress

    def __post_init__(self):
        self.total = self.plan.count('run')
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    @property
    def done(self) -> int:
        return len(self.outcomes)

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> list[Outcome]:
        """Execute the plan. Blocks until finished or cancelled."""
        exam, store = self.runner.exam, self.runner.store
        fresh = plan(exam, store, self.plan.selection, model_available=self.runner.model is not None)
        if fresh.token != self.plan.token:
            raise StalePlan("the preview is out of date")
        self.state = 'running'
        todo = [i for i in self.plan.items if i.action == 'run']
        stopped: set[tuple[str, str]] = set()
        overwrite = self.plan.selection.include_protected
        # Manual grades are replaced only when the grade stage itself was selected.
        regrade = overwrite and 'grade' in self.plan.selection.stages

        def task(item):
            if self._cancel.is_set():
                return None
            return self.runner.run_stage(item.submission, item.question, item.stage,
                                         overwrite=overwrite,
                                         deliberate=regrade and item.stage in ('evidence', 'grade'),
                                         reasoning=self.reasoning)

        with ThreadPoolExecutor(max_workers=max(1, self.parallel)) as pool:
            for name in stage_order(exam):
                batch = [i for i in todo if i.stage == name and (i.submission, i.question) not in stopped]
                if not batch:
                    continue
                self.current = name
                for item, out in zip(batch, pool.map(task, batch)):
                    if out is None:
                        continue
                    with self._lock:
                        self.outcomes.append(out)
                    if not out.ok:
                        stopped.add((item.submission, item.question))
                if self._cancel.is_set():
                    break
        self.current = ''
        self.state = 'cancelled' if self._cancel.is_set() else 'done'
        return self.outcomes
