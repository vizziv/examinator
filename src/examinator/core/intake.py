"""Intake: from stacks of scans to one PDF per submission.

    WORKSPACE/sources/ID.pdf     each scanned PDF as added, never changed (ID: start of its SHA-256)
    WORKSPACE/pdfs/s0001.pdf     one per submission, written when a plan is accepted

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
its extra sheets in order (`split.lay_out`), written upright. A submission
that would change or disappear while it has grading work (identity fields are
not grading work) is not touched unless the caller insists (`force`): `Busy`
names them. A rewritten submission's pages are new: what was remembered from
the old ones goes, and so does every value a reader wrote that no person typed
or verified (identity fields and grading alike): those fields are not read.
What a person typed or verified, and the pick, stay.

The store says "written" only about files in place: files are moved into
`pdfs/` before the store records them, and the ids being placed are noted
first (`PENDING`), so a crash anywhere leaves a state the next Accept repairs:
a placed file the store never recorded is deleted, not taken for a submission
(`unsettled`, which `Workspace.sync` skips), and a submission whose file may
not match its record, or is missing, counts as not written.

Who a submission is stays an attribute: the id is opaque and never changes.
The roster, the identity fields, and the person a grader picks are
`core.identity`.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from ..pdf import pages as pdf_pages, render
from ..pdf.orient import turn
from . import identity, split
from .ask import Printed
from .defs import Landmark, digest
from .reading import normalize, region_key
from .store import ANY, Row, Conflict, Made, Source

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
    return Printed(lm.ask, reads={k: str(v) for k, v in (lm.reads or {}).items()})


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
    model call left unread; a check where every call failed does not count as checked."""
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
    body['checked'] = read is not None and not read.all_failed
    body.pop(UNDONE, None)
    return ws.store.write(id, '', PLAN, body, origin='manual' if author else 'auto', author=author,
                          expect_rev=expect_rev)


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
    return ws.store.write(id, '', PLAN, body, origin='manual', author=author, expect_rev=cur.rev)


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
    return ws.store.write(id, '', PLAN, body, origin=old.origin, author=old.author, expect_rev=expect_rev)


# --- what is written, and accepting -------------------------------------------------

PENDING = 'intake_pending'      # setting: ids whose files are being placed or removed


def _pending(ws) -> set[str]:
    return set(ws.store.setting(PENDING, []))


def unsettled(ws) -> set[str]:
    """Files in `pdfs/` an Accept placed but never recorded (or was removing): not submissions."""
    made = ws.store.made()
    return {s for s in _pending(ws) if s not in made}


def outline(ws, id: str, body: Mapping | None = None) -> tuple[list[dict], list[str]]:
    """Per submission of the plan (or `body`), what is written: `id` (the written
    submission it is, by `split.link`, else None), `state` ('new': not written;
    'made': written as planned; 'changed': written otherwise, or its file is
    missing or may not match its record), `pages` (its PDF's pages: `split.written`,
    None a missing page) and their turns. Then the written submissions no run is."""
    body = plan(ws, id).body if body is None else body
    made = {s: m for s, m in ws.store.made().items() if m.source == id}
    ids, gone = split.link(split.runs(body), {s: m.pages for s, m in made.items()})
    pending, turns = _pending(ws), split.turns(body)
    out = []
    for sid, lay in zip(ids, split.layouts(body, ws.exam.pages)):
        pages = split.written(lay)
        orient = [0 if i is None else turns[i] for i in pages]
        if sid is None:
            state = 'new'
        else:
            m = made[sid]
            same = list(m.pages) == pages and list(m.orient) == orient
            state = 'made' if same and sid not in pending and (ws.pdfs / f'{sid}.pdf').exists() else 'changed'
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


def _repair(ws) -> None:
    """What an interrupted Accept left: placed files the store never recorded are
    deleted (the rest stay pending, so they count as not written), and temporary
    files are swept."""
    stray = unsettled(ws)
    for sid in stray:
        (ws.pdfs / f'{sid}.pdf').unlink(missing_ok=True)
    if stray:
        ws.store.set_setting(PENDING, sorted(_pending(ws) - stray))
    for tmp in ws.pdfs.glob('.*.part'):
        tmp.unlink(missing_ok=True)


def disturbed(ws, rows, gone) -> list[str]:
    """The submissions with grading work that accepting (`outline`) would change or remove."""
    def work(sid):
        return ws.store.has_work(sid, besides=identity.IDENTITY + '/')
    return [r['id'] for r in rows if r['state'] == 'changed' and work(r['id'])] + [s for s in gone if work(s)]


def forget_reads(ws, sid: str) -> None:
    """A submission's pages changed: drop what was read from the old ones (the memo,
    and values a reader wrote that no person typed or verified)."""
    ws.store.clear_memo(sid)
    for r in ws.store.all(kinds=['value'], submission=sid):
        if r.origin != 'manual' and (r.body or {}).get('verified') != 'person':
            ws.store.delete(sid, r.name, 'value')


def accept(ws, id: str, *, force: bool = False, expect_rev: Any = ANY) -> dict:
    """Accept the plan: every sheet becomes a person's (`split.accepted`), and its
    submissions are written into `pdfs/`. Unchanged ones are left alone; a
    written submission no run is any more is removed. Raises `Busy` if a
    submission with grading work would change or go, unless `force`.

    In order: the PDFs are written to temporary files; the ids about to be placed
    are noted (with the id counter), in one commit that also checks the plan has
    not changed; the files are moved into place; the store records them (the
    plan, `made`, the submissions) in one commit; removed files are deleted; the
    note is cleared. A crash at any point is repaired by the next Accept."""
    src = source(ws, id)
    art = plan(ws, id)
    if art is None:
        raise KeyError(id)
    if expect_rev is not ANY and art.rev != expect_rev:
        raise Conflict("the plan changed")
    _repair(ws)
    body = split.accepted(art.body)
    body.pop(UNDONE, None)
    rows, gone = outline(ws, id, body)
    busy = disturbed(ws, rows, gone)
    if busy and not force:
        raise Busy(busy)
    fresh = _next_ids(ws, {s.id for s in ws.store.submissions()} | set(ws.store.made()))
    next_n = None
    report = {'new': [], 'rewritten': [], 'removed': [], 'unchanged': 0}
    writes = []
    for r in rows:
        if r['state'] == 'made':
            report['unchanged'] += 1
            continue
        if r['id'] is None:
            r['id'], next_n = next(fresh)
        writes.append(r)
        report['new' if r['state'] == 'new' else 'rewritten'].append(r['id'])
    path = source_path(ws, src)
    tmps: dict[str, Path] = {}
    try:
        for r in writes:
            tmps[r['id']] = ws.pdfs / f'.{r["id"]}.{os.getpid()}.{threading.get_ident()}.part'
            pdf_pages.write(path, list(zip(r['pages'], r['orient'])), tmps[r['id']])
        with ws.store.transaction():
            if ws.store.get(id, '', PLAN).rev != art.rev:
                raise Conflict("the plan changed")
            if next_n is not None:
                ws.store.set_setting('intake_next', next_n)
            ws.store.set_setting(PENDING, sorted(_pending(ws) | set(tmps) | set(gone)))
        for sid, tmp in tmps.items():
            os.replace(tmp, ws.pdfs / f'{sid}.pdf')
    finally:
        for tmp in tmps.values():
            tmp.unlink(missing_ok=True)
    now = _now()
    with ws.store.transaction():
        if body != art.body:
            cur = ws.store.write(id, '', PLAN, body, origin='manual', author=art.author, expect_rev=art.rev)
        elif ws.store.get(id, '', PLAN).rev != art.rev:
            raise Conflict("the plan changed")
        else:
            cur = art
        for r in writes:
            sid = r['id']
            ws.store.set_made(Made(sid, id, tuple(r['pages']), tuple(r['orient']), now))
            ws.store.put_submissions([identity.submission(ws, sid, f'{sid}.pdf')])
            forget_reads(ws, sid)
        for sid in gone:
            ws.store.delete_made(sid)
            ws.store.delete_submission(sid)
            report['removed'].append(sid)
    for sid in gone:
        (ws.pdfs / f'{sid}.pdf').unlink(missing_ok=True)
    ws.store.set_setting(PENDING, sorted(_pending(ws) - set(tmps) - set(gone)))
    report['rev'] = cur.rev
    return report

