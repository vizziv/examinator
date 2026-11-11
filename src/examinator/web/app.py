"""The app: server-rendered pages plus a few JSON actions (DESIGN §13).

    GET  /                          grid
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

Every POST takes a JSON object (or a form), needs the page token, and answers
JSON: 403 bad token, 404 unknown target, 409 stale revision or plan, 400 input
a grader should correct. Nothing here keeps module-level state; it all hangs
off `app.state`.
"""
from __future__ import annotations

import hmac
import json
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

from ..core import batch, grid, overrides
from ..core.defs import Bool, Exam, Question, grade_kind, has_marker
from ..core.runner import Runner
from ..core.store import Conflict
from ..core.variants import resolved
from ..core.workflow import evidence_current, question_status, seen_revs, variant_current, variant_trusted
from ..core.workspace import Workspace
from .jobs import Batch, Jobs

HERE = Path(__file__).parent
LOOPBACK = ('127.0.0.1', 'localhost', '[::1]')
MAX_BODY = 400_000          # characters of an edited artifact
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
RAW_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"
STATE_LABELS = {
    'not_started': "not started", 'in_progress': "in progress", 'unverified': "unverified",
    'graded': "graded", 'needs_review': "needs review", 'outdated': "outdated",
    'manual': "manual", 'manual_outdated': "manual, outdated"}


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
        raise Bad("scan window: expected left, top, right, bottom")
    l, t, r, b = (_int(v, "scan window", 0, 1000) for v in value)
    if l >= r or t >= b:
        raise Bad("scan window: left < right and top < bottom")
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

def glyph(spec, st) -> str:
    """What the grade is, as one glyph. Where it is in the workflow is `st.state`."""
    if st.value is None:
        return {'needs_review': '?', 'unverified': '?', 'in_progress': '·'}.get(st.state, '')
    if 'blank' in st.flags:
        return '∅'
    if isinstance(st.value, bool):
        return '✓' if st.value else '✗'
    return f'{st.value:g}'


def value_class(st) -> str:
    if st.value is None:
        return 'v-n'
    if 'blank' in st.flags:
        return 'v-b'
    if isinstance(st.value, bool):
        return 'v-t' if st.value else 'v-f'
    return 'v-num'


def value_key(st) -> str:
    """The value as the grid's filter key."""
    if st.value is None:
        return ''
    if isinstance(st.value, bool):
        return 't' if st.value else 'f'
    return f'{st.value:g}'


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
        rows = []
        for row in grid.load(exam, store):
            cells = []
            for q in exam.questions.values():
                cell = row.cells[q.id]
                if q.variants:
                    cells.append({'variant': True, 'qid': q.id, 'text': cell.variant or '?',
                                  'open': cell.variant is None})
                for part, spec in q.result.items():
                    st = cell.status[part]
                    cells.append({'qid': q.id, 'col': q.column(part), 'state': st.state,
                                  'glyph': glyph(spec, st), 'cls': value_class(st), 'v': value_key(st),
                                  'blank': 'blank' in st.flags, 'counts': st.counts})
            rows.append({'sub': row.submission, 'cells': cells, 'points': f'{row.points:g}',
                         'complete': row.complete, 'sort': row.points})
        return page('grid.html', rows=rows, columns=columns, questions=list(exam.questions.values()),
                    states=[s for s in STATE_LABELS])

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

        parts = []
        for part, spec in q.result.items():
            st, g = status[part], arts.get(grade_kind(part))
            parts.append({'part': part, 'name': q.column(part), 'st': st, 'glyph': glyph(spec, st),
                          'cls': value_class(st), 'rev': g.rev if g else '',
                          'manual': g is not None and g.origin == 'manual',
                          'bool': isinstance(spec, Bool), 'min': getattr(spec, 'min', None),
                          'max': getattr(spec, 'max', None),
                          'step': 1 if getattr(spec, 'type', None) is int else 'any'})

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
        stages = [{'name': s.name, 'model': s.uses_model, 'replaces': replaces([s.name]),
                   'replaces_on': replaces(names[i:])} for i, s in enumerate(q.stages)
                  if have_model() or not s.uses_model]
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
            seen=seen_revs(q, arts),
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
        ws.set_layout(sub.id, cover_pages=cover, scan_window=_window(data.get('scan_window')),
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
        window = _window(data.get('scan_window'))
        if window is None:
            raise Bad("scan window: expected left, top, right, bottom")
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
        items = []
        for sid in (s.id for s in store.submissions()):
            for qid in qids:
                arts = store.for_question(sid, qid)
                art = arts.get('variant')
                if art is None or art.protected:
                    continue
                body = art.body if isinstance(art.body, dict) else {}
                if not variant_current(exam.questions[qid], arts) or resolved(body) is None:
                    items.append((sid, qid))

        def reread(sid, qid):
            out = runner.run_stage(sid, qid, 'variant')
            return [out] + ([o] if out.ok and (o := runner.recheck(sid, qid)) else [])

        return {'job': jobs.start_each(items, reread), 'n': len(items)}

    def post(path, fn):
        return Route(path, action(fn), methods=['POST'])

    routes = [
        Route('/', grid_page),
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
        Mount('/static', StaticFiles(directory=HERE / 'static'), name='static'),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(Guard, public_url=public_url)
    app.state.token = token
    app.state.jobs = jobs
    app.state.batch = None
    app.state.author = author
    app.state.public_url = public_url
    return app
