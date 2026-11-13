"""Every POST is a change to a row, a run in a slot, or a query (DESIGN, "Changes"): the
route table is the list. Each route has an example here, so a new one cannot be forgotten. A
change sent without its revision is refused (400), with a stale one it is refused with what
is there now (409, `current`). A run is the same request every time, refused only while its
slot runs. A query, and every GET, changes nothing. Synthetic workspaces only."""
import copy
import re
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from examinator import Check, Exam, Field, Region, load_exam, synth
from examinator.core import identity, overrides
from examinator.core.runner import Runner
from examinator.core.store import Conflict
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.web.app import create_app
from examinator.web.jobs import Jobs

from conftest import SCRIPT, Parse, exact, workspace, yes

BASE = 'http://127.0.0.1:8580'
TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'
STATIC = Path(__file__).resolve().parents[1] / 'src' / 'examinator' / 'web' / 'static'
UPLOAD = '/scans/upload'        # a file, not a statement: kept once by its digest


class Client:
    def __init__(self, app):
        self.app = app
        self.client = TestClient(app, base_url=BASE)
        self.token = re.search(r'<meta name="token" content="([^"]+)"', self.client.get('/settings').text).group(1)

    def get(self, path):
        return self.client.get(path)

    def post(self, path, data):
        return self.client.post(path, json=data, headers={'X-Token': self.token})

    def wait(self, job):
        for _ in range(500):
            info = self.client.get(f'/jobs/{job}').json()
            if info['state'] != 'running':
                return info
            time.sleep(0.02)
        raise AssertionError("job did not finish")


def mini():
    """Like conftest's exam, with a field that has a default (for /default)."""
    exam = Exam('mini', pages=2)
    exam.question('a', pages=[1], fields=[Field('x'), Field('y', default='0')], read=Parse(),
                  checks={'x': Check(exact('x'), isolate=False)})
    exam.question('b', pages=[2], fields=[Field('x')], read=Parse(), check=Check(yes, isolate=False))
    return exam


@pytest.fixture
def sites(tmp_path):
    """Two apps: a small exam with two submissions (the question routes), and the toy
    exam with a synthetic stack split, accepted and on a roster (the rest)."""
    exam = mini()
    ws = workspace(tmp_path / 'q', exam)
    runner = Runner(exam, ws, model=FakeModel(dict(SCRIPT)))
    runner.read('s1', 'a')
    runner.set('s1', 'a', 'y', "2", author='t')
    q = Client(create_app(exam, ws, runner, author='tester'))
    q.wait(q.app.state.startup)

    toy = load_exam(TOY)
    root = tmp_path / 'toy'
    paths = synth.stacks(toy, root)
    tws = Workspace(root, toy, create=True)
    trunner = Runner(toy, tws, model=synth.fake_model(root))
    s = Client(create_app(toy, tws, trunner, author='tester'))
    src = s.client.post('/scans/upload?name=a.pdf', content=paths[0].read_bytes(),
                        headers={'Content-Type': 'application/pdf', 'X-Token': s.token}).json()['id']
    plan = s.get(f'/scans/{src}/plan').json()
    s.wait(s.post(f'/scans/{src}/propose', {'check': True, 'rev': plan['rev']}).json()['job'])
    plan = s.get(f'/scans/{src}/plan').json()
    for _ in range(2):
        plan = s.post(f'/scans/{src}/edit', {'op': 'turn', 'sheet': plan['entries'][4]['start'], 'rev': plan['rev']}).json()['plan']
    assert s.post(f'/scans/{src}/accept', {'rev': plan['rev']}).status_code == 200
    s.post('/scans/roster', {'text': (root / 'roster.csv').read_text(), 'rev': None})
    s.src, s.ws = src, tws
    q.ws, q.runner = ws, runner
    yield q, s
    for r, w in ((runner, ws), (trunner, tws)):
        r.close()
        w.close()


def value_rev(site, sub, k):
    row = site.ws.store.get(sub, k, 'value')
    return row.rev if row else None


def decision_rev(q):
    row = q.ws.store.get('s1', 'a/x', 'decision')
    return row.rev if row else None


def override_rev(s, path):
    o = s.ws.store.overrides().get(path)
    return o.rev if o else None


def plan_rev(s):
    return s.get(f'/scans/{s.src}/plan').json()['rev']


def first_sub(s):
    return s.ws.store.submissions()[0].id


# route -> (which site, a function of it giving the URL and a body valid now)
CHANGES = {
    '/s/{sub}/{q}/save': ('q', lambda q: ('/s/s1/a/save', {'values': {'a/x': {'text': "5", 'rev': value_rev(q, 's1', 'a/x')}},
                                                         'verify': [{'key': 'a/y', 'rev': value_rev(q, 's1', 'a/y')}]})),
    '/s/{sub}/@identity/save': ('s', lambda s: (f'/s/{first_sub(s)}/@identity/save',
                                                {'values': {'@identity/name': {'text': "Ann", 'rev': value_rev(s, first_sub(s), '@identity/name')}}})),
    '/s/{sub}/{q}/unverify': ('q', lambda q: ('/s/s1/a/unverify', {'key': 'a/x', 'rev': value_rev(q, 's1', 'a/x')})),
    '/s/{sub}/{q}/decide': ('q', lambda q: ('/s/s1/a/decide', {'check': 'x', 'value': True, 'rev': decision_rev(q),
                                                              'seen': {'a/x': value_rev(q, 's1', 'a/x'), 'a/y': value_rev(q, 's1', 'a/y')}})),
    '/s/{sub}/{q}/clear': ('q', lambda q: ('/s/s1/a/clear', {'check': 'x', 'rev': decision_rev(q)})),
    '/s/{sub}/{q}/default': ('q', lambda q: ('/s/s1/a/default', {'key': 'a/y', 'rev': value_rev(q, 's1', 'a/y')})),
    '/s/{sub}/{q}/restore': ('q', lambda q: ('/s/s1/a/restore', {'key': 'a/x', 'rev': value_rev(q, 's1', 'a/x'),
                                                                'restore': q.ws.store.history('s1', 'a/x', 'value')[0].rev})),
    '/exam/set': ('s', lambda s: ('/exam/set', {'path': '@identity/fields/name/region', 'value': [1, 100, 210, 800, 280],
                                                'rev': override_rev(s, '@identity/fields/name/region')})),
    '/exam/clear': ('s', lambda s: ('/exam/clear', {'path': '@identity/fields/name/region',
                                                    'rev': override_rev(s, '@identity/fields/name/region')})),
    '/scans/roster': ('s', lambda s: ('/scans/roster', {'text': "Ada Lovelace, 31415926\n", 'rev': identity.roster_rev(s.ws)})),
    '/scans/who': ('s', lambda s: ('/scans/who', {'sub': first_sub(s), 'text': "Ann Example", 'rev': None})),
    '/scans/{src}/edit': ('s', lambda s: (f'/scans/{s.src}/edit', {'op': 'drop', 'sheet': 23, 'rev': plan_rev(s)})),
    '/scans/{src}/propose': ('s', lambda s: (f'/scans/{s.src}/propose', {'rev': plan_rev(s)})),
    '/scans/{src}/undo': ('s', lambda s: (f'/scans/{s.src}/undo', {'rev': plan_rev(s)})),
    '/scans/{src}/accept': ('s', lambda s: (f'/scans/{s.src}/accept', {'rev': plan_rev(s), 'force': True})),
}
# route -> (which site, the URL, the body as the page sends it: the same every time)
RUNS = {
    '/s/{sub}/{q}/read': ('q', '/s/s2/a/read', {'include_protected': False}),
    '/s/{sub}/{q}/check': ('q', '/s/s1/a/check', {'check': 'x'}),
    '/read/start': ('q', '/read/start', {'submissions': ['s2'], 'questions': ['b'], 'parallel': 1}),
    '/read/stop': ('q', '/read/stop', {}),
    '/scans/names/read': ('s', '/scans/names/read', {'again': True}),
}
QUERIES = {
    '/read/plan': ('q', {}),
    '/scans/roster/preview': ('s', {'text': "Ada Lovelace, 31415926\n"}),
}


def post_routes(app):
    return {r.path: getattr(r.endpoint, 'kind', None) for r in app.routes if 'POST' in (getattr(r, 'methods', None) or ())}


def strip_revs(x):
    """The body without any revision: every 'rev' key, at any depth, removed."""
    if isinstance(x, dict):
        return {k: strip_revs(v) for k, v in x.items() if k != 'rev'}
    if isinstance(x, list):
        return [strip_revs(v) for v in x]
    return x


def stale(x):
    """The body with every revision one that was never drawn."""
    if isinstance(x, dict):
        return {k: (('stale' if isinstance(v, str) else 10**9) if k == 'rev' else stale(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [stale(v) for v in x]
    return x


def test_every_post_route_is_a_change_a_run_or_a_query(sites):
    for site in sites:
        routes = post_routes(site.app)
        assert {p for p, kind in routes.items() if kind is None} == {UPLOAD}
        assert {p for p, kind in routes.items() if kind == 'change'} == set(CHANGES)
        assert {p for p, kind in routes.items() if kind == 'run'} == set(RUNS)
        assert {p for p, kind in routes.items() if kind == 'query'} == set(QUERIES)


def test_every_change_needs_the_revision_drawn_and_refuses_a_stale_one(sites):
    q, s = sites
    # what some examples change must exist first: a decision, an override, a history, a who
    q.post('/s/s1/a/decide', CHANGES['/s/{sub}/{q}/decide'][1](q)[1])
    q.post('/s/s1/a/save', {'values': {'a/x': {'text': "4", 'rev': value_rev(q, 's1', 'a/x')}}})
    s.post('/exam/set', CHANGES['/exam/set'][1](s)[1])
    for route, (which, example) in CHANGES.items():
        site = q if which == 'q' else s
        path, body = example(site)
        assert site.post(path, strip_revs(body)).status_code == 400, route
        r = site.post(path, stale(body))
        assert r.status_code == 409 and 'current' in r.json() and r.json()['error'], route
        r = site.post(path, body)
        assert r.status_code == 200, (route, r.json())
        if r.json().get('job'):
            site.wait(r.json()['job'])
        if 'rev' in r.json() and r.json()['rev'] != body.get('rev', r.json()['rev']):
            # sent again as drawn before: the revision has moved on, so it happens once
            again = site.post(path, body)
            assert again.status_code in (400, 409), route


def test_a_run_is_the_same_request_every_time(sites):
    """A run button's request, as the page sends it, is accepted again once the last run of
    its slot has finished: a run names no revision (the page cannot convert one wrongly)."""
    q, s = sites
    for route, (which, path, body) in RUNS.items():
        if route == '/read/stop':
            continue
        site = q if which == 'q' else s
        for _ in range(2):
            r = site.post(path, body)
            assert r.status_code == 200, (route, r.json())
            if r.json().get('job'):
                site.wait(r.json()['job'])
            while site.get('/read/status').json()['state'] == 'running':
                time.sleep(0.02)
    r = q.post('/read/stop', RUNS['/read/stop'][2])
    assert r.status_code == 409 and r.json()['error'] == "not running"


def test_a_slot_runs_one_thing_at_a_time():
    """A second run in a slot while one runs is refused, as "already running"; other slots
    are free; once it has finished the slot takes the next."""
    jobs, go = Jobs(workers=2), threading.Event()
    first = jobs.start(go.wait, slot='read:s1/a')
    with pytest.raises(Conflict, match="already running"):
        jobs.start(lambda: None, slot='read:s1/a')
    jobs.start(lambda: None, slot='read:s2/a')
    go.set()
    while jobs.get(first)['state'] == 'running':
        time.sleep(0.01)
    jobs.start(lambda: None, slot='read:s1/a')


def test_recomputing_everything_is_one_job_that_newer_calls_take_over():
    """Many quick changes to exam data never queue many recomputes of every submission: a
    newer call while one runs goes on in that job, from its own list's first."""
    jobs, go, seen = Jobs(workers=2), threading.Event(), []

    def work(sub):
        go.wait()
        seen.append(sub)
    one = jobs.start_each([('a',), ('b',), ('c',)], work, slot='recompute', latest=True)
    two = jobs.start_each([('c',), ('a',), ('b',)], work, slot='recompute', latest=True)
    assert one == two
    go.set()
    while jobs.get(one)['state'] == 'running':
        time.sleep(0.01)
    assert seen == ['a', 'c', 'a', 'b'] and jobs.get(one)['done'] == jobs.get(one)['total'] == 3


def get_routes(app):
    return [r.path for r in app.routes if 'GET' in (getattr(r, 'methods', None) or ())]


def test_every_get_changes_nothing(sites):
    """Every GET route, as the route table lists it: a page rendered ahead may never be shown,
    so loading anything changes nothing."""
    q, s = sites
    for site in sites:
        sub = site.ws.store.submissions()[0].id
        qid = next(iter(site.ws.exam.grading))
        values = {'sub': sub, 'q': qid, 'index': '0', 'scan': site.ws.renderer.key(site.ws.pdf_path(sub)),
                  'check': site.ws.exam.grading[qid].checks[0].name if site.ws.exam.grading[qid].checks else 'x',
                  'digest': '0' * 64, 'id': site.app.state.startup, 'src': getattr(site, 'src', 'none')}
        before = (site.ws.store.version(), {k: o.rev for k, o in site.ws.store.overrides().items()},
                  site.app.state.batch, copy.deepcopy(identity.people(site.ws)))
        for route in get_routes(site.app):
            path = re.sub(r'\{(\w+)(:\w+)?\}', lambda m: values[m.group(1)], route)
            r = site.get(path)
            assert r.status_code in (200, 404), (route, r.status_code)
            if r.headers['content-type'].startswith('text/html'):
                assert r.headers['cache-control'] == 'no-store', route
        after = (site.ws.store.version(), {k: o.rev for k, o in site.ws.store.overrides().items()},
                 site.app.state.batch, identity.people(site.ws))
        assert after == before


def test_errors_are_json_and_an_unknown_token_says_reload(sites):
    """A page says why a change failed from the answer: every error is JSON with `error`,
    and a token this run of the app did not give says to reload."""
    q, s = sites
    for path in ('/s/nobody/a', '/jobs/nope', '/nowhere'):
        r = q.get(path)
        assert r.status_code == 404 and r.json()['error']
    r = q.post('/s/nobody/a/save', {'values': {}})
    assert r.status_code == 404 and r.json()['error']
    r = q.client.post('/s/s1/a/save', json={'values': {}}, headers={'X-Token': 'from-before-a-restart'})
    assert r.status_code == 403 and 'reload' in r.json()['error']


def test_a_query_changes_nothing(sites):
    for site in sites:
        for path, (which, body) in QUERIES.items():
            if (which == 'q') != (site is sites[0]):
                continue
            before = (site.app.state.batch, copy.deepcopy(identity.people(site.ws)))
            version = site.ws.store.version()
            assert site.post(path, body).status_code == 200
            assert site.ws.store.version() == version
            assert (site.app.state.batch, identity.people(site.ws)) == before


def test_no_script_converts_a_revision():
    """A revision goes back exactly as the server drew it: no script turns one into a number
    (a run's hex id became NaN, so a button worked once)."""
    for js in STATIC.glob('*.js'):
        text = js.read_text()
        assert not re.search(r'(Number|parseInt|parseFloat)\([^()]*\brevs?\b', text), js.name
        assert not re.search(r'(?<![\w)\]\'"\s])\s*\+\s*(?:[\w$]+\.)*rev\b', text), js.name     # unary plus


def test_the_page_scripts_post_only_through_the_one_path():
    """No script sends a POST of its own: changes go through `Changes` in common.js, and the
    scan upload (a file) is the one exception."""
    for js in STATIC.glob('*.js'):
        text = js.read_text()
        posts = len(re.findall(r"method:\s*'POST'", text))
        if js.name == 'common.js':
            assert posts == 1, js.name
        elif js.name == 'scans.js':
            assert posts == 1 and '/scans/upload' in text, js.name
        else:
            assert posts == 0, js.name
        # `request` only reads: a body handed to it would be dropped, so none is written
        assert not re.search(r'\brequest\([^()]*(\([^()]*\))?[^()]*,', text), js.name


def test_a_change_that_does_not_go_through_is_said_once_by_changes():
    """When a change does not go through, `Changes.send` says so once, at the caller's line
    (else the header), and owns the pending marker: no page sets '…' for a change or flashes a
    refusal of its own."""
    for js in STATIC.glob('*.js'):
        if js.name == 'common.js':
            continue
        text = js.read_text()
        assert not re.search(r"""say\([^)]*'…'""", text) and "textContent = '…'" not in text, js.name
        assert 'changed since it was shown' not in text and 'save again to overwrite' not in text, js.name
