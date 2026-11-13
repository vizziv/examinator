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
    `fn` returns an `Outcome`, a list of them, or None.
    """

    def __init__(self, workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='examinator-job')
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._slots: dict[str, str] = {}

    def last(self, slot: str) -> str | None:
        """The last run started in `slot` (a kind of run on one subject): its revision."""
        with self._lock:
            return self._slots.get(slot)

    def _claim(self, slot: str | None, seen: Any, id: str) -> None:
        """A run is a change to its slot: refused unless the page saw the slot's last
        run (`seen`) and that run has finished. Called with the lock held."""
        if slot is None:
            return
        self._expect(slot, seen)
        self._slots[slot] = id

    def _expect(self, slot: str, seen: Any) -> None:
        last = self._slots.get(slot)
        running = last is not None and self._jobs.get(last, {}).get('state') == 'running'
        if last != seen or running:
            e = Conflict("already running" if running else "another run started")
            e.current = {'rev': last, 'running': running}
            raise e

    def expect(self, slot: str, seen: Any) -> None:
        """Refuse as a run would be refused, without starting one (a run with nothing to do)."""
        with self._lock:
            self._expect(slot, seen)

    def start(self, fn: Callable[[], Any], slot: str | None = None, seen: Any = None) -> str:
        id = secrets.token_hex(8)
        with self._lock:
            self._claim(slot, seen, id)
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

    def start_each(self, items: list, fn: Callable[..., Any], slot: str | None = None, seen: Any = None) -> str:
        """One job over many items: `fn(*item)` for each, in order, with progress.

        The job adds `done` and `total`; `outcomes` keeps only what did not finish
        cleanly (with its submission), so a long loop stays small."""
        id = secrets.token_hex(8)
        items = list(items)
        with self._lock:
            self._claim(slot, seen, id)
            self._jobs[id] = {'state': 'running', 'outcomes': [], 'done': 0, 'total': len(items)}

        def work():
            bad, failed = [], 0
            for i, item in enumerate(items):
                try:
                    out = _outcomes(fn(*item))
                    bad += [{**o, 'submission': item[0]} for o in out if o['status'] != 'done']
                except Exception as e:      # the type only, as above
                    failed += 1
                    bad.append({'step': '', 'status': 'failed', 'detail': type(e).__name__,
                                'submission': item[0]})
                with self._lock:
                    self._jobs[id] = {'state': 'running', 'outcomes': bad[:200], 'done': i + 1,
                                      'total': len(items)}
            with self._lock:
                self._jobs[id] = {'state': 'done', 'outcomes': bad[:200], 'done': len(items),
                                  'total': len(items), 'failed': failed}

        self._pool.submit(work)
        return id

    def get(self, id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(id)
            return dict(job) if job else None


class Batch:
    """One batch run in its own thread."""

    def __init__(self, run: BatchRun):
        self.id = secrets.token_hex(8)      # its revision: what Start and Stop name
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
