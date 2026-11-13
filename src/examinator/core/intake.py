"""Intake: from stacks of scans to one PDF per submission.

    WORKSPACE/sources/ID.pdf          each scanned PDF as added, never changed (ID: start of its SHA-256)
    WORKSPACE/made/s0001-TOKEN.pdf    a submission's PDF as Accept wrote it, never changed

A source's split plan (`core.split`) is the row `(source id, '', 'split')`,
so edits keep history and carry revisions like everything else a grader edits.
The plan is only the pile sorted: one ordered list of the source's sheets with
their marks; a sheet's turn (quarter turns clockwise) is its own and nothing
else's. It says nothing about what has been written.

What has been written is the store's `made` table and nothing else: per
submission, which sheets of which source it holds (`Made.pages`) and each
one's turn (`Made.orient`). Which run of the plan is which written submission
is derived from the two (`split.link`, in `outline`), for the page, its counts
and Accept alike. Ids are opaque, allocated once, never reused.

Accepting writes the PDFs whose sheets (or orientation) changed. A
submission's PDF is the exam's printed pages in order, page p at index p - 1,
with a blank placeholder where a page is missing (None in `Made.pages`), then
its extra sheets in order (`split.lay_out`), written upright.

**A written file is never changed; the store names the file.** A handed-in
PDF is named by its submission row (`pdfs/`), a written one by `made` alone
(`made/`, `Made.file`; its submission row's `pdf` is ''). Each PDF Accept
writes gets a fresh name; one commit records the plan,
`made` (the file included) and the submissions, so the store and the files it
names agree whatever fails, wherever. A file in `made/` that no `made` row
names is garbage, deleted at the start and end of every Accept (`sweep`).
`pdfs/` holds only PDFs a person put there (handed in already split, adopted by
`Workspace.sync`), so nothing Accept writes is ever taken for one. Accepts run
one at a time (the server's lock).

**Accept speaks for a person.** It makes every sheet of the plan a person's,
recorded under the person accepting. A written submission it would rewrite or
remove that a person said something about (`disturbed`: values a person typed
or verified, decisions, the name picked, regions or a print area of its own)
is not touched unless the person said it may be (`allow`); `Busy` names the
others. One with only what a model read and what was computed from it is
rewritten without asking: that is read again. A rewritten submission has new pages
(`repaged`): what was read from the old ones goes (every value no person
verified), and so does where things are on them (its own
regions and print area, drawn on the old pages; a region on a sheet it no
longer has would not even validate); what a person said about the student
stays (values a person verified, decisions, the pick). A removed submission
is gone; its id is never reused.

Who a submission is stays an attribute: the id is opaque and never changes.
The roster, the identity fields, and the person a grader picks are
`core.identity`.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Mapping

from PIL import Image

from ..pdf import pages as pdf_pages, render
from ..pdf.orient import turn
from . import identity, overrides, split
from .ask import Printed
from .defs import Landmark, digest
from .reading import normalize, region_key
from .store import ANY, Row, Conflict, Made, Source, Submission

PLAN = 'split'


class Busy(Conflict):
    """Accepting would change or remove submissions that already have grading work."""

    def __init__(self, submissions: list[str]):
        super().__init__("grading work exists for " + ", ".join(submissions))
        self.submissions = submissions
        self.current = {'busy': submissions}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# --- sources --------------------------------------------------------------------

def sources_dir(ws) -> Path:
    d = ws.root / 'sources'
    d.mkdir(exist_ok=True, mode=0o700)
    return d


def source_path(ws, src: Source) -> Path:
    path = sources_dir(ws) / src.file
    if path.is_symlink() or path.parent != sources_dir(ws):
        raise KeyError(src.id)
    return path


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def add_source(ws, path: str | Path, name: str | None = None, *, move: bool = False,
               sha256: str | None = None) -> tuple[Source, bool]:
    """Add a scanned PDF. Returns the source and whether it is new: the same
    content added again is recognised and not stored twice. With `move` the
    file (an upload's temporary file) is moved rather than copied."""
    path = Path(path)
    sha = sha256 or file_hash(path)
    have = next((s for s in ws.store.sources() if s.sha256 == sha), None)
    if have is not None:
        if move:
            path.unlink(missing_ok=True)
        return have, False
    try:
        n = render.page_count(path)
    except Exception:
        if move:
            path.unlink(missing_ok=True)
        raise ValueError("not a readable PDF") from None
    if n < 1:
        raise ValueError("the PDF has no pages")
    id = sha[:12]
    dest = sources_dir(ws) / f'{id}.pdf'
    if move:
        os.replace(path, dest)
    else:
        shutil.copyfile(path, dest)
    os.chmod(dest, 0o600)
    src = Source(id, dest.name, (name or path.name)[:200], sha, n, _now())
    ws.store.add_source(src)
    reguess(ws, src.id)
    return src, True


def source(ws, id: str) -> Source:
    src = ws.store.source(id)
    if src is None:
        raise KeyError(id)
    return src


# --- the plan ---------------------------------------------------------------------

def anchor(ws) -> int:
    """The first printed page (0-based) that carries a landmark."""
    first = []
    for lm in ws.exam.landmarks:
        first += list(lm.reads.values()) if lm.reads else [lm.region.page]
    return (min(first) if first else 1) - 1


def plan(ws, id: str) -> Row | None:
    return ws.store.get(id, '', PLAN)


def _put(ws, id: str, body: dict, **kw) -> Row:
    """Write a source's plan; a stale write is refused as "the plan changed"."""
    try:
        return ws.store.write(id, '', PLAN, body, **kw)
    except Conflict:
        raise Conflict("the plan changed", name='', kind=PLAN) from None


def page(ws, src: Source, sheet: int, turns: int = 0) -> Image.Image:
    """One sheet, turned."""
    return turn(ws.renderer.page(source_path(ws, src), sheet), turns)


def page_of(lm: Landmark, text: str | None) -> int | None:
    """The printed page a landmark reading points to."""
    if text is None:
        return None
    if lm.reads:
        table = {normalize(k): v for k, v in lm.reads.items()}
        return table.get(normalize(text))
    digits = ''.join(ch for ch in normalize(text) if ch.isdigit())
    return int(digits) if digits and len(digits) <= 4 else None


def ask(lm: Landmark) -> Printed:
    """How a landmark is read: printed text, asked about like a printed field."""
    return Printed(lm.ask)


class LandmarkReader:
    """Reads one sheet's position from the exam's landmarks (first that answers
    wins), through the model (`Ask.call`, as for a printed field), memoised per
    source, sheet, landmark, orientation and print area (the source's, else the
    exam's). A failed call is no reading and is not remembered: the sheets it
    left unread are `failed`; `calls` counts the calls made."""

    def __init__(self, ws, src: Source, body: Mapping, model):
        self.ws, self.src, self.body, self.model = ws, src, body, model
        self.layout = ws.source_layout(src.id)
        self.turns = split.turns(body)
        self.calls = 0                  # model calls made (memoised answers aside)
        self.failures = 0               # calls that failed
        self.failed: set[int] = set()   # sheets left unread by a failed call

    def __call__(self, i: int) -> int | None:
        ws, exam = self.ws, self.ws.exam
        o = self.turns.get(i, 0)
        image = None
        failed = False
        for lm in exam.landmarks:
            key = region_key('landmark', lm.name, digest(lm), self.layout, o, i)
            hit = ws.store.memo(self.src.id, key)
            if hit is None:
                if image is None:
                    image = page(ws, self.src, i, o)
                self.calls += 1
                complete = functools.partial(self.model.complete, tag=(self.src.id, f'{i}@{o}', 'landmark'))
                try:
                    got = ask(lm).call(complete, self.layout.crop(image, lm.region, exam.crop_pad))
                except Exception:
                    self.failures += 1
                    failed = True
                    continue
                hit = json.dumps(got)
                ws.store.set_memo(self.src.id, key, hit)
            p = page_of(lm, json.loads(hit))
            if p is not None:
                return p - 1
        if failed:
            self.failed.add(i)
        return None

    @property
    def all_failed(self) -> bool:
        return self.calls > 0 and self.failures == self.calls


def reader(ws, src: Source, body: Mapping, model) -> LandmarkReader | None:
    """The landmark reader for a source's sheets; None when there is no model or no landmark."""
    if model is None or not ws.exam.landmarks:
        return None
    return LandmarkReader(ws, src, body, model)


def reguess(ws, id: str, *, model=None, length: int | None = None,
            author: str | None = None, expect_rev: Any = ANY) -> Row:
    """Guess the plan again and write it (`split.guess`): every start and drop nobody
    has spoken for, checked with the landmarks when there is a model. `length`
    changes the expected length. `failed` in the plan counts the sheets a failed
    model call left unread."""
    src = source(ws, id)
    cur = plan(ws, id)
    if cur is None or not cur.body:
        body = split.new_plan(src.pages, length or ws.exam.pages, anchor=anchor(ws))
    else:
        body = dict(cur.body)
    resized = bool(length) and int(length) != body['length']
    if resized:
        body['length'] = int(length)
    body['anchor'] = anchor(ws)
    read = reader(ws, src, body, model)
    # Readings are positions within a submission: a new length makes them meaningless,
    # and a check reads every sheet again (cheaply: the model's answers are memoised).
    if resized or read is not None:
        body['readings'] = {}
    body = split.guess(body, read)
    body.pop('failed', None)
    if read is not None:
        for i in read.failed:       # unread, not read as nothing
            body['readings'].pop(str(i), None)
        if read.failed:
            body['failed'] = len(read.failed)
    body.pop(UNDONE, None)
    return _put(ws, id, body, origin='manual' if author else 'auto', author=author, expect_rev=expect_rev)


# A person's edits: (plan, sheet, delta) -> new plan. An edit of a submission names it by
# its first sheet, so that it means the submission the person saw whatever came before it.
_EDITS: dict[str, Callable[..., dict]] = {
    'confirm': lambda body, sheet, delta: split.confirm(body, _first(body, sheet)),
    'move': lambda body, sheet, delta: split.move(body, _first(body, sheet), _step(delta)),
    'cut': lambda body, sheet, delta: split.cut(body, sheet),
    'join': lambda body, sheet, delta: split.join(body, _first(body, sheet)),
    'drop': lambda body, sheet, delta: split.drop(body, sheet),
    'restore': lambda body, sheet, delta: split.restore(body, sheet),
    'extra': lambda body, sheet, delta: split.toggle_extra(body, sheet),
    'missing': lambda body, sheet, delta: split.add_missing(body, sheet),
    'unmissing': lambda body, sheet, delta: split.remove_missing(body, sheet),
    'shift': lambda body, sheet, delta: split.shift(body, sheet, _step(delta)),
    'turn_page': lambda body, sheet, delta: split.turn(body, [sheet]),
    'turn': lambda body, sheet, delta: split.turn(body, split.span(body, _first(body, sheet))),
    'turn_source': lambda body, sheet, delta: split.turn(body, split.turns(body)),
}
EDITS = tuple(_EDITS)


def _first(body: dict, sheet: Any) -> int:
    """The submission whose first sheet is `sheet`, else ValueError."""
    at = [body['sheets'][p]['i'] for p in split.starts(body)]
    if isinstance(sheet, bool) or sheet not in at:
        raise ValueError("not a first page any more")
    return at.index(sheet)


def _step(delta: Any) -> int:
    if delta not in (-1, 1):
        raise ValueError("move by one page")
    return delta


def edit(ws, id: str, op: str, *, expect_rev: Any, author: str | None,
         sheet: int | None = None, delta: int | None = None) -> Row:
    """One person's edit of a plan (see `core.split`), against the revision they saw.
    Of the submission whose first sheet is `sheet`: `confirm`, `move` (its first page,
    `delta`), `join` (with the next), `turn`. Of one `sheet`: `cut`, `drop`, `restore`,
    `extra` (toggle), `missing` (a missing page before it), `unmissing`, `shift` (`delta`
    places among the kept sheets), `turn_page`. And `turn_source` (every sheet). A turn
    is a quarter turn clockwise more."""
    source(ws, id)
    cur = plan(ws, id)
    if cur is None:
        raise KeyError(id)
    if expect_rev is not ANY and cur.rev != expect_rev:
        raise Conflict("the plan changed")
    if op not in _EDITS:
        raise ValueError("unknown edit")
    body = _EDITS[op](cur.body, sheet, delta)
    body.pop(UNDONE, None)
    return _put(ws, id, body, origin='manual', author=author, expect_rev=cur.rev)


UNDONE = 'undone'       # in a plan brought back by undo: the revision it was brought back from


def _before(ws, id: str, cur: Row | None) -> Row | None:
    """The plan one step back: the newest replaced version, or, when the current plan
    was itself brought back by undo, the newest one older than what it came from
    (so undoing twice goes back two steps instead of undoing the undo)."""
    limit = cur.body.get(UNDONE) if cur is not None else None
    return next((h for h in ws.store.history(id, '', PLAN) if limit is None or h.rev < limit), None)


def can_undo(ws, id: str) -> bool:
    return _before(ws, id, ws.store.get(id, '', PLAN)) is not None


def undo(ws, id: str, *, expect_rev: Any) -> Row:
    cur = ws.store.get(id, '', PLAN)
    old = _before(ws, id, cur)
    if old is None:
        raise ValueError("nothing to undo")
    body = {**old.body, UNDONE: old.body.get(UNDONE, old.rev)}
    return _put(ws, id, body, origin=old.origin, author=old.author, expect_rev=expect_rev)


# --- what is written, and accepting -------------------------------------------------

def outline(ws, id: str, body: Mapping | None = None) -> tuple[list[dict], list[str]]:
    """Per submission of the plan (or `body`), what is written: `id` (the written
    submission it is, by `split.link`, else None), `state` ('new': not written;
    'made': written as planned; 'changed': written otherwise), `pages` (its PDF's
    pages: `split.written`, None a missing page) and their turns. Then the written
    submissions no run is."""
    body = plan(ws, id).body if body is None else body
    made = {s: m for s, m in ws.store.made().items() if m.source == id}
    ids, gone = split.link(split.runs(body), {s: m.pages for s, m in made.items()})
    turns = split.turns(body)
    out = []
    for sid, lay in zip(ids, split.layouts(body, ws.exam.pages)):
        pages = split.written(lay)
        orient = [0 if i is None else turns[i] for i in pages]
        state = 'new' if sid is None else \
            'made' if (list(made[sid].pages), list(made[sid].orient)) == (pages, orient) else 'changed'
        out.append({'id': sid, 'state': state, 'pages': pages, 'orient': orient})
    return out, gone


def _next_ids(ws, taken: set[str]):
    """Fresh submission ids, never used before, each with the counter to save after it."""
    n = int(ws.store.setting('intake_next', 1))
    while True:
        sid = f's{n:04d}'
        n += 1
        if sid not in taken and not (ws.pdfs / f'{sid}.pdf').exists():
            taken.add(sid)
            yield sid, n


def sweep(ws) -> None:
    """Delete every file in `made/` that no `made` row names."""
    named = {m.file for m in ws.store.made().values()}
    for path in ws.made.iterdir():
        if path.name not in named:
            path.unlink(missing_ok=True)


SAID = ('values', 'decisions', 'name', 'regions')     # what `disturbed` names


def disturbed(ws, rows, gone) -> list[dict]:
    """The written submissions accepting (`outline`) would rewrite or remove that a
    person said something about, which a rewrite takes or leaves standing on pages
    it no longer has: `{'id', 'change': 'rewrite' | 'remove', 'said'}`, `said`
    naming (`SAID`) values a person typed or verified, decisions, the name (the
    pick) and its own regions or print area (overrides at its scope). What a reader
    read and the results computed from it are not asked about."""
    out = []
    for sid, change in [(r['id'], 'rewrite') for r in rows if r['state'] == 'changed'] + [(s, 'remove') for s in gone]:
        rows_ = ws.store.all(submission=sid, kinds=('value', 'decision'))
        said = [what for what, there in zip(SAID, (
            any(r.kind == 'value' and (r.origin == 'manual' or (r.body or {}).get('verified') == 'person')
                for r in rows_),
            any(r.kind == 'decision' for r in rows_),
            ws.store.get(sid, '', identity.WHO) is not None,
            bool(ws.store.overrides(overrides.scope(overrides.SUBMISSION, sid))))) if there]
        if said:
            out.append({'id': sid, 'change': change, 'said': said})
    return out


def repaged(ws, sid: str) -> None:
    """A submission's pages changed: what was read from the old ones goes (every value
    no person verified), and so does where things are on them (its own overrides:
    regions, print area). Values a person verified, decisions and the pick stay:
    they are about the student."""
    for r in ws.store.all(kinds=['value'], submission=sid):
        if (r.body or {}).get('verified') != 'person':
            ws.store.delete(sid, r.name, 'value')
    scope = overrides.scope(overrides.SUBMISSION, sid)
    for path in ws.store.overrides(scope):
        ws.store.clear_override(path, scope=scope)


def accept(ws, id: str, *, author: str | None = None, allow: Collection[str] = (), expect_rev: Any = ANY) -> dict:
    """A person (`author`) accepts the plan: every sheet becomes theirs (`split.accepted`),
    and its submissions are written. Unchanged ones are left alone; a written
    submission no run is any more is removed. Raises `Busy` if one that something
    was said or done about would be rewritten or removed (`disturbed`), unless the
    person said it may be (`allow`: its id).

    The PDFs are written under fresh names; one commit checks the plan has not
    changed and records the plan, `made` and the submissions; then the files no
    row names any more are deleted. Nothing named is ever overwritten, so a failure
    anywhere leaves the store and the files it names as they were or as accepted."""
    src = source(ws, id)
    art = plan(ws, id)
    if art is None:
        raise KeyError(id)
    if expect_rev is not ANY and art.rev != expect_rev:
        raise Conflict("the plan changed")
    sweep(ws)
    body = split.accepted(art.body)
    body.pop(UNDONE, None)
    rows, gone = outline(ws, id, body)
    busy = [b['id'] for b in disturbed(ws, rows, gone) if b['id'] not in allow]
    if busy:
        raise Busy(busy)
    fresh = _next_ids(ws, {s.id for s in ws.store.submissions()} | set(ws.store.made()))
    next_n = None
    report = {'new': [], 'rewritten': [], 'removed': list(gone), 'unchanged': 0}
    writes = []
    for r in rows:
        if r['state'] == 'made':
            report['unchanged'] += 1
            continue
        if r['id'] is None:
            r['id'], next_n = next(fresh)
        writes.append(r)
        report['new' if r['state'] == 'new' else 'rewritten'].append(r['id'])
    path, folder, now = source_path(ws, src), ws.made, _now()
    try:
        for r in writes:
            r['file'] = f'{r["id"]}-{secrets.token_hex(6)}.pdf'
            pdf_pages.write(path, list(zip(r['pages'], r['orient'])), folder / r['file'])
        with ws.store.transaction():
            cur = _put(ws, id, body, origin='manual', author=author, expect_rev=art.rev)
            if next_n is not None:
                ws.store.set_setting('intake_next', next_n)
            for r in writes:
                sid = r['id']
                ws.store.set_made(Made(sid, id, tuple(r['pages']), tuple(r['orient']), now, r['file']))
                ws.store.put_submissions([Submission(sid, '', '', '')])      # its file is `made`'s
                if r['state'] == 'changed':
                    repaged(ws, sid)
            for sid in gone:
                repaged(ws, sid)
                ws.store.delete_made(sid)
                ws.store.delete_submission(sid)
    finally:
        sweep(ws)
    report['rev'] = cur.rev
    return report
