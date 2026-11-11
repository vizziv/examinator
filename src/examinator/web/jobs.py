"""Background work, so that no request waits for a check or a batch."""
from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..core.batch import BatchRun, StalePlan

KEEP = 300   # finished jobs remembered


def _outcomes(result: Any) -> list[dict]:
    if result is None:
        return []
    items = result if isinstance(result, (list, tuple)) else [result]
    return [{'stage': o.stage, 'status': o.status, 'detail': o.detail} for o in items]


class Jobs:
    """Single runs and rechecks: `start(fn)` returns an id to poll.

    A job is `{'state': 'running' | 'done' | 'failed', 'outcomes': [...]}`;
    `fn` returns an `Outcome`, a list of them, or None.
    """

    def __init__(self, workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='examinator-job')
        self._lock = threading.Lock()
        self._jobs: OrderedDict[str, dict] = OrderedDict()

    def start(self, fn: Callable[[], Any]) -> str:
        id = secrets.token_hex(8)
        with self._lock:
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

    def start_each(self, items: list, fn: Callable[..., Any]) -> str:
        """One job over many items: `fn(*item)` for each, in order, with progress.

        The job adds `done` and `total`; `outcomes` keeps only what did not finish
        cleanly (with its submission), so a long loop stays small."""
        id = secrets.token_hex(8)
        items = list(items)
        with self._lock:
            self._jobs[id] = {'state': 'running', 'outcomes': [], 'done': 0, 'total': len(items)}

        def work():
            bad, failed = [], 0
            for i, item in enumerate(items):
                try:
                    out = _outcomes(fn(*item))
                    bad += [{**o, 'submission': item[0]} for o in out if o['status'] != 'done']
                except Exception as e:      # the type only, as above
                    failed += 1
                    bad.append({'stage': '', 'status': 'failed', 'detail': type(e).__name__,
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
        self.run = run
        self.error = ''
        self._thread = threading.Thread(target=self._work, name='examinator-batch', daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _work(self) -> None:
        try:
            self.run.run()
        except StalePlan:
            self.error = 'stale'
        except Exception as e:
            self.error = type(e).__name__

    @property
    def active(self) -> bool:
        return self._thread.is_alive()

    def info(self) -> dict:
        run = self.run
        outcomes = list(run.outcomes)
        state = 'running' if self.active else 'stale' if self.error == 'stale' else \
            'failed' if self.error else run.state
        failures = [o for o in outcomes if not o.ok]
        return {'state': state, 'done': len(outcomes), 'total': run.total, 'current': run.current,
                'error': self.error if state == 'failed' else '',
                'failed': len(failures),
                'failures': [{'submission': o.submission, 'question': o.question, 'stage': o.stage,
                              'status': o.status, 'detail': o.detail} for o in failures[:500]]}
