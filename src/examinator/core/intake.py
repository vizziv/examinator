"""Intake: from stacks of scans to one PDF per submission.

    WORKSPACE/sources/ID.pdf     each scanned PDF as added, never changed (ID: start of its SHA-256)
    WORKSPACE/pdfs/s0001.pdf     one per submission, written when a plan is accepted

A source's split plan (`core.split`) is the row `(source id, '', 'split')`,
so edits keep history and carry revisions like everything else a grader edits.
The plan is one ordered list of the source's sheets with their marks; a
sheet's turn (quarter turns clockwise) is its own and nothing else's. Pages are
written upright, and what intake made records each sheet's turn
(`Made.orient`); nothing downstream turns a page again.

Accepting a plan writes the PDFs whose sheets (or orientation) changed and
records, per submission, which sheets of which source it holds (`Made.pages`).
A submission's PDF is the exam's printed pages in order, page p at index p - 1,
with a blank placeholder where a page is missing (None in `Made.pages`), then
its extra sheets in order (`split.lay_out`). Nothing downstream offsets a page. A submission
that would change or disappear while it has grading work (identity fields are
not grading work) is not touched unless the caller insists (`force`): `Busy`
names them. A rewritten submission's remembered readings, and what a reader
read in its identity fields, are dropped.

Who a submission is stays an attribute: the id is opaque and never changes.
The roster, the identity fields, and the person a grader picks are
`core.identity`.
"""
from __future__ import annotations

import copy
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

def expected_length(ws) -> int:
    """Sheets per submission unless the plan says otherwise: the exam's pages."""
    return ws.exam.pages


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
    """Guess the plan again and write it: every unconfirmed start, checked with the
    landmarks when there is a model. A person's confirmed starts, the order and
    every sheet's own marks stay. `length` changes the expected length.
    `failed` in the plan counts the sheets a failed model call left unread; a
    check where every call failed does not count as checked."""
    src = source(ws, id)
    cur = plan(ws, id)
    if cur is None or not cur.body:
        body = split.new_plan(src.pages, length or expected_length(ws), pages=ws.exam.pages, anchor=anchor(ws))
    else:
        body = dict(cur.body)
    resized = bool(length) and int(length) != body['length']
    if resized:
        body['length'] = int(length)
    body['anchor'], body['pages'] = anchor(ws), ws.exam.pages
    read = reader(ws, src, body, model)
    # Readings are positions within a submission: a new length makes them meaningless,
    # and a check reads every sheet again (cheaply: the model's answers are memoised).
    if resized or read is not None:
        body['readings'] = {}
    body = split.refill(body, read)
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


# A person's edits: (plan, k, sheet, delta) -> new plan.
_EDITS: dict[str, Callable[..., dict]] = {
    'confirm': lambda body, k, sheet, delta: split.confirm(body, k),
    'move': lambda body, k, sheet, delta: split.move(body, k, _step(delta)),
    'cut': lambda body, k, sheet, delta: split.cut(body, sheet),
    'join': lambda body, k, sheet, delta: split.join(body, k),
    'drop': lambda body, k, sheet, delta: split.drop(body, sheet),
    'restore': lambda body, k, sheet, delta: split.restore(body, sheet),
    'extra': lambda body, k, sheet, delta: split.toggle_extra(body, sheet),
    'missing': lambda body, k, sheet, delta: split.add_missing(body, sheet),
    'unmissing': lambda body, k, sheet, delta: split.remove_missing(body, sheet),
    'shift': lambda body, k, sheet, delta: split.shift(body, sheet, _step(delta)),
    'turn_page': lambda body, k, sheet, delta: split.turn(body, [sheet]),
    'turn': lambda body, k, sheet, delta: split.turn(body, split.span(body, k)),
    'turn_source': lambda body, k, sheet, delta: split.turn(body, split.turns(body)),
}
EDITS = tuple(_EDITS)


def _step(delta: Any) -> int:
    if delta not in (-1, 1):
        raise ValueError("move by one page")
    return delta


def edit(ws, id: str, op: str, *, expect_rev: Any, author: str | None, k: int | None = None,
         sheet: int | None = None, delta: int | None = None) -> Row:
    """One person's edit of a plan (see `core.split`), against the revision they saw:
    `confirm`, `move` (submission `k`'s first page, `delta`), `cut`, `join`, `drop`,
    `restore`, and of one `sheet`: `extra` (toggle), `missing` (a missing page
    before it), `unmissing`, `shift` (`delta` places in the list). Turning, a
    quarter turn clockwise more for each sheet: `turn_page` (one `sheet`), `turn`
    (every sheet of submission `k`), `turn_source` (every sheet)."""
    source(ws, id)
    cur = plan(ws, id)
    if cur is None:
        raise KeyError(id)
    if expect_rev is not ANY and cur.rev != expect_rev:
        raise Conflict("the plan changed")
    if op not in _EDITS:
        raise ValueError("unknown edit")
    body = _EDITS[op]({**cur.body, 'pages': ws.exam.pages}, k, sheet, delta)
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


# --- accepting: writing submissions ---------------------------------------------------

def _sheets_of(body: Mapping) -> list[tuple[list[int | None], list[int]]]:
    """Per submission, its PDF's pages (`split.written`: None, a missing page) and their turns."""
    out, turns = [], split.turns(body)
    for lay in split.layouts(body):
        pages = split.written(lay)
        out.append((pages, [0 if i is None else turns[i] for i in pages]))
    return out


def state(ws, id: str) -> list[str]:
    """Per entry: 'new' (not written yet), 'made' (written as planned), 'changed'."""
    art = plan(ws, id)
    made = ws.store.made()
    out = []
    for e, (sheets, orients) in zip(split.entries(art.body), _sheets_of(art.body)):
        m = made.get(e.get('id') or '')
        if m is None or m.source != id:
            out.append('new')
        else:
            out.append('made' if list(m.pages) == sheets and m.orient.get('sheets') == orients else 'changed')
    return out


def removed(ws, id: str) -> list[str]:
    """Submissions written from this source that the plan no longer has: no
    entry carries their id, or the entry that does has no pages left."""
    art = plan(ws, id)
    ids = {e.get('id') for e, sheets in zip(split.entries(art.body), split.runs(art.body)) if sheets}
    return sorted(s for s, m in ws.store.made().items() if m.source == id and s not in ids)


def _next_ids(ws, taken: set[str]):
    """Fresh submission ids, never used before, each with the counter to save after it."""
    n = int(ws.store.setting('intake_next', 1))
    while True:
        sid = f's{n:04d}'
        n += 1
        if sid not in taken and not (ws.pdfs / f'{sid}.pdf').exists():
            taken.add(sid)
            yield sid, n


def accept(ws, id: str, *, force: bool = False,
           expect_rev: Any = ANY) -> dict:
    """Write the plan's submissions into `pdfs/`. Unchanged ones are left alone;
    a submission whose entry lost all its pages is removed (the entry keeps no id).
    Raises `Busy` if a submission with grading work would change or go, unless `force`.

    The PDFs are written to temporary files first and moved into place only once
    the store has recorded them, so a plan edit that lands in between (`Conflict`)
    leaves `pdfs/` as it was."""
    src = source(ws, id)
    art = plan(ws, id)
    if art is None:
        raise KeyError(id)
    if expect_rev is not ANY and art.rev != expect_rev:
        raise Conflict("the plan changed")
    body = copy.deepcopy(art.body)
    made = ws.store.made()
    states = state(ws, id)
    gone = removed(ws, id)
    entries = split.entries(body)
    sheets = _sheets_of(body)
    def work(sid):
        return ws.store.has_work(sid, besides=identity.IDENTITY + '/')
    kept = split.runs(body)
    busy = [e['id'] for e, st, mine in zip(entries, states, kept)
            if st == 'changed' and mine and work(e['id'])]
    busy += [s for s in gone if work(s)]
    if busy and not force:
        raise Busy(busy)
    fresh = _next_ids(ws, {s.id for s in ws.store.submissions()} | set(made))
    next_n = None
    report = {'new': [], 'rewritten': [], 'removed': [], 'unchanged': 0, 'empty': 0}
    writes = []
    for k, (e, st, mine, (pages, orients)) in enumerate(zip(entries, states, kept, sheets)):
        e['confirmed'] = True       # accepted: every start (and every proposed mark) is now a person's decision
        split.settle(body, k)
        if not mine:
            if e.get('id') in gone:
                e['id'] = None      # its submission is removed below
            report['empty'] += 1
            continue
        if st == 'made':
            report['unchanged'] += 1
            continue
        if not e.get('id') or (e['id'] in made and made[e['id']].source != id):
            e['id'], next_n = next(fresh)
        writes.append((e['id'], pages, orients))
        report['new' if st == 'new' else 'rewritten'].append(e['id'])
    path = source_path(ws, src)
    tmps: dict[str, Path] = {}
    try:
        for sid, pages, orients in writes:
            tmps[sid] = ws.pdfs / f'.{sid}.{os.getpid()}.{threading.get_ident()}.part'
            pdf_pages.write(path, list(zip(pages, orients)), tmps[sid])
        now = _now()
        with ws.store.transaction():
            body.pop(UNDONE, None)
            cur = ws.store.write(id, '', PLAN, body, origin=art.origin, author=art.author, expect_rev=art.rev)
            if next_n is not None:
                ws.store.set_setting('intake_next', next_n)
            for sid, pages, orients in writes:
                ws.store.set_made(Made(sid, id, tuple(pages), {'sheets': orients}, now))
                ws.store.put_submissions([identity.submission(ws, sid, f'{sid}.pdf')])
                ws.store.clear_memo(sid)        # readings of the old pages
                identity.forget(ws, sid)        # and what a reader read in the identity fields
            for sid in gone:
                ws.store.delete_made(sid)
                ws.store.delete_submission(sid)
                report['removed'].append(sid)
    except BaseException:
        for tmp in tmps.values():
            tmp.unlink(missing_ok=True)
        raise
    for sid, tmp in tmps.items():
        os.replace(tmp, ws.pdfs / f'{sid}.pdf')
    for sid in gone:
        (ws.pdfs / f'{sid}.pdf').unlink(missing_ok=True)
    report['rev'] = cur.rev
    return report
