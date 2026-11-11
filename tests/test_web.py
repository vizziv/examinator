"""The web app, against the synthetic mini exam and the scripted model."""
import re
import time

import pytest
from starlette.testclient import TestClient

from examinator import Evidence
from examinator.core.runner import Runner
from examinator.core.workflow import question_status, seen_revs
from examinator.web.app import create_app, find_raw, prepare_blocks

BASE = 'http://127.0.0.1:8580'
STAGES = ('variant', 'transcript', 'parse', 'evidence')


class Site:
    """A test client that sends the page token on POST, as the page's script does."""

    def __init__(self, exam, ws, runner, **kw):
        self.exam, self.ws, self.runner = exam, ws, runner
        self.app = create_app(exam, ws, runner, author='tester', **kw)
        self.client = TestClient(self.app, base_url=BASE)
        html = self.client.get('/').text
        self.token = re.search(r'<meta name="token" content="([^"]+)"', html).group(1)

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def post(self, path, data=None, headers=None):
        return self.client.post(path, json=data or {}, headers={'X-Token': self.token, **(headers or {})})

    def wait(self, job):
        for _ in range(400):
            info = self.get(f'/jobs/{job}').json()
            if info['state'] != 'running':
                return info
            time.sleep(0.02)
        raise AssertionError("job did not finish")

    def rev(self, sub, q, kind):
        art = self.ws.store.get(sub, q, kind)
        return art.rev if art else None

    def decide(self, sub, q, part, value=None, **extra):
        """Decide as the page does: against the grade and parse revisions on screen."""
        seen = seen_revs(self.exam.questions[q], self.ws.store.for_question(sub, q))
        data = {'part': part, 'value': value, 'rev': self.rev(sub, q, f'grade:{part}' if part else 'grade'),
                'seen': seen, **extra}
        return self.post(f'/s/{sub}/{q}/decide', data)

    def state(self, sub, q, part=''):
        question = self.exam.questions[q]
        return question_status(question, self.ws.store.for_question(sub, q))[part]

    def run_all(self, sub='s1', q='a'):
        for stage in STAGES:
            assert self.runner.run_stage(sub, q, stage).ok


@pytest.fixture
def site(env):
    return Site(*env)


# --- pages ---

def test_grid_lists_every_submission(site):
    site.run_all()
    r = site.get('/')
    assert r.status_code == 200
    assert 'data-id="s1"' in r.text and 'data-id="s2"' in r.text
    for column in ('a.x', 'a.y', 'b'):
        assert f'data-col="{column}"' in r.text
    assert 'href="/s/s1/a"' in r.text
    assert 'data-s="unverified"' in r.text and 'data-s="not_started"' in r.text


def test_question_page_before_and_after_stages(site):
    r = site.get('/s/s1/a')
    assert r.status_code == 200 and 'not started' in r.text and 'no parse' in r.text
    site.run_all()
    r = site.get('/s/s1/a')
    assert r.status_code == 200
    assert 'data-label="x" value="1"' in r.text
    assert 'pill st st-unverified' in r.text
    assert 'data-cue="c1"' in r.text and '/page/s1/0.jpg' in r.text
    assert site.get('/s/s1/b').status_code == 200
    for path in ('/runs', '/settings', '/scan'):
        assert site.get(path).status_code == 200


def test_security_headers(site):
    for path in ('/', '/s/s1/a', '/static/app.css'):
        r = site.get(path)
        csp = r.headers['content-security-policy']
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and 'unsafe' not in csp
        assert r.headers['x-content-type-options'] == 'nosniff'
    html = site.get('/s/s1/a').text
    assert '<script>' not in html and ' style="' not in html and not re.search(r'\son\w+="', html)


def test_theme_and_actions_are_static_and_controls_declare_actions(site):
    # theme.js runs before the stylesheet (no flash); the action registry loads on every page.
    for path in ('/', '/s/s1/a', '/filter', '/runs', '/exam', '/settings'):
        html = site.get(path).text
        assert html.index('/static/theme.js"></script>') < html.index('/static/app.css')
        assert '/static/actions.js' in html
        assert '<script>' not in html and ' style="' not in html and not re.search(r'\son\w+="', html)
    assert '/static/theme.js' in site.get('/scan').text
    for name in ('theme.js', 'actions.js'):
        r = site.get('/static/' + name)
        assert r.status_code == 200 and 'javascript' in r.headers['content-type']
        assert "script-src 'self'" in r.headers['content-security-policy']
    site.run_all()
    html = site.get('/s/s1/a').text
    for action in ('prev-student', 'next-student', 'scan', 'verify', 'save', 'edit', 'decide', 'decide-blank', 'run'):
        assert f'data-action="{action}"' in html, action
    assert 'data-act=' not in html and '<kbd>' not in html      # hints come from the key map
    assert 'pill g-' in html and 'pill st st-' in html          # grade and state: two pills
    assert 'data-action="clear-search"' in site.get('/').text
    assert 'data-action="clear-filter"' in site.get('/filter').text
    runs = site.get('/runs').text
    assert all(f'data-action="{a}"' in runs for a in ('run-preview', 'run-start', 'run-stop'))
    settings = site.get('/settings').text
    assert all(f'id="{i}"' in settings for i in ('theme', 'accent', 'accent2', 'k-single', 'k-preset', 'keymap'))
    assert 'aria-current="page"' in settings


def test_page_image(site):
    r = site.get('/page/s1/0.jpg')
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'
    assert r.content[:2] == b'\xff\xd8'
    assert site.get('/page/s1/1.jpg').status_code == 200


# --- request validation ---

def test_post_needs_token(site):
    site.run_all()
    rev = site.rev('s1', 'a', 'parse')
    body = {'kind': 'parse', 'body': {'x': '9', 'y': '9'}, 'rev': rev}
    assert site.client.post('/s/s1/a/save', json=body).status_code == 403
    assert site.client.post('/s/s1/a/save', json=body, headers={'X-Token': 'wrong'}).status_code == 403
    assert site.client.post('/settings', data={'token': 'wrong', 'cover_pages': '1'}).status_code == 403
    assert site.client.post('/runs/stop').status_code == 403
    assert site.rev('s1', 'a', 'parse') == rev
    # The token may also arrive as a form field.
    r = site.client.post('/s/s1/a/decide', data={
        'token': site.token, 'part': 'x', 'value': 'yes', 'rev': str(site.rev('s1', 'a', 'grade:x')),
        'seen': '{"parse": %d}' % rev})
    assert r.status_code == 200


def test_bad_host_and_origin(site):
    assert site.get('/', headers={'Host': 'evil.example'}).status_code == 400
    assert site.get('/', headers={'Host': '127.0.0.1.evil.example:8580'}).status_code == 400
    assert site.get('/', headers={'Host': 'localhost:8580'}).status_code == 200
    r = site.post('/runs/stop', headers={'Origin': 'http://evil.example'})
    assert r.status_code == 403
    assert site.post('/runs/stop', headers={'Origin': BASE}).status_code == 200


def test_public_url_host_and_author(env):
    exam, ws, runner = env
    site = Site(exam, ws, runner, public_url='https://grading.example/exam')
    assert 'href="/exam/static/app.css"' in site.get('/').text
    r = site.client.get('/', headers={'Host': 'grading.example'})
    assert r.status_code == 200
    assert site.client.get('/', headers={'Host': 'other.example'}).status_code == 400
    r = site.client.post('/s/s1/b/decide', json={'part': '', 'value': True, 'rev': None, 'seen': {'parse': None}},
                         headers={'X-Token': site.token, 'Host': 'grading.example',
                                  'Origin': 'https://grading.example', 'X-Forwarded-User': 'ta-one'})
    assert r.status_code == 200
    assert ws.store.get('s1', 'b', 'grade').author == 'ta-one'


def test_unknown_targets_are_404(site, tmp_path):
    (tmp_path / 'secret.jpg').write_bytes(b'secret')
    for path in ('/s/nobody/a', '/s/s1/zzz', '/page/nobody/0.jpg', '/page/s1/2.jpg', '/page/s1/99.jpg',
                 '/page/s1/-1.jpg', '/page/s1/x.jpg', '/jobs/nope', '/raw/s1/a/0',
                 '/page/../secret.jpg', '/page/s1/..%2f..%2fsecret.jpg', '/page/..%2f..%2fsecret/0.jpg',
                 '/s/..%2f..%2fetc/passwd', '/s/%2e%2e/%2e%2e', '/static/..%2fapp.py',
                 '/static/../app.py', '/static/%2e%2e/app.py', '/page/s1%2f..%2fs2/0.jpg'):
        r = site.get(path)
        assert r.status_code == 404, path
        assert b'secret' not in r.content
    assert site.post('/s/nobody/a/save', {'kind': 'parse', 'body': {}, 'rev': None}).status_code == 404
    assert site.post('/s/s1/zzz/decide', {'part': '', 'value': True, 'rev': None, 'seen': {}}).status_code == 404
    assert site.post('/s/nobody/layout', {'cover_pages': 1}).status_code == 404


def test_symlinked_pdf_is_not_served(site):
    pdfs = site.ws.pdfs
    (pdfs / 's1.pdf').rename(pdfs / 'real.pdf')
    (pdfs / 's1.pdf').symlink_to(pdfs / 'real.pdf')
    assert site.get('/page/s1/0.jpg').status_code == 404
    assert site.get('/s/s1/a').status_code == 200


# --- editing ---

def test_save_conflict_keeps_the_stored_body(site):
    site.run_all()
    rev = site.rev('s1', 'a', 'parse')
    assert site.runner.save('s1', 'a', 'parse', {'x': '5', 'y': '2'}, author='other', expect_rev=rev)
    r = site.post('/s/s1/a/save', {'kind': 'parse', 'body': {'x': 'mine', 'y': '2'}, 'rev': rev})
    assert r.status_code == 409 and r.json()['conflict']
    assert site.ws.store.get('s1', 'a', 'parse').body == {'x': '5', 'y': '2'}
    assert site.post('/s/s1/a/save', {'kind': 'parse', 'body': {'x': 'mine'}}).status_code == 400   # no rev
    assert site.post('/s/s1/a/save', {'kind': 'parse', 'body': ['x'], 'rev': site.rev('s1', 'a', 'parse')}).status_code == 400
    assert site.post('/s/s1/a/save', {'kind': 'evidence', 'body': {}, 'rev': None}).status_code == 400
    assert site.ws.store.get('s1', 'a', 'parse').body == {'x': '5', 'y': '2'}


def test_save_and_verify_rechecks_and_grades(site):
    site.run_all()
    assert site.state('s1', 'a', 'x').state == 'unverified'
    r = site.post('/s/s1/a/save', {'kind': 'parse', 'body': {'x': '3', 'y': '2'},
                                   'rev': site.rev('s1', 'a', 'parse'), 'verify': True})
    assert r.status_code == 200
    assert site.wait(r.json()['job'])['state'] == 'done'
    parse = site.ws.store.get('s1', 'a', 'parse')
    assert parse.body == {'x': '3', 'y': '2'} and parse.verified and parse.author == 'tester'
    assert r.json()['rev'] == parse.rev
    x, y = site.state('s1', 'a', 'x'), site.state('s1', 'a', 'y')
    assert (x.state, x.value) == ('graded', False)
    assert (y.state, y.value) == ('graded', True)
    html = site.get('/s/s1/a').text
    assert 'st-graded' in html and 'g-f' in html and 'verified · tester' in html
    assert 'restore' in html      # the replaced parse is in the history


def test_verify_unverify_and_markers(site):
    site.run_all()
    rev = site.rev('s1', 'a', 'parse')
    r = site.post('/s/s1/a/verify', {'kind': 'parse', 'rev': rev})
    assert r.status_code == 200
    site.wait(r.json()['job'])
    assert site.state('s1', 'a', 'x').state == 'graded'
    assert site.post('/s/s1/a/verify', {'kind': 'parse', 'rev': rev, 'verified': False}).status_code == 200
    assert site.state('s1', 'a', 'x').state == 'unverified'
    assert site.post('/s/s1/a/verify', {'kind': 'parse', 'rev': rev + 1000}).status_code == 409
    r = site.post('/s/s1/a/save', {'kind': 'parse', 'body': {'x': '[REVIEW: smudge]', 'y': '2'},
                                   'rev': rev, 'verify': True})
    assert r.status_code == 400
    r = site.post('/s/s1/a/save', {'kind': 'parse', 'body': {'x': '[REVIEW: smudge]', 'y': '2'}, 'rev': rev})
    assert r.status_code == 200
    site.wait(r.json()['job'])
    assert 'review marker' in site.get('/s/s1/a').text


def test_transcript_edit_and_restore(site):
    site.run_all()
    r = site.post('/s/s1/a/save', {'kind': 'transcript', 'body': "x: 7\ny: 2", 'rev': site.rev('s1', 'a', 'transcript')})
    assert r.status_code == 200
    assert site.ws.store.get('s1', 'a', 'transcript').body == "x: 7\ny: 2"
    old = site.rev('s1', 'a', 'parse')
    r = site.post('/s/s1/a/save', {'kind': 'parse', 'body': {'x': '8', 'y': '2'}, 'rev': old})
    site.wait(r.json()['job'])
    assert site.post('/s/s1/a/restore', {'rev': old, 'expect': old}).status_code == 409
    r = site.post('/s/s1/a/restore', {'rev': old, 'expect': site.rev('s1', 'a', 'parse')})
    assert r.status_code == 200
    site.wait(r.json()['job'])
    assert site.ws.store.get('s1', 'a', 'parse').body == {'x': '1', 'y': '2'}
    assert site.post('/s/s1/a/restore', {'rev': 987654, 'expect': site.rev('s1', 'a', 'parse')}).status_code == 400


def test_decide_and_undecide(site):
    site.run_all()
    assert site.decide('s1', 'a', 'x', False, rev=site.rev('s1', 'a', 'grade:x') + 50).status_code == 409
    assert site.post('/s/s1/a/decide', {'part': 'x', 'value': False,
                                        'rev': site.rev('s1', 'a', 'grade:x')}).status_code == 400   # no seen
    assert site.decide('s1', 'a', 'x', False).status_code == 200
    st = site.state('s1', 'a', 'x')
    assert (st.state, st.value, st.author) == ('manual', False, 'tester')
    assert site.state('s1', 'a', 'y').state == 'unverified'      # parts are decided independently
    html = site.get('/s/s1/a').text
    assert 'st-manual' in html and 'tester' in html and 'data-action="undecide"' in html
    assert site.decide('s1', 'a', 'nope', True, rev=None).status_code == 400
    assert site.decide('s1', 'a', 'x', 3).status_code == 400
    r = site.decide('s1', 'a', 'y', blank=True)
    assert r.status_code == 200 and site.state('s1', 'a', 'y').flags == ('blank',)
    r = site.post('/s/s1/a/undecide', {'part': 'x', 'rev': site.rev('s1', 'a', 'grade:x')})
    assert r.status_code == 200
    st = site.state('s1', 'a', 'x')
    assert (st.state, st.value) == ('unverified', True)
    # A decision needs no artifacts at all.
    assert site.decide('s2', 'b', '', True).status_code == 200
    assert site.state('s2', 'b').state == 'manual'


def test_decision_against_a_changed_parse_is_refused(site):
    site.run_all()
    html = site.get('/s/s1/a').text
    seen = {'parse': site.rev('s1', 'a', 'parse')}
    assert f"data-seen='{{\"parse\": {seen['parse']}}}'" in html
    site.runner.save('s1', 'a', 'parse', {'x': '5', 'y': '2'}, author='other', expect_rev=seen['parse'])
    grade = site.ws.store.get('s1', 'a', 'grade:x')
    r = site.post('/s/s1/a/decide', {'part': 'x', 'value': True, 'rev': grade.rev, 'seen': seen})
    assert r.status_code == 409
    assert site.ws.store.get('s1', 'a', 'grade:x') == grade


def test_variant_choice_and_cue_correction(site):
    site.run_all()
    r = site.post('/s/s1/a/cue', {'cue': 'c1', 'reading': '2', 'rev': site.rev('s1', 'a', 'variant')})
    assert r.status_code == 200
    site.wait(r.json()['job'])
    body = site.ws.store.get('s1', 'a', 'variant').body
    assert body['resolved'] is None and body['cues']['c1'] == {'reading': '2', 'variant': 'two'}
    html = site.get('/s/s1/a').text
    assert 'unresolved' in html and 'variant unresolved' in html
    r = site.post('/s/s1/a/variant', {'variant': 'two', 'rev': site.rev('s1', 'a', 'variant')})
    assert r.status_code == 200
    site.wait(r.json()['job'])
    art = site.ws.store.get('s1', 'a', 'variant')
    assert art.body['resolved'] == 'two' and art.author == 'tester'
    assert site.state('s1', 'a', 'x').value is False            # rechecked against the other key
    assert site.post('/s/s1/a/variant', {'variant': 'three', 'rev': art.rev}).status_code == 400
    assert site.post('/s/s1/a/variant', {'variant': 'one', 'rev': art.rev + 99}).status_code == 409
    assert site.post('/s/s1/a/cue', {'cue': 'zz', 'reading': '1', 'rev': art.rev}).status_code == 400
    r = site.post('/s/s1/a/verify', {'kind': 'variant', 'rev': art.rev})
    assert r.status_code == 200 and site.ws.store.get('s1', 'a', 'variant').verified
    r = site.post('/s/s1/a/variant', {'variant': None, 'rev': art.rev})
    assert r.status_code == 200
    site.wait(r.json()['job'])
    assert site.ws.store.get('s1', 'a', 'variant').body['resolved'] is None
    assert site.post('/s/s1/b/variant', {'variant': 'one', 'rev': None}).status_code == 400


# --- running ---

def test_run_stage_and_from(site):
    r = site.post('/s/s1/a/run', {'stage': 'variant', 'onward': True, 'params': 'n=3 mode=fast'})
    assert r.status_code == 200
    info = site.wait(r.json()['job'])
    assert [o['status'] for o in info['outcomes']] == ['done'] * 4
    assert site.state('s1', 'a', 'x').state == 'unverified'
    # The deliberate check replaces a manual grade; a plain recheck never does.
    assert site.decide('s1', 'a', 'x', False).status_code == 200
    assert 'data-confirm="manual grade"' in site.get('/s/s1/a').text
    r = site.post('/s/s1/a/run', {'stage': 'evidence'})
    site.wait(r.json()['job'])
    assert site.state('s1', 'a', 'x').state == 'unverified'
    # An explicit rerun also replaces a verified parse; the button says so first.
    r = site.post('/s/s1/a/verify', {'kind': 'parse', 'rev': site.rev('s1', 'a', 'parse')})
    site.wait(r.json()['job'])
    assert 'data-confirm="verified parse"' in site.get('/s/s1/a').text
    r = site.post('/s/s1/a/run', {'stage': 'parse'})
    assert site.wait(r.json()['job'])['outcomes'][0]['status'] == 'done'
    assert not site.ws.store.get('s1', 'a', 'parse').verified
    assert site.post('/s/s1/a/run', {'stage': 'nope'}).status_code == 400
    assert site.post('/s/s1/a/run', {'stage': 'evidence', 'params': 'broken'}).status_code == 400
    r = site.post('/s/s2/a/run', {'stage': 'evidence'})
    assert site.wait(r.json()['job'])['outcomes'][0]['status'] == 'blocked'


def test_model_stages_refused_without_a_model(env):
    exam, ws, runner = env
    site = Site(exam, ws, runner)
    site.run_all()
    offline = Runner(exam, ws, model=None, pool=runner.pool)
    site = Site(exam, ws, offline)
    html = site.get('/s/s1/a').text
    assert 'no model' in html
    assert 'data-stage="transcript"' not in html and 'data-stage="evidence"' in html
    for stage in ('variant', 'transcript', 'parse'):
        r = site.post('/s/s1/a/run', {'stage': stage, 'onward': True})
        assert r.status_code == 400 and 'model' in r.json()['error']
    before = site.rev('s1', 'a', 'transcript')
    r = site.post('/s/s1/a/run', {'stage': 'evidence'})
    assert site.wait(r.json()['job'])['outcomes'][0]['status'] == 'done'
    assert site.rev('s1', 'a', 'transcript') == before
    # Everything else still works.
    r = site.post('/s/s1/a/save', {'kind': 'parse', 'body': {'x': '1', 'y': '2'},
                                   'rev': site.rev('s1', 'a', 'parse'), 'verify': True})
    site.wait(r.json()['job'])
    assert site.state('s1', 'a', 'x').state == 'graded'
    plan = site.post('/runs/preview', {'stages': ['transcript', 'evidence']}).json()
    assert plan['counts']['blocked'] > 0


def wait_batch(site):
    for _ in range(400):
        info = site.get('/runs/status').json()
        if info['state'] not in ('running', 'ready'):
            return info
        time.sleep(0.02)
    raise AssertionError("batch did not finish")


def test_batch_preview_run_status(site):
    assert site.get('/runs/status').json() == {'state': 'idle'}
    r = site.post('/runs/preview', {'stages': list(STAGES) + ['grade']})
    assert r.status_code == 200
    plan = r.json()
    assert plan['counts']['run'] == 14 and plan['counts']['blocked'] == 0     # the grade follows its check
    assert {g['action'] for g in plan['groups']} == {'run'}
    r = site.post('/runs/start', {'plan': plan['plan'], 'parallel': 2})
    assert r.status_code == 200
    info = wait_batch(site)
    assert info['state'] == 'done' and info['done'] == info['total'] == 14 and info['failures'] == []
    assert site.state('s2', 'b').state == 'unverified'
    # Nothing is protected yet, so everything would run again; missing-only skips it all.
    again = site.post('/runs/preview', {'stages': list(STAGES), 'missing_only': True}).json()
    assert again['counts']['run'] == 0 and again['counts']['skip_present'] > 0
    assert site.post('/runs/preview', {'stages': []}).status_code == 400
    assert site.post('/runs/preview', {'stages': ['evidence'], 'submissions': ['ghost']}).status_code == 400
    assert site.post('/runs/preview', {'stages': ['evidence'], 'questions': ['ghost']}).status_code == 400
    one = site.post('/runs/preview', {'stages': ['evidence'], 'submissions': ['s2'], 'questions': ['b']}).json()
    assert one['counts']['run'] == 1 and one['groups'][0]['submissions'] == ['s2']


def test_stale_preview_is_refused(site):
    site.run_all()
    plan = site.post('/runs/preview', {'stages': ['evidence']}).json()
    site.runner.save('s1', 'a', 'parse', {'x': '4', 'y': '2'}, author='other')
    r = site.post('/runs/start', {'plan': plan['plan']})
    assert r.status_code == 409 and 'out of date' in r.json()['error']
    assert site.post('/runs/start', {'plan': 'unknown'}).status_code == 409
    assert site.get('/runs/status').json() == {'state': 'idle'}
    assert site.post('/runs/stop').status_code == 200


# --- overrides and settings ---

def test_pages_and_layout_overrides(site):
    r = site.post('/s/s1/a/pages', {'pages': '1, 2', 'note': "continues overleaf"})
    assert r.status_code == 200
    assert site.ws.selected_pages('s1', site.exam.questions['a']) == ((1, 2), "continues overleaf")
    html = site.get('/s/s1/a').text
    assert '/page/s1/1.jpg' in html and 'continues overleaf' in html
    assert site.post('/s/s1/a/pages', {'pages': '7'}).status_code == 400
    assert site.post('/s/s1/a/pages', {'pages': 'x'}).status_code == 400
    assert site.post('/s/s1/a/pages', {'pages': ''}).status_code == 200
    assert site.ws.selected_pages('s1', site.exam.questions['a'])[0] == (1,)
    r = site.post('/s/s1/layout', {'cover_pages': 1, 'scan_window': '100 100 900 900'})
    assert r.status_code == 200
    layout = site.ws.layout('s1')
    assert layout.cover_pages == 1 and layout.window == (100, 100, 900, 900)
    assert site.ws.layout('s2').cover_pages == 0
    assert site.ws.store.get('s1', '', 'layout').author == 'tester'
    assert site.post('/s/s1/layout', {'scan_window': [900, 0, 100, 1000]}).status_code == 400
    assert site.post('/s/s1/layout', {'cover_pages': '', 'scan_window': ''}).status_code == 200
    assert site.ws.layout('s1').cover_pages == 0


def test_settings(site):
    r = site.post('/settings', {'cover_pages': 1, 'scan_window': [50, 40, 950, 900]})
    assert r.status_code == 200
    assert site.ws.cover_pages == 1 and site.ws.scan_window == (50, 40, 950, 900)
    html = site.get('/settings').text
    assert 'value="950"' in html
    assert site.post('/settings', {'cover_pages': -1, 'scan_window': [0, 0, 1000, 1000]}).status_code == 400
    assert site.post('/settings', {'cover_pages': 0, 'scan_window': [0, 0, 1000]}).status_code == 400
    assert site.post('/settings', {'cover_pages': 0, 'scan_window': [0, 0, 2000, 1000]}).status_code == 400
    assert site.ws.cover_pages == 1


# --- untrusted content ---

EVIL = '<script>alert(1)</script>'


def test_student_text_is_escaped(site):
    site.run_all()
    store = site.ws.store
    blocks = [
        {'type': 'note', 'text': f"note {EVIL}", 'tone': 'bad'},
        {'type': 'table', 'columns': [f"col {EVIL}"], 'rows': [[EVIL, None, 3]]},
        {'type': 'code', 'text': f"x = 1\n{EVIL}", 'highlight': [2]},
        {'type': 'details', 'summary': f"more {EVIL}", 'audience': 'grader',
         'blocks': [{'type': 'raw', 'html': f"<svg></svg>{EVIL}"}]},
        {'type': 'plot', 'title': f"plot {EVIL}", 'xlabel': "x", 'ylabel': "y",
         'series': [{'name': EVIL, 'style': 'step', 'x': [0, 1, None], 'y': [0, None, 1]}]},
        {'type': 'note', 'text': "hidden from everyone", 'audience': 'nobody'},
        "not a block",
    ]
    ev = store.get('s1', 'a', 'evidence')
    store.write('s1', 'a', 'evidence', Evidence({'fields': {}}, blocks).to_json(), origin='auto',
                inputs=dict(ev.inputs))
    store.write('s1', 'a', 'parse', {'x': EVIL, 'y': '"><img src=x onerror=alert(1)>'}, origin='manual')
    store.write('s1', 'a', 'transcript', f"</textarea>{EVIL}", origin='manual')
    site.runner.correct_cue('s1', 'a', 'c1', EVIL, author=EVIL)
    r = site.get('/s/s1/a')
    assert r.status_code == 200
    assert '<script>alert' not in r.text and '<img src=x' not in r.text and '</textarea><' not in r.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in r.text
    assert 'hidden from everyone' not in r.text
    # Plots come first, and their data travels as an escaped attribute.
    assert r.text.index('class="plot"') < r.text.index('class="note')
    assert '\\u003cscript' in r.text
    # Raw blocks are never inlined: a sandboxed frame points at a sandboxed response.
    assert re.search(r'<iframe class="raw" sandbox src="/raw/s1/a/3-0"', r.text)
    raw = site.get('/raw/s1/a/3-0')
    assert raw.status_code == 200 and EVIL in raw.text
    assert raw.headers['content-security-policy'].startswith('sandbox;')
    assert "script-src" not in raw.headers['content-security-policy']
    for path in ('/raw/s1/a/0', '/raw/s1/a/3', '/raw/s1/a/3-1', '/raw/s1/a/9', '/raw/s1/a/x', '/raw/s1/a/3-0-0'):
        assert site.get(path).status_code == 404, path
    assert '<script>alert' not in site.get('/').text


def test_names_are_escaped_in_the_grid(env):
    from examinator.core.store import Submission
    exam, ws, runner = env
    ws.store.put_submissions([Submission('s1', f"Eve {EVIL}", "", 's1.pdf')])
    site = Site(exam, ws, runner)
    for path in ('/', '/s/s1/a', '/settings'):
        text = site.get(path).text
        assert '<script>alert' not in text
    assert 'Eve &lt;script&gt;' in site.get('/').text


def test_block_helpers():
    blocks = [{'type': 'details', 'summary': "d", 'blocks': [{'type': 'raw', 'html': "<b>hi</b>"}]},
              {'type': 'code', 'text': "a\nb", 'highlight': [2]}, {'type': 'mystery'}]
    out = prepare_blocks(blocks)
    assert [b['type'] for b in out] == ['details', 'code']
    assert out[0]['blocks'] == [{'type': 'raw', 'path': '0-0'}]
    assert out[1]['lines'] == [(1, 'a', False), (2, 'b', True)]
    assert find_raw(blocks, '0-0') == "<b>hi</b>"
    assert find_raw(blocks, '0') is None and find_raw(blocks, '1') is None and find_raw(blocks, '') is None


# --- exam data: overrides ------------------------------------------------------

from pathlib import Path  # noqa: E402

from PIL import Image  # noqa: E402

from examinator import load_exam  # noqa: E402
from examinator.core.store import Submission  # noqa: E402
from examinator.core.workspace import Workspace  # noqa: E402
from examinator.model.fake import FakeModel  # noqa: E402
from examinator.pdf.layout import Layout  # noqa: E402

from conftest import SCRIPT  # noqa: E402
from test_overrides import make_keyed_exam  # noqa: E402

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'


def keyed_workspace(root):
    exam = make_keyed_exam()
    if not (root / 'pdfs').exists():
        (root / 'pdfs').mkdir(parents=True)
        pages = [Image.new('RGB', (200, 260), 'white') for _ in range(2)]
        for sid in ('s1', 's2'):
            pages[0].save(root / 'pdfs' / f'{sid}.pdf', 'PDF', save_all=True, append_images=pages[1:])
    ws = Workspace(root, exam)
    ws.store.put_submissions([Submission(s, s, "", f'{s}.pdf') for s in ('s1', 's2')])
    return exam, ws


@pytest.fixture
def ksite(tmp_path):
    """The keyed exam (keys held as data), every question graded and verified."""
    exam, ws = keyed_workspace(tmp_path / 'ws')
    runner = Runner(exam, ws, model=FakeModel(dict(SCRIPT)))
    for sid in ('s1', 's2'):
        for qid, q in exam.questions.items():
            assert all(o.ok for o in runner.run_from(sid, qid, q.stages[0].name))
            runner.verify(sid, qid, 'parse', author='t', expect_rev=ws.store.get(sid, qid, 'parse').rev)
    yield Site(exam, ws, runner)
    runner.close()
    ws.close()


def crop_box(html, path):
    m = re.search(rf'data-box="([^"]+)" data-region="{re.escape(path)}"', html)
    return m and m.group(1)


def test_exam_page_lists_entries(site):
    r = site.get('/exam')
    assert r.status_code == 200
    for path in ('a/pages', 'a/require', 'a/fields/x/region', 'a/cues/c1/region', 'a/cues/c1/reads',
                 'a/cues/c1/ask', 'a/stages/transcript/notes', 'a/stages/transcript/prompt', 'b/pages'):
        assert f'data-path="{path}"' in r.text, path
    assert '1 = one\n2 = two' in r.text          # a map, one reading per line
    assert 'href="/exam"' in site.get('/').text
    html = r.text
    assert '<script>' not in html and ' style="' not in html and not re.search(r'\son\w+="', html)


def test_exam_page_of_the_toy_exam(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    runner = Runner(exam, ws)
    try:
        r = Site(exam, ws, runner).get('/exam')
        assert r.status_code == 200
        assert 'data-path="areas/stages/evidence/key/green/A.2"' in r.text
        assert 'data-path="parity/stages/evidence/key/B" data-kind="choice"' in r.text
        assert 'even\nE\noption 1' in r.text
        assert 'value="2 400 300 900 420"' in r.text
    finally:
        runner.close()
        ws.close()


def test_set_and_clear_round_trip(site):
    path = 'a/cues/c1/ask'
    r = site.post('/exam/set', {'path': path, 'value': "the first cue, printed", 'rev': None})
    assert r.status_code == 200, r.text
    rev = r.json()['rev']
    assert site.exam.questions['a'].cues[0].ask == "the first cue, printed"
    assert site.ws.store.overrides()[path].author == 'tester'
    page = site.get('/exam').text
    assert 'package: <code>first cue</code>' in page and f'<code>{path}</code>' in page
    assert "a/cues/c1/ask: &#39;first cue&#39; -&gt; &#39;the first cue, printed&#39;" in page
    r = site.post('/exam/clear', {'path': path, 'rev': rev})
    assert r.status_code == 200 and r.json()['value'] == "first cue" and not r.json()['overridden']
    assert site.exam.questions['a'].cues[0].ask == "first cue" and not site.ws.store.overrides()


def test_set_validation_conflict_and_token(site):
    path = 'a/fields/x/region'
    r = site.post('/exam/set', {'path': path, 'value': [1, 500, 0, 100, 100], 'rev': None})
    assert r.status_code == 400 and 'left < right' in r.json()['error']
    assert site.post('/exam/set', {'path': 'a/stages/grade/fn', 'value': 1, 'rev': None}).status_code == 400
    assert site.post('/exam/set', {'path': path, 'value': [1, 0, 0, 10, 10]}).status_code == 400   # no rev
    assert site.client.post('/exam/set', json={'path': path, 'value': [1, 0, 0, 10, 10], 'rev': None}).status_code == 403
    assert site.client.post('/exam/clear', json={'path': path, 'rev': None},
                            headers={'X-Token': 'wrong'}).status_code == 403
    assert not site.ws.store.overrides()
    ok = site.post('/exam/set', {'path': path, 'value': [1, 0, 0, 10, 10], 'rev': None})
    assert ok.status_code == 200 and ok.json()['questions'] == [] and ok.json()['job'] is None
    stale = site.post('/exam/set', {'path': path, 'value': [1, 0, 0, 20, 20], 'rev': None})
    assert stale.status_code == 409
    assert stale.json()['current'] == {'rev': ok.json()['rev'], 'value': [1, 0, 0, 10, 10], 'who': 'tester'}
    assert site.post('/exam/clear', {'path': path, 'rev': ok.json()['rev'] + 100}).status_code == 409
    assert site.ws.store.overrides()[path].value == [1, 0, 0, 10, 10]


def test_region_override_moves_the_crop(ksite):
    path = 'a/fields/x/region'
    before = crop_box(ksite.get('/s/s1/a').text, path)
    assert before
    r = ksite.post('/exam/set', {'path': path, 'value': [1, 100, 300, 600, 450], 'rev': None, 'sub': 's1'})
    assert r.status_code == 200 and r.json()['affected'] == 0
    html = ksite.get('/s/s1/a').text
    after = crop_box(html, path)
    layout = ksite.ws.layout('s1')
    want = Layout(layout.cover_pages, layout.window).box(ksite.exam.questions['a'].fields[0].region, ksite.exam.crop_pad)
    assert after != before and after == ','.join(f'{v:.4f}' for v in want)
    assert '"path": "a/fields/x/region"' in html and '[1, 100, 300, 600, 450]' in html
    assert ksite.state('s1', 'a', 'x').state == 'graded'


def test_key_override_outdates_and_rechecks(ksite):
    path = 'a/stages/evidence/key/one/x'
    page = ksite.get('/s/s1/a').text
    assert f'data-path="{path}"' in page and 'data-path="a/stages/evidence/key/two/x"' not in page
    r = ksite.post('/exam/set', {'path': path, 'value': '5', 'rev': None, 'sub': 's2'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['questions'] == ['a'] and body['affected'] == 4 and body['variants'] == []
    assert ksite.state('s1', 'a', 'x').state in ('outdated', 'graded')     # the recheck is under way
    info = ksite.wait(body['job'])
    assert info['state'] == 'done' and info['done'] == info['total'] == 2 and not info['outcomes']
    for sid in ('s1', 's2'):
        st = ksite.state(sid, 'a', 'x')
        assert (st.state, st.value) == ('graded', False)
        assert ksite.state(sid, 'b').value is True
    r = ksite.post('/exam/clear', {'path': path, 'rev': body['rev']})
    assert r.json()['affected'] == 4       # parts x and y of both submissions
    ksite.wait(r.json()['job'])
    assert (ksite.state('s1', 'a', 'x').state, ksite.state('s1', 'a', 'x').value) == ('graded', True)


def test_choice_key_from_the_question_page(ksite):
    path = 'b/stages/evidence/key/x'
    assert f'data-path="{path}" data-kind="choice"' in ksite.get('/s/s1/b').text
    r = ksite.post('/exam/set', {'path': path, 'value': ['no', 'nope'], 'rev': None, 'sub': 's1'})
    assert r.status_code == 200 and r.json()['text'] == 'no\nnope'
    ksite.wait(r.json()['job'])
    assert ksite.state('s1', 'b').value is False
    assert ksite.post('/exam/set', {'path': path, 'value': [], 'rev': r.json()['rev']}).status_code == 400


def test_changed_cue_rereads_variants(ksite):
    path = 'a/cues/c1/region'
    r = ksite.post('/exam/set', {'path': path, 'value': [1, 0, 0, 1000, 250], 'rev': None})
    body = r.json()
    assert body['questions'] == ['a'] and body['variants'] == ['a']
    ksite.wait(body['job'])
    assert ksite.ws.store.get('s1', 'a', 'variant').body['resolved'] is None
    r = ksite.post('/exam/variants', {'questions': ['a']})
    assert r.status_code == 200 and r.json()['n'] == 2
    info = ksite.wait(r.json()['job'])
    assert info['state'] == 'done' and info['done'] == 2
    for sid in ('s1', 's2'):
        assert ksite.ws.store.get(sid, 'a', 'variant').body['resolved'] == 'one'
        assert ksite.state(sid, 'a', 'x').value is True
    assert ksite.post('/exam/variants', {'questions': ['b']}).status_code == 400


def test_overrides_survive_a_new_app(tmp_path):
    exam, ws = keyed_workspace(tmp_path / 'ws')
    runner = Runner(exam, ws)
    site = Site(exam, ws, runner)
    assert site.post('/exam/set', {'path': 'a/cues/c1/ask', 'value': "again", 'rev': None}).status_code == 200
    runner.close()
    ws.close()
    exam, ws = keyed_workspace(tmp_path / 'ws')      # a fresh package, nothing applied
    assert exam.questions['a'].cues[0].ask == "first cue"
    runner = Runner(exam, ws)
    try:
        site = Site(exam, ws, runner)
        assert exam.questions['a'].cues[0].ask == "again"
        assert 'package: <code>first cue</code>' in site.get('/exam').text
    finally:
        runner.close()
        ws.close()


# --- the focus ---------------------------------------------------------------------

def test_filter_page_and_values(site):
    site.run_all()
    r = site.get('/filter')
    assert r.status_code == 200 and 'id="fx-questions"' in r.text and '/static/filter.js' in r.text
    assert "'unsafe-inline'" not in r.headers['content-security-policy'].split('style-src')[0]
    assert '<script>' not in r.text and ' on' + 'click=' not in r.text
    assert 'href="/filter"' in site.get('/').text                     # in the top bar
    data = site.get('/focus/values').json()
    assert data['subs'] == ['s1', 's2']
    assert [q['id'] for q in data['questions']] == ['a', 'b']
    ids = [f['id'] for f in data['questions'][0]['facets']]
    assert ids[:5] == ['a/grade:x', 'a/state:x', 'a/grade:y', 'a/state:y', 'a/variant']
    assert data['values']['a/state:x'] == {'s1': ['unverified'], 's2': ['not_started']}
    assert data['values']['a/variant'] == {'s1': ['one'], 's2': ['unresolved']}
    one = site.get('/focus/values?sub=s2').json()
    assert one['subs'] == ['s2'] and one['values']['a/state:x'] == {'s2': ['not_started']}
    assert site.get('/focus/values?sub=nobody').status_code == 404


def test_grid_honours_a_focus_in_the_url(site):
    import json
    from urllib.parse import quote
    site.run_all()

    def shown(sel):
        html = site.get('/?focus=' + quote(json.dumps(sel))).text
        return [m.group(1) for m in re.finditer(r'<tr data-id="([^"]+)">', html)]

    assert shown({}) == ['s1', 's2']
    assert shown({'a/state:x': ['unverified']}) == ['s1']
    assert shown({'a/state:x': ['unverified', 'not_started']}) == ['s1', 's2']               # OR within
    assert shown({'a/state:x': ['unverified'], 'b/state': ['in_progress']}) == []            # AND across
    assert shown({'a/variant': ['unresolved']}) == ['s2']
    assert shown({'no/such': ['x']}) == ['s1', 's2']                                          # unknown: ignored
    assert site.get('/?focus=%7Bnot json').status_code == 200
    html = site.get('/?focus=' + quote(json.dumps({'a/variant': ['one']}))).text
    assert 'data-action="clear-focus" class="quiet" title="clear the focus">' in html and 'id="f-state"' not in html


def test_question_page_has_focus_slots(site):
    r = site.get('/s/s1/a')
    assert 'id="unfocused"' in r.text and 'id="like"' in r.text and '/static/focus.js' in r.text


def test_focus_values_follow_decisions(site):
    site.run_all()
    assert site.get('/focus/values').json()['values']['b/state']['s1'] == ['not_started']
    assert site.decide('s1', 'b', '', True).status_code == 200
    data = site.get('/focus/values').json()['values']
    assert data['b/state']['s1'] == ['manual'] and data['b/grade']['s1'] == ['✓']
