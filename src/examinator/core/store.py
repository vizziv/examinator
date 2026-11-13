"""The SQLite store: one file per workspace, keyed rows.

Everything stored per submission is a row `(submission, kind, name)` with a
JSON body and a revision:

    kind        name                body
    'value'     'question/label'    a field's value: {'text', 'settled', 'by'}   (`core.state`)
    'result'    'question/check'    a check's result: grade, evidence, what it read
    'decision'  'question/check'    a person's grade for a check, and what they saw
    'pages'     'question'          the question's pages for this submission, and a note
    'region'    'question/label'    a field's region for this submission
    'layout'    ''                  this submission's cover pages and print area
    'identity', 'who'   ''          intake: who wrote it (`core.identity`)
    'split'     ''                  intake: a source's split plan (`submission` is the source id)
    'print_area' ''                 a source's print area, for every submission from it (`submission`: the source id)

Every normal read is a keyed lookup. Every write may carry the `rev` the
writer last saw; a mismatch raises `Conflict` and nothing changes. Revisions
come from one workspace-wide counter, so a row that is deleted and recreated
never reuses one. A write may keep the row's revision (`keep_rev`) when what
changes is not its content (a value verified: same text, same revision).
Replaced rows go to history.
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
create table if not exists row (
  submission text not null,
  kind       text not null,
  name       text not null,
  body       text,
  origin     text not null,
  author     text,
  updated_at text not null,
  rev        integer not null,
  primary key (submission, kind, name)
);
create table if not exists history (
  submission text not null, name text not null, kind text not null,
  body text, origin text not null, author text, updated_at text not null,
  rev integer not null, replaced_at text not null
);
create index if not exists history_key on history (submission, kind, name, rev);
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

COLUMNS = 'submission, name, kind, body, origin, author, updated_at, rev'
WORK = ('value', 'result', 'decision')      # kinds that are grading work (intake will not rewrite over them)


class Conflict(Exception):
    """A write was rejected because something it depended on has changed."""


class _Any:
    def __repr__(self):
        return 'ANY'


ANY = _Any()   # `expect_rev=ANY`: write whatever is there


@dataclass(frozen=True)
class Row:
    submission: str
    name: str
    kind: str
    body: Any
    origin: str                 # 'auto' | 'manual'
    author: str | None
    updated_at: str
    rev: int


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


def _row(r) -> Row:
    return Row(r[0], r[1], r[2], json.loads(r[3]) if r[3] is not None else None, r[4], r[5], r[6], r[7])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _text(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


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

    def get(self, submission: str, name: str, kind: str) -> Row | None:
        with self._lock:
            r = self._db.execute(
                f'select {COLUMNS} from row where submission=? and kind=? and name=?',
                (submission, kind, name)).fetchone()
        return _row(r) if r else None

    def rows(self, submission: str, kind: str) -> dict[str, Row]:
        """One kind's rows of a submission, by name (every field value, say)."""
        with self._lock:
            rows = self._db.execute(
                f'select {COLUMNS} from row where submission=? and kind=?', (submission, kind)).fetchall()
        return {r[1]: _row(r) for r in rows}

    def all(self, *, kinds: Iterable[str] | None = None, drop: str | None = None,
            submission: str | None = None) -> list[Row]:
        """Every row (of some kinds, of one submission), in one query. `drop`
        is a JSON path left out of each body ('$.blocks'), so large parts are never loaded."""
        body = 'body' if drop is None else 'json_remove(body, ?)'
        sql, args = f'select submission, name, kind, {body}, origin, author, updated_at, rev from row', []
        if drop is not None:
            args.append(drop)
        where = []
        if kinds is not None:
            kinds = tuple(kinds)
            where.append(f"kind in ({','.join('?' * len(kinds))})")
            args += kinds
        if submission is not None:
            where.append('submission=?')
            args.append(submission)
        if where:
            sql += ' where ' + ' and '.join(where)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [_row(r) for r in rows]

    def version(self) -> int:
        """Changes whenever anything per submission or an override is written:
        a key for caching what is computed from stored rows."""
        return self.setting('changes', 0)

    def history(self, submission: str, name: str, kind: str) -> list[Row]:
        """Replaced versions, newest first."""
        with self._lock:
            rows = self._db.execute(
                f'select {COLUMNS} from history where submission=? and kind=? and name=? '
                'order by replaced_at desc, rev desc', (submission, kind, name)).fetchall()
        return [_row(r) for r in rows]

    # --- writing ---

    def _next_rev(self) -> int:
        r = self._db.execute("select value from setting where key='rev'").fetchone()
        rev = (json.loads(r[0]) if r else 0) + 1
        self._db.execute("insert or replace into setting values ('rev', ?)", (json.dumps(rev),))
        return rev

    def _changed(self) -> None:
        r = self._db.execute("select value from setting where key='changes'").fetchone()
        self._db.execute("insert or replace into setting values ('changes', ?)",
                         (json.dumps((json.loads(r[0]) if r else 0) + 1),))

    def _check(self, submission, name, kind, expect_rev, guard) -> Row | None:
        cur = self.get(submission, name, kind)
        if expect_rev is not ANY and (cur.rev if cur else None) != expect_rev:
            raise Conflict(f"{name or kind} changed")
        for (other, k), rev in (guard or {}).items():
            g = self.get(submission, other, k)
            if (g.rev if g else None) != rev:
                raise Conflict(f"{other or k} changed")
        return cur

    def _archive(self, cur: Row) -> None:
        self._db.execute(
            f'insert into history select {COLUMNS}, ? from row where submission=? and kind=? and name=?',
            (_now(), cur.submission, cur.kind, cur.name))

    def write(self, submission: str, name: str, kind: str, body: Any, *,
              origin: str, author: str | None = None, expect_rev: int | None | _Any = ANY,
              guard: Mapping[tuple[str, str], int | None] | None = None, keep_rev: bool = False) -> Row:
        """Create or replace one row; the replaced row goes to history.

        `expect_rev` is the revision the writer last saw (`None`: must not
        exist). `guard` maps other rows of the same submission, `(name, kind)`,
        to the revisions a computation read; the write happens only if all
        are still current. `keep_rev` keeps the row's revision (its content
        did not change). Writing the body a row already has is a no-op.
        """
        assert origin in ('auto', 'manual')
        text = _text(body)
        with self._lock, self._tx():
            cur = self._check(submission, name, kind, expect_rev, guard)
            if cur is not None and _text(cur.body) == text:
                return cur
            if cur is not None:
                self._archive(cur)
            rev = cur.rev if keep_rev and cur is not None else self._next_rev()
            self._db.execute(
                f'insert or replace into row ({COLUMNS}) values (?,?,?,?,?,?,?,?)',
                (submission, name, kind, text, origin, author, _now(), rev))
            self._changed()
            return self.get(submission, name, kind)

    def delete(self, submission: str, name: str, kind: str, *, expect_rev: int | _Any = ANY) -> None:
        with self._lock, self._tx():
            cur = self._check(submission, name, kind, expect_rev, None)
            if cur is not None:
                self._archive(cur)
                self._db.execute('delete from row where submission=? and kind=? and name=?',
                                 (submission, kind, name))
                self._changed()

    def restore(self, submission: str, name: str, kind: str, rev: int, *,
                expect_rev: int | None | _Any = ANY) -> Row:
        """Undo: bring a replaced version back as it was (same origin and author),
        under a new revision."""
        with self._lock, self._tx():
            old = next((a for a in self.history(submission, name, kind) if a.rev == rev), None)
            if old is None:
                raise KeyError(rev)
            return self.write(submission, name, kind, old.body, origin=old.origin, author=old.author,
                              expect_rev=expect_rev)

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
            self._changed()
            return self.overrides()[path]

    def clear_override(self, path: str, *, expect_rev: int | _Any = ANY) -> None:
        """Remove one override (back to the package's value); the row goes to history."""
        with self._lock, self._tx():
            if self._check_override(path, expect_rev) is not None:
                self._db.execute('delete from override where path=?', (path,))
                self._changed()

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

    def has_work(self, submission: str) -> bool:
        """Whether a submission has grading work: a value, a result or a decision."""
        with self._lock:
            return self._db.execute(
                f"select 1 from row where submission=? and kind in ({','.join('?' * len(WORK))}) limit 1",
                (submission, *WORK)).fetchone() is not None

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

    def made_of(self, submission: str) -> Made | None:
        with self._lock:
            r = self._db.execute('select submission, source, pages, orient, made_at from made where submission=?',
                                 (submission,)).fetchone()
        return Made(r[0], r[1], tuple(json.loads(r[2])), json.loads(r[3]) if r[3] else {}, r[4]) if r else None

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

    def delete_setting(self, key: str) -> None:
        assert key not in ('rev', 'changes')
        with self._lock:
            self._db.execute('delete from setting where key=?', (key,))

    def set_setting(self, key: str, value: Any) -> None:
        assert key not in ('rev', 'changes')
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
