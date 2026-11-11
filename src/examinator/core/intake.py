"""Intake: from stacks of scans to one PDF per submission.

    WORKSPACE/sources/ID.pdf     each scanned PDF as added, never changed (ID: start of its SHA-256)
    WORKSPACE/pdfs/s0001.pdf     one per submission, written when a plan is accepted

A source's split plan (`core.split`) is the artifact `(source id, '', 'split')`,
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

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from ..pdf import pages as pdf_pages
from ..pdf.layout import Layout
from ..pdf.orient import HALF, MIRROR, QUARTER, Orient
from . import identity, split
from .defs import Landmark, digest
from .store import ANY, Artifact, Conflict, Made, Source
from .variants import CUE_PROMPT, json_value, normalize

PLAN = 'split'
NO_WORK = (identity.WHO, identity.READ, 'layout')       # kinds that are not grading work


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
        n = pdf_pages.page_count(path)
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
    propose(ws, src.id)
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


def plan(ws, id: str) -> Artifact | None:
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


def page_of(lm: Landmark, reading: str | None) -> int | None:
    """The printed page a landmark reading points to."""
    if reading is None:
        return None
    if lm.reads:
        table = {normalize(k): v for k, v in lm.reads.items()}
        return table.get(normalize(reading))
    digits = ''.join(ch for ch in normalize(reading) if ch.isdigit())
    return int(digits) if digits and len(digits) <= 4 else None


LANDMARK_PROMPT = CUE_PROMPT


def _ask(model, image: Image.Image, lm: Landmark, tag: tuple[str, str, str]) -> str | None:
    prompt = LANDMARK_PROMPT.format(ask=lm.ask)
    if lm.reads:
        prompt += "\nThe value is expected to be one of: " + " | ".join(lm.reads) + "."
    return json_value(model.complete(prompt, images=[image], expect='json', tag=tag))


def reader(ws, src: Source, body: Mapping, model) -> split.Reader | None:
    """Reads one sheet's position from the exam's landmarks (first that answers
    wins), through the model, memoised per source, sheet, landmark, orientation
    and scan window. None when there is no model or no landmark."""
    exam = ws.exam
    if model is None or not exam.landmarks:
        return None
    layout = Layout(0, ws.scan_window)
    lead = cover(ws, body)

    def read(i: int) -> int | None:
        o = sheet_orient(body, i)
        image = None
        for lm in exam.landmarks:
            key = f'landmark:{lm.name}:{digest(lm)}:{list(ws.scan_window)}:{o.turn}{int(o.flip)}:{i}'
            hit = ws.store.memo(src.id, key)
            if hit is None:
                if image is None:
                    image = page(ws, src, i, body)
                try:
                    reading = _ask(model, layout.crop(image, lm.region, exam.crop_pad), lm,
                                   (src.id, f'{i}@{o.turn}{int(o.flip)}', 'landmark'))
                except Exception:       # a failed call is no reading, and is not remembered
                    continue
                hit = json.dumps(reading)
                ws.store.set_memo(src.id, key, hit)
            p = page_of(lm, json.loads(hit))
            if p is not None:
                return lead + p - 1
        return None
    return read


def propose(ws, id: str, *, model=None, length: int | None = None, fresh: bool = False,
            author: str | None = None, expect_rev: Any = ANY) -> Artifact:
    """Guess the plan again: every unconfirmed start (all of them with `fresh`),
    checked with the landmarks when there is a model. A person's confirmed
    starts and dropped sheets stay. `length` changes the expected length."""
    src = source(ws, id)
    cur = plan(ws, id)
    body = dict(cur.body) if cur is not None and not fresh else {}
    if not body:
        body = split.new_plan(src.pages, length or expected_length(ws), anchor=anchor(ws))
        if cur is not None:
            body['orient'] = cur.body.get('orient', body['orient'])
    if length:
        if length != body['length']:
            body['readings'] = {}
        body['length'] = int(length)
    body['anchor'] = anchor(ws, cover(ws, body))
    read = reader(ws, src, body, model)
    if read is not None:
        body['readings'] = {}       # read again (cheap: the model's answers are memoised)
    body = split.refill(body, src.pages, read)
    body['checked'] = read is not None
    return ws.store.write(id, '', PLAN, body, origin='manual' if author else 'auto', author=author,
                          expect_rev=expect_rev)


EDITS = ('confirm', 'move', 'cut', 'join', 'drop', 'restore', 'turn_source', 'flip_source',
         'turn', 'flip', 'upright')


def _clear_readings(body: dict, sheets) -> None:
    sheets = {str(i) for i in sheets}
    body['readings'] = {k: v for k, v in (body.get('readings') or {}).items() if k not in sheets}


def edit(ws, id: str, op: str, *, expect_rev: Any, author: str | None, k: int | None = None,
         sheet: int | None = None, delta: int | None = None) -> Artifact:
    """One person's edit of a plan (see `core.split`), against the revision they saw.
    Orientation: `turn_source` (a quarter turn), `flip_source` (mirror), and for
    submission `k`: `turn` (a half turn), `flip`, `upright` (back to the source's)."""
    src = source(ws, id)
    cur = plan(ws, id)
    if cur is None:
        raise KeyError(id)
    if expect_rev is not ANY and cur.rev != expect_rev:
        raise Conflict("the plan changed")
    n, body = src.pages, cur.body
    if op == 'confirm':
        body = split.confirm(body, k, n)
    elif op == 'move':
        if delta not in (-1, 1):
            raise ValueError("move by one page")
        body = split.move(body, k, delta, n)
    elif op == 'cut':
        body = split.cut(body, sheet, n)
    elif op == 'join':
        body = split.join(body, k, n)
    elif op == 'drop':
        body = split.drop(body, sheet, n)
    elif op == 'restore':
        body = split.restore(body, sheet, n)
    elif op in ('turn_source', 'flip_source'):
        body = json.loads(json.dumps(body))
        body['orient'] = Orient.from_json(body.get('orient')).then(
            QUARTER if op == 'turn_source' else MIRROR).to_json()
        _clear_readings(body, range(n))
    elif op in ('turn', 'flip', 'upright'):
        body = json.loads(json.dumps(body))
        if not isinstance(k, int) or not 0 <= k < len(body['entries']):
            raise ValueError("no such submission")
        e = body['entries'][k]
        base = Orient.from_json(body.get('orient'))
        mine = Orient.from_json(e['orient']) if e.get('orient') is not None else base
        new = base if op == 'upright' else mine.then(HALF if op == 'turn' else MIRROR)
        if new == base:
            e.pop('orient', None)
        else:
            e['orient'] = new.to_json()
        _clear_readings(body, split.pages(body, n)[k])
    else:
        raise ValueError("unknown edit")
    return ws.store.write(id, '', PLAN, body, origin='manual', author=author, expect_rev=cur.rev)


def undo(ws, id: str, *, expect_rev: Any) -> Artifact:
    hist = ws.store.history(id, '', PLAN)
    if not hist:
        raise ValueError("nothing to undo")
    return ws.store.restore(id, '', PLAN, hist[0].rev, expect_rev=expect_rev)


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
    """Submissions written from this source that the plan no longer has."""
    art = plan(ws, id)
    ids = {e.get('id') for e in art.body['entries']}
    return sorted(s for s, m in ws.store.made().items() if m.source == id and s not in ids)


def _next_id(ws, taken: set[str]) -> str:
    n = int(ws.store.setting('intake_next', 1))
    while True:
        sid = f's{n:04d}'
        n += 1
        if sid not in taken and not (ws.pdfs / f'{sid}.pdf').exists():
            ws.store.set_setting('intake_next', n)
            return sid


def accept(ws, id: str, *, author: str | None = None, force: bool = False,
           expect_rev: Any = ANY) -> dict:
    """Write the plan's submissions into `pdfs/`. Unchanged ones are left alone.
    Raises `Busy` if a submission with grading work would change or go, unless `force`."""
    src = source(ws, id)
    art = plan(ws, id)
    if art is None:
        raise KeyError(id)
    if expect_rev is not ANY and art.rev != expect_rev:
        raise Conflict("the plan changed")
    body = json.loads(json.dumps(art.body))
    n = src.pages
    made = ws.store.made()
    states = state(ws, id)
    gone = removed(ws, id)
    entries = body['entries']
    sheets = _sheets_of(body, n)
    busy = [e['id'] for e, st in zip(entries, states) if st == 'changed' and ws.store.has_work(e['id'], ignore=NO_WORK)]
    busy += [s for s in gone if ws.store.has_work(s, ignore=NO_WORK)]
    if busy and not force:
        raise Busy(busy)
    taken = {s.id for s in ws.store.submissions()} | set(made)
    report = {'new': [], 'rewritten': [], 'removed': [], 'unchanged': 0, 'empty': 0}
    writes = []
    for e, st, (pages, orients) in zip(entries, states, sheets):
        e['confirmed'] = True       # accepted: every start is now a person's decision
        if not pages:
            report['empty'] += 1
            continue
        if st == 'made':
            report['unchanged'] += 1
            continue
        if not e.get('id') or (e['id'] in made and made[e['id']].source != id):
            e['id'] = _next_id(ws, taken)
            taken.add(e['id'])
        writes.append((e['id'], pages, orients))
        report['new' if st == 'new' else 'rewritten'].append(e['id'])
    path = source_path(ws, src)
    for sid, pages, orients in writes:
        pdf_pages.write(path, [(i, Orient.from_json(o)) for i, o in zip(pages, orients)],
                        ws.pdfs / f'{sid}.pdf')
    now = _now()
    with ws.store.transaction():
        cur = ws.store.write(id, '', PLAN, body, origin=art.origin, author=art.author, expect_rev=art.rev)
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
    for sid in gone:
        (ws.pdfs / f'{sid}.pdf').unlink(missing_ok=True)
    report['rev'] = cur.rev
    return report
