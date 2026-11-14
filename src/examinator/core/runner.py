"""The small changes (MODEL.md): everything the app or a batch run does is one
or more of these.

    field value   read (a reader)  set, verify, unverify (a person)
    result        compute (automatic: `refresh` recomputes what is outdated)
    decision      decide, clear (a person)

What a read reads is one computation (`to_read`), which the read executes,
the batch plan counts and the question page shows. A read writes only values
no person verified (unless told to), and a read that yields the same text
changes nothing: same revision, still verified. Changing
how a field is read changes no value; reading again is a person's choice. A compute
runs outside any transaction and is published only if every field it read is
still at the revision it read. A check that times out, crashes or cannot decide
is stored as an undecided result: never "incorrect". Writes by people carry
the revision they saw; a mismatch raises `store.Conflict`.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, replace
from typing import AbstractSet, Any, Mapping, Protocol, Sequence

from ..model.client import ModelError
from .context import BadOutput, CheckContext, ReadContext, Unavailable
from .defs import DEFAULT, Exam, Field, Grade, Question, key, one_line
from .reading import Reading
from .isolate import CheckTimeout, Pool, PoolClosed
from .result import BadResult, Undecided, settle, undecided
from .status import Decision, Ledger, Stored, Value, current
from .store import ANY, Conflict
from .workspace import Workspace

DEFAULT_TIMEOUT = 60.0
MAX_DETAIL = 20_000      # characters of a traceback shown


def worded(e: Conflict) -> Conflict:
    """A conflict on a field value or a decision, in the page's words: the field's
    label ("A.1 changed"), "the decision changed"."""
    if e.kind == 'value':
        return Conflict(f"{(e.name or '').partition('/')[2] or e.name} changed", name=e.name, kind=e.kind)
    if e.kind == 'decision':
        return Conflict("the decision changed", name=e.name, kind=e.kind)
    return e


def _in_words(fn):
    """A person's change whose conflicts are `worded`."""
    @functools.wraps(fn)
    def run(*args, **kw):
        try:
            return fn(*args, **kw)
        except Conflict as e:
            raise worded(e) from None
    return run


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
    asked: int = 0       # a read: the fields it asked the reader for ...
    read: int = 0        # ... and how many got a value (the rest stay not read)

    @property
    def ok(self) -> bool:
        return self.status in ('done', 'skipped')


class Services:
    """What a reader's context reaches: pages and crops from the workspace, and the model."""

    def __init__(self, workspace: Workspace, model: Model | None):
        self.ws, self._model = workspace, model

    def pages(self, submission, question):
        # `question` as it is for the submission; all there: a reader of pages runs only
        # when none is missing (`to_read`)
        return [self.ws.page_image(submission, p) for p in question.pages]

    def crop(self, submission, region):
        return self.ws.crop(submission, region)

    def model(self, prompt, *, images, text, expect, tag):
        if self._model is None:
            raise Unavailable("model unavailable")
        return self._model.complete(prompt, images=images, text=text, expect=expect, tag=tag)


def structural(e: Exception) -> str:
    """A failure as graders may see it: model errors are structural by contract;
    anything else might quote student text, so only its type."""
    if isinstance(e, (Unavailable, BadOutput, ModelError)):
        return str(e)
    return type(e).__name__


def looks_at(reader: Any, f: Field, pages: Sequence[int]) -> tuple[int, ...]:
    """The pages reading field `f` with `reader` looks at: its region's page for a
    reader of regions (`needs_region`; none without a region), else all of the
    question's `pages`."""
    if getattr(reader, 'needs_region', False):
        return (f.region.page,) if f.region is not None else ()
    return tuple(pages)


# Why a field with a reader is not read: not asked for, kept by the options, or blocked.
NOT_ASKED, PRESENT, PROTECTED = 'not asked', 'present', 'protected'
NO_REGION, PAGE_MISSING, NO_MODEL = 'no region', 'page missing', 'model unavailable'
BLOCKED = (NO_REGION, PAGE_MISSING, NO_MODEL)       # what stops a read that was asked for


@dataclass(frozen=True)
class Reads:
    """What reading one question of one submission does (`to_read`): per reader,
    the fields it reads; per other field with a reader, why it is not read."""
    question: Question                                  # as it is for the submission
    groups: tuple[tuple[Any, tuple[Field, ...]], ...]   # (reader, the fields it reads), none empty
    why: Mapping[str, str]                              # label: NOT_ASKED, PRESENT, PROTECTED or one of BLOCKED

    @property
    def count(self) -> int:
        """How many fields it reads."""
        return sum(len(fields) for _, fields in self.groups)

    @property
    def blocked(self) -> dict[str, str]:
        """The fields asked for that cannot be read now, and why."""
        return {label: why for label, why in self.why.items() if why in BLOCKED}


def to_read(q: Question, values: Mapping[str, Value], gone: AbstractSet[int], *,
            labels: Sequence[str] | None = None, missing_only: bool = False,
            include_protected: bool = False, model: bool = True) -> Reads:
    """What a read of `q` (as it is for one submission: `Layers.question`) does,
    given the submission's stored `values`, the pages its PDF lacks (`gone`:
    `Workspace.missing`) and the read's options: `labels` (only these fields),
    `missing_only` (only fields with no value), `include_protected` (also values
    a person verified), `model` (whether there is one). Pure; the one rule for
    the runner, the batch plan and the question page. A read needs its pages: a
    reader of the question's pages reads only when all of them are there, so no
    reader is ever called without images."""
    groups, why = [], {}
    for reader, fields in q.groups():
        mine = []
        for f in fields:
            v = values.get(key(q.id, f.label))
            pages = looks_at(reader, f, q.pages)
            reason = (NOT_ASKED if labels is not None and f.label not in labels
                      else PRESENT if missing_only and v is not None
                      else PROTECTED if v is not None and v.protected and not include_protected
                      else NO_REGION if not pages           # a question always has pages
                      else PAGE_MISSING if gone.intersection(pages)
                      else NO_MODEL if not model and getattr(reader, 'uses_model', True)
                      else None)
            if reason is None:
                mine.append(f)
            else:
                why[f.label] = reason
        if mine:
            groups.append((reader, tuple(mine)))
    return Reads(q, tuple(groups), why)


def read_group(ws: Workspace, submission: str, q: Question, reader: Any, fields: Sequence[Field],
               services: Services) -> dict[str, Reading]:
    """One reader on some of a question's fields (with this submission's regions):
    `{label: Reading}` for the fields it read (a reader may return plain text).
    Model errors and bad output raise."""
    ctx = ReadContext(submission=submission, question=q, services=services)
    got = reader.read(ctx, tuple(fields))
    out = {}
    for f in fields:
        v = got.get(f.label) if isinstance(got, Mapping) else None
        if isinstance(v, str):
            v = Reading(v)
        if v is not None:
            if not isinstance(v, Reading) or not isinstance(v.text, str):
                raise BadOutput("a reader returned a value that is not text")
            out[f.label] = v
    return out


def try_check(exam: Exam, qid: str, check: str, values: Mapping[str, str]) -> dict:
    """Run one check in-process on given values (`{'question/label': text}`; a
    field left out is not read, or at its default) and return its result as it would be stored:
    `{'grade', 'fields', 'blocks', 'reads', 'images'}` (`images`: {digest: bytes}).
    For an exam's own tests. A check that raises `Undecided` is an undecided
    result; anything else raises (`BadResult` for evidence that does not fit)."""
    q = exam.questions[qid]
    c = q.check(check)
    ctx = CheckContext(submission='test', question=q, check=check,
                       values={k: (i + 1, v) for i, (k, v) in enumerate(values.items())}, fields=exam.fields())
    try:
        out = settle(c.points, c.fn(ctx))
    except Undecided as e:
        out = settle(c.points, undecided(str(e)))
    return {**out.body, 'reads': dict(ctx.reads), 'images': {d: b for d, (_, b) in out.images.items()}}


class Runner:
    """The small changes on one workspace, whose definition (`ws.exam`) it reads."""

    def __init__(self, workspace: Workspace, *, model: Model | None = None, pool: Pool | None = None):
        self.ws = workspace
        self.store = workspace.store
        self.model = model
        path = workspace.exam.path
        self.pool = pool if pool is not None else (Pool(path) if path else None)
        self._services = Services(workspace, model)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()

    # --- what is stored -------------------------------------------------------

    def ledger(self, submission: str) -> Ledger:
        return Ledger.of(self.store, submission)

    def values(self, submission: str) -> dict[str, Value]:
        return Ledger.of(self.store, submission, kinds=('value',)).values

    # --- read -------------------------------------------------------------------

    def to_read(self, submission: str, qid: str, **options: Any) -> Reads:
        """What `read` with these options would do now (`to_read`)."""
        return to_read(self.ws.layers(submission).question(qid), self.values(submission),
                       self.ws.missing(submission), model=self.model is not None, **options)

    def read(self, submission: str, qid: str, *, missing_only: bool = False, include_protected: bool = False,
             labels: Sequence[str] | None = None) -> list[Outcome]:
        """Read what `to_read` says (a question's fields, or those of `labels`; values
        a person verified only with `include_protected`; with `missing_only`, only
        fields with no value) and write what was read. One outcome per reason a
        field asked for is blocked, then one per reader run. The identity group is
        read by its name (`IDENTITY`)."""
        plan = self.to_read(submission, qid, labels=labels, missing_only=missing_only,
                            include_protected=include_protected)
        outs = []

        def out(status, detail="", asked=0, read=0):
            outs.append(Outcome(submission, qid, 'read', status, detail, asked, read))

        blocked: dict[str, list[str]] = {}
        for label, why in plan.blocked.items():
            blocked.setdefault(why, []).append(label)
        for why, which in blocked.items():
            out('blocked', f"{why}: {', '.join(which)}")
        q = plan.question
        for reader, targets in plan.groups:
            try:
                got = read_group(self.ws, submission, q, reader, targets, self._services)
            except Exception as e:
                out('failed', structural(e))
                continue
            vouches = getattr(reader, 'vouches', None)
            lost = []
            for f in targets:
                if f.label not in got:
                    continue
                r = got[f.label] if q.multiline(f.label) else replace(got[f.label], text=one_line(got[f.label].text))
                vouched = bool(callable(vouches) and vouches(f, r))
                if not self._put_read(submission, qid, f.label, r, vouched, include_protected):
                    lost.append(f.label)
            silent = [f.label for f in targets if f.label not in got]
            n = len(targets), len(targets) - len(silent)
            if lost:
                out('discarded', f"changed meanwhile: {', '.join(lost)}", *n)
            else:
                out('done', f"not read: {', '.join(silent)}" if silent else "", *n)
        return outs

    def _put_read(self, submission, qid, label, got: Reading, vouched, include_protected) -> bool:
        k = key(qid, label)
        row = self.store.get(submission, k, 'value')
        cur = Value.of(row) if row is not None else None
        if cur is not None and cur.protected and not include_protected:
            return True
        try:
            self._write(submission, k, cur, Value.read(got, vouched, cur), origin='auto',
                        expect_rev=cur.rev if cur else None)
        except Conflict:
            return False
        return True

    def _write(self, submission: str, k: str, cur: Value | None, new: Value, *, origin: str,
               author: str | None = None, expect_rev: Any) -> Value:
        """Store a field's new value; it keeps the revision while its text is unchanged."""
        return Value.of(self.store.write(submission, k, 'value', new.body(), origin=origin, author=author,
                                         expect_rev=expect_rev, keep_rev=cur is not None and cur.text == new.text))

    # --- compute ----------------------------------------------------------------

    def compute(self, submission: str, qid: str, check: str) -> Outcome:
        """Run one check on the current values and store its result."""
        exam = self.ws.exam
        q = exam.questions.get(qid)
        c = q.check(check) if q is not None else None

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
                got = self.pool.call(qid, check, {
                    'submission': submission, 'values': {k: list(v) for k, v in values.items()},
                    'overrides': exam.overrides}, timeout, reads)
            else:
                ctx = CheckContext(submission=submission, question=q, check=check, values=values,
                                   fields=exam.fields(), reads=reads)
                got = settle(c.points, c.fn(ctx))
        except (Undecided, BadResult) as e:
            got = settle(c.points, undecided(str(e)))
        except PoolClosed:
            return out('failed', "shutting down")
        except CheckTimeout:
            got = settle(c.points, undecided(f"timed out after {timeout:g} s"))
        except Exception as e:
            detail = getattr(e, 'detail', "") or f"{type(e).__name__}: {e}"
            got = settle(c.points, undecided("the check failed to run", [
                {'type': 'details', 'summary': "Error",
                 'blocks': [{'type': 'code', 'text': detail[-MAX_DETAIL:]}]}]))
        body = {**got.body, 'reads': dict(reads), 'defn': exam.check_hash(q, c), 'code': exam.code_hash}
        try:
            self.store.write(submission, key(qid, check), 'result', body, origin='auto', images=got.images,
                             guard={(k, 'value'): None if rev == DEFAULT else rev for k, rev in reads.items()})
        except Conflict as e:
            return out('discarded', str(worded(e)))
        return out('done')

    def refresh(self, submission: str) -> list[Outcome]:
        """The one background rule: compute every check of a submission whose result
        is not current and which has something to compute from. A result that is
        outdated is recomputed; a check that never ran runs once every field of its
        question has a value (stored, or its default). So a check is 'not run' only
        while its question has a field not read. Never touches a value or a decision.
        Called after anything that may leave such a check (`refresh` in MODEL.md)."""
        ledger, exam = self.ledger(submission), self.ws.exam
        outs = []
        for q in exam.questions.values():
            for c in q.checks:
                if c.fn is None:
                    continue
                stored = ledger.results.get(key(q.id, c.name))
                if stored is None:
                    if not all(key(q.id, f.label) in ledger.values or f.default is not None for f in q.fields):
                        continue
                elif current(stored, ledger.values, defn=exam.check_hash(q, c), code=exam.code_hash):
                    continue
                outs.append(self.compute(submission, q.id, c.name))
        return outs

    # --- what people do ---------------------------------------------------------
    # Each raises `Conflict` if `expect_rev` is no longer current, and `ValueError`
    # for input a person should correct. None computes: call `refresh` afterwards
    # (the app does so in the background).

    def _field(self, qid: str, label: str) -> Field:
        """A field of a question, or of the identity group by its name."""
        q = self.ws.exam.group(qid)
        f = q.field(label) if q is not None else None
        if f is None:
            raise ValueError(f"no field {label!r} in {qid}")
        return f

    @_in_words
    def set(self, submission: str, qid: str, label: str, text: str, *, author: str | None,
            expect_rev: Any = ANY) -> Value:
        """A person's value, verified by them (any review is cleared)."""
        self._field(qid, label)
        k = key(qid, label)
        if not isinstance(text, str):
            raise ValueError("a value is text")
        if not self.ws.exam.group(qid).multiline(label):
            text = one_line(text)
        row = self.store.get(submission, k, 'value')
        return self._write(submission, k, Value.of(row) if row is not None else None, Value.set(text, author),
                           origin='manual', author=author, expect_rev=expect_rev)

    @_in_words
    def unset(self, submission: str, qid: str, label: str, *, expect_rev: Any = ANY) -> None:
        """Drop a field's stored value: back to its default (only a field with one)."""
        if self._field(qid, label).default is None:
            raise ValueError(f"{label} has no default")
        self.store.delete(submission, key(qid, label), 'value', expect_rev=expect_rev)

    @_in_words
    def verify(self, submission: str, qid: str, label: str, *, author: str | None, expect_rev: int,
               verified: bool = True) -> Value:
        """Verify the value as it stands, which clears any review (or, `verified=False`,
        unverify it). Same revision."""
        cur, row = self._current(submission, qid, label, expect_rev)
        return self._write(submission, row.name, cur, cur.verify(author) if verified else cur.unverify(),
                           origin=row.origin, author=row.author, expect_rev=expect_rev)

    @_in_words
    def review(self, submission: str, qid: str, label: str, *, note: str | None, expect_rev: int) -> Value:
        """Flag the value for review with a note (possibly ''), which unverifies it; or,
        `note=None`, take the flag off (it stays unverified). Same revision."""
        cur, row = self._current(submission, qid, label, expect_rev)
        return self._write(submission, row.name, cur, cur.flag(note), origin=row.origin, author=row.author,
                           expect_rev=expect_rev)

    def _current(self, submission, qid, label, expect_rev):
        self._field(qid, label)
        k = key(qid, label)
        row = self.store.get(submission, k, 'value')
        if row is None or row.rev != expect_rev:
            raise Conflict(name=k, kind='value')
        return Value.of(row), row

    def page(self, submission: str, qid: str, *, check: str | None = None) -> list[str]:
        """The fields on a question's page: its own, then those its checks (or one
        check) read from other questions, as stored. What a decision is recorded against."""
        q = self.ws.exam.questions[qid]
        out = [key(qid, label) for label in q.labels]
        for c in q.checks:
            if check is not None and c.name != check:
                continue
            row = self.store.get(submission, key(qid, c.name), 'result')
            for k in (Stored.of(row).reads if row is not None else {}):
                if k not in out:
                    out.append(k)
        return out

    @_in_words
    def decide(self, submission: str, qid: str, check: str, value: Any, *, author: str | None,
               flags: Sequence[str] = (), reason: str | None = None, expect_rev: Any = ANY,
               seen: Mapping[str, int | None] | None = None) -> Decision:
        """A person's grade for one check, recorded against the fields on the page:
        `seen` maps each to the revision on their screen (refused if one changed;
        without `seen`, the current revisions)."""
        q = self.ws.exam.questions[qid]
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
                    raise Conflict(name=k, kind='value')
        row = self.store.write(submission, key(qid, check), 'decision', {'grade': grade.to_json(), 'seen': now},
                               origin='manual', author=author, expect_rev=expect_rev,
                               guard={(k, 'value'): rev for k, rev in now.items()})
        return Decision.of(row)

    @_in_words
    def clear(self, submission: str, qid: str, check: str, *, expect_rev: Any = ANY) -> None:
        """Remove a decision: the check's own result shows again."""
        self.store.delete(submission, key(qid, check), 'decision', expect_rev=expect_rev)
