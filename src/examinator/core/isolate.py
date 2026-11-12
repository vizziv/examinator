"""Isolated stage execution: warm worker processes with a wall-clock limit.

A worker imports the exam package once, then runs stage functions on request.
Each request carries the parent's applied definition overrides; the worker
applies them again whenever they differ from what it last applied.
The parent kills a worker that exceeds its time limit and starts another, so
a hung check costs one process, not the server. Requests and replies are
length-prefixed JSON on the worker's stdin and stdout.

Run as a worker: `python -m examinator.core.isolate /path/to/exam_package`.
"""
from __future__ import annotations

import json
import os
import select
import struct
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any


class StageTimeout(Exception):
    pass


class PoolClosed(Exception):
    """The app is shutting down; nothing was run."""


class StageCrash(Exception):
    """The stage raised, or its worker died. `detail` is a traceback, for graders only."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def _send(f, obj: Any) -> None:
    data = json.dumps(obj).encode()
    f.write(struct.pack('>I', len(data)) + data)
    f.flush()


def _recv_exact(fd: int, n: int, deadline: float | None) -> bytes:
    buf = b''
    while len(buf) < n:
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise StageTimeout()
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            raise EOFError()
        buf += chunk
    return buf


def _recv(fd: int, deadline: float | None) -> Any:
    (n,) = struct.unpack('>I', _recv_exact(fd, 4, deadline))
    return json.loads(_recv_exact(fd, n, deadline))


class Pool:
    """Warm workers for one exam package. Thread-safe; grows with demand."""

    def __init__(self, exam_path: str | Path):
        self.exam_path = str(exam_path)
        self._idle: list[subprocess.Popen] = []
        self._lock = threading.Lock()
        self._closed = False

    def _spawn(self) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, '-m', 'examinator.core.isolate', self.exam_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def call(self, question: str, stage: str, payload: dict, timeout: float,
             reads: dict | None = None) -> Any:
        """Run one stage function in a worker and return its body.

        `payload` holds the context fields: submission, inputs, params, note,
        parses (`Context.parses`, as lists), and the exam's applied `overrides`.
        What the stage read of other questions is added to `reads`, whatever
        the outcome. Raises `StageTimeout`, `StageCrash` or `Undecided`.
        """
        with self._lock:
            if self._closed:
                raise PoolClosed()
            proc = self._idle.pop() if self._idle else None
        proc = proc or self._spawn()
        try:
            _send(proc.stdin, {'question': question, 'stage': stage, **payload})
            reply = _recv(proc.stdout.fileno(), time.monotonic() + timeout)
        except StageTimeout:
            proc.kill()
            proc.wait()
            raise
        except (EOFError, OSError):
            proc.kill()
            proc.wait()
            raise StageCrash("the check's process died")
        with self._lock:
            if self._closed:
                proc.kill()
            else:
                self._idle.append(proc)
        if reads is not None:
            reads.update(reply.get('reads') or {})
        if 'error' in reply:
            raise StageCrash(reply['error'], reply.get('detail', ""))
        if 'undecided' in reply:
            from .evidence import Undecided
            raise Undecided(reply['undecided'])
        return reply['body']

    def close(self) -> None:
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for p in idle:
            p.kill()
            p.wait()


def _worker(exam_path: str) -> None:
    from .context import Context
    from .defs import load_exam
    from .evidence import Undecided, body_of
    from .overrides import apply

    out = os.fdopen(os.dup(sys.stdout.fileno()), 'wb')
    sys.stdout = sys.stderr            # stray prints must not corrupt the protocol
    inp = sys.stdin.buffer.fileno()
    exam = load_exam(exam_path)
    applied: dict = {}
    while True:
        try:
            req = _recv(inp, None)
        except EOFError:
            return
        ctx = None
        try:
            if (req.get('overrides') or {}) != applied:
                applied = req.get('overrides') or {}
                apply(exam, applied)
            q = exam.questions[req['question']]
            parses = req.get('parses')
            ctx = Context(submission=req['submission'], question=q, stage=req['stage'],
                          inputs=req.get('inputs') or {}, params=req.get('params') or {},
                          note=req.get('note') or "",
                          parses=None if parses is None else {k: tuple(v) if v else None for k, v in parses.items()})
            reply = {'body': body_of(q.stage(req['stage']).fn(ctx))}
            json.dumps(reply)
        except Undecided as e:
            reply = {'undecided': str(e)}
        except Exception as e:
            reply = {'error': f"{type(e).__name__}: {e}", 'detail': traceback.format_exc()}
        reply['reads'] = ctx.reads if ctx is not None else {}
        _send(out, reply)


if __name__ == '__main__':
    _worker(sys.argv[1])
