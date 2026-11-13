"""The small changes (MODEL.md): everything the app or a batch run does is one
or more of these.

    field value   read (a reader)  set, verify, unverify (a person)
    result        compute (automatic: `refresh` recomputes what is outdated)
    decision      decide, clear (a person)

A read writes only values no person verified (unless told to), and a read that
yields the same text changes nothing: same revision, still verified. A value a
reader wrote records what it was read from (`sources`); when that differs from
what the reader would read now, the value needs reading again (`Value.stale`). A compute
runs outside any transaction and is published only if every field it read is
still at the revision it read. A check that times out, crashes or cannot decide
is stored as an undecided result: never "incorrect". Writes by people carry
the revision they saw; a mismatch raises `store.Conflict`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from .context import BadOutput, CheckContext, ReadContext, Unavailable
from .defs import DEFAULT, Exam, Field, Grade, Question, digest, has_marker, key, one_line
from .isolate import CheckTimeout, Pool, PoolClosed
from .result import Undecided, settle, undecided
from .status import Decision, Sheet, Stored, Value, current
from .store import ANY, Conflict
from .workspace import Workspace

DEFAULT_TIMEOUT = 60.0


class Model(Protocol):
    """A model client (see `examinator.model`). Errors raised are `ModelError`s
    whose text is structural only: never prompt or response content."""

    def complete(self, prompt: str, *, images: Sequence[Any] = (), text: str | None = None,
                 expect: str = 'text', tag: tuple[str, str, str] | None = None) -> str: ...


@dataclass(frozen=True)
class Outcome:
    submission: str
    question: str
    step: str            # 'read', or a check's name
    status: str          # 'done' | 'blocked' | 'failed' | 'discarded' | 'skipped'
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ('done', 'skipped')


class Services:
    """What a reader's context reaches: pages and crops from the workspace, the
    model, and the memo: the store's, or with `memo` (a dict) one kept in memory."""

    def __init__(self, workspace: Workspace, model: Model | None, memo: dict | None = None):
        self.ws, self._model, self._memo = workspace, model, memo

    def pages(self, submission, question):
        gone = self.ws.missing(submission)          # a placeholder is never shown to a reader
        return [self.ws.page_image(submission, p) for p in self.ws.selected_pages(submission, question)
                if p not in gone]

    def crop(self, submission, region):
        return self.ws.crop(submission, region)

    def layout(self, submission):
        return self.ws.layout(submission)

    def model(self, prompt, *, images, text, expect, tag):
        if self._model is None:
            raise Unavailable("model unavailable")
        return self._model.complete(prompt, images=images, text=text, expect=expect, tag=tag)

    def memo(self, submission, key):
        if self._memo is not None:
            return self._memo.get((submission, key))
        return self.ws.store.memo(submission, key)

    def set_memo(self, submission, key, value):
        if self._memo is not None:
            self._memo[(submission, key)] = value
        else:
            self.ws.store.set_memo(submission, key, value)


def structural(e: Exception) -> str:
    """A failure as graders may see it: model errors are structural by contract;
    anything else might quote student text, so only its type."""
    if isinstance(e, (Unavailable, BadOutput)) or type(e).__name__ == 'ModelError':
        return str(e)
    return type(e).__name__


def read_group(ws: Workspace, submission: str, q: Question, reader: Any, fields: Sequence[Field],
               services: Services, *, fresh: bool = False) -> dict[str, str]:
    """One reader on some of a question's fields (with this submission's regions):
    `{label: text}`. Model errors and bad output raise."""
    ctx = ReadContext(submission=submission, question=q, services=services, fresh=fresh)
    got = reader.read(ctx, tuple(fields))
    out = {}
    for f in fields:
        v = got.get(f.label) if isinstance(got, Mapping) else None
        if v is not None:
            if not isinstance(v, str):
                raise BadOutput("a reader returned a value that is not text")
            out[f.label] = v
    return out


def sources(ws: Workspace, submission: str, q: Question, fields: Sequence[Field], *,
            layout: Any = None) -> dict[str, str]:
    """What reading each of `fields` (a question's, with this submission's regions)
    looks at, as a key: the reader's memo key when it has one (`Ask.memo_key`: its
    definition, the region, the print area), else the reader, the region and the
    question's pages; and which scanned sheets those pages are, turned how. A read
    records it with the value it writes."""
    layout = layout if layout is not None else ws.layout(submission)
    made = ws.store.made_of(submission)
    pages = None
    out = {}
    for f in fields:
        reader = q.reader_of(f.label)
        if reader is None:
            continue
        if f.region is not None:
            on: tuple[int, ...] = (f.region.page,)
        else:
            on = pages = pages if pages is not None else ws.selected_pages(submission, q)
        memo_key = getattr(reader, 'memo_key', None)
        what = memo_key(q.id, f, layout) if callable(memo_key) else {
            'read': reader, 'region': f.region, 'pages': on, 'layout': list(layout.print_area)}
        out[f.label] = digest({'what': what, 'sheets': [sheet(made, p) for p in on]})
    return out


def sheet(made: Any, page: int) -> list | None:
    """The scanned sheet a page of a submission intake made is, and its turn (None:
    a PDF handed in split)."""
    if made is None:
        return None
    i, turns = page - 1, made.orient or ()
    return [made.source, made.pages[i] if 0 <= i < len(made.pages) else None,
            turns[i] if 0 <= i < len(turns) else None]


def try_check(exam: Exam, qid: str, check: str, values: Mapping[str, str]) -> dict:
    """Run one check in-process on given values (`{'question/label': text}`; a
    field left out is not read, or at its default) and return its result as it would be stored:
    `{'grade', 'fields', 'blocks', 'reads'}`. For an exam's own tests. A check
    that raises `Undecided` is an undecided result; anything else raises."""
    q = exam.questions[qid]
    c = q.check(check)
    ctx = CheckContext(submission='test', question=q, check=check,
                       values={k: (i + 1, v) for i, (k, v) in enumerate(values.items())}, fields=exam.fields())
    try:
        body = settle(c.points, c.fn(ctx))
    except Undecided as e:
        body = settle(c.points, undecided(str(e)))
    return {**body, 'reads': dict(ctx.reads)}


class Runner:
    def __init__(self, exam: Exam, workspace: Workspace, *, model: Model | None = None,
                 pool: Pool | None = None):
        self.exam = exam
        self.ws = workspace
        self.store = workspace.store
        self.model = model
        self.pool = pool if pool is not None else (Pool(exam.path) if exam.path else None)
        self._services = Services(workspace, model)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()

    # --- what is stored -------------------------------------------------------

    def sheet(self, submission: str) -> Sheet:
        return Sheet.of(self.store.all(submission=submission, kinds=('value', 'result', 'decision'), drop='$.blocks'))

    def values(self, submission: str) -> dict[str, Value]:
        return {k: Value.of(r) for k, r in self.store.rows(submission, 'value').items()}

    # --- read -------------------------------------------------------------------

    def read(self, submission: str, qid: str, *, missing_only: bool = False, include_protected: bool = False, fresh: bool = False,
             labels: Sequence[str] | None = None) -> list[Outcome]:
        """Read a question's fields (or those of `labels`) and write what was read.
        Values a person verified are skipped unless `include_protected`; with
        `missing_only`, only fields with no value are read. One outcome per reader run."""
        resolved = self.ws.question(submission, qid)
        have = self.values(submission)
        gone = self.ws.missing(submission)
        outs = []

        def out(status, detail=""):
            outs.append(Outcome(submission, qid, 'read', status, detail))

        for reader, fields in resolved.groups():
            targets = []
            for f in fields:
                v = have.get(key(qid, f.label))
                if labels is not None and f.label not in labels:
                    continue
                if missing_only and v is not None:
                    continue
                if v is not None and v.verified == 'person' and not include_protected:
                    continue
                targets.append(f)
            if not targets:
                continue
            if getattr(reader, 'needs_region', False):
                bare = [f.label for f in targets if f.region is None]
                if bare:
                    out('blocked', f"no region: {', '.join(bare)}")
                targets = [f for f in targets if f.region is not None]
            off = [f.label for f in targets if f.region is not None and f.region.page in gone]
            if off:                 # never read from a placeholder
                out('blocked', f"page missing: {', '.join(off)}")
                targets = [f for f in targets if f.label not in off]
            if not targets:
                continue
            if getattr(reader, 'uses_model', True) and self.model is None:
                out('blocked', "model unavailable")
                continue
            try:
                src = sources(self.ws, submission, resolved, targets)
                got = read_group(self.ws, submission, resolved, reader, targets, self._services, fresh=fresh)
            except Exception as e:
                out('failed', structural(e))
                continue
            vouches = getattr(reader, 'vouches', None)
            lost = []
            for f in targets:
                if f.label not in got:
                    continue
                text = got[f.label] if resolved.multiline(f.label) else one_line(got[f.label])
                vouched = bool(callable(vouches) and vouches(f, text))
                if not self._put_read(submission, qid, f.label, text, vouched, src.get(f.label), include_protected):
                    lost.append(f.label)
            out('discarded', f"changed meanwhile: {', '.join(lost)}") if lost else out('done')
        return outs

    def _put_read(self, submission, qid, label, text, vouched, source, include_protected) -> bool:
        k = key(qid, label)
        row = self.store.get(submission, k, 'value')
        cur = Value.of(row) if row is not None else None
        if cur is not None and cur.verified == 'person' and not include_protected:
            return True
        verified = 'reader' if vouched and not has_marker(text) else None
        try:
            if cur is not None and cur.text == text:       # the same value: same revision (where it was read from may be new)
                if cur.verified == 'person':
                    return True
                self.store.write(submission, k, 'value', replace(cur, verified=verified, by=None, source=source).body(),
                                 origin='auto', expect_rev=cur.rev, keep_rev=True)
            else:
                self.store.write(submission, k, 'value', Value(text, 0, verified, source=source).body(),
                                 origin='auto', expect_rev=cur.rev if cur else None)
        except Conflict:
            return False
        return True

    # --- compute ----------------------------------------------------------------

    def compute(self, submission: str, qid: str, check: str) -> Outcome:
        """Run one check on the current values and store its result."""
        q = self.exam.questions[qid]
        c = q.check(check)

        def out(status, detail=""):
            return Outcome(submission, qid, check, status, detail)

        if c is None:
            return out('blocked', "no such check")
        if c.fn is None:
            return out('blocked', "decided by a person")
        values = {k: (v.rev, v.text) for k, v in self.values(submission).items()}
        reads: dict[str, int | None] = {}
        timeout = c.timeout or DEFAULT_TIMEOUT
        try:
            if c.isolate and self.pool is not None:
                body = self.pool.call(qid, check, {
                    'submission': submission, 'values': {k: list(v) for k, v in values.items()},
                    'overrides': self.exam.overrides}, timeout, reads)
            else:
                ctx = CheckContext(submission=submission, question=q, check=check, values=values,
                                   fields=self.exam.fields(), reads=reads)
                body = settle(c.points, c.fn(ctx))
        except Undecided as e:
            body = settle(c.points, undecided(str(e)))
        except PoolClosed:
            return out('failed', "shutting down")
        except CheckTimeout:
            body = settle(c.points, undecided(f"timed out after {timeout:g} s"))
        except Exception as e:
            detail = getattr(e, 'detail', "") or f"{type(e).__name__}: {e}"
            body = settle(c.points, undecided("the check failed to run", [
                {'type': 'details', 'summary': "Error",
                 'blocks': [{'type': 'code', 'text': detail}]}]))
        body.update(reads=dict(reads), defn=self.exam.check_hash(q, c), code=self.exam.code_hash)
        try:
            self.store.write(submission, key(qid, check), 'result', body, origin='auto',
                             guard={(k, 'value'): None if rev == DEFAULT else rev for k, rev in reads.items()})
        except Conflict as e:
            return out('discarded', str(e))
        return out('done')

    def refresh(self, submission: str, *, questions: Sequence[str] = ()) -> list[Outcome]:
        """Recompute every outdated result of a submission, and run the checks of
        `questions` that never ran. Never touches a value or a decision."""
        sheet = self.sheet(submission)
        outs = []
        for q in self.exam.questions.values():
            for c in q.checks:
                if c.fn is None:
                    continue
                stored = sheet.results.get(key(q.id, c.name))
                if stored is None:
                    if q.id not in questions:
                        continue
                elif current(stored, sheet.values, defn=self.exam.check_hash(q, c), code=self.exam.code_hash):
                    continue
                outs.append(self.compute(submission, q.id, c.name))
        return outs

    # --- what people do ---------------------------------------------------------
    # Each raises `Conflict` if `expect_rev` is no longer current, and `ValueError`
    # for input a person should correct. None computes: call `refresh` afterwards
    # (the app does so in the background).

    def _field(self, qid: str, label: str) -> str:
        q = self.exam.questions.get(qid)
        if q is None or q.field(label) is None:
            raise ValueError(f"no field {label!r} in {qid}")
        return key(qid, label)

    def set(self, submission: str, qid: str, label: str, text: str, *, author: str | None,
            expect_rev: Any = ANY) -> Value:
        """A person's value. It is verified by them, unless it carries a review marker."""
        k = self._field(qid, label)
        if not isinstance(text, str):
            raise ValueError("a value is text")
        if not self.exam.questions[qid].multiline(label):
            text = one_line(text)
        verified = None if has_marker(text) else 'person'
        row = self.store.get(submission, k, 'value')
        cur = Value.of(row) if row is not None else None
        keep = cur is not None and cur.text == text
        new = Value(text, 0, verified, author if verified else None)
        return Value.of(self.store.write(submission, k, 'value', new.body(), origin='manual', author=author,
                                         expect_rev=expect_rev, keep_rev=keep))

    def unset(self, submission: str, qid: str, label: str, *, expect_rev: Any = ANY) -> None:
        """Drop a field's stored value: back to its default (only a field with one)."""
        k = self._field(qid, label)
        if self.exam.questions[qid].field(label).default is None:
            raise ValueError(f"{label} has no default")
        self.store.delete(submission, k, 'value', expect_rev=expect_rev)

    def verify(self, submission: str, qid: str, label: str, *, author: str | None, expect_rev: int,
               verified: bool = True) -> Value:
        """Verify the value as it stands (or, `verified=False`, unverify it). Same revision."""
        k = self._field(qid, label)
        row = self.store.get(submission, k, 'value')
        if row is None or row.rev != expect_rev:
            raise Conflict(f"{label} changed")
        cur = Value.of(row)
        if verified and cur.marker:
            raise ValueError("resolve review markers before verifying")
        new = replace(cur, verified='person' if verified else None, by=author if verified else None)
        return Value.of(self.store.write(submission, k, 'value', new.body(), origin=row.origin, author=row.author,
                                         expect_rev=expect_rev, keep_rev=True))

    def page(self, submission: str, qid: str, *, check: str | None = None) -> list[str]:
        """The fields on a question's page: its own, then those its checks (or one
        check) read from other questions, as stored. What a decision is recorded against."""
        q = self.exam.questions[qid]
        out = [key(qid, label) for label in q.labels]
        for c in q.checks:
            if check is not None and c.name != check:
                continue
            row = self.store.get(submission, key(qid, c.name), 'result')
            for k in (Stored.of(row).reads if row is not None else {}):
                if k not in out:
                    out.append(k)
        return out

    def decide(self, submission: str, qid: str, check: str, value: Any, *, author: str | None,
               flags: Sequence[str] = (), reason: str | None = None, expect_rev: Any = ANY,
               seen: Mapping[str, int | None] | None = None) -> Decision:
        """A person's grade for one check, recorded against the fields on the page:
        `seen` maps each to the revision on their screen (refused if one changed;
        without `seen`, the current revisions)."""
        q = self.exam.questions[qid]
        c = q.check(check)
        if c is None:
            raise ValueError(f"no check {check!r} in {qid}")
        grade = c.points.coerce(Grade(value, tuple(flags), reason))
        if grade.value is None:
            raise ValueError("a decision needs a value")
        values = self.values(submission)
        now = {k: (values[k].rev if k in values else None) for k in self.page(submission, qid, check=check)}
        if seen is not None:
            for k, rev in now.items():
                if k not in seen or seen[k] != rev:
                    raise Conflict(f"{k.partition('/')[2] or k} changed")
        row = self.store.write(submission, key(qid, check), 'decision', {'grade': grade.to_json(), 'seen': now},
                               origin='manual', author=author, expect_rev=expect_rev,
                               guard={(k, 'value'): rev for k, rev in now.items()})
        return Decision.of(row)

    def clear(self, submission: str, qid: str, check: str, *, expect_rev: Any = ANY) -> None:
        """Remove a decision: the check's own result shows again."""
        self.store.delete(submission, key(qid, check), 'decision', expect_rev=expect_rev)
