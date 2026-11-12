"""Intake: from stacks of scans to one PDF per submission.

    WORKSPACE/sources/ID.pdf     each scanned PDF as added, never changed (ID: start of its SHA-256)
    WORKSPACE/pdfs/s0001.pdf     one per submission, written when a plan is accepted

A source's split plan (`core.split`) is the row `(source id, '', 'split')`,
so edits keep history and carry revisions like everything else a grader edits.
The plan also says how the sheets are turned: an orientation for the whole
source, and optionally one per submission (`core.split` entries' `orient`).
Pages are written upright; after intake nothing knows orientation existed.

Accepting a plan writes the PDFs whose sheets (or orientation) changed and
records, per submission, which sheets of which source it holds. A submission
that would change or disappear while it has grading work is not touched unless
the caller insists (`force`): `Busy` names them.

Who a submission is stays an attribute: the id is opaque and never changes.
The roster, the model's reading of the name and ID boxes, and the person a
grader picks are `core.identity`.
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
from ..pdf.layout import Layout
from ..pdf.orient import HALF, MIRROR, QUARTER, Orient
from . import identity, reading, split
from .defs import Landmark, digest
from .reading import normalize
from .printed import PRINTED_PROMPT
from .store import ANY, Row, Conflict, Made, Source

PLAN = 'split'
LANDMARK_PROMPT = PRINTED_PROMPT    # a landmark, like a printed field, is printed text


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
    """Sheets per submission unless the plan says otherwise: the exam's pages. A raw
    stack has no cover sheets; the scanning services that add them also split."""
    return ws.exam.pages


def cover(ws, body: Mapping | None = None) -> int:
    """Leading sheets of each submission that are not exam pages: whatever the
    plan's length has beyond the exam's pages."""
    return max(0, int(body['length']) - ws.exam.pages) if body else 0


def anchor(ws, cover: int = 0) -> int:
    """The first position in a submission that carries a landmark."""
    first = []
    for lm in ws.exam.landmarks:
        first += list(lm.reads.values()) if lm.reads else [lm.region.page]
    return cover + (min(first) if first else 1) - 1


def plan(ws, id: str) -> Row | None:
    return ws.store.get(id, '', PLAN)


def sheet_orient(body: Mapping, sheet: int) -> Orient:
    """The orientation of one sheet: its submission's own, else the source's."""
    entry = None
    for e in body.get('entries') or ():
        if e['start'] <= sheet:
            entry = e
    if entry is not None and entry.get('orient') is not None:
        return Orient.from_json(entry['orient'])
    return Orient.from_json(body.get('orient'))


def page(ws, src: Source, sheet: int, body: Mapping | None = None) -> Image.Image:
    """One sheet as the plan says it should be turned."""
    im = ws.renderer.page(source_path(ws, src), sheet)
    o = sheet_orient(body or {}, sheet)
    return im if o.identity else o.apply(im)


def page_of(lm: Landmark, text: str | None) -> int | None:
    """The printed page a landmark reading points to."""
    if text is None:
        return None
    if lm.reads:
        table = {normalize(k): v for k, v in lm.reads.items()}
        return table.get(normalize(text))
    digits = ''.join(ch for ch in normalize(text) if ch.isdigit())
    return int(digits) if digits and len(digits) <= 4 else None


class LandmarkReader:
    """Reads one sheet's position from the exam's landmarks (first that answers
    wins), through the model, memoised per source, sheet, landmark, orientation
    and scan window. A failed call is no reading and is not remembered: the
    sheets it left unread are `failed`; `calls` counts the calls made."""

    def __init__(self, ws, src: Source, body: Mapping, model):
        self.ws, self.src, self.body, self.model = ws, src, body, model
        self.layout = Layout(0, ws.scan_window)
        self.lead = cover(ws, body)
        self.calls = 0                  # model calls made (memoised answers aside)
        self.failures = 0               # calls that failed
        self.failed: set[int] = set()   # sheets left unread by a failed call

    def __call__(self, i: int) -> int | None:
        ws, exam = self.ws, self.ws.exam
        o = sheet_orient(self.body, i)
        image = None
        failed = False
        for lm in exam.landmarks:
            key = reading.memo_key('landmark', lm.name, digest(lm), list(ws.scan_window), o.code, i)
            hit = ws.store.memo(self.src.id, key)
            if hit is None:
                if image is None:
                    image = page(ws, self.src, i, self.body)
                self.calls += 1
                complete = functools.partial(self.model.complete, tag=(self.src.id, f'{i}@{o.code}', 'landmark'))
                try:
                    got = reading.ask(complete, LANDMARK_PROMPT, lm.ask,
                                      self.layout.crop(image, lm.region, exam.crop_pad), lm.reads or ())
                except Exception:
                    self.failures += 1
                    failed = True
                    continue
                hit = json.dumps(got)
                ws.store.set_memo(self.src.id, key, hit)
            p = page_of(lm, json.loads(hit))
            if p is not None:
                return self.lead + p - 1
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


def reguess(ws, id: str, *, model=None, length: int | None = None, fresh: bool = False,
            author: str | None = None, expect_rev: Any = ANY) -> Row:
    """Guess the plan again and write it: every unconfirmed start (all of them with
    `fresh`), checked with the landmarks when there is a model. A person's
    confirmed starts and dropped sheets stay. `length` changes the expected length.
    `failed` in the plan counts the sheets a failed model call left unread; a
    check where every call failed does not count as checked."""
    src = source(ws, id)
    cur = plan(ws, id)
    if cur is None or fresh or not cur.body:
        body = split.new_plan(src.pages, length or expected_length(ws), anchor=anchor(ws))
        if cur is not None:
            body['orient'] = (cur.body or {}).get('orient', body['orient'])
    else:
        body = dict(cur.body)
    resized = bool(length) and int(length) != body['length']
    if resized:
        body['length'] = int(length)
    body['anchor'] = anchor(ws, cover(ws, body))
    read = reader(ws, src, body, model)
    # Readings are positions within a submission: a new length makes them meaningless,
    # and a check reads every sheet again (cheaply: the model's answers are memoised).
    if resized or read is not None:
        body['readings'] = {}
    body = split.refill(body, src.pages, read)
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


# The plain split edits: (plan, n, k, sheet, delta) -> new plan.
_SPLIT_EDITS: dict[str, Callable[..., dict]] = {
    'confirm': lambda body, n, k, sheet, delta: split.confirm(body, k, n),
    'move': lambda body, n, k, sheet, delta: split.move(body, k, _step(delta), n),
    'cut': lambda body, n, k, sheet, delta: split.cut(body, sheet, n),
    'join': lambda body, n, k, sheet, delta: split.join(body, k),
    'drop': lambda body, n, k, sheet, delta: split.drop(body, sheet, n),
    'restore': lambda body, n, k, sheet, delta: split.restore(body, sheet, n),
}
_SOURCE_TURNS = {'turn_source': QUARTER, 'flip_source': MIRROR}
_ENTRY_TURNS = {'turn': HALF, 'flip': MIRROR, 'upright': None}
EDITS = (*_SPLIT_EDITS, *_SOURCE_TURNS, *_ENTRY_TURNS)


def _step(delta: Any) -> int:
    if delta not in (-1, 1):
        raise ValueError("move by one page")
    return delta


def _clear_readings(body: dict, sheets) -> None:
    sheets = {str(i) for i in sheets}
    body['readings'] = {k: v for k, v in (body.get('readings') or {}).items() if k not in sheets}


def _turn_source(body: Mapping, n: int, by: Orient) -> dict:
    """The whole source turned `by` more; every reading is stale."""
    body = copy.deepcopy(dict(body))
    body['orient'] = Orient.from_json(body.get('orient')).then(by).to_json()
    _clear_readings(body, range(n))
    return body


def _turn_entry(body: Mapping, n: int, k: Any, by: Orient | None) -> dict:
    """Submission `k` turned `by` more, or back to the source's orientation (None)."""
    body = copy.deepcopy(dict(body))
    e = body['entries'][split.entry_index(body, k)]
    base = Orient.from_json(body.get('orient'))
    mine = Orient.from_json(e['orient']) if e.get('orient') is not None else base
    new = base if by is None else mine.then(by)
    if new == base:
        e.pop('orient', None)
    else:
        e['orient'] = new.to_json()
    _clear_readings(body, split.pages(body, n)[k])
    return body


def edit(ws, id: str, op: str, *, expect_rev: Any, author: str | None, k: int | None = None,
         sheet: int | None = None, delta: int | None = None) -> Row:
    """One person's edit of a plan (see `core.split`), against the revision they saw.
    Orientation: `turn_source` (a quarter turn), `flip_source` (mirror), and for
    submission `k`: `turn` (a half turn), `flip`, `upright` (back to the source's)."""
    src = source(ws, id)
    cur = plan(ws, id)
    if cur is None:
        raise KeyError(id)
    if expect_rev is not ANY and cur.rev != expect_rev:
        raise Conflict("the plan changed")
    n = src.pages
    if op in _SPLIT_EDITS:
        body = _SPLIT_EDITS[op](cur.body, n, k, sheet, delta)
    elif op in _SOURCE_TURNS:
        body = _turn_source(cur.body, n, _SOURCE_TURNS[op])
    elif op in _ENTRY_TURNS:
        body = _turn_entry(cur.body, n, k, _ENTRY_TURNS[op])
    else:
        raise ValueError("unknown edit")
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

def _sheets_of(body: Mapping, n: int) -> list[tuple[list[int], list[dict]]]:
    out = []
    for sheets in split.pages(body, n):
        out.append((sheets, [sheet_orient(body, i).to_json() for i in sheets]))
    return out


def state(ws, id: str) -> list[str]:
    """Per entry: 'new' (not written yet), 'made' (written as planned), 'changed'."""
    src = source(ws, id)
    art = plan(ws, id)
    made = ws.store.made()
    out = []
    for e, (sheets, orients) in zip(art.body['entries'], _sheets_of(art.body, src.pages)):
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
    src = source(ws, id)
    ids = {e.get('id') for e, sheets in zip(art.body['entries'], split.pages(art.body, src.pages)) if sheets}
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


def accept(ws, id: str, *, author: str | None = None, force: bool = False,
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
    entries = body['entries']
    sheets = _sheets_of(body, src.pages)
    busy = [e['id'] for e, st, (pages, _) in zip(entries, states, sheets)
            if st == 'changed' and pages and ws.store.has_work(e['id'])]
    busy += [s for s in gone if ws.store.has_work(s)]
    if busy and not force:
        raise Busy(busy)
    fresh = _next_ids(ws, {s.id for s in ws.store.submissions()} | set(made))
    next_n = None
    report = {'new': [], 'rewritten': [], 'removed': [], 'unchanged': 0, 'empty': 0}
    writes = []
    for e, st, (pages, orients) in zip(entries, states, sheets):
        e['confirmed'] = True       # accepted: every start is now a person's decision
        if not pages:
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
            pdf_pages.write(path, [(i, Orient.from_json(o)) for i, o in zip(pages, orients)], tmps[sid])
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
                ws.store.delete(sid, '', identity.READ)     # and what the model read on them
                # The written PDF starts with as many cover sheets as the plan has, whatever the workspace's default.
                kept = ws.store.get(sid, '', 'layout')
                ws.set_layout(sid, cover_pages=cover(ws, body), author=author,
                              scan_window=(kept.body or {}).get('scan_window') if kept else None)
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
