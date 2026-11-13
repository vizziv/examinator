"""Background work, so that no request waits for a check or a batch."""
from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..core.batch import BatchRun
from ..core.store import Conflict

KEEP = 300   # finished jobs remembered


def _outcomes(result: Any) -> list[dict]:
    if result is None:
        return []
    items = result if isinstance(result, (list, tuple)) else [result]
    return [{'step': o.step, 'status': o.status, 'detail': o.detail} for o in items]


class Jobs:
    """Single runs and rechecks: `start(fn)` returns an id to poll.

    A job is `{'state': 'running' | 'done' | 'failed', 'outcomes': [...]}`;
    `fn` returns an `Outcome`, a list of them, or None. A run may name a slot (a kind
    of run on one subject), which runs one thing at a time.
    """

    def __init__(self, workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='examinator-job')
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._slots: dict[str, str] = {}     # slot -> its last job
        self._next: dict[str, list] = {}     # slot -> the items a `latest` job goes on over

    def _claim(self, slot: str | None, id: str) -> None:
        """A slot runs one thing at a time: a second while one runs is refused. Called
        with the lock held."""
        if slot is None:
            return
        last = self._slots.get(slot)
        if last is not None and self._jobs.get(last, {}).get('state') == 'running':
            raise Conflict("already running")
        self._slots[slot] = id

    def start(self, fn: Callable[[], Any], slot: str | None = None) -> str:
        id = secrets.token_hex(8)
        with self._lock:
            self._claim(slot, id)
            self._jobs[id] = {'state': 'running', 'outcomes': []}
            for old in [k for k, v in self._jobs.items() if v['state'] != 'running'][:-KEEP]:
                del self._jobs[old]

        def work():
            try:
                done = {'state': 'done', 'outcomes': _outcomes(fn())}
            except Exception as e:      # the type only: messages might quote student text
                done = {'state': 'failed', 'outcomes': [], 'error': type(e).__name__}
            with self._lock:
                self._jobs[id] = done

        self._pool.submit(work)
        return id

    def start_each(self, items: list, fn: Callable[..., Any], slot: str | None = None, latest: bool = False) -> str:
        """One job over many items: `fn(*item)` for each, in order, with progress.

        The job adds `done` and `total`; `outcomes` keeps only what did not finish
        cleanly (with its submission), so a long loop stays small. With `latest`, a
        start while the slot's job runs is not refused: that job goes on over these
        items instead, from the first (one recompute of everything, never a queue of them)."""
        id = secrets.token_hex(8)
        items = list(items)
        with self._lock:
            last = self._slots.get(slot) if slot else None
            if latest and last is not None and self._jobs.get(last, {}).get('state') == 'running':
                self._next[slot] = items
                return last
            self._claim(slot, id)
            self._jobs[id] = {'state': 'running', 'outcomes': [], 'done': 0, 'total': len(items)}

        def work():
            todo, bad, failed, i = items, [], 0, 0
            while i < len(todo):
                item = todo[i]
                try:
                    out = _outcomes(fn(*item))
                    bad += [{**o, 'submission': item[0]} for o in out if o['status'] != 'done']
                except Exception as e:      # the type only, as above
                    failed += 1
                    bad.append({'step': '', 'status': 'failed', 'detail': type(e).__name__,
                                'submission': item[0]})
                i += 1
                with self._lock:
                    if slot in self._next:
                        todo, i = self._next.pop(slot), 0
                    self._jobs[id] = {'state': 'running' if i < len(todo) else 'done', 'outcomes': bad[:200],
                                      'done': i, 'total': len(todo), **({} if i < len(todo) else {'failed': failed})}
            with self._lock:
                if not todo:
                    self._jobs[id] = {'state': 'done', 'outcomes': [], 'done': 0, 'total': 0, 'failed': 0}

        self._pool.submit(work)
        return id

    def get(self, id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(id)
            return dict(job) if job else None


class Batch:
    """One batch run in its own thread."""

    def __init__(self, run: BatchRun):
        self.id = secrets.token_hex(8)
        self.run = run
        self.error = ''
        self._thread = threading.Thread(target=self._work, name='examinator-batch', daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _work(self) -> None:
        try:
            self.run.run()
        except Exception as e:
            self.error = type(e).__name__

    @property
    def active(self) -> bool:
        return self._thread.is_alive()

    def info(self) -> dict:
        run = self.run
        outcomes = list(run.outcomes)
        state = 'running' if self.active else 'failed' if self.error else run.state
        failures = [o for o in outcomes if not o.ok]
        return {'id': self.id, 'state': state, 'done': run.done, 'total': run.total, 'current': run.current,
                'error': self.error if state == 'failed' else '',
                'failed': len(failures),
                'failures': [{'submission': o.submission, 'question': o.question, 'step': o.step,
                              'status': o.status, 'detail': o.detail} for o in failures[:500]]}
