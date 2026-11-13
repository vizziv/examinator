"""The app: server-rendered pages plus a few JSON actions (DESIGN §13).

    GET  /                          grid (?focus=... applies a shared focus)
    GET  /filter                    the filter page: facets of every question
    GET  /focus/values              facet values for all submissions (?sub=: one), JSON
    GET  /s/{sub}/{q}               question page
    POST /s/{sub}/{q}/{action}      save unverify decide clear read check default restore
    GET  /page/{sub}/{index}.jpg    cached rendering of one scanned page
    GET  /image/{sub}/{q}/{check}/{path}?v=   one evidence image (path '2' or '1-0-3')
    GET  /jobs/{id}                 background job state
    GET  /read                      batch reads;  POST /read/plan|start|stop,  GET /read/status
    GET  /settings                  this browser's keys and appearance
    GET  /exam                      exam data (overrides);  POST /exam/set|clear, at a scope:
                                    every submission, one scan's, one submission's
    GET  /scans                     intake: sources, split plans, accept;  GET /scans/{src}/plan (JSON)
    POST /scans/upload?name=        a scanned PDF as the raw request body (streamed to disk)
    POST /scans/{src}/{action}      edit propose undo accept;  POST /scans/roster, /scans/who
    GET  /scans/names               who wrote each submission: identity values, proposals, picks (JSON)
    POST /scans/names/read          read the identity fields with the model (a job)
    GET  /sheet/{src}/{index}.jpg   one sheet of a source (?o= quarter turns, ?w= width)

Every POST needs the page token in an `X-Token` header and (but the upload)
takes a JSON object of at most MAX_JSON bytes; it answers JSON: 403 bad token,
415 not JSON, 413 too large, 404 unknown target, 409 stale revision or a run
in progress (with `current`, where the page can save again knowingly), 400
input a grader should correct. Nothing here keeps module-level state; it all
hangs off `app.state`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
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
from ..core.defs import IDENTITY, Bool, Check, Exam, Question, key, split_key
from ..core.as_python import describe_changes
from ..core.overrides import EXAM, SOURCE, SUBMISSION
from ..core.reading import normalize
from ..core.runner import Runner
from ..core.store import Conflict
from ..core.workspace import PRINT_AREA, Workspace
from ..pdf.layout import Layout
from ..std.blocks import MEDIA_TYPES
from ..pdf import orient
from .jobs import Batch, Jobs

HERE = Path(__file__).parent
LOOPBACK = ('127.0.0.1', 'localhost', '[::1]')
MAX_BODY = 400_000          # characters of an edited artifact
MAX_JSON = 2 << 20          # bytes of one JSON request body
MAX_UPLOAD = 4 << 30        # bytes of one scanned PDF
SHEET_WIDTHS = (140, 360, 1000)   # pixel widths a sheet is served at (0: as rendered)
MAX_ROSTER = 2_000_000      # characters of roster text
ROSTER_PROBLEMS = 20        # problems a roster preview lists
ROSTER_SAMPLE = 5           # people a roster preview shows
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
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


def _rev(data: dict) -> int | None:
    """The revision the page last saw; `null` means "does not exist yet"."""
    if 'rev' not in data:
        raise Bad("rev is required")
    rev = data['rev']
    if rev is None or rev == '':
        return None
    return _int(rev, "rev", 0, 2**62)


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


def image_version(b: dict) -> str:
    """A short digest of an image block's data: its URL changes when it does."""
    return hashlib.sha256(str(b.get('data', '')).encode()).hexdigest()[:16]


def prepare_blocks(blocks: Any, prefix: str = '') -> list[dict]:
    """Evidence blocks ready for the template: unknown shapes dropped, images
    given the path that addresses them and a version of their data."""
    out = []
    for i, b in enumerate(blocks if isinstance(blocks, list) else []):
        if not isinstance(b, dict):
            continue
        path = f'{prefix}{i}'
        kind = b.get('type')
        if kind == 'details':
            out.append({'type': 'details', 'summary': str(b.get('summary', "")),
                        'blocks': prepare_blocks(b.get('blocks'), path + '-')})
        elif kind == 'image':
            if b.get('media_type') in MEDIA_TYPES and isinstance(b.get('data'), str):
                out.append({'type': 'image', 'path': path, 'v': image_version(b),
                            'alt': str(b.get('alt') or ""), 'title': str(b.get('title') or "")})
        elif kind == 'table':
            out.append({'type': 'table', 'columns': list(b.get('columns') or []),
                        'rows': [list(r) for r in b.get('rows') or [] if isinstance(r, (list, tuple))]})
        elif kind == 'code':
            hl = set(b.get('highlight') or [])
            out.append({'type': 'code', 'lines': [(n, line, n in hl) for n, line in
                                                  enumerate(str(b.get('text', "")).split('\n'), 1)]})
        elif kind == 'note':
            tone = b.get('tone')
            out.append({'type': 'note', 'text': str(b.get('text', "")),
                        'tone': tone if tone in ('info', 'good', 'bad', 'warn') else 'info'})
    return out


def find_image(blocks: Any, path: str) -> dict | None:
    """The image block at `path` ('2' or '1-0-3'), or None."""
    node: Any = None
    for part in path.split('-'):
        if not part.isdigit() or len(part) > 4 or not isinstance(blocks, list):
            return None
        i = int(part)
        if i >= len(blocks) or not isinstance(blocks[i], dict):
            return None
        node = blocks[i]
        blocks = node.get('blocks')
    if node is None or node.get('type') != 'image' or node.get('media_type') not in MEDIA_TYPES \
            or not isinstance(node.get('data'), str):
        return None
    return node


# --- exam data (overrides) ---------------------------------------------------------

LONG_TEXT = 160      # characters: longer text entries start collapsed


def show_value(kind: str, value: Any) -> str:
    """An entry's JSON value as the text its input holds (see exam.js `readEntry`)."""
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
    # Stored overrides apply from the start, whether or not the caller applied them.
    overrides.refresh(exam, store)
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
        """The submission and question a path names, looked up; never used as paths."""
        sub = store.submission(request.path_params['sub'])
        if sub is None:
            raise HTTPException(404)
        if not need_question:
            return sub, None
        q = exam.questions.get(request.path_params['q'])
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
                extra = getattr(e, 'current', None)
                return JSONResponse({'error': str(e) or "changed elsewhere", 'conflict': True,
                                     **({'current': extra} if extra is not None else {})}, 409)
            except ValueError as e:
                return JSONResponse({'error': str(e)[:300]}, 400)
            return JSONResponse({'ok': True, **(out or {})})
        return endpoint

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
        rows, started = [], False
        for row in grid.load(exam, store):
            cells = []
            started = started or bool(row.sheet.values or row.sheet.results)
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
            blocks = prepare_blocks((row.body or {}).get('blocks')) if row else []
            for label, j in (stored.fields if stored else {}).items():
                judged.setdefault(label, []).append({'check': c.name, **j})
            spec = c.points
            checks.append({'name': c.name, 'column': q.column(c.name), 'st': st, 'glyph': glyph(st),
                           'cls': value_class(st), 'fn': c.fn is not None,
                           'rev': decision.rev if decision else '', 'decided': decision is not None,
                           'bool': isinstance(spec, Bool), 'min': getattr(spec, 'min', None),
                           'max': getattr(spec, 'max', None), 'counts': spec.counts,
                           'step': 1 if getattr(spec, 'type', None) is int else 'any',
                           'stored': stored,
                           'current': stored is not None and status.current(
                               stored, sheet.values, defn=exam.check_hash(q, c), code=exam.code_hash),
                           'figures': [b for b in blocks if b['type'] == 'image'],
                           'rest': [b for b in blocks if b['type'] != 'image']})

        # the fields on the page: its own, then those its checks read elsewhere
        own = [key(q.id, label) for label in q.labels]
        keys = own + [k for c in checks if c['stored'] for k in c['stored'].reads if k not in own]
        keys = list(dict.fromkeys(keys))
        fields = []
        for k in keys:
            qid, label = split_key(k)
            other = exam.questions.get(qid)
            f = other.field(label) if other else None
            if f is None:
                continue
            v = sheet.values.get(k)
            reader = other.reader_of(label)
            region = ws.region(sub.id, other, label)
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
            can_read=bool(readers_), off=bool(readers_) and not have_model(),
            pages=pages, pages_default=q.pages, own=own, extras=max(0, count - exam.pages),
            regions=regions, print_area=levels(lay, PRINT_AREA, list(exam.base_attrs['print_area']), stored),
            scopes={'own': overrides.scope(SUBMISSION, sub.id), 'all': '',
                    'scan': overrides.scope(SOURCE, lay.source) if lay.source else None},
            first_page=(q.pages or (1,))[0], ids=[s.id for s in store.submissions()],
            prev_q=qids[i - 1] if i else None, next_q=qids[i + 1] if i is not None and i + 1 < len(qids) else None)

    def field_of(data: dict, k: Any) -> tuple[str, str]:
        if not isinstance(k, str) or '/' not in k:
            raise Bad("unknown field")
        qid, label = split_key(k)
        if qid not in exam.questions or exam.questions[qid].field(label) is None:
            raise Bad("unknown field")
        return qid, label

    def save(request, data):
        """Set the values a person changed (each verified by them), then verify
        the ones listed: 'save', 'save and verify', 'verify all here'."""
        sub, q = target(request)
        changed, verify = data.get('values') or {}, data.get('verify') or []
        if not isinstance(changed, dict) or not isinstance(verify, list):
            raise Bad("unexpected content")
        author, revs = who(request), {}
        for k, item in changed.items():
            qid, label = field_of(data, k)
            text = item.get('text') if isinstance(item, dict) else None
            if not isinstance(text, str) or len(text) > MAX_BODY:
                raise Bad("unexpected content")
            v = runner.set(sub.id, qid, label, text, author=author, expect_rev=_rev(item))
            revs[k] = v.rev
        for item in verify:
            k = item.get('key') if isinstance(item, dict) else None
            qid, label = field_of(data, k)
            if k in revs:
                continue            # set just now: verified by this person
            runner.verify(sub.id, qid, label, author=author, expect_rev=_rev(item))
        return {'revs': revs, 'job': refresh(sub.id, q.id) if revs else None}

    def unverify(request, data):
        sub, q = target(request)
        qid, label = field_of(data, data.get('key'))
        rev = _rev(data)
        if rev is None:
            raise Bad("nothing to unverify")
        runner.verify(sub.id, qid, label, author=who(request), expect_rev=rev, verified=False)

    def check_of(q: Question, data: dict):
        c = q.check(data.get('check')) if isinstance(data.get('check'), str) else None
        if c is None:
            raise Bad("unknown check")
        return c

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
        d = runner.decide(sub.id, q.id, c.name, value, author=who(request), flags=flags,
                          expect_rev=_rev(data), seen=seen)
        return {'rev': d.rev}

    def clear(request, data):
        sub, q = target(request)
        runner.clear(sub.id, q.id, check_of(q, data).name, expect_rev=_rev(data))

    def read(request, data):
        """Read this question's fields again (not what a person verified, unless asked)."""
        sub, q = target(request)
        if not have_model():
            raise Bad("model unavailable")
        protected = bool(data.get('include_protected'))

        def work():
            outs = runner.read(sub.id, q.id, include_protected=protected, fresh=True)
            return outs + runner.refresh(sub.id, questions=[q.id])
        return {'job': jobs.start(work)}

    def run_check(request, data):
        """A grader's explicit run of one check (one that timed out, say)."""
        sub, q = target(request)
        c = check_of(q, data)
        return {'job': jobs.start(lambda: runner.compute(sub.id, q.id, c.name))}

    def to_default(request, data):
        """A field back to its default: its stored value dropped."""
        sub, q = target(request)
        qid, label = field_of(data, data.get('key'))
        runner.unset(sub.id, qid, label, expect_rev=_rev(data))
        return {'job': refresh(sub.id, q.id)}

    def restore(request, data):
        """A field's earlier value back, set by this person."""
        sub, q = target(request)
        qid, label = field_of(data, data.get('key'))
        rev = _rev(data)
        old = next((h for h in store.history(sub.id, key(qid, label), 'value') if h.rev == rev), None)
        if old is None:
            raise Bad("no such version")
        runner.set(sub.id, qid, label, str((old.body or {}).get('text', '')), author=who(request),
                   expect_rev=_rev({'rev': data.get('expect')}))
        return {'job': refresh(sub.id, q.id)}

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
        """Where every field's crop is on its scanned page, by region path."""
        layout = ws.layout(sid)
        return {overrides.join(qid, 'fields', f.label, 'region'): box(layout, f.region)
                for qid, q in exam.questions.items() for f in ws.fields(sid, q) if f.region is not None}

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
        sub, q = target(request)
        row = store.get(sub.id, key(q.id, request.path_params['check']), 'result')
        b = find_image((row.body or {}).get('blocks'), request.path_params['path']) \
            if row and isinstance(row.body, dict) else None
        if b is None:
            raise HTTPException(404)
        try:
            data = base64.b64decode(b['data'], validate=True)
        except ValueError:
            raise HTTPException(404) from None
        # The URL names the data's version: cache it for good; any other version, not at all.
        fresh = request.query_params.get('v') == image_version(b)
        return Response(data, media_type=b['media_type'], headers={
            'Content-Security-Policy': IMAGE_CSP, 'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, max-age=31536000, immutable' if fresh else 'no-store'})

    def job(request: Request) -> Response:
        info = jobs.get(request.path_params['id'])
        if info is None:
            raise HTTPException(404)
        return JSONResponse(info)

    def read_page(request: Request) -> Response:
        return page('read.html', questions=list(exam.grading.values()),
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
        return batch.plan(exam, store, sel, model_available=have_model(), regions=ws.fields, missing=ws.missing)

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

    def read_start(request, data):
        """Read what the selection would read now (planned here, at the start)."""
        with lock:
            current: Batch | None = request.app.state.batch
            if current is not None and current.active:
                raise Conflict("a read is already in progress")
            plan = batch_plan(data)
            run = batch.BatchRun(runner, plan, parallel=_int(data.get('parallel', 2), "parallel", 1, 16))
            request.app.state.batch = Batch(run)
            request.app.state.batch.start()
        return {'total': plan.count('read')}

    def read_status(request: Request) -> Response:
        current: Batch | None = request.app.state.batch
        return JSONResponse(current.info() if current else {'state': 'idle'})

    def read_stop(request, data):
        current: Batch | None = request.app.state.batch
        if current is not None:
            current.run.cancel()

    # --- settings ---

    def settings_page(request: Request) -> Response:
        return page('settings.html')

    # --- exam data: overrides of the package's plain data, for every submission ---

    def exam_page(request: Request) -> Response:
        stored = store.overrides()
        revs = {p: o.rev for p, o in stored.items()}
        by_q: dict[str, list] = {}
        for e in overrides.editable(exam):
            root = overrides.split(e.path)[0]
            by_q.setdefault(overrides.AT if root[1:] in exam.ATTRS else root, []).append(entry_view(e, revs))
        titles = {overrides.AT: "print area"}
        questions = [{'id': qid, 'title': titles.get(qid) or getattr(exam.questions.get(qid), 'title', ''),
                      'entries': items} for qid, items in by_q.items()]
        skipped = []
        for path, o in stored.items():
            if path not in exam.overrides:
                try:
                    overrides.validate(exam, path, o.value)
                    reason = "not applied"
                except ValueError as e:
                    reason = str(e)
                skipped.append({'path': path, 'reason': reason})
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
        requesting page's submission first; say which fields to offer to read again,
        and what is now in force at that scope (and, for the page's submission, at each)."""
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
        if level == EXAM:
            entry = overrides.entry(exam, path)
            value = entry.current if entry else None
        else:
            value = ws.layers(**{level: id_}).value(path) if kind else None
        out = {'rev': o.rev if o else None, 'value': value, 'text': show_value(kind, value),
               'overridden': o is not None, 'checks': [f'{q}.{c}' for q, c in change.checks],
               'affected': len(change.checks) * len(subs), 'job': job,
               'reread': [f'{q}/{label}' for q, label in change.fields],
               'skipped': [{'path': s.path, 'reason': s.reason} for s in change.skipped]}
        if first in subs and kind in ('region', 'print_area'):
            lay = ws.layers(first)
            out['levels'] = levels(lay, path, overrides.entry(exam, path).base, store.overrides())
            if kind == 'print_area':
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
        level, id_ = overrides.parse_scope(scope)
        if level == SUBMISSION and isinstance(data.get('value'), list) and overrides.kind(exam, path) == 'pages':
            last = exam.pages + ws.extras(id_)          # one submission's pages may name its extra sheets
            if any(isinstance(p, int) and p > last for p in data['value']):
                raise Bad(f"pages must be in 1..{last}" + (f" (pages beyond {exam.pages}: its extra sheets)" if last > exam.pages else ""))
        try:
            with lock:
                change = overrides.set(exam, store, path, data.get('value'), scope=scope, author=who(request),
                                       expect_rev=rev)
        except Conflict as e:
            raise _conflict(e, path, scope) from None
        return _changed(path, scope, change, data)

    def exam_clear(request, data):
        path, rev, scope = _path(data), _rev(data), _scope(data, False)
        try:
            with lock:
                change = overrides.clear(exam, store, path, scope=scope, expect_rev=rev)
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
        art = intake.plan(ws, src.id)
        body = {**art.body, 'pages': exam.pages}
        runs, doubts, lays, states = split.runs(body), split.doubts(body), split.layouts(body), intake.state(ws, src.id)
        made, turns, L = store.made(), split.turns(body), body['sheets']
        entries = []
        for k, (e, p) in enumerate(zip(split.entries(body), split.starts(body))):
            sid = e.get('id')
            entries.append({'start': L[p]['i'], 'verified': bool(e.get('confirmed')), 'id': sid,
                            'pages': runs[k], 'lay': lays[k], 'proposed': e.get('proposed') or {},
                            'doubts': [split.DOUBTS[d] for d in doubts[k]], 'state': states[k],
                            'name': identity.who(ws, sid)['name'] if sid in made else ''})
        return {'id': src.id, 'name': src.name, 'n': src.pages, 'rev': art.rev, 'length': body['length'],
                'checked': bool(body.get('checked')) and bool(body.get('readings')), 'failed': body.get('failed', 0),
                'order': [r['i'] for r in L], 'turn': [turns[i] for i in range(src.pages)],
                'entries': entries, 'dropped': split.dropped(body), 'missing': [r['i'] for r in L if r.get('missing')],
                'readings': {i: None if r is None else f'p.{r + 1}' for i, r in split.readings(body).items()},
                'removed': intake.removed(ws, src.id), 'cursor': split.cursor(body),
                'undo': intake.can_undo(ws, src.id), 'idboxes': idboxes(src.id)}

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
            states = intake.state(ws, src.id)
            counts[src.id] = {'open': sum(not e.get('confirmed') for e in split.entries(body)),
                              'write': sum(st != 'made' for st in states) + len(intake.removed(ws, src.id))}
        roster = [{k: p[k] for k in ('name', 'id', 'email', 'key')} for p in identity.people(ws)]
        return page('scans.html', sources=sources, counts=json.dumps(counts), people=roster,
                    expected=intake.expected_length(ws), idboxes=json.dumps(boxes), fields=identity.placed(ws),
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
        intake.edit(ws, src.id, op, expect_rev=_rev(data), author=who(request), **kw)
        return {'plan': plan_view(src)}

    def scans_propose(request, data):
        src = the_source(request)
        length = data.get('length')
        length = None if length in (None, '') else _int(length, "pages per submission", 1, 999)
        kw = dict(length=length, author=who(request), expect_rev=_rev(data))
        if data.get('check'):
            if not have_model():
                raise Bad("model unavailable")
            if not exam.landmarks:
                raise Bad("the exam declares no landmarks")

            def work():
                intake.reguess(ws, src.id, model=runner.model, **kw)
            return {'job': jobs.start(work)}
        intake.reguess(ws, src.id, **kw)
        return {'plan': plan_view(src)}

    def scans_undo(request, data):
        src = the_source(request)
        intake.undo(ws, src.id, expect_rev=_rev(data))
        return {'plan': plan_view(src)}

    def scans_accept(request, data):
        src = the_source(request)
        with lock:
            report = intake.accept(ws, src.id, force=bool(data.get('force')),
                                   expect_rev=_rev(data))
        return {'report': report, 'plan': plan_view(src)}

    def scans_roster(request, data):
        """A roster as text (a CSV file's contents or pasted lines). `dry`: only
        say what would be read: how many people, the first few, the problems."""
        text = data.get('text', data.get('csv'))
        if not isinstance(text, str) or len(text) > MAX_ROSTER:
            raise Bad("expected roster text")
        people, problems = identity.read_roster(text)
        out = {'people': len(people), 'problems': problems[:ROSTER_PROBLEMS],
               'more': max(0, len(problems) - ROSTER_PROBLEMS),
               'sample': [{'name': p['name'], 'id': p['id'], 'email': p['email']} for p in people[:ROSTER_SAMPLE]]}
        if not data.get('dry'):
            identity.set_roster(ws, text)
        return out

    def scans_who(request, data):
        sub = store.submission(str(data.get('sub')))
        if sub is None:
            raise Bad("unknown submission")
        text = data.get('text')
        if text is not None and not isinstance(text, str):
            raise Bad("expected text")
        kw = {'expect_rev': _rev(data)} if 'rev' in data else {}
        return {'who': identity.set_who(ws, sub.id, text or '', author=who(request), **kw)}

    proposals: dict = {}        # identity.overview's cache: proposals by values and roster

    def scans_names(request: Request) -> Response:
        """Every submission's identity values, proposal and pick; where each identity
        field is (its crop, this submission's own region); and the regions for every
        submission, for the box editor."""
        with lock:
            view = identity.overview(ws, cache=proposals)
        paths = {f.label: overrides.join(IDENTITY, 'fields', f.label, 'region') for f in identity.group(ws).fields}
        for row in view['rows']:
            sid = row['sub']
            layout = ws.layout(sid)
            row['crops'] = [{'name': f.label, 'index': f.region.page - 1, 'box': box(layout, f.region)}
                            for f in identity.placed(ws, sid)]
            mine = ws.layers(sid).stored.get(SUBMISSION, {})
            row['own'] = {c: {'value': mine[p].value, 'rev': mine[p].rev} for c, p in paths.items() if p in mine}
            row['print_area'] = list(layout.print_area)     # for the boxes drawn on the page
        stored = store.overrides()
        view['nobody'] = [identity.label(p) for p in view['nobody']]
        view['fields'] = [{'name': f.label} for f in identity.placed(ws)]
        view['columns'] = []
        for c, path in paths.items():           # the regions for every submission, for the box editor
            e = overrides.entry(exam, path)
            view['columns'].append({'name': c, 'path': path, 'value': e.current, 'base': e.base,
                                    'rev': stored[path].rev if path in stored else None})
        view['pad'] = exam.crop_pad
        view['doubts'] = identity.DOUBTS
        view['roster'] = len(identity.people(ws))
        return JSONResponse(view)

    def scans_names_read(request, data):
        if not have_model():
            raise Bad("model unavailable")
        if not any(identity.placed(ws, s.id) for s in store.submissions()):
            raise Bad("no name or ID boxes: draw them first")
        fresh = bool(data.get('fresh'))
        subs = [s.id for s in store.submissions()] if fresh else identity.unread(ws)
        return {'job': jobs.start_each([(s,) for s in subs], lambda sid: identity.read(runner, sid, fresh=fresh)),
                'n': len(subs)}

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

    def post(path, fn):
        return Route(path, action(fn), methods=['POST'])

    routes = [
        Route('/', grid_page),
        Route('/filter', filter_page),
        Route('/focus/values', focus_values),
        Route('/s/{sub}/{q}', question_page),
        post('/s/{sub}/{q}/save', save),
        post('/s/{sub}/{q}/unverify', unverify),
        post('/s/{sub}/{q}/decide', decide),
        post('/s/{sub}/{q}/clear', clear),
        post('/s/{sub}/{q}/read', read),
        post('/s/{sub}/{q}/check', run_check),
        post('/s/{sub}/{q}/default', to_default),
        post('/s/{sub}/{q}/restore', restore),
        Route('/page/{sub}/{index:int}.jpg', page_image),
        Route('/image/{sub}/{q}/{check}/{path}', evidence_image),
        Route('/jobs/{id}', job),
        Route('/read', read_page),
        post('/read/plan', read_plan),
        post('/read/start', read_start),
        post('/read/stop', read_stop),
        Route('/read/status', read_status),
        Route('/settings', settings_page),
        Route('/exam', exam_page),
        post('/exam/set', exam_set),
        post('/exam/clear', exam_clear),
        Route('/scans', scans_page),
        Route('/scans/upload', scans_upload, methods=['POST']),
        post('/scans/roster', scans_roster),
        post('/scans/who', scans_who),
        Route('/scans/names', scans_names),
        post('/scans/names/read', scans_names_read),
        Route('/scans/{src}/plan', scans_plan),
        post('/scans/{src}/edit', scans_edit),
        post('/scans/{src}/propose', scans_propose),
        post('/scans/{src}/undo', scans_undo),
        post('/scans/{src}/accept', scans_accept),
        Route('/sheet/{src}/{index:int}.jpg', sheet_image),
        Mount('/static', StaticFiles(directory=HERE / 'static'), name='static'),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(Guard, public_url=public_url)
    app.state.batch = None
    # Results outdated while the app was down (the exam's code or data changed) are recomputed now.
    app.state.startup = jobs.start_each([(s.id,) for s in store.submissions()], runner.refresh)
    return app
