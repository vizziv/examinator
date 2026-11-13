"""The app: server-rendered pages plus a few JSON actions (DESIGN §13).

    GET  /                          grid (?focus=... applies a shared focus)
    GET  /filter                    the filter page: facets of every question
    GET  /focus/values              facet values for all submissions (?sub=: one), JSON
    GET  /s/{sub}/{q}               question page (a question graders work through: `exam.grading`)
    POST /s/{sub}/{q}/{action}      save unverify decide clear read check default restore
    POST /s/{sub}/@identity/save    the Names step's values (the identity fields)
    GET  /page/{sub}/{index}.jpg    cached rendering of one scanned page
    GET  /image/{sub}/{q}/{check}/{digest}    one evidence image of that check's result
    GET  /jobs/{id}                 background job state
    GET  /read                      batch reads;  POST /read/plan|start|stop,  GET /read/status
    GET  /settings                  this browser's keys and appearance
    GET  /exam                      exam data (overrides);  POST /exam/set|clear, at a scope:
                                    every submission, one scan's, one submission's
    GET  /scans                     intake: sources, split plans, accept;  GET /scans/{src}/plan (JSON)
    POST /scans/upload?name=        a scanned PDF as the raw request body (streamed to disk)
    POST /scans/{src}/{action}      edit propose undo accept;  POST /scans/roster(/preview), /scans/who
    GET  /scans/names               who wrote each submission: identity values, proposals, picks (JSON)
    POST /scans/names/read          read the identity fields with the model (a job)
    GET  /sheet/{src}/{index}.jpg   one sheet of a source (?o= quarter turns, ?w= width)

Every POST needs the page token in an `X-Token` header and (but the upload)
takes a JSON object of at most MAX_JSON bytes. A POST is a change or a query
(DESIGN §13, Changes): a change names the revision the page drew (`rev`) and
answers the new one. It answers JSON: 403 bad token, 415 not JSON, 413 too
large, 404 unknown target, 409 stale revision or a run in progress (with
`current`, what is there now), 400 input a grader should correct. Nothing here keeps module-level state; it all
hangs off `app.state`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import jinja2
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..core import batch, focus, grid, identity, intake, overrides, split, status
from ..core.context import CheckContext
from ..core.ask import Printed
from ..core.defs import IDENTITY, PRINT_AREA, Bool, Check, Exam, Question, key, split_key
from ..core.as_python import describe_changes
from ..core.overrides import EXAM, SOURCE, SUBMISSION
from ..core.reading import normalize
from ..core.runner import Runner, unreadable
from ..core.store import Conflict
from ..core.workspace import Workspace
from ..pdf.layout import Layout
from ..std.blocks import MEDIA_TYPES
from ..pdf import orient
from .jobs import Batch, Jobs

HERE = Path(__file__).parent
LOOPBACK = ('127.0.0.1', 'localhost', '[::1]')
MAX_BODY = 400_000          # characters of a typed value
MAX_JSON = 2 << 20          # bytes of one JSON request body
MAX_UPLOAD = 4 << 30        # bytes of one scanned PDF
SHEET_WIDTHS = (140, 360, 1000)   # pixel widths a sheet is served at (0: as rendered)
MAX_ROSTER = 2_000_000      # characters of roster text
ROSTER_PROBLEMS = 20        # problems a roster preview lists
ROSTER_SAMPLE = 5           # people a roster preview shows
# 'inline-speculation-rules' lets a page's script list pages to render ahead (JSON, never code).
CSP = ("default-src 'none'; script-src 'self' 'inline-speculation-rules'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
IMAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
# What a person sees of a status: one word per idea (DESIGN.md, Words on screen).
STATE_LABELS = {
    'decided': "decided", 'decided_outdated': "decided, outdated", 'to_decide': "to decide",
    'not_run': "not run", 'outdated': "outdated", 'unverified': "unverified", 'graded': "graded",
    # a question with no checks: how its fields stand
    'not_read': "not read", 'verified': "verified"}


class Bad(ValueError):
    """Input the grader (or the page's script) should correct: HTTP 400."""


# --- security ------------------------------------------------------------------

class Guard:
    """Validates Host and Origin, and adds the security headers to every response."""

    def __init__(self, app, public_url: str | None):
        self.app = app
        pub = urlsplit(public_url) if public_url else None
        self.public_host = pub.netloc.lower() if pub else None
        self.public_origin = f'{pub.scheme}://{pub.netloc}'.lower() if pub else None

    def host_ok(self, host: str) -> bool:
        if not host:
            return False
        if host == self.public_host:
            return True
        name, sep, port = host.rpartition(':')
        if not sep or not port.isdigit():
            name = host
        return name in LOOPBACK

    def origin_ok(self, origin: str, host: str) -> bool:
        o = urlsplit(origin)
        if o.scheme not in ('http', 'https'):
            return False
        return o.netloc.lower() == host or f'{o.scheme}://{o.netloc}'.lower() == self.public_origin

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            if scope['type'] == 'lifespan':
                await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get('host', '').lower()
        origin = headers.get('origin')

        async def guarded(message):
            if message['type'] == 'http.response.start':
                h = MutableHeaders(scope=message)
                h.setdefault('Content-Security-Policy', CSP)
                h['X-Content-Type-Options'] = 'nosniff'
                h['Referrer-Policy'] = 'no-referrer'
                h.setdefault('Cache-Control', 'no-cache' if scope['path'].startswith('/static/') else 'no-store')
            await send(message)

        if not self.host_ok(host):
            return await PlainTextResponse("bad host", 400)(scope, receive, guarded)
        if origin is not None and not self.origin_ok(origin, host):
            return await PlainTextResponse("bad origin", 403)(scope, receive, guarded)
        await self.app(scope, receive, guarded)


# --- small parsers of request data ---------------------------------------------

def _int(value: Any, what: str, lo: int, hi: int) -> int:
    try:
        if isinstance(value, bool) or (isinstance(value, float) and value != int(value)):
            raise ValueError
        n = int(value)
    except (TypeError, ValueError):
        raise Bad(f"{what}: expected a whole number") from None
    if not lo <= n <= hi:
        raise Bad(f"{what}: expected {lo} to {hi}")
    return n


def _rev(data: dict) -> int | str | None:
    """The revision the page was drawn with; `null` means "did not exist". A row's is a
    number; a run's (a job id) or the roster's (a digest) is a short string."""
    if 'rev' not in data:
        raise Bad("rev is required")
    rev = data['rev']
    if rev is None or rev == '':
        return None
    if isinstance(rev, str) and not rev.isdigit():
        if len(rev) > 64:
            raise Bad("rev: too long")
        return rev
    return _int(rev, "rev", 0, 2**62)


@contextmanager
def now(current: Callable[[], Any]):
    """A change refused as stale (Conflict) answers what is there now: `current()`."""
    try:
        yield
    except Conflict as e:
        if getattr(e, 'current', None) is None:
            e.current = current()
        raise


def _strings(value: Any, allowed, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Bad(f"{what}: expected a list")
    unknown = [v for v in value if v not in allowed]
    if unknown:
        raise Bad(f"{what}: unknown {unknown[0]!r}"[:200])
    return tuple(dict.fromkeys(value))


# --- view helpers ----------------------------------------------------------------

def selection(text: str | None) -> dict:
    """A focus from a URL (`?focus=` JSON: {facet_id: [values] | "search"}); {} if malformed."""
    try:
        data = json.loads(text) if text and len(text) <= 20_000 else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v if isinstance(v, str) else [str(x) for x in v] for k, v in data.items()
            if isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, (str, int, float)) for x in v))}


def glyph(st) -> str:
    """What the grade is, as one glyph. Where it is in the workflow is `st.state`."""
    text = focus.grade_text(st)
    if text is None:
        return {'to_decide': '?', 'unverified': '?', 'outdated': '·'}.get(st.state, '')
    return '∅' if text == 'blank' else text


def value_class(st) -> str:
    return {None: 'v-n', 'blank': 'v-b', '✓': 'v-t', '✗': 'v-f'}.get(focus.grade_text(st), 'v-num')


def value_key(st) -> str:
    """The value as the grid's sort key (a blank sorts by its value)."""
    text = focus.grade_text(st, blank=False)
    return {None: '', '✓': 't', '✗': 'f'}.get(text, text)


DIGEST = re.compile(r'[0-9a-f]{64}')


# --- exam data (overrides) ---------------------------------------------------------

LONG_TEXT = 160      # characters: longer text entries start collapsed


def show_value(kind: str, value: Any) -> str:
    """An entry's JSON value as the text its input holds (see entries.js `readEntry`)."""
    if value is None:
        return ''
    if kind == 'pages':
        return ', '.join(str(p) for p in value)
    if kind == 'print_area':
        return ' '.join(str(v) for v in value)
    if kind == 'map':
        return '\n'.join(f'{k} = {v}' for k, v in value.items())
    if kind == 'choice':
        return '\n'.join(value)
    if kind == 'region':
        return ' '.join(str(v) for v in value)
    if kind == 'number':
        return f'{value:g}'
    return str(value)


def entry_view(e: overrides.Entry, revs: dict, label: str | None = None) -> dict:
    segs = overrides.split(e.path)
    text = show_value(e.kind, e.current)
    return {'path': e.path, 'kind': e.kind, 'label': label or ' / '.join(segs[1:]), 'current': e.current,
            'text': text, 'base_text': show_value(e.kind, e.base), 'overridden': e.overridden,
            'stored': e.path in revs, 'nullable': e.nullable, 'rev': revs.get(e.path, ''),
            'long': e.kind == 'text' and (len(text) > LONG_TEXT or text.count('\n') > 2),
            'area': e.kind in ('map', 'choice') or (e.kind == 'text' and (len(text) > 50 or '\n' in text)),
            'rows': min(max(text.count('\n') + 2, 2), 24)}


def picked_key(exam: Exam, q: Question, c: Check, values: dict) -> str | None:
    """Which of a key per value (a check's `key` with `by`, as `std.math.keyed`
    has) the current values pick, or None: no such key, or it cannot tell."""
    by, key_ = getattr(c.fn, 'by', None), getattr(c.fn, 'key', None)
    if by is None or not isinstance(key_, dict):
        return None
    ctx = CheckContext(submission='', question=q, check=c.name, values={k: (v.rev, v.text) for k, v in values.items()},
                       fields=exam.fields())
    try:
        value = normalize(str(by(ctx)))
    except Exception:
        return None
    return next((name for name in key_ if normalize(str(name)) == value), None)


def key_entries(entries: list[overrides.Entry], revs: dict, c: Check, picked: str | None) -> list[dict]:
    """A check's key entries (configuration under 'key'); of a key per value,
    only the one picked when that is known."""
    names = [str(k) for k in getattr(c.fn, 'key', None) or {}] if getattr(c.fn, 'by', None) else []
    out = []
    for e in entries:
        segs = overrides.split(e.path)[1:]
        if len(segs) < 4 or segs[:2] != ('checks', c.name) or segs[2] != 'key':
            continue
        rest = segs[3:]
        if len(rest) > 1 and rest[0] in names and picked is not None:
            if rest[0] != picked:
                continue
            rest = rest[1:]
        out.append(entry_view(e, revs, ' / '.join(rest)))
    return out


# --- the app -----------------------------------------------------------------------

def create_app(exam: Exam, ws: Workspace, runner: Runner, *, author: str | None = None,
               public_url: str | None = None) -> Starlette:
    store = ws.store
    token = secrets.token_urlsafe(32)
    root = urlsplit(public_url).path.rstrip('/') if public_url else ''
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / 'templates'), autoescape=True,
                             trim_blocks=True, lstrip_blocks=True)
    env.globals.update(labels=STATE_LABELS)
    jobs = Jobs()
    lock = threading.Lock()

    def have_model() -> bool:
        return runner.model is not None

    def who(request: Request) -> str | None:
        if public_url:
            for name in ('x-forwarded-user', 'remote-user'):
                value = request.headers.get(name, '').strip()
                if value:
                    return value[:120]
        return author

    def page(name: str, **ctx) -> HTMLResponse:
        html = env.get_template(name).render(exam=exam, token=token, root=root, model=have_model(), **ctx)
        return HTMLResponse(html)

    def box(layout: Layout, region) -> str:
        """Where a region's crop is on its scanned page, as the page's script reads it."""
        return ','.join(f'{v:.4f}' for v in layout.box(region, exam.crop_pad))

    def target(request: Request, need_question: bool = True):
        """The submission and question a path names, looked up; never used as paths.
        Only a question graders work through (`exam.grading`): a group kept apart
        is reached through its own step's routes."""
        sub = store.submission(request.path_params['sub'])
        if sub is None:
            raise HTTPException(404)
        if not need_question:
            return sub, None
        q = exam.grading.get(request.path_params['q'])
        if q is None:
            raise HTTPException(404)
        return sub, q

    def token_ok(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get('x-token', '').encode(), token.encode())

    def action(fn: Callable[[Request, dict], Any]):
        """A state-changing endpoint: the token header first, then a JSON object
        of at most MAX_JSON bytes, and the errors graders can fix."""
        async def endpoint(request: Request) -> Response:
            if not token_ok(request):
                return JSONResponse({'error': "bad token"}, 403)
            if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                return JSONResponse({'error': "expected JSON"}, 415)
            raw = bytearray()
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > MAX_JSON:
                    return JSONResponse({'error': "request too large"}, 413)
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = None
            if not isinstance(data, dict):
                return JSONResponse({'error': "bad request body"}, 400)
            try:
                out = await run_in_threadpool(fn, request, data)
            except Conflict as e:
                return JSONResponse({'error': str(e) or "changed elsewhere", 'conflict': True,
                                     'current': getattr(e, 'current', None)}, 409)
            except ValueError as e:
                return JSONResponse({'error': str(e)[:300]}, 400)
            return JSONResponse({'ok': True, **(out or {})})
        return endpoint

    read_slot = lambda sid, qid: f'read:{sid}/{qid}'
    check_slot = lambda sid, qid, c: f'check:{sid}/{qid}/{c}'

    def refresh(sid: str, qid: str | None = None) -> str:
        """Recompute in the background what a change outdated (and this question's checks never run)."""
        return jobs.start(lambda: runner.refresh(sid, questions=[qid] if qid else []))

    # --- grid ---

    def grid_page(request: Request) -> Response:
        columns = []

        def todo(q, check=None) -> dict:      # the column's state facet, and its values still to do
            name, values = focus.state_facet(q, check)
            return {'facet': f'{q.id}/{name}', 'todo': ' '.join(values)}
        for q in exam.grading.values():
            for c in q.checks:
                columns.append({'q': q, 'name': q.column(c.name), 'counts': c.points.counts, **todo(q, c.name)})
            if not q.checks:        # no checks: the column shows how its fields stand
                columns.append({'q': q, 'name': q.id, 'counts': False, **todo(q)})
        sel = selection(request.query_params.get('focus'))
        table = values() if sel else {}
        rows, started, graded = [], False, exam.grading     # started: anything of a graded question
        for row in grid.load(exam, store):
            cells = []
            started = started or any(split_key(k)[0] in graded for k in (*row.sheet.values, *row.sheet.results))
            for q in exam.grading.values():
                for c in q.checks:
                    st = row.status[(q.id, c.name)]
                    cells.append({'qid': q.id, 'col': q.column(c.name), 'state': st.state,
                                  'glyph': glyph(st), 'cls': value_class(st), 'v': value_key(st),
                                  'blank': 'blank' in st.flags, 'counts': st.counts,
                                  'title': STATE_LABELS[st.state]})
                if not q.checks:
                    state = row.fields[q.id]
                    cells.append({'qid': q.id, 'col': q.id, 'state': state,
                                  'glyph': {'verified': '–', 'unverified': '?'}.get(state, ''),
                                  'cls': 'v-n', 'v': '', 'blank': False, 'counts': False,
                                  'title': STATE_LABELS[state]})
            rows.append({'sub': row.submission, 'cells': cells, 'points': f'{row.points:g}',
                         'complete': row.complete, 'sort': row.points,
                         'hidden': bool(sel) and not focus.matches(table, sel, row.submission.id)})
        return page('grid.html', rows=rows, columns=columns, focus=focus.active(table, sel), started=started)

    # --- focus ---

    cached: dict[str, Any] = {}
    memo: dict = {}             # what facets compute (normal forms), kept between tables

    def values() -> dict:
        """The whole values table, recomputed only when stored rows have changed."""
        key_ = store.version()
        with lock:
            if cached.get('key') == key_:
                return cached['table']
            if len(memo) > 200_000:
                memo.clear()
        table = focus.values_table(exam, store, memo=memo)
        with lock:
            cached.update(key=key_, table=table)
        return table

    def filter_page(request: Request) -> Response:
        return page('filter.html')

    def focus_values(request: Request) -> Response:
        sid = request.query_params.get('sub')
        if sid is not None and store.submission(sid) is None:
            raise HTTPException(404)
        subs = store.submissions() if sid is None else [store.submission(sid)]
        return JSONResponse({'subs': [s.id for s in subs], 'names': [s.name for s in subs],
                             'questions': focus.describe_facets(exam),
                             'values': values() if sid is None else
                             focus.values_table(exam, store, submission=sid, memo=memo)})

    # --- question page ---

    def question_page(request: Request) -> Response:
        sub, q = target(request)
        sheet = runner.sheet(sub.id)
        layout = ws.layout(sub.id)
        try:
            count = ws.scan_page_count(sub.id)
        except Exception:
            count = 0

        gone = ws.missing(sub.id)

        def crop(region, path):
            if region is None or region.page in gone or not 0 < region.page <= count:
                return None
            return {'index': region.page - 1, 'box': box(layout, region), 'path': path}

        pages = ws.selected_pages(sub.id, q)
        # every page of the PDF, to scroll through: the printed pages (a missing one is its
        # placeholder), then the extra sheets; `on` marks this question's
        scans = [{'index': i, 'on': i + 1 in pages,
                  'label': "missing" if i + 1 in gone else str(i + 1) if i < exam.pages else f"extra {i + 1 - exam.pages}"}
                 for i in range(count)]

        # the checks: status, proposal, evidence
        checks, judged = [], {}
        for c in q.checks:
            k = key(q.id, c.name)
            st = sheet.status(exam, q, c)
            stored, decision = sheet.results.get(k), sheet.decisions.get(k)
            row = store.get(sub.id, k, 'result')
            blocks = row.body['blocks'] if row else []
            figures = next((i for i, b in enumerate(blocks) if b['type'] != 'image'), len(blocks))
            for label, j in (stored.fields if stored else {}).items():
                judged.setdefault(label, []).append({'check': c.name, **j})
            spec = c.points
            checks.append({'name': c.name, 'column': q.column(c.name), 'st': st, 'glyph': glyph(st),
                           'run': jobs.last(check_slot(sub.id, q.id, c.name)) or '',
                           'cls': value_class(st), 'fn': c.fn is not None,
                           'rev': decision.rev if decision else '', 'decided': decision is not None,
                           'bool': isinstance(spec, Bool), 'min': getattr(spec, 'min', None),
                           'max': getattr(spec, 'max', None), 'counts': spec.counts,
                           'step': 1 if getattr(spec, 'type', None) is int else 'any',
                           'stored': stored,
                           'current': stored is not None and status.current(
                               stored, sheet.values, defn=exam.check_hash(q, c), code=exam.code_hash),
                           'figures': blocks[:figures], 'rest': blocks[figures:]})

        # the fields on the page: its own, then those its checks read elsewhere
        own = [key(q.id, label) for label in q.labels]
        keys = own + [k for c in checks if c['stored'] for k in c['stored'].reads if k not in own]
        keys = list(dict.fromkeys(keys))
        fields, why = [], {}         # why: per question, the fields that cannot be read now
        for k in keys:
            qid, label = split_key(k)
            other = exam.questions.get(qid)
            f = other.field(label) if other else None
            if f is None:
                continue
            v = sheet.values.get(k)
            reader = other.reader_of(label)
            region = ws.region(sub.id, other, label)
            if qid not in why:
                why[qid] = unreadable(ws, sub.id, other, gone)
            fields.append({
                'key': k, 'qid': qid, 'label': label, 'own': qid == q.id,
                'title': label if qid == q.id else f"{qid} {label}",
                'value': v.text if v else '', 'rev': v.rev if v else '', 'read': v is not None,
                'verified': v.verified if v else None, 'by': v.by if v else None,
                'marker': bool(v and v.marker),
                'multiline': other.multiline(label),
                'printed': isinstance(reader, Printed), 'ask': getattr(reader, 'ask', ''),
                'typed': reader is None, 'default': f.default,
                'crop': crop(region, overrides.join(qid, 'fields', label, 'region')),
                'blocked': why[qid].get(label),         # 'page missing', 'no region': not read
                'judged': judged.get(label, []) if qid == q.id else []})
        for n, f in enumerate(fields):
            f['n'] = n
        unverified = [f for f in fields if f['read'] and f['verified'] is None and not f['marker']]
        waiting = [f['key'] for f in unverified if f['own']]           # what Verify here verifies
        waiting_other = [f['key'] for f in unverified if not f['own']]

        history = []
        for f in fields:
            for h in store.history(sub.id, f['key'], 'value')[:20]:
                b = h.body or {}
                history.append({'field': f['title'], 'key': f['key'], 'rev': h.rev,
                                'at': h.updated_at[:16].replace('T', ' '), 'who': h.author or "read",
                                'verified': b.get('verified'), 'text': str(b.get('text', ''))[:300]})
        history.sort(key=lambda h: h['at'], reverse=True)

        qids = list(exam.grading)
        i = qids.index(q.id) if q.id in qids else None
        lay = ws.layers(sub.id)
        stored = store.overrides()
        revs = {p: o.rev for p, o in stored.items()}
        entries = overrides.editable(exam, q.id)
        regions = [{'label': overrides.split(e.path)[2], 'printed': isinstance(q.reader_of(overrides.split(e.path)[2]), Printed),
                    **levels(lay, e.path, e.base, stored)} for e in entries if e.kind == 'region']
        mine = lay.stored.get(SUBMISSION, {})
        path = overrides.join(q.id, 'pages')
        own = {'path': path, 'rev': mine[path].rev if path in mine else ''}
        keyed = []
        for c in q.checks:
            picked = picked_key(exam, q, c, sheet.values)
            entries_ = key_entries(entries, revs, c, picked)
            if entries_:
                keyed.append({'check': c.name, 'picked': picked, 'entries': entries_,
                              'edited': any(e['overridden'] for e in entries_)})
        readers_ = [r for r, _ in q.groups()]
        return page(
            'question.html', sub=sub, q=q, checks=checks, fields=fields, waiting=waiting, waiting_other=waiting_other,
            scans=scans, history=history[:60], count=count, keyed=keyed,
            can_read=bool(readers_), read_run=jobs.last(read_slot(sub.id, q.id)) or '', off=bool(readers_) and not have_model(),
            pages=pages, pages_default=q.pages, own=own, extras=max(0, count - exam.pages),
            regions=regions, print_area=levels(lay, PRINT_AREA, overrides.entry(exam, PRINT_AREA).base, stored),
            scopes={'own': overrides.scope(SUBMISSION, sub.id), 'all': '',
                    'scan': overrides.scope(SOURCE, lay.source) if lay.source else None},
            first_page=(q.pages or (1,))[0], ids=[s.id for s in store.submissions()],
            prev_q=qids[i - 1] if i else None, next_q=qids[i + 1] if i is not None and i + 1 < len(qids) else None)

    def field_of(k: Any, questions: dict | None = None) -> tuple[str, str]:
        """A field of the questions graders work through (or of `questions`), by key."""
        questions = exam.grading if questions is None else questions
        if not isinstance(k, str) or '/' not in k:
            raise Bad("unknown field")
        qid, label = split_key(k)
        if qid not in questions or questions[qid].field(label) is None:
            raise Bad("unknown field")
        return qid, label

    def value_rev(sid: str, k: str) -> int | None:
        row = store.get(sid, k, 'value')
        return row.rev if row else None

    def save_values(request, sub, data: dict, questions: dict | None = None) -> tuple[dict, bool]:
        """Set the values a person changed (each verified by them), then verify
        the ones listed: 'save', 'save and verify', 'verify all here'. Each names the
        revision drawn. Their new revisions, and whether any was set."""
        changed, verify = data.get('values') or {}, data.get('verify') or []
        if not isinstance(changed, dict) or not isinstance(verify, list):
            raise Bad("unexpected content")
        if not changed and not verify:
            raise Bad("nothing to save")
        keys = list(changed) + [i.get('key') for i in verify if isinstance(i, dict)]
        author, revs, wrote = who(request), {}, False
        with now(lambda: {'revs': {k: value_rev(sub.id, k) for k in keys if isinstance(k, str)}}):
            for k, item in changed.items():
                qid, label = field_of(k, questions)
                text = item.get('text') if isinstance(item, dict) else None
                if not isinstance(text, str) or len(text) > MAX_BODY:
                    raise Bad("unexpected content")
                v = runner.set(sub.id, qid, label, text, author=author, expect_rev=_rev(item))
                revs[k], wrote = v.rev, True
            for item in verify:
                k = item.get('key') if isinstance(item, dict) else None
                qid, label = field_of(k, questions)
                if k in revs:
                    continue            # set just now: verified by this person
                revs[k] = runner.verify(sub.id, qid, label, author=author, expect_rev=_rev(item)).rev
        return revs, wrote

    def save(request, data):
        sub, q = target(request)
        revs, wrote = save_values(request, sub, data)
        return {'revs': revs, 'job': refresh(sub.id, q.id) if wrote else None}

    def names_save(request, data):
        """The Names step's own save: the identity fields' values, typed or verified."""
        sub, _ = target(request, need_question=False)
        revs, wrote = save_values(request, sub, data, {IDENTITY: exam.identity})
        return {'revs': revs, 'job': refresh(sub.id) if wrote else None}

    def unverify(request, data):
        sub, q = target(request)
        qid, label = field_of(data.get('key'))
        rev = _rev(data)
        if rev is None:
            raise Bad("nothing to unverify")
        with now(lambda: {'rev': value_rev(sub.id, key(qid, label))}):
            v = runner.verify(sub.id, qid, label, author=who(request), expect_rev=rev, verified=False)
        return {'rev': v.rev}

    def check_of(q: Question, data: dict):
        c = q.check(data.get('check')) if isinstance(data.get('check'), str) else None
        if c is None:
            raise Bad("unknown check")
        return c

    def decision_rev(sid: str, q: Question, c: Check) -> int | None:
        row = store.get(sid, key(q.id, c.name), 'decision')
        return row.rev if row else None

    def decide(request, data):
        sub, q = target(request)
        c = check_of(q, data)
        spec, value, flags = c.points, data.get('value'), ()
        if data.get('blank'):
            value, flags = spec.coerce('blank').value, ('blank',)
        elif isinstance(value, str):
            value = spec.parse(value)
        seen = data.get('seen')
        if not isinstance(seen, dict):
            raise Bad("seen is required")
        seen = {str(k): _rev({'rev': v}) for k, v in seen.items()}
        with now(lambda: {'rev': decision_rev(sub.id, q, c), 'seen': {k: value_rev(sub.id, k) for k in seen}}):
            d = runner.decide(sub.id, q.id, c.name, value, author=who(request), flags=flags,
                              expect_rev=_rev(data), seen=seen)
        return {'rev': d.rev}

    def clear(request, data):
        sub, q = target(request)
        c = check_of(q, data)
        with now(lambda: {'rev': decision_rev(sub.id, q, c)}):
            runner.clear(sub.id, q.id, c.name, expect_rev=_rev(data))
        return {'rev': None}

    def read(request, data):
        """Read this question's fields again (not what a person verified, unless asked).
        A run: its revision is the last read of this question started (`read_run`)."""
        sub, q = target(request)
        if not have_model():
            raise Bad("model unavailable")
        protected = bool(data.get('include_protected'))

        def work():
            outs = runner.read(sub.id, q.id, include_protected=protected, fresh=True)
            return outs + runner.refresh(sub.id, questions=[q.id])
        job_ = jobs.start(work, slot=read_slot(sub.id, q.id), seen=_rev(data))
        return {'job': job_, 'rev': job_}

    def run_check(request, data):
        """A grader's explicit run of one check (one that timed out, say); a run, as above."""
        sub, q = target(request)
        c = check_of(q, data)
        job_ = jobs.start(lambda: runner.compute(sub.id, q.id, c.name), slot=check_slot(sub.id, q.id, c.name),
                          seen=_rev(data))
        return {'job': job_, 'rev': job_}

    def to_default(request, data):
        """A field back to its default: its stored value dropped."""
        sub, q = target(request)
        qid, label = field_of(data.get('key'))
        with now(lambda: {'rev': value_rev(sub.id, key(qid, label))}):
            runner.unset(sub.id, qid, label, expect_rev=_rev(data))
        return {'rev': None, 'job': refresh(sub.id, q.id)}

    def restore(request, data):
        """A field's earlier value (`restore`: its revision in the history) back, set by
        this person, against the revision drawn (`rev`)."""
        sub, q = target(request)
        qid, label = field_of(data.get('key'))
        old_rev = _rev({'rev': data.get('restore')})
        old = next((h for h in store.history(sub.id, key(qid, label), 'value') if h.rev == old_rev), None)
        if old is None:
            raise Bad("no such version")
        with now(lambda: {'rev': value_rev(sub.id, key(qid, label))}):
            v = runner.set(sub.id, qid, label, str((old.body or {}).get('text', '')), author=who(request),
                           expect_rev=_rev(data))
        return {'rev': v.rev, 'job': refresh(sub.id, q.id)}

    def levels(lay, path: str, base, stored: dict) -> dict:
        """One value at each scope, for the scope editor: this submission's own ('own'),
        its scan's ('scan'), every submission's ('all', the exam's override), each with its
        revision; the package's ('base'); and the one in force ('value')."""
        out = {'path': path, 'base': base, 'value': lay.value(path), 'source': lay.source}
        for name, o in (('own', lay.stored.get(SUBMISSION, {}).get(path)),
                        ('scan', lay.stored.get(SOURCE, {}).get(path)), ('all', stored.get(path))):
            out[name], out[name + '_rev'] = (o.value, o.rev) if o else (None, None)
        return out

    def crop_boxes(sid: str) -> dict:
        """Where every field's crop is for one submission, by region path: the scanned
        page (`index`) and the box on it; the page's script only places them."""
        lay = ws.layers(sid)
        layout = Layout(lay.root(PRINT_AREA))
        return {overrides.join(qid, 'fields', f.label, 'region'): {'index': f.region.page - 1,
                                                                    'box': box(layout, f.region)}
                for qid in exam.questions for f in lay.root(qid).fields if f.region is not None}

    # --- pages, evidence images, jobs ---

    def page_image(request: Request) -> Response:
        sub, _ = target(request, need_question=False)
        index = request.path_params['index']
        try:
            if not 0 <= index < ws.scan_page_count(sub.id):
                raise HTTPException(404)
            path = ws.scan_page_path(sub.id, index)
        except (KeyError, IndexError, OSError):
            raise HTTPException(404) from None
        cache = (ws.root / 'cache').resolve()
        if path.is_symlink() or cache not in path.resolve().parents:
            raise HTTPException(404)
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=600'})

    def evidence_image(request: Request) -> Response:
        """The image with this digest, if this check's stored result shows it. The
        digest names the content, so the URL is immutable."""
        sub, q = target(request)
        digest = request.path_params['digest']
        got = store.image(sub.id, key(q.id, request.path_params['check']), digest) \
            if DIGEST.fullmatch(digest) else None
        if got is None or got[0] not in MEDIA_TYPES:
            raise HTTPException(404)
        return Response(got[1], media_type=got[0], headers={
            'Content-Security-Policy': IMAGE_CSP, 'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, max-age=31536000, immutable'})

    def job(request: Request) -> Response:
        info = jobs.get(request.path_params['id'])
        if info is None:
            raise HTTPException(404)
        return JSONResponse(info)

    def read_page(request: Request) -> Response:
        return page('read.html', questions=list(exam.grading.values()), run=batch_now()['id'],
                    total=len(store.submissions()))

    def batch_plan(data: dict) -> batch.Plan:
        """What the selection in a request would read now."""
        known = [s.id for s in store.submissions()]
        subs = data.get('submissions')
        subs = tuple(known) if subs is None else _strings(subs, set(known), "submissions")
        questions = data.get('questions')
        questions = tuple(exam.grading) if questions is None else _strings(questions, exam.grading, "questions")
        if not subs or not questions:
            raise Bad("nothing selected")
        sel = batch.Selection(subs, questions, missing_only=bool(data.get('missing_only')),
                              include_protected=bool(data.get('include_protected')))
        return batch.plan(ws, sel, model_available=have_model())

    def read_plan(request, data):
        plan = batch_plan(data)
        groups: dict[tuple, list[str]] = {}
        for item in plan.items:
            groups.setdefault((item.action, item.question, item.reason), []).append(item.submission)
        rank = {a: i for i, a in enumerate(batch.ACTIONS)}
        return {'counts': {a: plan.count(a) for a in batch.ACTIONS},
                'groups': [{'action': a, 'question': qn, 'reason': r, 'n': len(ids),
                            'submissions': ids[:200]}
                           for (a, qn, r), ids in sorted(groups.items(), key=lambda kv: (rank[kv[0][0]], kv[0][1:]))]}

    def batch_now() -> dict:
        current: Batch | None = app.state.batch
        return current.info() if current else {'id': None, 'state': 'idle'}

    def read_start(request, data):
        """Read what the selection would read now (planned here, at the start). A run:
        `rev` is the batch the page last showed (null: none), refused while one runs."""
        with lock, now(batch_now):
            current: Batch | None = request.app.state.batch
            if (current.id if current else None) != _rev(data):
                raise Conflict("another read started")
            if current is not None and current.active:
                raise Conflict("a read is already in progress")
            plan = batch_plan(data)
            run = batch.BatchRun(runner, plan, parallel=_int(data.get('parallel', 2), "parallel", 1, 16))
            request.app.state.batch = Batch(run)
            request.app.state.batch.start()
        return {'total': plan.count('read'), 'rev': request.app.state.batch.id}

    def read_status(request: Request) -> Response:
        return JSONResponse(batch_now())

    def read_stop(request, data):
        """Stop the batch the page shows running (`rev`: its id)."""
        rev = _rev(data)
        with now(batch_now):
            current: Batch | None = request.app.state.batch
            if current is None or current.id != rev or not current.active:
                raise Conflict("not running")
            current.run.cancel()
        return {'rev': current.id}

    # --- settings ---

    def settings_page(request: Request) -> Response:
        return page('settings.html')

    # --- exam data: overrides of the package's plain data, for every submission ---

    def exam_page(request: Request) -> Response:
        stored = store.overrides()
        revs = {p: o.rev for p, o in stored.items()}
        by_q: dict[str, list] = {}
        for e in overrides.editable(exam):
            by_q.setdefault(overrides.split(e.path)[0], []).append(entry_view(e, revs))
        questions = [{'id': qid, 'title': "print area" if qid == PRINT_AREA else exam.questions[qid].title,
                      'entries': items} for qid, items in by_q.items()]
        skipped = [{'path': s.path, 'reason': s.reason} for s in ws.skipped]
        active = [{'path': p, 'who': o.author or '?', 'at': o.updated_at[:16].replace('T', ' ')}
                  for p, o in stored.items()]
        names, first = {s.id: s.name for s in store.sources()}, next(iter(exam.grading), None)
        scoped = []             # what one scan or one submission has of its own
        for o in store.all_overrides():
            if not o.scope:
                continue
            level, id_ = overrides.parse_scope(o.scope)
            try:
                kind = overrides.kind(exam, o.path)
            except ValueError:
                kind = ''
            root = overrides.split(o.path)[0]
            scoped.append({'scope': o.scope, 'level': "scan" if level == SOURCE else level,
                           'where': names.get(id_, id_) if level == SOURCE else id_,
                           'gone': store.submission(id_) is None if level == SUBMISSION else id_ not in names,
                           'href': (f'/scans#sub={id_}' if root == IDENTITY else
                                    f'/s/{id_}/{root if root in exam.grading else first}' if first else None)
                           if level == SUBMISSION else None,
                           'path': o.path, 'text': show_value(kind, o.value), 'rev': o.rev,
                           'who': o.author or '?', 'at': o.updated_at[:16].replace('T', ' ')})
        return page('exam.html', questions=questions, active=active, skipped=skipped, scoped=scoped,
                    lines=describe_changes(exam, store))

    def _path(data: dict) -> str:
        path = data.get('path')
        if not isinstance(path, str) or not path or len(path) > 500:
            raise Bad("path is required")
        return path

    def _scope(data: dict, setting: bool) -> str:
        """The scope a request names, as stored: '' (every submission), 'source:<id>'
        (one scan's) or 'submission:<id>'. To set a value there, it must exist."""
        scope = data.get('scope') or ''
        if not isinstance(scope, str):
            raise Bad("bad scope")
        level, id_ = overrides.parse_scope(scope)
        if setting and (store.submission(id_) is None if level == SUBMISSION
                        else store.source(id_) is None if level == SOURCE else False):
            raise Bad("no such scan or submission")
        return scope

    def _changed(path: str, scope: str, change: overrides.Change, data: dict) -> dict:
        """After an edit: recompute in the background what it outdated, the
        requesting page's submission first; say what is now in force at that scope (and, for the page's submission, at each)."""
        o = store.overrides(scope).get(path)
        subs = [s.id for s in store.submissions()]
        first = data.get('sub')
        if first in subs:
            subs.remove(first)
            subs.insert(0, first)
        job = jobs.start_each([(s,) for s in subs], runner.refresh) if change.checks else None
        level, id_ = overrides.parse_scope(scope)
        try:
            kind = overrides.kind(exam, path)
        except ValueError:
            kind = ''
        value = ws.layers(**({} if level == EXAM else {level: id_})).value(path) if kind else None
        out = {'rev': o.rev if o else None, 'value': value, 'text': show_value(kind, value),
               'overridden': o is not None, 'checks': [f'{q}.{c}' for q, c in change.checks],
               'affected': len(change.checks) * len(subs), 'job': job,
               'skipped': [{'path': s.path, 'reason': s.reason} for s in change.skipped]}
        if first in subs and kind in ('region', 'print_area'):     # where the page's crops now are
            out['levels'] = levels(ws.layers(first), path, overrides.entry(exam, path).base, store.overrides())
            out['boxes'] = crop_boxes(first)
        return out

    def _conflict(e: Conflict, path: str, scope: str) -> Conflict:
        """What is saved now, so that saving again overwrites it knowingly."""
        o = store.overrides(scope).get(path)
        e.current = {'rev': o.rev if o else None, 'value': o.value if o else None,
                     'who': (o.author or '?') if o else None}
        return e

    def exam_set(request, data):
        path, rev, scope = _path(data), _rev(data), _scope(data, True)
        try:
            with lock:
                change = overrides.set(ws, path, data.get('value'), scope=scope, author=who(request), expect_rev=rev)
        except Conflict as e:
            raise _conflict(e, path, scope) from None
        return _changed(path, scope, change, data)

    def exam_clear(request, data):
        path, rev, scope = _path(data), _rev(data), _scope(data, False)
        try:
            with lock:
                change = overrides.clear(ws, path, scope=scope, expect_rev=rev)
        except Conflict as e:
            raise _conflict(e, path, scope) from None
        return _changed(path, scope, change, data)

    # --- intake: scans in, submissions out ---

    def the_source(request: Request):
        src = store.source(request.path_params['src'])
        if src is None:
            raise HTTPException(404)
        return src

    def plan_view(src) -> dict:
        """The plan as the page shows it: per submission, and per sheet whose it is and
        what printed page it counts as, so the page derives nothing."""
        art = intake.plan(ws, src.id)
        body, L = art.body, art.body['sheets']
        rows, gone = intake.outline(ws, src.id, body)
        said, lays = split.verified(body), split.layouts(body, exam.pages)
        doubts, turns = split.doubts(body, exam.pages), split.turns(body)
        owner, slot, entries = [-1] * src.pages, [None] * src.pages, []
        for k, (run, row, lay) in enumerate(zip(split.runs(body), rows, lays)):
            before = []
            for i, p in lay:
                if i is None:
                    before.append(p)
                    continue
                owner[i], slot[i], before = k, {'label': f'p{p}' if p else 'extra', 'before': before}, []
            sid = row['id']
            entries.append({'start': run[0], 'verified': said[k], 'id': sid, 'pages': run, 'lay': lay,
                            'doubts': [split.DOUBTS[d] for d in doubts[k]], 'state': row['state'],
                            'name': identity.who(ws, sid)['name'] if sid else ''})
        return {'id': src.id, 'name': src.name, 'n': src.pages, 'rev': art.rev, 'length': body['length'],
                'checked': bool(body.get('checked')) and bool(body.get('readings')), 'failed': body.get('failed', 0),
                'order': [r['i'] for r in L], 'kept': [r['i'] for r in L if not r.get('dropped')],
                'turn': [turns[i] for i in range(src.pages)], 'owner': owner, 'slot': slot,
                'entries': entries, 'dropped': split.dropped(body), 'missing': sorted(set(split.marks(body)[1])),
                'readings': {i: None if r is None else f'p.{r + 1}' for i, r in split.readings(body).items()},
                'removed': gone, 'cursor': split.cursor(body), 'counts': split_counts(body, rows, gone),
                'undo': intake.can_undo(ws, src.id), 'idboxes': idboxes(src.id),
                'busy': intake.disturbed(ws, rows, gone)}

    def split_counts(body, rows, gone) -> dict:
        """What the workflow strip counts for a scan: first pages to verify, submissions to write or remove."""
        return {'open': sum(not v for v in split.verified(body)),
                'write': sum(r['state'] != 'made' for r in rows) + len(gone)}

    def idboxes(source: str | None) -> list[dict]:
        """Where the identity fields are on a sheet of a source, by printed page."""
        layout = ws.source_layout(source)
        return [{'name': f.label, 'offset': f.region.page - 1, 'box': box(layout, f.region)}
                for f in identity.placed(ws)]

    def scans_page(request: Request) -> Response:
        boxes = idboxes(None)
        made = store.made()
        sources = store.sources()
        # per scan, what the workflow strip counts: first pages to verify, submissions to write
        counts = {}
        for src in sources:
            body = intake.plan(ws, src.id).body
            counts[src.id] = split_counts(body, *intake.outline(ws, src.id, body))
        roster = [{k: p[k] for k in ('name', 'id', 'email', 'key')} for p in identity.people(ws)]
        return page('scans.html', sources=sources, counts=json.dumps(counts), people=roster,
                    roster_rev=identity.roster_rev(ws) or '',
                    expected=exam.pages, idboxes=json.dumps(boxes), fields=identity.boxed(ws),
                    widths=json.dumps(SHEET_WIDTHS), made=len(store.submissions()),
                    handed=sum(1 for s in store.submissions() if s.id not in made))

    def scans_plan(request: Request) -> Response:
        return JSONResponse(plan_view(the_source(request)))

    async def scans_upload(request: Request) -> Response:
        if not token_ok(request):
            return JSONResponse({'error': "bad token"}, 403)
        name = Path(request.query_params.get('name') or 'scan.pdf').name[:200] or 'scan.pdf'
        tmp = intake.sources_dir(ws) / f'.upload-{secrets.token_hex(8)}.part'
        h, size, head = hashlib.sha256(), 0, b''
        try:
            with open(tmp, 'wb') as fh:
                os.chmod(tmp, 0o600)
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        return JSONResponse({'error': "file too large"}, 400)
                    if len(head) < 5:
                        head += chunk[:5]
                    h.update(chunk)
                    fh.write(chunk)
            if not head.startswith(b'%PDF-'):
                return JSONResponse({'error': "not a PDF"}, 400)
            try:
                src, new = await run_in_threadpool(intake.add_source, ws, tmp, name, move=True,
                                                   sha256=h.hexdigest())
            except ValueError as e:
                return JSONResponse({'error': str(e)[:300]}, 400)
        finally:
            tmp.unlink(missing_ok=True)
        return JSONResponse({'ok': True, 'id': src.id, 'new': new, 'pages': src.pages})

    def scans_edit(request, data):
        src = the_source(request)
        op = data.get('op')
        if op not in intake.EDITS:
            raise Bad("unknown edit")
        kw = {}
        if 'k' in data:
            kw['k'] = _int(data['k'], "submission", 0, src.pages)
        if 'sheet' in data:
            kw['sheet'] = _int(data['sheet'], "page", 0, src.pages - 1)
        if 'delta' in data:
            kw['delta'] = _int(data['delta'], "move", -1, 1)
        with now(lambda: plan_view(src)):
            intake.edit(ws, src.id, op, expect_rev=_rev(data), author=who(request), **kw)
        return planned(src)

    def scans_propose(request, data):
        src = the_source(request)
        length = data.get('length')
        length = None if length in (None, '') else _int(length, "pages per submission", 1, 999)
        kw = dict(length=length, author=who(request), expect_rev=_rev(data))
        with now(lambda: plan_view(src)):
            if data.get('check'):
                if not have_model():
                    raise Bad("model unavailable")
                if not exam.landmarks:
                    raise Bad("the exam declares no landmarks")
                if intake.plan(ws, src.id).rev != kw['expect_rev']:
                    raise Conflict("the plan changed")

                def work():
                    intake.reguess(ws, src.id, model=runner.model, **kw)
                # one reading of a scan's pages at a time: a run in the scan's slot, from the last one
                return {'job': jobs.start(work, slot=f'split-read:{src.id}', seen=jobs.last(f'split-read:{src.id}')),
                        'rev': kw['expect_rev']}
            intake.reguess(ws, src.id, **kw)
        return planned(src)

    def planned(src) -> dict:
        """After a change to a plan: the plan as it is now, and its revision."""
        view = plan_view(src)
        return {'plan': view, 'rev': view['rev']}

    def scans_undo(request, data):
        src = the_source(request)
        with now(lambda: plan_view(src)):
            intake.undo(ws, src.id, expect_rev=_rev(data))
        return planned(src)

    def scans_accept(request, data):
        src = the_source(request)
        try:
            with lock:
                report = intake.accept(ws, src.id, force=bool(data.get('force')), expect_rev=_rev(data))
        except Conflict as e:           # stale, or work it would disturb (`busy`): the plan as it is now
            e.current = plan_view(src)
            raise
        return {'report': report, **planned(src)}

    def roster_text(data: dict) -> str:
        text = data.get('text')
        if not isinstance(text, str) or len(text) > MAX_ROSTER:
            raise Bad("expected roster text")
        return text

    def scans_roster_preview(request, data):
        """What a roster text (a CSV file's contents or pasted lines) would read as: how
        many people, the first few, the problems. Changes nothing."""
        people, problems = identity.read_roster(roster_text(data))
        return {'people': len(people), 'problems': problems[:ROSTER_PROBLEMS],
                'more': max(0, len(problems) - ROSTER_PROBLEMS),
                'sample': [{'name': p['name'], 'id': p['id'], 'email': p['email']} for p in people[:ROSTER_SAMPLE]]}

    def scans_roster(request, data):
        """Replace the roster, against the one the page showed (`rev`)."""
        identity.set_roster(ws, roster_text(data), expect_rev=_rev(data))
        return {'rev': identity.roster_rev(ws)}

    def scans_who(request, data):
        """Name one submission, as the person saw it: `{sub, rev}` and one of `key` (a
        roster person), `text` (someone typed in), `restore` (a revision of the pick:
        undo), or none of them (unname). `rev` is the pick's revision they saw (null:
        none); anything else is refused (409, with the pick as it is now)."""
        sub = store.submission(str(data.get('sub')))
        if sub is None:
            raise Bad("unknown submission")
        rev = _rev(data)
        said = [k for k in ('key', 'text', 'restore') if k in data]
        if len(said) > 1:
            raise Bad("one of key, text, restore")
        try:
            if 'restore' in data:
                w = identity.restore(ws, sub.id, _int(data['restore'], "restore", 0, 2**62), expect_rev=rev)
                return {'who': w, 'rev': w['rev']}
            if 'key' in data:
                person = identity.on_roster(ws, data['key']) if isinstance(data['key'], str) else None
                if person is None:
                    raise Bad("not on the roster (it changed?): reload")
            else:
                person = identity.typed(data['text']) if 'text' in data else None
            w = identity.pick(ws, sub.id, person, author=who(request), expect_rev=rev)
            return {'who': w, 'rev': w['rev']}
        except Conflict as e:
            cur = store.get(sub.id, '', identity.WHO)
            e.current = {**identity.who(ws, sub.id), 'rev': cur.rev if cur else None}
            raise
        except KeyError:
            raise Bad("nothing to undo") from None

    proposals: dict = {}        # identity.overview's cache: proposals by values and roster

    def scans_names(request: Request) -> Response:
        """Every submission's identity values, proposal and pick; where each identity
        field is (its crop, this submission's own region); and the regions for every
        submission, for the box editor."""
        with lock:
            ats = {s.id: identity.at(ws, s.id) for s in store.submissions()}
            view = identity.overview(ws, cache=proposals, ats=ats)
        paths = {f.label: overrides.join(IDENTITY, 'fields', f.label, 'region') for f in identity.group(ws).fields}
        own: dict[str, dict] = {}           # each submission's own boxes, by scope
        for o in store.all_overrides():
            if o.path in paths.values():
                own.setdefault(o.scope, {})[o.path] = o
        for row in view['rows']:
            sid, a = row['sub'], ats[row['sub']]
            row['crops'] = [{'name': f.label, 'index': f.region.page - 1, 'box': box(a.layout, f.region)}
                            for f in a.fields]
            mine = own.get(overrides.scope(SUBMISSION, sid), {})
            row['own'] = {c: {'value': mine[p].value, 'rev': mine[p].rev} for c, p in paths.items() if p in mine}
            row['print_area'] = list(a.layout.print_area)     # for the boxes drawn on the page
        stored = store.overrides()
        view['nobody'] = [identity.label(p) for p in view['nobody']]
        view['boxed'] = any(a.fields for a in ats.values())
        view['columns'] = []
        for c, path in paths.items():           # the regions for every submission, for the box editor
            e = overrides.entry(exam, path)
            view['columns'].append({'name': c, 'path': path, 'value': e.current, 'base': e.base,
                                    'rev': stored[path].rev if path in stored else None})
        view['pad'] = exam.crop_pad
        view['doubts'] = identity.DOUBTS
        view['roster'] = len(identity.people(ws))
        view['run'] = jobs.last('names-read')
        return JSONResponse(view)

    def scans_names_read(request, data):
        """Read the name boxes: a run, against the last one the page showed (`rev`)."""
        seen = _rev(data)
        if not have_model():
            raise Bad("model unavailable")
        if not identity.boxed(ws):
            raise Bad("no name or ID boxes: draw them first")
        again = bool(data.get('again'))       # also read again what no person verified
        subs = [s.id for s in store.submissions()] if again else identity.unread(ws)
        job_ = jobs.start_each([(s,) for s in subs], lambda sid: identity.read(runner, sid, again=again),
                               slot='names-read', seen=seen)
        return {'job': job_, 'rev': job_, 'n': len(subs)}

    def sheet_image(request: Request) -> Response:
        """A sheet turned as asked (the page asks for the plan's orientation) and
        shrunk to one of a few widths, cached; the source and the renderer are untouched."""
        src = the_source(request)
        index = request.path_params['index']
        w, o = request.query_params.get('w', '0'), request.query_params.get('o', '0')
        if not 0 <= index < src.pages or not w.isdigit() or int(w) not in (0, *SHEET_WIDTHS) or o not in ('0', '1', '2', '3'):
            raise HTTPException(404)
        cache = (ws.root / 'cache').resolve()
        out = cache / 'sheets' / src.id / f'{index:04d}-{o}-{int(w)}.jpg'
        if not out.exists():
            try:
                im = ws.renderer.page(intake.source_path(ws, src), index)
            except (KeyError, IndexError, OSError):
                raise HTTPException(404) from None
            im = orient.turn(im, int(o))
            if int(w) and im.width > int(w):
                im = im.resize((int(w), max(1, round(im.height * int(w) / im.width))))
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = out.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
            im.convert('RGB').save(tmp, 'JPEG', quality=85)
            os.replace(tmp, out)
        if out.is_symlink() or cache not in out.resolve().parents:
            raise HTTPException(404)
        return FileResponse(out, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=3600'})

    def post(kind: str):
        """A POST route of a kind: 'change' (names the revision drawn, refused when stale)
        or 'query' (changes nothing: a POST only for its body). The route table says which."""
        def route(path, fn):
            endpoint = action(fn)
            endpoint.kind = kind
            return Route(path, endpoint, methods=['POST'])
        return route
    change, query = post('change'), post('query')

    routes = [
        Route('/', grid_page),
        Route('/filter', filter_page),
        Route('/focus/values', focus_values),
        change(f'/s/{{sub}}/{IDENTITY}/save', names_save),        # the Names step's; the rest name graded questions
        Route('/s/{sub}/{q}', question_page),
        change('/s/{sub}/{q}/save', save),
        change('/s/{sub}/{q}/unverify', unverify),
        change('/s/{sub}/{q}/decide', decide),
        change('/s/{sub}/{q}/clear', clear),
        change('/s/{sub}/{q}/read', read),
        change('/s/{sub}/{q}/check', run_check),
        change('/s/{sub}/{q}/default', to_default),
        change('/s/{sub}/{q}/restore', restore),
        Route('/page/{sub}/{index:int}.jpg', page_image),
        Route('/image/{sub}/{q}/{check}/{digest}', evidence_image),
        Route('/jobs/{id}', job),
        Route('/read', read_page),
        query('/read/plan', read_plan),
        change('/read/start', read_start),
        change('/read/stop', read_stop),
        Route('/read/status', read_status),
        Route('/settings', settings_page),
        Route('/exam', exam_page),
        change('/exam/set', exam_set),
        change('/exam/clear', exam_clear),
        Route('/scans', scans_page),
        Route('/scans/upload', scans_upload, methods=['POST']),     # a file, kept once by its digest
        change('/scans/roster', scans_roster),
        query('/scans/roster/preview', scans_roster_preview),
        change('/scans/who', scans_who),
        Route('/scans/names', scans_names),
        change('/scans/names/read', scans_names_read),
        Route('/scans/{src}/plan', scans_plan),
        change('/scans/{src}/edit', scans_edit),
        change('/scans/{src}/propose', scans_propose),
        change('/scans/{src}/undo', scans_undo),
        change('/scans/{src}/accept', scans_accept),
        Route('/sheet/{src}/{index:int}.jpg', sheet_image),
        Mount('/static', StaticFiles(directory=HERE / 'static'), name='static'),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(Guard, public_url=public_url)
    app.state.batch = None
    # Results outdated while the app was down (the exam's code or data changed) are recomputed now.
    app.state.startup = jobs.start_each([(s.id,) for s in store.submissions()], runner.refresh)
    return app
