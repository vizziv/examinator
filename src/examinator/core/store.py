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
from contextlib import contextmanager
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
create table if not exists submission (id text primary key, name text, email text, pdf text, student text);
create table if not exists setting (key text primary key, value text not null);
create table if not exists override (
  path text primary key, value text, author text, updated_at text not null, rev integer not null
);
create table if not exists override_history (
  path text not null, value text, author text, updated_at text not null, rev integer not null,
  replaced_at text not null
);
create table if not exists source (
  id text primary key, file text not null, name text, sha256 text not null unique,
  pages integer not null, added_at text not null
);
create table if not exists made (
  submission text primary key, source text not null, pages text not null, orient text, made_at text not null
);
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
    student: str = ''       # the student's ID, when a roster gives one


@dataclass(frozen=True)
class Override:
    """A value replacing one piece of an exam definition (see `core.overrides`)."""
    path: str
    value: Any                  # JSON
    author: str | None
    updated_at: str
    rev: int
    replaced_at: str | None = None   # set on history rows


@dataclass(frozen=True)
class Source:
    """A scanned PDF as added (many submissions), kept unchanged under `sources/`."""
    id: str
    file: str            # file name under sources/
    name: str            # the name it was added under (display only)
    sha256: str
    pages: int
    added_at: str


@dataclass(frozen=True)
class Made:
    """A submission PDF written from a source: which sheets, and how they were turned."""
    submission: str
    source: str
    pages: tuple[int, ...]
    orient: Mapping[str, Any]
    made_at: str


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

    def bodies(self, question: str, kind: str, path: str | None = None, *,
               submission: str | None = None) -> dict[str, Any]:
        """One kind's bodies for every submission of a question, in one query:
        `{submission: body}`. With a JSON `path` ('$.data.fields') only that part
        is read out of each body (SQLite's `->`), so large parts such as plot
        blocks are never loaded; rows where the path is absent or null are left out."""
        col = 'body' if path is None else 'body -> ?'
        sql = f'select submission, {col} from artifact where question=? and kind=?'
        args: tuple = ((path,) if path is not None else ()) + (question, kind)
        if submission is not None:
            sql += ' and submission=?'
            args += (submission,)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        out = {}
        for sub, text in rows:
            value = json.loads(text) if text is not None else None
            if value is not None:
                out[sub] = value
        return out

    def version(self) -> tuple:
        """Changes whenever an artifact or override is written, deleted, verified or
        unverified: a key for caching what is computed from stored rows."""
        with self._lock:
            return self._db.execute(
                "select (select value from setting where key='rev'), count(*), total(verified_rev), "
                "(select count(*) from override) from artifact").fetchone()

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
              verified: bool = False, unless_protected: bool = False) -> Artifact:
        """Create or replace one artifact; the replaced row goes to history.

        `expect_rev` is the revision the writer last saw (`None`: must not
        exist). `guard` maps other kinds of the same submission and question to
        the revisions a computation read; the write happens only if all are
        still current. With `unless_protected`, content a grader wrote or
        verified is never replaced, even if that happened a moment ago.
        Saving unchanged content by hand is a no-op.
        """
        assert origin in ('auto', 'manual')
        text = json.dumps(body, ensure_ascii=False)
        with self._lock, self._tx():
            cur = self._check(submission, question, kind, expect_rev, guard)
            if unless_protected and cur is not None and cur.protected:
                raise Conflict(f"{kind} is protected")
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
                expect_rev: int | None | _Any = ANY) -> Artifact:
        """Undo: bring a replaced version back as it was (same origin, author and
        inputs), under a new revision and unverified."""
        with self._lock, self._tx():
            old = next((a for a in self.history(submission, question, kind) if a.rev == rev), None)
            if old is None:
                raise KeyError(rev)
            cur = self._check(submission, question, kind, expect_rev, None)
            if cur is not None:
                self._archive(cur)
            new = self._next_rev()
            self._db.execute(
                f'insert or replace into artifact ({COLUMNS}) values (?,?,?,?,?,?,?,?,?,?,?)',
                (submission, question, kind, json.dumps(old.body, ensure_ascii=False), old.origin,
                 old.author, _now(), new, json.dumps(dict(old.inputs)) if old.inputs else None, None, None))
            return self.get(submission, question, kind)

    def purge_history(self) -> None:
        with self._lock:
            self._db.execute('delete from history')

    def _tx(self):
        return _Tx(self._db)

    # --- definition overrides ---

    def overrides(self) -> dict[str, Override]:
        with self._lock:
            rows = self._db.execute(
                'select path, value, author, updated_at, rev from override order by path').fetchall()
        return {r[0]: Override(r[0], json.loads(r[1]), *r[2:]) for r in rows}

    def override_history(self, path: str) -> list[Override]:
        """Replaced and cleared values of one path, newest first."""
        with self._lock:
            rows = self._db.execute(
                'select path, value, author, updated_at, rev, replaced_at from override_history '
                'where path=? order by rev desc', (path,)).fetchall()
        return [Override(r[0], json.loads(r[1]), *r[2:]) for r in rows]

    def _check_override(self, path: str, expect_rev) -> Override | None:
        cur = self.overrides().get(path)
        if expect_rev is not ANY and (cur.rev if cur else None) != expect_rev:
            raise Conflict(f"{path} changed")
        if cur is not None:
            self._db.execute(
                'insert into override_history select path, value, author, updated_at, rev, ? '
                'from override where path=?', (_now(), path))
        return cur

    def set_override(self, path: str, value: Any, *, author: str | None,
                     expect_rev: int | None | _Any = ANY) -> Override:
        """Create or replace one override (`expect_rev=None`: must not exist yet);
        the replaced row goes to history."""
        text = json.dumps(value, ensure_ascii=False)
        with self._lock, self._tx():
            self._check_override(path, expect_rev)
            self._db.execute('insert or replace into override values (?,?,?,?,?)',
                             (path, text, author, _now(), self._next_rev()))
            return self.overrides()[path]

    def clear_override(self, path: str, *, expect_rev: int | _Any = ANY) -> None:
        """Remove one override (back to the package's value); the row goes to history."""
        with self._lock, self._tx():
            if self._check_override(path, expect_rev) is not None:
                self._db.execute('delete from override where path=?', (path,))

    # --- submissions, settings, memo ---

    def put_submissions(self, subs: Iterable[Submission]) -> None:
        with self._lock, self._tx():
            for s in subs:
                self._db.execute('insert or replace into submission values (?,?,?,?,?)',
                                 (s.id, s.name, s.email, s.pdf, s.student))

    def submissions(self) -> list[Submission]:
        with self._lock:
            rows = self._db.execute('select id, name, email, pdf, student from submission order by id').fetchall()
        return [Submission(*r[:4], r[4] or '') for r in rows]

    def submission(self, id: str) -> Submission | None:
        with self._lock:
            r = self._db.execute('select id, name, email, pdf, student from submission where id=?',
                                 (id,)).fetchone()
        return Submission(*r[:4], r[4] or '') if r else None

    def delete_submission(self, id: str) -> None:
        with self._lock:
            self._db.execute('delete from submission where id=?', (id,))

    def has_work(self, submission: str, *, ignore: Iterable[str] = ()) -> bool:
        """Whether any artifact exists for a submission (kinds in `ignore` aside)."""
        ignore = tuple(ignore)
        sql = 'select 1 from artifact where submission=?'
        if ignore:
            sql += f" and kind not in ({','.join('?' * len(ignore))})"
        with self._lock:
            return self._db.execute(sql + ' limit 1', (submission, *ignore)).fetchone() is not None

    @contextmanager
    def transaction(self):
        """Several writes as one: all or none."""
        with self._lock, self._tx():
            yield self

    # --- intake: sources and the submissions made from them ---

    def add_source(self, src: Source) -> None:
        with self._lock:
            self._db.execute('insert into source values (?,?,?,?,?,?)',
                             (src.id, src.file, src.name, src.sha256, src.pages, src.added_at))

    def sources(self) -> list[Source]:
        with self._lock:
            rows = self._db.execute('select id, file, name, sha256, pages, added_at from source '
                                    'order by added_at, id').fetchall()
        return [Source(*r) for r in rows]

    def source(self, id: str) -> Source | None:
        return next((s for s in self.sources() if s.id == id), None)

    def made(self) -> dict[str, Made]:
        with self._lock:
            rows = self._db.execute('select submission, source, pages, orient, made_at from made').fetchall()
        return {r[0]: Made(r[0], r[1], tuple(json.loads(r[2])), json.loads(r[3]) if r[3] else {}, r[4])
                for r in rows}

    def set_made(self, m: Made) -> None:
        with self._lock:
            self._db.execute('insert or replace into made values (?,?,?,?,?)',
                             (m.submission, m.source, json.dumps(list(m.pages)), json.dumps(dict(m.orient)),
                              m.made_at))

    def delete_made(self, submission: str) -> None:
        with self._lock:
            self._db.execute('delete from made where submission=?', (submission,))

    def clear_memo(self, submission: str) -> None:
        with self._lock:
            self._db.execute('delete from memo where submission=?', (submission,))

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
