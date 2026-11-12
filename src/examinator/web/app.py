"""The app: server-rendered pages plus a few JSON actions (DESIGN §13).

    GET  /                          grid (?focus=... applies a shared focus)
    GET  /filter                    the filter page: facets of every question
    GET  /focus/values              facet values for all submissions (?sub=: one), JSON
    GET  /s/{sub}/{q}               question page
    POST /s/{sub}/{q}/{action}      save verify decide undecide variant cue run pages restore
    POST /s/{sub}/layout            per-submission layout override
    GET  /page/{sub}/{index}.jpg    cached rendering of one scanned page
    GET  /raw/{sub}/{q}/{path}      one evidence 'raw' block, sandboxed
    GET  /jobs/{id}                 background job state
    GET  /scan                      scan window that follows the question page
    GET  /runs                      batch runs;  POST /runs/preview|start|stop,  GET /runs/status
    GET  /settings                  workspace defaults;  POST /settings
    GET  /exam                      exam data (overrides);  POST /exam/set|clear|variants
    GET  /scans                     intake: sources, split plans, accept;  GET /scans/{src}/plan (JSON)
    POST /scans/upload?name=        a scanned PDF as the raw request body (streamed to disk)
    POST /scans/{src}/{action}      edit propose undo accept;  POST /scans/roster, /scans/who
    GET  /scans/names               who wrote each submission: readings, proposals, picks (JSON)
    POST /scans/names/read          read the name and ID boxes with the model (a job)
    GET  /sheet/{src}/{index}.jpg   one sheet of a source (?o= orientation, ?w= width)

Every POST takes a JSON object (or a form), needs the page token, and answers
JSON: 403 bad token, 404 unknown target, 409 stale revision or plan, 400 input
a grader should correct. Nothing here keeps module-level state; it all hangs
off `app.state`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from collections import OrderedDict
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

from ..core import batch, focus, grid, identity, intake, overrides, split
from ..core.defs import Bool, Exam, Question, grade_kind, has_marker
from ..core.runner import Runner
from ..core.store import Conflict
from ..core import variants
from ..core.variants import resolved
from ..core.workflow import evidence_current, question_status, seen_revs, variant_trusted
from ..core.workspace import Workspace
from ..pdf.layout import Layout
from ..pdf.orient import Orient
from .jobs import Batch, Jobs

HERE = Path(__file__).parent
LOOPBACK = ('127.0.0.1', 'localhost', '[::1]')
MAX_BODY = 400_000          # characters of an edited artifact
MAX_UPLOAD = 4 << 30        # bytes of one scanned PDF
SHEET_WIDTHS = (140, 360, 1000)   # pixel widths a sheet is served at (0: as rendered)
MAX_ROSTER = 2_000_000      # characters of roster text
ROSTER_PROBLEMS = 20        # problems a roster preview lists
ROSTER_SAMPLE = 5           # people a roster preview shows
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
RAW_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
# What a person sees of a workflow state: one word per idea (DESIGN.md, Vocabulary). Needs
# review is shown as unverified (its grade is the undecided glyph ?): a person is asked either way.
STATE_LABELS = {
    'not_started': "not started", 'in_progress': "in progress", 'unverified': "unverified",
    'graded': "graded", 'needs_review': "unverified", 'outdated': "outdated",
    'manual': "manual", 'manual_outdated': "manual, outdated"}
REASONS = {"by hand": "manual"}       # workflow's words for an undecided grade, as shown


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


def _window(value: Any) -> list[int] | None:
    if value in (None, '', []):
        return None
    if isinstance(value, str):
        value = value.replace(',', ' ').split()
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise Bad("print area: expected left, top, right, bottom")
    l, t, r, b = (_int(v, "print area", 0, 1000) for v in value)
    if l >= r or t >= b:
        raise Bad("print area: left < right and top < bottom")
    return [l, t, r, b]


def _strings(value: Any, allowed, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Bad(f"{what}: expected a list")
    unknown = [v for v in value if v not in allowed]
    if unknown:
        raise Bad(f"{what}: unknown {unknown[0]!r}"[:200])
    return tuple(dict.fromkeys(value))


def _params(text: Any) -> dict:
    """'key=value key2=value2' -> dict. Values that read as JSON scalars are converted."""
    out = {}
    for item in str(text or '').replace(',', ' ').split():
        key, sep, value = item.partition('=')
        if not sep or not key:
            raise Bad("params: expected key=value")
        try:
            v = json.loads(value)
            out[key] = v if isinstance(v, (bool, int, float, str)) else value
        except ValueError:
            out[key] = value
    return out


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
        return {'needs_review': '?', 'unverified': '?', 'in_progress': '·'}.get(st.state, '')
    return '∅' if text == 'blank' else text


def value_class(st) -> str:
    return {None: 'v-n', 'blank': 'v-b', '✓': 'v-t', '✗': 'v-f'}.get(focus.grade_text(st), 'v-num')


def value_key(st) -> str:
    """The value as the grid's sort key (a blank sorts by its value)."""
    text = focus.grade_text(st, blank=False)
    return {None: '', '✓': 't', '✗': 'f'}.get(text, text)


def prepare_blocks(blocks: Any, prefix: str = '') -> list[dict]:
    """Evidence blocks ready for the template: unknown shapes dropped, raw blocks
    given the path that addresses them, plots given their series as JSON."""
    out = []
    for i, b in enumerate(blocks if isinstance(blocks, list) else []):
        if not isinstance(b, dict) or b.get('audience', 'all') not in ('all', 'grader'):
            continue
        path = f'{prefix}{i}'
        kind = b.get('type')
        if kind == 'details':
            out.append({'type': 'details', 'summary': str(b.get('summary', "")),
                        'blocks': prepare_blocks(b.get('blocks'), path + '-')})
        elif kind == 'raw':
            out.append({'type': 'raw', 'path': path})
        elif kind == 'plot':
            out.append({'type': 'plot', 'plot': {k: b.get(k) for k in ('title', 'xlabel', 'ylabel', 'series')}})
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


def find_raw(blocks: Any, path: str) -> str | None:
    """The html of the raw block at `path` ('2' or '1-0-3'), or None."""
    node: Any = None
    for part in path.split('-'):
        if not part.isdigit() or len(part) > 4 or not isinstance(blocks, list):
            return None
        i = int(part)
        if i >= len(blocks) or not isinstance(blocks[i], dict):
            return None
        node = blocks[i]
        blocks = node.get('blocks')
    if node is None or node.get('type') != 'raw' or not isinstance(node.get('html'), str):
        return None
    return node['html']


# --- exam data (overrides) ---------------------------------------------------------

LONG_TEXT = 160      # characters: longer text entries start collapsed


def show_value(kind: str, value: Any) -> str:
    """An entry's JSON value as the text its input holds (see exam.js `readEntry`)."""
    if value is None:
        return ''
    if kind == 'pages':
        return ', '.join(str(p) for p in value)
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


def key_entries(q: Question, entries: list[overrides.Entry], revs: dict, variant: str | None) -> list[dict]:
    """A question's key entries (stage configuration under 'key'), for one variant
    when the key is per variant and the variant is known."""
    out = []
    for e in entries:
        segs = overrides.split(e.path)[1:]
        if len(segs) < 4 or segs[0] != 'stages' or segs[2] != 'key':
            continue
        rest = segs[3:]
        if len(rest) > 1 and rest[0] in q.variants:
            if variant is not None and rest[0] != variant:
                continue
            if variant is not None:
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
    with_overrides = overrides.refresh(exam, store)
    plans: OrderedDict[str, batch.Plan] = OrderedDict()
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

    def action(fn: Callable[[Request, dict], Any]):
        """A state-changing endpoint: token, body, and the errors graders can fix."""
        async def endpoint(request: Request) -> Response:
            data: Any = {}
            try:
                if 'json' in request.headers.get('content-type', ''):
                    raw = await request.body()
                    data = json.loads(raw) if raw else {}
                else:
                    data = dict(await request.form())
            except Exception:
                data = None
            sent = request.headers.get('x-token') or (data.get('token') if isinstance(data, dict) else '') or ''
            if not hmac.compare_digest(str(sent).encode(), token.encode()):
                return JSONResponse({'error': "bad token"}, 403)
            if not isinstance(data, dict):
                return JSONResponse({'error': "bad request body"}, 400)
            data.pop('token', None)
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

    def recheck(sid: str, qid: str) -> str:
        return jobs.start(lambda: runner.recheck(sid, qid))

    # --- grid ---

    def grid_page(request: Request) -> Response:
        columns = []      # (question, part, column name)
        for q in exam.questions.values():
            for part, spec in q.result.items():
                columns.append({'q': q, 'part': part, 'name': q.column(part), 'counts': spec.counts})
        sel = selection(request.query_params.get('focus'))
        table = values() if sel else {}
        rows = []
        for row in grid.load(exam, store):
            cells = []
            for q in exam.questions.values():
                cell = row.cells[q.id]
                if q.variants:
                    cells.append({'variant': True, 'qid': q.id, 'text': cell.variant or '?',
                                  'open': cell.variant is None})
                for part in q.result:
                    st = cell.status[part]
                    cells.append({'qid': q.id, 'col': q.column(part), 'state': st.state,
                                  'glyph': glyph(st), 'cls': value_class(st), 'v': value_key(st),
                                  'blank': 'blank' in st.flags, 'counts': st.counts})
            rows.append({'sub': row.submission, 'cells': cells, 'points': f'{row.points:g}',
                         'complete': row.complete, 'sort': row.points,
                         'hidden': bool(sel) and not focus.matches(table, sel, row.submission.id)})
        started = any(c.get('state', 'not_started') != 'not_started' or c.get('variant') and not c['open']
                      for r in rows for c in r['cells'])
        return page('grid.html', rows=rows, columns=columns, questions=list(exam.questions.values()),
                    focus=focus.active(table, sel), started=started)

    # --- focus ---

    cached: dict[str, Any] = {}

    def values() -> dict:
        """The whole values table, recomputed only when stored rows have changed."""
        key = store.version()
        with lock:
            if cached.get('key') == key:
                return cached['table']
        table = focus.values_table(exam, store)
        with lock:
            cached.update(key=key, table=table)
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
                             'values': values() if sid is None else focus.values_table(exam, store, submission=sid)})

    # --- question page ---

    def question_page(request: Request) -> Response:
        sub, q = target(request)
        arts = store.for_question(sub.id, q.id)
        status = question_status(q, arts, code_hash=exam.code_hash)
        layout = ws.layout(sub.id)
        try:
            count = ws.scan_page_count(sub.id)
        except Exception:
            count = 0

        def crop(region, *path):
            if region is None:
                return None
            index = layout.scan_index(region.page)
            if not 0 <= index < count:
                return None
            return {'index': index, 'box': ','.join(f'{v:.4f}' for v in layout.box(region, exam.crop_pad)),
                    'path': overrides.join(q.id, *path)}

        pages, note = ws.selected_pages(sub.id, q)
        scans = [{'page': p, 'index': layout.scan_index(p)} for p in pages
                 if 0 <= layout.scan_index(p) < count]

        def editor(kind):
            stage, art = q.stage(kind), arts.get(kind)
            if stage is None:
                return None
            return {'kind': kind, 'art': art, 'rev': art.rev if art else '', 'editable': stage.editable,
                    'marker': art is not None and has_marker(art.body)}

        parse = editor('parse')
        if parse:
            body = parse['art'].body if parse['art'] else None
            # No declared body shape: follow the stored body; with none, a form if there are fields.
            parse['mode'] = ('code' if isinstance(body, str) else 'form' if isinstance(body, dict)
                             else 'form' if q.stage('parse').shape == 'fields' else 'code')
            regions = {f.label: f.region for f in q.fields}
            if parse['mode'] == 'form':
                body = body if isinstance(body, dict) else {}
                names = list(q.labels) + [k for k in body if k not in q.labels]
                parse['fields'] = [{'label': n, 'value': '' if body.get(n) is None else str(body[n]),
                                    'crop': crop(regions.get(n), 'fields', n, 'region'),
                                    'marker': has_marker(body.get(n))}
                                   for n in names]
            else:
                parse['text'] = body or ''
                parse['crops'] = [c for c in (crop(r, 'fields', n, 'region') for n, r in regions.items()) if c]
        # A question without a parse stage (decided by hand) still shows its answer areas.
        loose = [] if parse else [{'label': f.label, 'crop': c} for f in q.fields
                                  if (c := crop(f.region, 'fields', f.label, 'region'))]
        transcript = editor('transcript')
        if transcript:
            transcript.update(mode='code', text=transcript['art'].body if transcript['art'] else '')

        variant = None
        if q.variants:
            art = arts.get('variant')
            body = art.body if art and isinstance(art.body, dict) else {}
            cues = body.get('cues') or {}
            variant = {
                'art': art, 'rev': art.rev if art else '', 'resolved': resolved(body),
                'candidates': body.get('candidates') or [], 'trusted': variant_trusted(q, arts),
                'manual': art is not None and art.origin == 'manual',
                'cues': [{'name': c.name, 'ask': c.ask, 'crop': crop(c.region, 'cues', c.name, 'region'),
                          'fixed': c.fn is not None,
                          'reading': (cues.get(c.name) or {}).get('reading'),
                          'variant': (cues.get(c.name) or {}).get('variant')} for c in q.cues]}

        ev = arts.get('evidence')
        blocks = prepare_blocks((ev.body or {}).get('blocks')) if ev and isinstance(ev.body, dict) else []
        evidence = {'art': ev, 'current': evidence_current(q, arts),
                    'plots': [b for b in blocks if b['type'] == 'plot'],
                    'rest': [b for b in blocks if b['type'] != 'plot']}

        def todo(st) -> str:
            """What a person must do for a part, in one phrase without repeated words."""
            out = []
            if st.waiting == ('input',):
                out.append("decide")
            elif st.waiting:
                out.append("verify " + ", ".join(w for w in st.waiting if w != 'input'))
            reason = REASONS.get(st.reason, st.reason) if st.value is None else None
            if reason and not ('variant' in st.waiting and 'variant' in reason):
                out.append(reason)
            return " · ".join(out)

        parts = []
        for part, spec in q.result.items():
            st, g = status[part], arts.get(grade_kind(part))
            parts.append({'part': part, 'name': q.column(part), 'st': st, 'glyph': glyph(st),
                          'cls': value_class(st), 'rev': g.rev if g else '',
                          'manual': g is not None and g.origin == 'manual',
                          'bool': isinstance(spec, Bool), 'min': getattr(spec, 'min', None),
                          'max': getattr(spec, 'max', None),
                          'step': 1 if getattr(spec, 'type', None) is int else 'any', 'todo': todo(st)})

        def replaces(names):
            """What a deliberate run of these stages would overwrite of a grader's work."""
            out = []
            for n in names:
                a = arts.get(n)
                if n not in ('evidence', 'grade') and a is not None and a.protected:
                    out.append(f"{'verified' if a.verified else 'edited'} {n}")
                if n in ('evidence', 'grade') and any(p['manual'] for p in parts) and "manual grade" not in out:
                    out.append("manual grade")
            return ", ".join(out)

        names = [s.name for s in q.stages]
        stages = [{'name': s.name, 'model': s.uses_model, 'off': s.uses_model and not have_model(),
                   'replaces': replaces([s.name]), 'replaces_on': replaces(names[i:])} for i, s in enumerate(q.stages)]
        # what a person is asked to verify before the grades count, parse first
        waiting = [w for w in ('parse', 'transcript', 'variant') if any(w in p['st'].waiting for p in parts)]
        history = [{'rev': a.rev, 'at': a.updated_at[:16].replace('T', ' '), 'who': a.author or a.origin,
                    'verified': a.verified,
                    'preview': (a.body if isinstance(a.body, str) else json.dumps(a.body, ensure_ascii=False))[:300]}
                   for a in store.history(sub.id, q.id, 'parse')[:30]]
        qids = list(exam.questions)
        i = qids.index(q.id)
        over = store.get(sub.id, '', 'layout')
        revs = {p: o.rev for p, o in store.overrides().items()}
        entries = overrides.editable(exam, q.id)
        regions = []
        for e in entries:
            if e.kind == 'region':
                segs = overrides.split(e.path)
                regions.append({'path': e.path, 'label': segs[2], 'cue': segs[1] == 'cues', 'region': e.current,
                                'base': e.base, 'stored': e.path in revs, 'rev': revs.get(e.path)})
        keys = key_entries(q, entries, revs, variant['resolved'] if variant else None)
        return page(
            'question.html', sub=sub, q=q, parts=parts, scans=scans, parse=parse, loose=loose, transcript=transcript,
            variant=variant, evidence=evidence, stages=stages, history=history, count=count,
            seen=seen_revs(q, arts), waiting=waiting,
            pages=pages, note=note, pages_default=q.pages, layout=layout,
            layout_over=over.body if over else None, regions=regions, keys=keys,
            first_page=(q.pages or (1,))[0],
            ids=[s.id for s in store.submissions()],
            prev_q=qids[i - 1] if i else None, next_q=qids[i + 1] if i + 1 < len(qids) else None)

    def save(request, data):
        sub, q = target(request)
        kind, body = data.get('kind'), data.get('body')
        if kind not in ('parse', 'transcript'):
            raise Bad("not editable")
        cur = store.get(sub.id, q.id, kind)
        if kind == 'transcript' or (cur is not None and isinstance(cur.body, str)):
            ok = isinstance(body, str)
        elif cur is not None:
            ok = isinstance(body, dict)
        else:
            ok = isinstance(body, (str, dict))
        if ok and isinstance(body, dict):
            ok = all(isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in body.items())
        if not ok or len(json.dumps(body)) > MAX_BODY:
            raise Bad("unexpected content")
        art = runner.save(sub.id, q.id, kind, body, author=who(request), expect_rev=_rev(data),
                          verify=bool(data.get('verify')))
        return {'rev': art.rev, 'job': recheck(sub.id, q.id)}

    def verify(request, data):
        sub, q = target(request)
        kind = data.get('kind')
        stage = q.stage(kind) if isinstance(kind, str) else None
        if stage is None or not stage.editable:
            raise Bad("nothing to verify")
        rev = _rev(data)
        if rev is None:
            raise Bad("nothing to verify")
        art = runner.verify(sub.id, q.id, kind, author=who(request), expect_rev=rev,
                            verified=data.get('verified', True) is not False)
        return {'rev': art.rev, 'job': recheck(sub.id, q.id)}

    def part_of(q: Question, data: dict) -> str:
        part = data.get('part', '')
        if part not in q.result:
            raise Bad("unknown part")
        return part

    def decide(request, data):
        sub, q = target(request)
        part = part_of(q, data)
        spec, value, flags = q.result[part], data.get('value'), ()
        if data.get('blank'):
            value, flags = spec.coerce('blank').value, ('blank',)
        elif isinstance(value, str):
            value = spec.parse(value)
        seen = data.get('seen')
        if isinstance(seen, str):          # a form field
            try:
                seen = json.loads(seen)
            except ValueError:
                seen = None
        if not isinstance(seen, dict):
            raise Bad("seen is required")
        seen = {str(k): _rev({'rev': v}) for k, v in seen.items()}
        art = runner.decide(sub.id, q.id, part, value, author=who(request), flags=flags,
                            expect_rev=_rev(data), seen=seen)
        return {'rev': art.rev}

    def undecide(request, data):
        sub, q = target(request)
        runner.undecide(sub.id, q.id, part_of(q, data), expect_rev=_rev(data))

    def choose_variant(request, data):
        sub, q = target(request)
        variant = data.get('variant') or None
        if not q.variants or (variant is not None and variant not in q.variants):
            raise Bad("unknown variant")
        runner.choose_variant(sub.id, q.id, variant, author=who(request), expect_rev=_rev(data))
        return {'job': recheck(sub.id, q.id)}

    def correct_cue(request, data):
        sub, q = target(request)
        reading = data.get('reading')
        if reading is not None and not isinstance(reading, str):
            raise Bad("reading: expected text")
        reading = (reading or '').strip()[:200] or None
        runner.correct_cue(sub.id, q.id, str(data.get('cue')), reading, author=who(request),
                           expect_rev=_rev(data))
        return {'job': recheck(sub.id, q.id)}

    def run(request, data):
        sub, q = target(request)
        name = data.get('stage')
        stage = q.stage(name) if isinstance(name, str) else None
        if stage is None:
            raise Bad("unknown stage")
        if stage.uses_model and not have_model():
            raise Bad("model unavailable")
        params = _params(data.get('params'))
        # A grader's explicit run: it replaces protected content and manual grades
        # (kept in history); the page says so on the button and asks first.
        kw = dict(params=params, reasoning=bool(data.get('reasoning')), deliberate=True)
        if data.get('onward'):
            return {'job': jobs.start(lambda: runner.run_from(sub.id, q.id, name, **kw))}
        return {'job': jobs.start(lambda: runner.run_stage(sub.id, q.id, name, **kw))}

    def set_pages(request, data):
        sub, q = target(request)
        pages = data.get('pages') or []
        if isinstance(pages, str):
            pages = pages.replace(',', ' ').split()
        if not isinstance(pages, list):
            raise Bad("pages: expected a list")
        pages = [_int(p, "page", 1, exam.pages) for p in pages]
        ws.set_pages(sub.id, q, pages, str(data.get('note') or '')[:2000], author=who(request))

    def restore(request, data):
        sub, q = target(request)
        if 'expect' not in data:
            raise Bad("rev is required")
        try:
            store.restore(sub.id, q.id, 'parse', _rev(data), expect_rev=_rev({'rev': data['expect']}))
        except KeyError:
            raise Bad("no such version") from None
        return {'job': recheck(sub.id, q.id)}

    def set_layout(request, data):
        sub, _ = target(request, need_question=False)
        cover = data.get('cover_pages')
        cover = None if cover in (None, '') else _int(cover, "cover pages", 0, 99)
        ws.set_layout(sub.id, cover_pages=cover, scan_window=_window(data.get('print_area')),
                      author=who(request))

    # --- pages, raw blocks, jobs ---

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

    def raw_block(request: Request) -> Response:
        sub, q = target(request)
        ev = store.get(sub.id, q.id, 'evidence')
        html = find_raw((ev.body or {}).get('blocks'), request.path_params['path']) \
            if ev and isinstance(ev.body, dict) else None
        if html is None:
            raise HTTPException(404)
        # Exam-supplied markup: an opaque origin, no scripts, nothing fetched.
        return HTMLResponse('<!doctype html><meta charset="utf-8">' + html,
                            headers={'Content-Security-Policy': RAW_CSP})

    def job(request: Request) -> Response:
        info = jobs.get(request.path_params['id'])
        if info is None:
            raise HTTPException(404)
        return JSONResponse(info)

    def scan_page(request: Request) -> Response:
        return page('scan.html')

    # --- runs ---

    def runs_page(request: Request) -> Response:
        order = batch.stage_order(exam)
        uses_model = {s.name for q in exam.questions.values() for s in q.stages if s.uses_model}
        return page('runs.html', stages=[{'name': n, 'model': n in uses_model} for n in order],
                    questions=list(exam.questions.values()), total=len(store.submissions()))

    def runs_preview(request, data):
        known = [s.id for s in store.submissions()]
        subs = data.get('submissions')
        subs = tuple(known) if subs is None else _strings(subs, set(known), "submissions")
        questions = data.get('questions')
        questions = tuple(exam.questions) if questions is None else _strings(questions, exam.questions, "questions")
        order = batch.stage_order(exam)
        chosen = _strings(data.get('stages') or [], order, "stages")
        if not subs or not questions or not chosen:
            raise Bad("nothing selected")
        sel = batch.Selection(subs, questions, tuple(n for n in order if n in chosen),
                              missing_only=bool(data.get('missing_only')),
                              include_protected=bool(data.get('include_protected')))
        plan = batch.plan(exam, store, sel, model_available=have_model())
        with lock:
            plans[plan.token] = plan
            while len(plans) > 8:
                plans.popitem(last=False)
        groups: dict[tuple, list[str]] = {}
        for item in plan.items:
            groups.setdefault((item.action, item.stage, item.question, item.reason), []).append(item.submission)
        rank = {a: i for i, a in enumerate(batch.ACTIONS)}
        return {'plan': plan.token, 'counts': {a: plan.count(a) for a in batch.ACTIONS},
                'groups': [{'action': a, 'stage': s, 'question': qn, 'reason': r, 'n': len(ids),
                            'submissions': ids[:200]}
                           for (a, s, qn, r), ids in sorted(groups.items(), key=lambda kv: (rank[kv[0][0]], kv[0][1:]))]}

    def runs_start(request, data):
        with lock:
            plan = plans.get(str(data.get('plan')))
            if plan is None:
                raise Conflict("no such preview; preview again")
            current: Batch | None = request.app.state.batch
            if current is not None and current.active:
                raise Conflict("a run is already in progress")
            fresh = batch.plan(exam, store, plan.selection, model_available=have_model())
            if fresh.token != plan.token:
                raise Conflict("the preview is out of date; preview again")
            request.app.state.batch = Batch(batch.BatchRun(
                runner, plan, parallel=_int(data.get('parallel', 2), "parallel", 1, 16),
                reasoning=bool(data.get('reasoning'))))
            request.app.state.batch.start()
        return {'total': plan.count('run')}

    def runs_status(request: Request) -> Response:
        current: Batch | None = request.app.state.batch
        return JSONResponse(current.info() if current else {'state': 'idle'})

    def runs_stop(request, data):
        current: Batch | None = request.app.state.batch
        if current is not None:
            current.run.cancel()

    # --- settings ---

    def settings_page(request: Request) -> Response:
        subs = store.submissions()
        return page('settings.html', cover=ws.cover_pages, window=ws.scan_window, subs=subs[:500],
                    pages=exam.pages)

    def settings_save(request, data):
        window = _window(data.get('print_area'))
        if window is None:
            raise Bad("print area: expected left, top, right, bottom")
        ws.set_defaults(cover_pages=_int(data.get('cover_pages'), "cover pages", 0, 99), scan_window=window)

    # --- exam data: overrides of the package's plain data, for every submission ---

    def exam_page(request: Request) -> Response:
        stored = store.overrides()
        revs = {p: o.rev for p, o in stored.items()}
        by_q: dict[str, list] = {}
        for e in overrides.editable(exam):
            by_q.setdefault(overrides.split(e.path)[0], []).append(entry_view(e, revs))
        questions = [{'id': qid, 'title': getattr(exam.questions.get(qid), 'title', ''), 'entries': items}
                     for qid, items in by_q.items()]
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
        return page('exam.html', questions=questions, active=active, skipped=skipped,
                    lines=overrides.describe_changes(exam, store),
                    startup=[s.path for s in with_overrides])

    def _path(data: dict) -> str:
        path = data.get('path')
        if not isinstance(path, str) or not path or len(path) > 500:
            raise Bad("path is required")
        return path

    def _changed(path: str, change: overrides.Change, data: dict) -> dict:
        """After an edit: count what it outdated and recheck it in the background,
        the requesting page's submission first."""
        o = store.overrides().get(path)
        subs = [s.id for s in store.submissions()]
        first = data.get('sub')
        if first in subs:
            subs.remove(first)
            subs.insert(0, first)
        affected = 0
        for qid in change.questions:
            q = exam.questions[qid]
            for sid in subs:
                arts = store.for_question(sid, qid)
                if arts:
                    status = question_status(q, arts, code_hash=exam.code_hash)
                    affected += sum(st.state in ('outdated', 'manual_outdated') for st in status.values())
        job = jobs.start_each([(s, qid) for s in subs for qid in change.questions], runner.recheck) \
            if change.questions else None
        segs = overrides.split(path)
        cue = len(segs) == 4 and segs[1] == 'cues' and segs[3] in ('region', 'ask')
        entry = next((e for e in overrides.editable(exam, segs[0]) if e.path == path), None)
        return {'rev': o.rev if o else None, 'value': entry.current if entry else None,
                'text': show_value(entry.kind, entry.current) if entry else '',
                'overridden': o is not None, 'questions': list(change.questions), 'affected': affected,
                'job': job, 'variants': [segs[0]] if cue and have_model() else [],
                'skipped': [{'path': s.path, 'reason': s.reason} for s in change.skipped]}

    def _conflict(e: Conflict, path: str) -> Conflict:
        o = store.overrides().get(path)
        e.current = {'rev': o.rev if o else None, 'value': o.value if o else None,
                     'who': (o.author or '?') if o else None}
        return e

    def exam_set(request, data):
        path, rev = _path(data), _rev(data)
        try:
            with lock:
                change = overrides.set(exam, store, path, data.get('value'), author=who(request),
                                       expect_rev=rev)
        except Conflict as e:
            raise _conflict(e, path) from None
        return _changed(path, change, data)

    def exam_clear(request, data):
        path, rev = _path(data), _rev(data)
        try:
            with lock:
                change = overrides.clear(exam, store, path, expect_rev=rev)
        except Conflict as e:
            raise _conflict(e, path) from None
        return _changed(path, change, data)

    def exam_variants(request, data):
        """Read the cues again where a changed cue left the variant open."""
        if not have_model():
            raise Bad("model unavailable")
        qids = _strings(data.get('questions') or [], [k for k, q in exam.questions.items() if q.variants],
                        "questions")
        items = variants.to_read_again(exam, store, qids)

        def reread(sid, qid):
            out = runner.run_stage(sid, qid, 'variant')
            return [out] + ([o] if out.ok and (o := runner.recheck(sid, qid)) else [])

        return {'job': jobs.start_each(items, reread), 'n': len(items)}

    # --- intake: scans in, submissions out ---

    def the_source(request: Request):
        src = store.source(request.path_params['src'])
        if src is None:
            raise HTTPException(404)
        return src

    def plan_view(src) -> dict:
        art = intake.plan(ws, src.id)
        body, n, cover = art.body, src.pages, intake.cover(ws, art.body)
        pages, doubts, states = split.pages(body, n), split.doubts(body, n), intake.state(ws, src.id)
        made = store.made()
        entries = []
        for k, e in enumerate(body['entries']):
            sid = e.get('id')
            entries.append({'start': e['start'], 'verified': bool(e.get('confirmed')), 'id': sid,
                            'pages': pages[k], 'doubts': [split.DOUBTS[d] for d in doubts[k]],
                            'state': states[k], 'own': e.get('orient') is not None,
                            'name': identity.who(ws, sid)['name'] if sid in made else ''})
        so = Orient.from_json(body.get('orient'))
        return {'id': src.id, 'name': src.name, 'n': n, 'rev': art.rev, 'length': body['length'],
                'checked': bool(body.get('checked')) and bool(body.get('readings')), 'failed': body.get('failed', 0), 'o': so.code, 'orient': so.label(),
                'sheet_o': [intake.sheet_orient(body, i).code for i in range(n)],
                'cover': cover, 'entries': entries, 'loose': split.loose(body, n), 'dropped': body.get('dropped') or [],
                'readings': {i: (None if r is None else 'cover' if r < cover else f'p.{r - cover + 1}')
                             for i, r in split.readings(body).items()},
                'removed': intake.removed(ws, src.id), 'cursor': split.cursor(body),
                'undo': intake.can_undo(ws, src.id)}

    def box(layout: Layout, region) -> str:
        return ','.join(f'{v:.4f}' for v in layout.box(region, exam.crop_pad))

    def scans_page(request: Request) -> Response:
        # where the identity boxes are on a sheet of a source (the plan adds cover sheets)
        boxes = [{'name': f.name, 'offset': f.region.page - 1, 'box': box(Layout(0, ws.scan_window), f.region)}
                 for f in exam.identity]
        made = store.made()
        sources = store.sources()
        # per scan, what the workflow strip counts: first pages to verify, submissions to write
        counts = {}
        for src in sources:
            body = intake.plan(ws, src.id).body
            states = intake.state(ws, src.id)
            counts[src.id] = {'open': sum(not e.get('confirmed') for e in body['entries']),
                              'write': sum(st != 'made' for st in states) + len(intake.removed(ws, src.id))}
        return page('scans.html', sources=sources, counts=json.dumps(counts),
                    people=[identity.label(p) for p in identity.people(ws)],
                    expected=intake.expected_length(ws), idboxes=json.dumps(boxes), fields=exam.identity,
                    widths=json.dumps(SHEET_WIDTHS), made=len(made),
                    handed=sum(1 for s in store.submissions() if s.id not in made))

    def scans_plan(request: Request) -> Response:
        return JSONResponse(plan_view(the_source(request)))

    async def scans_upload(request: Request) -> Response:
        sent = request.headers.get('x-token') or ''
        if not hmac.compare_digest(sent.encode(), token.encode()):
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
        kw = dict(length=length, fresh=bool(data.get('fresh')), author=who(request), expect_rev=_rev(data))
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
            report = intake.accept(ws, src.id, author=who(request), force=bool(data.get('force')),
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
        if sub is None or sub.id not in store.made():
            raise Bad("unknown submission")
        text = data.get('text')
        if text is not None and not isinstance(text, str):
            raise Bad("expected text")
        kw = {'expect_rev': _rev(data)} if 'rev' in data else {}
        return {'who': identity.set_who(ws, sub.id, text or '', author=who(request), **kw)}

    proposals: dict = {}        # identity.overview's cache: proposals by reading and roster

    def scans_names(request: Request) -> Response:
        with lock:
            view = identity.overview(ws, cache=proposals)
        for row in view['rows']:
            layout = ws.layout(row['sub'])
            row['crops'] = [{'name': f.name, 'index': layout.scan_index(f.region.page), 'box': box(layout, f.region)}
                            for f in exam.identity]
        view['nobody'] = [identity.label(p) for p in view['nobody']]
        view['fields'] = [{'name': f.name, 'roster': f.roster} for f in exam.identity]
        view['doubts'] = identity.DOUBTS
        view['roster'] = len(identity.people(ws))
        return JSONResponse(view)

    def scans_names_read(request, data):
        if not have_model():
            raise Bad("model unavailable")
        if not exam.identity:
            raise Bad("the exam declares no identity boxes")
        fresh = bool(data.get('fresh'))
        subs = sorted(store.made()) if fresh else identity.unread(ws)

        def read(sid):
            identity.read(ws, sid, runner.model, fresh=fresh)
        return {'job': jobs.start_each([(s,) for s in subs], read), 'n': len(subs)}

    def sheet_image(request: Request) -> Response:
        """A sheet turned as asked (the page asks for the plan's orientation) and
        shrunk to one of a few widths, cached; the source and the renderer are untouched."""
        src = the_source(request)
        index = request.path_params['index']
        w = request.query_params.get('w', '0')
        try:
            o = Orient.from_code(request.query_params.get('o', '00'))
        except ValueError:
            raise HTTPException(404) from None
        if not 0 <= index < src.pages or not w.isdigit() or int(w) not in (0, *SHEET_WIDTHS):
            raise HTTPException(404)
        cache = (ws.root / 'cache').resolve()
        out = cache / 'sheets' / src.id / f'{index:04d}-{o.code}-{int(w)}.jpg'
        if not out.exists():
            try:
                im = ws.renderer.page(intake.source_path(ws, src), index)
            except (KeyError, IndexError, OSError):
                raise HTTPException(404) from None
            im = o.apply(im)
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
        post('/s/{sub}/layout', set_layout),
        post('/s/{sub}/{q}/save', save),
        post('/s/{sub}/{q}/verify', verify),
        post('/s/{sub}/{q}/decide', decide),
        post('/s/{sub}/{q}/undecide', undecide),
        post('/s/{sub}/{q}/variant', choose_variant),
        post('/s/{sub}/{q}/cue', correct_cue),
        post('/s/{sub}/{q}/run', run),
        post('/s/{sub}/{q}/pages', set_pages),
        post('/s/{sub}/{q}/restore', restore),
        Route('/page/{sub}/{index:int}.jpg', page_image),
        Route('/raw/{sub}/{q}/{path}', raw_block),
        Route('/jobs/{id}', job),
        Route('/scan', scan_page),
        Route('/runs', runs_page),
        post('/runs/preview', runs_preview),
        post('/runs/start', runs_start),
        post('/runs/stop', runs_stop),
        Route('/runs/status', runs_status),
        Route('/settings', settings_page),
        post('/settings', settings_save),
        Route('/exam', exam_page),
        post('/exam/set', exam_set),
        post('/exam/clear', exam_clear),
        post('/exam/variants', exam_variants),
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
    return app
