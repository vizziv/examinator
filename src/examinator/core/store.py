"""The SQLite store: one file per workspace, keyed rows.

Every normal read is a keyed lookup on `artifact`. Every write carries the
`rev` the writer last saw; a mismatch raises `Conflict` and nothing changes.
Revisions come from one workspace-wide counter, so a row that is deleted and
recreated never reuses a revision.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA = """
create table if not exists artifact (
  submission   text not null,
  question     text not null,
  kind         text not null,
  body         text,
  origin       text not null,
  author       text,
  updated_at   text not null,
  rev          integer not null,
  inputs       text,
  verified_rev integer,
  verified_by  text,
  primary key (submission, question, kind)
);
create table if not exists history (
  submission text not null, question text not null, kind text not null,
  body text, origin text not null, author text, updated_at text not null,
  rev integer not null, inputs text, verified_rev integer, verified_by text,
  replaced_at text not null
);
create index if not exists history_key on history (submission, question, kind, rev);
create table if not exists submission (id text primary key, name text, email text, pdf text);
create table if not exists setting (key text primary key, value text not null);
create table if not exists memo (
  submission text not null, key text not null, value text not null,
  primary key (submission, key)
);
"""

COLUMNS = ('submission, question, kind, body, origin, author, updated_at, rev, '
           'inputs, verified_rev, verified_by')


class Conflict(Exception):
    """A write was rejected because something it depended on has changed."""


class _Any:
    def __repr__(self):
        return 'ANY'


ANY = _Any()   # `expect_rev=ANY`: write whatever is there


@dataclass(frozen=True)
class Artifact:
    submission: str
    question: str
    kind: str
    body: Any
    origin: str                 # 'auto' | 'manual'
    author: str | None
    updated_at: str
    rev: int
    inputs: Mapping[str, Any]   # {'revs': {kind: rev}, 'defn': hash, 'code': hash}
    verified_rev: int | None
    verified_by: str | None

    @property
    def verified(self) -> bool:
        return self.verified_rev == self.rev

    @property
    def protected(self) -> bool:
        """A grader wrote, edited or verified this content."""
        return self.origin == 'manual' or self.verified

    @property
    def revs(self) -> Mapping[str, int | None]:
        return self.inputs.get('revs') or {}


@dataclass(frozen=True)
class Submission:
    id: str
    name: str
    email: str
    pdf: str


def _row(r) -> Artifact:
    return Artifact(r[0], r[1], r[2], json.loads(r[3]) if r[3] is not None else None,
                    r[4], r[5], r[6], r[7], json.loads(r[8]) if r[8] else {}, r[9], r[10])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        new = not self.path.exists()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if new:
            os.chmod(self.path, 0o600)
        self._lock = threading.RLock()
        self._db.execute('pragma journal_mode = wal')
        self._db.execute('pragma busy_timeout = 5000')
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- reading ---

    def get(self, submission: str, question: str, kind: str) -> Artifact | None:
        with self._lock:
            r = self._db.execute(
                f'select {COLUMNS} from artifact where submission=? and question=? and kind=?',
                (submission, question, kind)).fetchone()
        return _row(r) if r else None

    def for_question(self, submission: str, question: str) -> dict[str, Artifact]:
        with self._lock:
            rows = self._db.execute(
                f'select {COLUMNS} from artifact where submission=? and question=?',
                (submission, question)).fetchall()
        return {r[2]: _row(r) for r in rows}

    def all(self, *, kinds: Iterable[str] | None = None, bodies: bool = True) -> list[Artifact]:
        """Every artifact, in one query (the grid). `bodies=False` leaves bodies `None`."""
        cols = COLUMNS if bodies else COLUMNS.replace('body', 'null')
        sql, args = f'select {cols} from artifact', ()
        if kinds is not None:
            kinds = tuple(kinds)
            sql += f" where kind in ({','.join('?' * len(kinds))})"
            args = kinds
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [_row(r) for r in rows]

    def history(self, submission: str, question: str, kind: str) -> list[Artifact]:
        """Replaced versions, newest first."""
        with self._lock:
            rows = self._db.execute(
                f'select {COLUMNS} from history where submission=? and question=? and kind=? '
                'order by rev desc', (submission, question, kind)).fetchall()
        return [_row(r) for r in rows]

    # --- writing ---

    def _next_rev(self) -> int:
        r = self._db.execute("select value from setting where key='rev'").fetchone()
        rev = (json.loads(r[0]) if r else 0) + 1
        self._db.execute("insert or replace into setting values ('rev', ?)", (json.dumps(rev),))
        return rev

    def _check(self, submission, question, kind, expect_rev, guard) -> Artifact | None:
        cur = self.get(submission, question, kind)
        if expect_rev is not ANY and (cur.rev if cur else None) != expect_rev:
            raise Conflict(f"{kind} changed")
        for k, rev in (guard or {}).items():
            g = self.get(submission, question, k)
            if (g.rev if g else None) != rev:
                raise Conflict(f"{k} changed")
        return cur

    def _archive(self, cur: Artifact) -> None:
        self._db.execute(
            f'insert into history select {COLUMNS}, ? from artifact '
            'where submission=? and question=? and kind=?',
            (_now(), cur.submission, cur.question, cur.kind))

    def write(self, submission: str, question: str, kind: str, body: Any, *,
              origin: str, author: str | None = None, inputs: Mapping | None = None,
              expect_rev: int | None | _Any = ANY, guard: Mapping[str, int | None] | None = None,
              verified: bool = False) -> Artifact:
        """Create or replace one artifact; the replaced row goes to history.

        `expect_rev` is the revision the writer last saw (`None`: must not
        exist). `guard` maps other kinds of the same submission and question to
        the revisions a computation read; the write happens only if all are
        still current. Saving unchanged content by hand is a no-op.
        """
        assert origin in ('auto', 'manual')
        text = json.dumps(body, ensure_ascii=False)
        with self._lock, self._tx():
            cur = self._check(submission, question, kind, expect_rev, guard)
            if (cur is not None and origin == 'manual' and inputs is None
                    and json.dumps(cur.body, ensure_ascii=False) == text):
                if verified and not cur.verified:
                    self._db.execute(
                        'update artifact set verified_rev=rev, verified_by=? '
                        'where submission=? and question=? and kind=?',
                        (author, submission, question, kind))
                return self.get(submission, question, kind)
            if cur is not None:
                self._archive(cur)
            rev = self._next_rev()
            self._db.execute(
                f'insert or replace into artifact ({COLUMNS}) values (?,?,?,?,?,?,?,?,?,?,?)',
                (submission, question, kind, text, origin, author, _now(), rev,
                 json.dumps(inputs) if inputs else None,
                 rev if verified else None, author if verified else None))
            return self.get(submission, question, kind)

    def verify(self, submission: str, question: str, kind: str, *, expect_rev: int,
               author: str | None, verified: bool = True) -> Artifact:
        """Approve (or un-approve) the current content. Does not change who wrote it."""
        with self._lock, self._tx():
            self._check(submission, question, kind, expect_rev, None)
            self._db.execute(
                'update artifact set verified_rev=?, verified_by=? '
                'where submission=? and question=? and kind=?',
                (expect_rev if verified else None, author if verified else None,
                 submission, question, kind))
            return self.get(submission, question, kind)

    def delete(self, submission: str, question: str, kind: str, *,
               expect_rev: int | _Any = ANY) -> None:
        with self._lock, self._tx():
            cur = self._check(submission, question, kind, expect_rev, None)
            if cur is not None:
                self._archive(cur)
                self._db.execute('delete from artifact where submission=? and question=? and kind=?',
                                 (submission, question, kind))

    def restore(self, submission: str, question: str, kind: str, rev: int, *,
                author: str | None) -> Artifact:
        """Undo: bring a replaced version back as a new manual write."""
        old = next((a for a in self.history(submission, question, kind) if a.rev == rev), None)
        if old is None:
            raise KeyError(rev)
        return self.write(submission, question, kind, old.body, origin='manual', author=author,
                          inputs=dict(old.inputs) or None)

    def purge_history(self) -> None:
        with self._lock:
            self._db.execute('delete from history')

    def _tx(self):
        return _Tx(self._db)

    # --- submissions, settings, memo ---

    def put_submissions(self, subs: Iterable[Submission]) -> None:
        with self._lock, self._tx():
            for s in subs:
                self._db.execute('insert or replace into submission values (?,?,?,?)',
                                 (s.id, s.name, s.email, s.pdf))

    def submissions(self) -> list[Submission]:
        with self._lock:
            rows = self._db.execute('select id, name, email, pdf from submission order by id').fetchall()
        return [Submission(*r) for r in rows]

    def submission(self, id: str) -> Submission | None:
        with self._lock:
            r = self._db.execute('select id, name, email, pdf from submission where id=?', (id,)).fetchone()
        return Submission(*r) if r else None

    def setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            r = self._db.execute('select value from setting where key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_setting(self, key: str, value: Any) -> None:
        assert key != 'rev'
        with self._lock:
            self._db.execute('insert or replace into setting values (?, ?)', (key, json.dumps(value)))

    def memo(self, submission: str, key: str) -> str | None:
        with self._lock:
            r = self._db.execute('select value from memo where submission=? and key=?',
                                 (submission, key)).fetchone()
        return r[0] if r else None

    def set_memo(self, submission: str, key: str, value: str) -> None:
        with self._lock:
            self._db.execute('insert or replace into memo values (?,?,?)', (submission, key, value))


class _Tx:
    """`begin immediate` ... `commit`, rolling back on error. Re-entrant: inner uses join the outer."""

    def __init__(self, db):
        self.db = db
        self.outer = False

    def __enter__(self):
        self.outer = not self.db.in_transaction
        if self.outer:
            self.db.execute('begin immediate')

    def __exit__(self, exc_type, *_):
        if self.outer:
            self.db.execute('rollback' if exc_type else 'commit')
        return False
