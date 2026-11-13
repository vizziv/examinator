"""The web app, against the synthetic mini exam and the scripted model."""
import base64
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from examinator import load_exam
from examinator.core.runner import Runner
from examinator.core.store import Made, Submission
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.pdf.layout import Layout
from examinator.std.blocks import MAX_IMAGE, Image
from examinator.web.app import create_app, find_image, prepare_blocks

from conftest import SCRIPT, workspace
from test_overrides import make_keyed_exam

BASE = 'http://127.0.0.1:8580'
TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'
EVIL = '<script>alert(1)</script>'
PNG = b'\x89PNG\r\n\x1a\n' + b'not really'      # bytes as stored; the route does not parse them
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class Site:
    """A test client that sends the page token on POST, as the page's script does."""

    def __init__(self, exam, ws, runner, **kw):
        self.exam, self.ws, self.runner = exam, ws, runner
        self.app = create_app(exam, ws, runner, author='tester', **kw)
        self.client = TestClient(self.app, base_url=BASE)
        self.wait(self.app.state.startup)
        html = self.client.get('/').text
        self.token = re.search(r'<meta name="token" content="([^"]+)"', html).group(1)

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def post(self, path, data=None, headers=None):
        return self.client.post(path, json=data or {}, headers={'X-Token': self.token, **(headers or {})})

    def wait(self, job):
        for _ in range(600):
            info = self.client.get(f'/jobs/{job}').json()
            if info['state'] != 'running':
                return info
            time.sleep(0.02)
        raise AssertionError("job did not finish")

    def value(self, sub, k):
        return self.runner.values(sub).get(k)

    def rev(self, sub, k):
        v = self.value(sub, k)
        return v.rev if v else None

    def seen(self, sub, q):
        """What a page shows: every field on it, at its revision."""
        return {k: self.rev(sub, k) for k in self.runner.page(sub, q)}

    def decide(self, sub, q, check='check', value=None, **extra):
        d = self.ws.store.get(sub, f'{q}/{check}', 'decision')
        return self.post(f'/s/{sub}/{q}/decide', {'check': check, 'value': value, 'rev': d.rev if d else None,
                                                  'seen': self.seen(sub, q), **extra})

    def state(self, sub, q, check='check'):
        question = self.exam.questions[q]
        return self.runner.sheet(sub).status(self.exam, question, question.check(check))

    def run_all(self, sub='s1', verify_printing=True):
        """Read 'p' and 'a' and run a's checks; with `verify_printing`, verify the printing,
        so that only 'a' itself waits for a person."""
        for q in ('p', 'a'):
            assert all(o.ok for o in self.runner.read(sub, q))
        if verify_printing:
            for label in ('c1', 'c2'):
                self.runner.verify(sub, 'p', label, author='t', expect_rev=self.rev(sub, f'p/{label}'))
        self.runner.refresh(sub, questions=['a'])


@pytest.fixture
def site(env):
    return Site(*env)


# --- pages ---

def test_grid_lists_every_submission(site):
    site.run_all()
    r = site.get('/')
    assert r.status_code == 200
    assert 'data-id="s1"' in r.text and 'data-id="s2"' in r.text
    for column in ('p', 'a.x', 'a.y', 'b'):       # 'p' has no checks: its column says how its fields stand
        assert f'data-col="{column}"' in r.text
    assert 'href="/s/s1/a"' in r.text
    assert 'data-q="p" data-col="p" data-s="verified"' in r.text and 'href="/s/s1/p" title="verified"' in r.text
    assert 'data-q="p" data-col="p" data-s="not_read"' in r.text       # s2: not read yet
    assert 'data-s="unverified"' in r.text and 'data-s="not_run"' in r.text
    # each column's "to do" toggle names its state facet and the states that do not count yet
    assert 'data-facet="a/state:y" data-todo="decided_outdated to_decide not_run outdated unverified"' in r.text
    assert 'data-facet="b/state"' in r.text and 'data-facet="p/state" data-todo="not_read unverified"' in r.text


def test_question_page_before_and_after_reading(site):
    r = site.get('/s/s1/a')
    assert r.status_code == 200 and 'not run' in r.text and 'placeholder="not read"' in r.text
    site.run_all()
    r = site.get('/s/s1/a')
    assert r.status_code == 200
    assert 'data-key="a/x" data-rev="' in r.text and 'value="1"' in r.text
    assert 'pill st st-unverified' in r.text and '<span class="tag unv">unverified</span>' in r.text
    assert '/page/s1/0.jpg' in r.text
    # the fields its checks read elsewhere are on the page too, linked to their question
    assert 'data-key="p/c1"' in r.text and 'href="/s/s1/p"' in r.text
    printing = site.get('/s/s1/p').text                        # no checks: values to verify, no grade
    assert 'data-key="p/c1"' in printing and 'value="one"' in printing and '<span class="ftag">printed</span>' in printing
    assert 'title="printed: first cue"' in printing and 'data-region="p/fields/c1/region"' in printing
    assert 'no grade' in printing and 'data-action="decide"' not in printing
    assert site.get('/s/s1/b').status_code == 200
    for path in ('/read', '/settings'):
        assert site.get(path).status_code == 200


def test_evidence_beside_each_field_and_blocks_below(site):
    site.run_all()
    html = site.get('/s/s1/a').text
    assert re.search(r'class="judged o-correct" title="x: correct', html)
    assert re.search(r'class="judged o-correct" title="y: correct', html)


def test_security_headers(site):
    for path in ('/', '/s/s1/a', '/static/app.css'):
        r = site.get(path)
        csp = r.headers['content-security-policy']
        assert "script-src 'self'" in csp and "style-src 'self'" in csp and 'unsafe' not in csp
        assert r.headers['x-content-type-options'] == 'nosniff'
    html = site.get('/s/s1/a').text
    assert '<script>' not in html and ' style="' not in html and not re.search(r'\son\w+="', html)


def test_theme_and_actions_are_static_and_controls_declare_actions(site):
    for path in ('/', '/s/s1/a', '/filter', '/read', '/exam', '/settings'):
        html = site.get(path).text
        assert html.index('/static/theme.js"></script>') < html.index('/static/app.css')
        assert '/static/actions.js' in html
        assert '<script>' not in html and ' style="' not in html and not re.search(r'\son\w+="', html)
    site.run_all()
    html = site.get('/s/s1/a').text
    for action in ('prev-student', 'next-student', 'toggle-scan', 'verify', 'verify-next', 'save', 'edit', 'decide',
                   'decide-blank', 'read', 'check', 'unverify'):
        assert f'data-action="{action}"' in html, action
    assert '<kbd>' not in html                                  # hints come from the key map
    assert 'pill g-' in html and 'pill st st-' in html          # grade and state: two pills
    grid = site.get('/').text
    assert 'class="c cell s-' in grid and 'title="incomplete' in grid


def test_page_image(site):
    r = site.get('/page/s1/0.jpg')
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg' and r.content[:2] == b'\xff\xd8'


# --- request validation ---

def test_post_needs_token(site):
    site.run_all()
    rev = site.rev('s1', 'a/x')
    body = {'values': {'a/x': {'text': '9', 'rev': rev}}}
    assert site.client.post('/s/s1/a/save', json=body).status_code == 403
    assert site.client.post('/s/s1/a/save', json=body, headers={'X-Token': 'wrong'}).status_code == 403
    assert site.client.post('/exam/set', json={'path': '@print_area', 'value': [1, 1, 999, 999], 'rev': None}).status_code == 403
    assert site.client.post('/read/stop').status_code == 403
    assert site.value('s1', 'a/x').text == '1'
    # the token header, then JSON only: no forms, no token in the body
    seen = site.seen('s1', 'b')
    r = site.client.post('/s/s1/b/decide', data={'check': 'check', 'value': 'yes', 'rev': '', 'seen': json.dumps(seen)},
                         headers={'X-Token': site.token})
    assert r.status_code == 415 and site.ws.store.get('s1', 'b/check', 'decision') is None
    r = site.client.post('/s/s1/b/decide', content=b'{"check": ', headers={'X-Token': site.token,
                                                                          'Content-Type': 'application/json'})
    assert r.status_code == 400
    assert site.post('/read/stop', [1]).status_code == 400                 # not an object
    big = {'values': {}, 'pad': 'x' * (2 << 20)}
    assert site.post('/s/s1/a/save', big).status_code == 413
    # a body is not even read without the token
    assert site.client.post('/s/s1/a/save', json=big).status_code == 403


def test_bad_host_and_origin(site):
    assert site.get('/', headers={'Host': 'evil.example'}).status_code == 400
    assert site.get('/', headers={'Host': '127.0.0.1.evil.example:8580'}).status_code == 400
    assert site.get('/', headers={'Host': 'localhost:8580'}).status_code == 200
    assert site.post('/read/stop', headers={'Origin': 'http://evil.example'}).status_code == 403
    assert site.post('/read/stop', headers={'Origin': BASE}).status_code == 200


def test_public_url_host_and_author(env):
    exam, ws, runner = env
    site = Site(exam, ws, runner, public_url='https://grading.example/exam')
    assert 'href="/exam/static/app.css"' in site.get('/').text
    assert site.client.get('/', headers={'Host': 'grading.example'}).status_code == 200
    assert site.client.get('/', headers={'Host': 'other.example'}).status_code == 400
    r = site.client.post('/s/s1/b/decide', json={'check': 'check', 'value': True, 'rev': None,
                                                 'seen': site.seen('s1', 'b')},
                         headers={'X-Token': site.token, 'Host': 'grading.example',
                                  'Origin': 'https://grading.example', 'X-Forwarded-User': 'ta-one'})
    assert r.status_code == 200
    assert ws.store.get('s1', 'b/check', 'decision').author == 'ta-one'


def test_unknown_targets_are_404(site, tmp_path):
    (tmp_path / 'secret.jpg').write_bytes(b'secret')
    for path in ('/s/nobody/a', '/s/s1/zzz', '/page/nobody/0.jpg', '/page/s1/2.jpg', '/page/s1/99.jpg',
                 '/page/s1/-1.jpg', '/page/s1/x.jpg', '/jobs/nope', '/image/s1/a/x/0',
                 '/page/../secret.jpg', '/page/s1/..%2f..%2fsecret.jpg', '/page/..%2f..%2fsecret/0.jpg',
                 '/s/..%2f..%2fetc/passwd', '/s/%2e%2e/%2e%2e', '/static/..%2fapp.py',
                 '/static/../app.py', '/static/%2e%2e/app.py', '/page/s1%2f..%2fs2/0.jpg'):
        r = site.get(path)
        assert r.status_code == 404, path
        assert b'secret' not in r.content
    assert site.post('/s/nobody/a/save', {'values': {}}).status_code == 404
    assert site.post('/s/s1/zzz/decide', {'check': 'check', 'value': True, 'rev': None, 'seen': {}}).status_code == 404
    assert site.post('/exam/set', {'path': '@print_area', 'value': [1, 1, 999, 999], 'rev': None,
                                  'scope': 'submission:nobody'}).status_code == 400


def test_symlinked_pdf_is_not_served(site):
    pdfs = site.ws.pdfs
    (pdfs / 's1.pdf').rename(pdfs / 'real.pdf')
    (pdfs / 's1.pdf').symlink_to(pdfs / 'real.pdf')
    assert site.get('/page/s1/0.jpg').status_code == 404
    assert site.get('/s/s1/a').status_code == 200


# --- the small changes, from the page ---

def test_save_conflict_keeps_the_stored_value(site):
    site.run_all()
    rev = site.rev('s1', 'a/x')
    site.runner.set('s1', 'a', 'x', "5", author='other', expect_rev=rev)
    r = site.post('/s/s1/a/save', {'values': {'a/x': {'text': 'mine', 'rev': rev}}})
    assert r.status_code == 409 and r.json()['conflict']
    assert site.value('s1', 'a/x').text == '5'
    assert site.post('/s/s1/a/save', {'values': {'a/x': {'text': 'mine'}}}).status_code == 400    # no rev
    assert site.post('/s/s1/a/save', {'values': {'a/nope': {'text': 'x', 'rev': None}}}).status_code == 400
    assert site.post('/s/s1/a/save', {'values': ['x']}).status_code == 400


def test_save_and_verify_all_here_recomputes_and_grades(site):
    site.run_all()
    assert site.state('s1', 'a', 'x').state == 'unverified'
    waiting = [{'key': k, 'rev': site.rev('s1', k)} for k in ('a/y',)]
    r = site.post('/s/s1/a/save', {'values': {'a/x': {'text': '3', 'rev': site.rev('s1', 'a/x')}},
                                   'verify': waiting})
    assert r.status_code == 200
    assert site.wait(r.json()['job'])['state'] == 'done'
    assert site.value('s1', 'a/x').verified == 'person' and site.value('s1', 'a/x').by == 'tester'
    assert site.value('s1', 'a/y').verified == 'person'
    assert (site.state('s1', 'a', 'x').state, site.state('s1', 'a', 'x').value) == ('graded', False)
    assert (site.state('s1', 'a', 'y').state, site.state('s1', 'a', 'y').value) == ('graded', True)
    assert '<span class="tag unv">unverified</span>' not in site.get('/s/s1/a').text      # no badge once verified


def test_verify_unverify_and_markers(site):
    site.run_all()
    rev = site.rev('s1', 'a/x')
    assert site.post('/s/s1/a/unverify', {'key': 'a/x', 'rev': rev}).status_code == 200      # already unverified
    assert site.post('/s/s1/a/save', {'verify': [{'key': 'a/x', 'rev': rev}]}).status_code == 200
    assert site.value('s1', 'a/x').verified == 'person' and site.rev('s1', 'a/x') == rev     # same revision
    assert site.post('/s/s1/a/unverify', {'key': 'a/x', 'rev': rev}).status_code == 200
    assert site.value('s1', 'a/x').verified is None
    r = site.post('/s/s1/a/save', {'values': {'a/x': {'text': '1 [REVIEW: or 7]', 'rev': rev}}})
    assert r.status_code == 200 and site.value('s1', 'a/x').verified is None     # a marker is never verified
    r = site.post('/s/s1/a/save', {'verify': [{'key': 'a/x', 'rev': site.rev('s1', 'a/x')}]})
    assert r.status_code == 400 and 'review' in r.json()['error']
    assert 'tag unv" title="a review marker' in site.get('/s/s1/a').text


def test_history_and_restore(site):
    site.run_all()
    site.runner.set('s1', 'a', 'x', "7", author='ta')
    html = site.get('/s/s1/a').text
    m = re.search(r'data-action="restore" data-key="a/x" data-rev="(\d+)"', html)
    assert m
    r = site.post('/s/s1/a/restore', {'key': 'a/x', 'rev': int(m.group(1)), 'expect': site.rev('s1', 'a/x')})
    assert r.status_code == 200 and site.value('s1', 'a/x').text == '1'
    assert site.value('s1', 'a/x').verified == 'person'


def test_decide_and_clear(site):
    site.run_all()
    r = site.decide('s1', 'a', 'x', False)
    assert r.status_code == 200
    st = site.state('s1', 'a', 'x')
    assert (st.state, st.value, st.author) == ('decided', False, 'tester')
    assert site.decide('s1', 'a', 'x', None, blank=True).status_code == 200
    assert site.state('s1', 'a', 'x').flags == ('blank',)
    assert site.decide('s1', 'a', 'x', 'maybe').status_code == 400
    assert site.decide('s1', 'a', 'nope', True).status_code == 400
    assert site.post('/s/s1/a/decide', {'check': 'x', 'value': True, 'rev': None}).status_code == 400    # no seen
    d = site.ws.store.get('s1', 'a/x', 'decision')
    assert 'data-action="clear"' in site.get('/s/s1/a').text
    assert site.post('/s/s1/a/clear', {'check': 'x', 'rev': d.rev}).status_code == 200
    assert site.state('s1', 'a', 'x').state == 'unverified'


def test_a_decision_against_a_changed_page_is_refused(site):
    site.run_all()
    seen = site.seen('s1', 'a')
    site.runner.set('s1', 'p', 'c1', 'two', author='other')        # a field the check read, elsewhere
    r = site.post('/s/s1/a/decide', {'check': 'x', 'value': True, 'rev': None, 'seen': seen})
    assert r.status_code == 409
    assert site.ws.store.get('s1', 'a/x', 'decision') is None


def page_seen(html):
    """What question.js sends as `seen`: every input on the page, at the revision it shows."""
    return {k: int(rev) if rev else None
            for k, rev in re.findall(r'<(?:input|textarea) [^>]*data-key="([^"]+)" data-rev="([^"]*)"', html)}


def test_a_decision_is_made_against_what_the_page_shows(site):
    assert all(o.ok for o in site.runner.read('s1', 'a'))
    site.runner.refresh('s1', questions=['a'])              # 'a' reads the printing, not read yet
    seen = page_seen(site.get('/s/s1/a').text)
    assert set(seen) == set(site.runner.page('s1', 'a'))   # other questions' fields included
    assert seen['p/c1'] is None and seen['a/x'] is not None
    assert all(o.ok for o in site.runner.read('s1', 'p'))  # read meanwhile: the page still shows no value
    r = site.post('/s/s1/a/decide', {'check': 'x', 'value': True, 'rev': None, 'seen': seen})
    assert r.status_code == 409 and site.ws.store.get('s1', 'a/x', 'decision') is None
    seen = page_seen(site.get('/s/s1/a').text)              # reloaded: now it shows them
    r = site.post('/s/s1/a/decide', {'check': 'x', 'value': True, 'rev': None, 'seen': seen})
    assert r.status_code == 200 and site.state('s1', 'a', 'x').state == 'decided'


def test_evidence_of_an_outdated_result_is_marked_stale(site):
    site.run_all()
    store = site.ws.store
    row = store.get('s1', 'a/x', 'result')
    store.write('s1', 'a/x', 'result', {**row.body, 'blocks': [{'type': 'note', 'text': "evidence"}]}, origin='auto')
    assert site.decide('s1', 'a', 'x', True).status_code == 200
    html = site.get('/s/s1/a').text
    assert 'class="panel evidence"' in html and 'evidence stale' not in html
    site.runner.set('s1', 'p', 'c1', 'two', author='other')     # the result (and the decision) is outdated now
    assert site.state('s1', 'a', 'x').state == 'decided_outdated'
    assert 'class="panel evidence stale"' in site.get('/s/s1/a').text
    site.decide('s1', 'a', 'x', True)                           # decided again: the result is still outdated
    assert site.state('s1', 'a', 'x').state == 'decided'
    assert 'class="panel evidence stale"' in site.get('/s/s1/a').text


def test_read_and_check_from_the_page(site):
    r = site.post('/s/s1/a/read', {})
    assert r.status_code == 200 and site.wait(r.json()['job'])['state'] == 'done'
    assert site.value('s1', 'a/x').text == '1'
    assert site.state('s1', 'a', 'x').state == 'unverified'        # its own checks ran after the read
    site.runner.set('s1', 'a', 'x', "8", author='ta')
    r = site.post('/s/s1/a/read', {})                               # a person's value stays
    site.wait(r.json()['job'])
    assert site.value('s1', 'a/x').text == '8'
    r = site.post('/s/s1/a/read', {'include_protected': True})
    site.wait(r.json()['job'])
    assert site.value('s1', 'a/x').text == '1'
    r = site.post('/s/s1/a/check', {'check': 'x'})
    assert site.wait(r.json()['job'])['state'] == 'done'
    assert site.post('/s/s1/a/check', {'check': 'nope'}).status_code == 400


def test_a_field_with_a_default_on_the_page(tmp_path):
    from examinator import Check, Exam, Field
    exam = Exam('d', pages=1)
    exam.question('q', pages=[1], fields=[Field('k', default='10')],
                  check=Check(lambda ctx: int(ctx.read('k')) > 5, isolate=False))
    ws = workspace(tmp_path, exam, subs=('s1',))
    site = Site(exam, ws, Runner(exam, ws))
    site.runner.refresh('s1', questions=['q'])
    html = site.get('/s/s1/q').text
    assert 'placeholder="default 10"' in html and '>default</span>' in html and 'data-region="' not in html
    assert site.state('s1', 'q').state == 'graded'                  # nobody need verify a default
    r = site.post('/s/s1/q/save', {'values': {'q/k': {'text': '3', 'rev': None}}})
    site.wait(r.json()['job'])
    assert (site.state('s1', 'q').state, site.state('s1', 'q').value) == ('graded', False)
    assert 'back to default 10' in site.get('/s/s1/q').text
    r = site.post('/s/s1/q/default', {'key': 'q/k', 'rev': site.rev('s1', 'q/k')})
    site.wait(r.json()['job'])
    assert site.value('s1', 'q/k') is None and site.state('s1', 'q').value is True
    assert site.post('/s/s1/q/default', {'key': 'q/k', 'rev': None}).status_code == 200     # nothing to drop
    ws.close()


def test_reading_is_refused_without_a_model(env):
    exam, ws, runner = env
    runner.model = None
    site = Site(exam, ws, runner)
    assert site.post('/s/s1/a/read', {}).status_code == 400
    assert 'data-action="read" disabled' in site.get('/s/s1/a').text


OWN = 'submission:s1'


def test_a_region_for_this_submission_only(site):
    site.run_all()
    path = 'p/fields/c1/region'
    r = site.post('/exam/set', {'path': path, 'value': [1, 0, 0, 500, 100], 'rev': None, 'scope': OWN, 'sub': 's1'})
    lv = r.json()['levels']
    assert r.status_code == 200 and lv['own'] == lv['value'] == [1, 0, 0, 500, 100] and lv['own_rev'] == r.json()['rev']
    assert r.json()['reread'] == ['p/c1'] and r.json()['job'] is None
    assert site.value('s1', 'p/c1').text == 'one'                 # no value changes
    assert site.ws.region('s2', site.exam.questions['p'], 'c1') == site.exam.questions['p'].fields[0].region
    html = site.get('/s/s1/p').text
    layout = site.ws.layout('s1')
    from examinator import Region
    want = ','.join(f'{v:.4f}' for v in Layout(layout.print_area).box(Region(1, 0, 0, 500, 100), 12))
    assert f'data-box="{want}" data-region="p/fields/c1/region"' in html
    r = site.post('/exam/clear', {'path': path, 'rev': r.json()['rev'], 'scope': OWN, 'sub': 's1'})
    assert r.status_code == 200 and r.json()['levels']['own'] is None and r.json()['rev'] is None
    assert r.json()['levels']['value'] == [1, 0, 0, 1000, 200]
    bad = {'path': path, 'value': [1, 500, 0, 10, 10], 'rev': None, 'scope': OWN}
    assert site.post('/exam/set', bad).status_code == 400
    assert site.post('/exam/set', {**bad, 'value': None}).status_code == 400        # to drop it, clear it
    assert site.post('/exam/set', {**bad, 'path': 'p/fields/zz/region', 'value': [1, 0, 0, 5, 5]}).status_code == 400
    assert site.post('/exam/set', {**bad, 'scope': 'source:nope', 'value': [1, 0, 0, 5, 5]}).status_code == 400
    assert site.post('/exam/set', {**bad, 'scope': 'everyone', 'value': [1, 0, 0, 5, 5]}).status_code == 400


def test_a_region_conflict_says_what_is_saved_so_saving_again_overwrites(site):
    from examinator.core import overrides
    path, exam = 'p/fields/c1/region', site.exam
    set_ = lambda value, rev: site.post('/exam/set', {'path': path, 'value': value, 'rev': rev, 'scope': OWN, 'sub': 's1'})
    first = set_([1, 0, 0, 500, 100], None).json()
    overrides.set(exam, site.ws.store, path, [1, 0, 200, 1000, 400], scope=OWN, author='other',
                  expect_rev=first['rev'])                                       # changed elsewhere
    r = set_([1, 0, 0, 600, 100], first['rev'])
    assert r.status_code == 409
    cur = r.json()['current']
    assert cur['who'] == 'other' and cur['value'] == [1, 0, 200, 1000, 400] and cur['rev'] != first['rev']
    r = set_([1, 0, 0, 600, 100], cur['rev'])                                    # knowingly
    assert r.status_code == 200 and r.json()['value'] == [1, 0, 0, 600, 100]
    overrides.clear(exam, site.ws.store, path, scope=OWN)                        # dropped elsewhere
    r = set_([1, 0, 0, 700, 100], r.json()['rev'])
    assert r.status_code == 409 and r.json()['current'] == {'rev': None, 'value': None, 'who': None}
    assert set_([1, 0, 0, 700, 100], None).status_code == 200


# --- read ---

def wait_batch(site):
    for _ in range(600):
        info = site.get('/read/status').json()
        if info['state'] not in ('running',):
            return info
        time.sleep(0.02)
    raise AssertionError("batch did not finish")


def test_batch_plan_run_status(site):
    assert site.get('/read/status').json() == {'state': 'idle'}
    r = site.post('/read/plan', {})
    assert r.status_code == 200
    plan = r.json()
    assert plan['counts']['read'] == 6 and plan['counts']['blocked'] == 0      # 3 reads, twice
    r = site.post('/read/start', {'parallel': 2})
    assert r.status_code == 200 and r.json()['total'] == 6
    info = wait_batch(site)
    # every read, then one refresh per submission: the checks ran without being asked for
    assert info['state'] == 'done' and info['done'] == info['total'] == 8 and info['failures'] == []
    assert site.state('s2', 'b').state == 'unverified'
    again = site.post('/read/plan', {'missing_only': True}).json()
    assert again['counts']['read'] == 0 and again['counts']['skip_present'] > 0
    for bad in ({'questions': []}, {'submissions': ['ghost']}, {'questions': ['ghost']}):
        assert site.post('/read/plan', bad).status_code == 400
        assert site.post('/read/start', bad).status_code == 400
    one = site.post('/read/plan', {'submissions': ['s2'], 'questions': ['b']}).json()
    assert one['counts']['read'] == 1 and one['groups'][0]['submissions'] == ['s2']
    assert site.post('/read/stop').status_code == 200


def test_a_batch_never_replaces_what_a_person_set_unless_told(site):
    site.run_all()
    site.runner.set('s1', 'a', 'x', "9", author='ta')
    plan = site.post('/read/plan', {'submissions': ['s1'], 'questions': ['a']}).json()
    assert plan['counts']['read'] == 1                     # y is not verified: it is read
    site.post('/read/start', {'submissions': ['s1'], 'questions': ['a']})
    wait_batch(site)
    assert site.value('s1', 'a/x').text == '9'
    site.post('/read/start', {'submissions': ['s1'], 'questions': ['a'], 'include_protected': True})
    wait_batch(site)
    assert site.value('s1', 'a/x').text == '1'


# --- overrides and settings ---

def test_pages_of_one_submission(site):
    own = lambda path, value, rev=None: site.post('/exam/set' if value is not None else '/exam/clear',
                                                  {'path': path, 'value': value, 'rev': rev, 'scope': OWN})
    assert own('a/pages', [1, 2]).status_code == 200
    assert site.ws.selected_pages('s1', site.exam.questions['a']) == (1, 2)
    assert site.ws.selected_pages('s2', site.exam.questions['a']) == (1,)
    assert '/page/s1/1.jpg' in site.get('/s/s1/a').text
    assert own('a/pages', [7]).status_code == 400 and own('a/pages', []).status_code == 400
    assert own('a/note', "continues overleaf").status_code == 400          # no such thing
    rev = site.ws.store.overrides(OWN)['a/pages'].rev
    assert own('a/pages', None, rev).status_code == 200
    assert site.ws.selected_pages('s1', site.exam.questions['a']) == (1,)
    assert own('@print_area', [100, 100, 900, 900]).status_code == 200
    assert site.ws.layout('s1') == Layout((100, 100, 900, 900)) and site.ws.layout('s2') == Layout((0, 0, 1000, 1000))


def test_print_area_at_each_scope(site):
    from examinator.core.store import Source
    site.ws.store.add_source(Source('src1', 'x.pdf', 'stack.pdf', 'h', 2, 'now'))
    site.ws.store.set_made(Made('s1', 'src1', (0,), {}, 'now'))
    SCAN = 'source:src1'
    html = site.get('/s/s1/a').text                 # two editing modes; the region picker lists fields only
    assert 'data-action="regions"' in html and 'data-action="print-area"' in html
    assert '#print-area' not in html and 'data-action="print-save-scan"' in html and SCAN in html
    pa = lambda scope, value, rev: site.post('/exam/set' if value is not None else '/exam/clear',
                                             {'path': '@print_area', 'value': value, 'rev': rev, 'scope': scope,
                                              'sub': 's1'})
    r = pa(OWN, [100, 100, 900, 900], None)
    assert r.status_code == 200 and r.json()['levels']['own'] == [100, 100, 900, 900]
    assert all(len(b.split(',')) == 4 for b in r.json()['boxes'].values()) and r.json()['boxes']
    rev = r.json()['levels']['own_rev']
    r = pa(OWN, [0, 0, 1000, 1000], rev + 99)
    assert r.status_code == 409 and r.json()['current'] == {'rev': rev, 'value': [100, 100, 900, 900], 'who': 'tester'}
    r = pa(SCAN, [20, 20, 980, 980], None)
    assert r.json()['levels']['scan'] == [20, 20, 980, 980]
    assert r.json()['levels']['value'] == [100, 100, 900, 900]           # its own wins
    r = pa(OWN, None, rev)
    assert r.json()['levels']['value'] == [20, 20, 980, 980]             # then the scan's
    scan_rev = r.json()['levels']['scan_rev']
    r = pa(SCAN, [30, 30, 970, 970], None)                               # it exists already
    assert r.status_code == 409 and r.json()['current']['rev'] == scan_rev
    assert r.json()['current']['value'] == [20, 20, 980, 980]
    assert pa(SCAN, None, scan_rev).status_code == 200
    r = pa('', [5, 5, 995, 995], None)
    assert r.json()['levels']['value'] == [5, 5, 995, 995] and site.exam.print_area == (5, 5, 995, 995)
    assert site.ws.layout('s2').print_area == (5, 5, 995, 995)
    assert pa('', None, r.json()['rev']).json()['levels']['value'] == list(site.exam.base_attrs['print_area'])
    assert pa(OWN, [900, 0, 100, 1000], None).status_code == 400
    assert pa(OWN, [0, 0, 1000], None).status_code == 400
    assert site.post('/exam/set', {'path': '@print_area', 'value': [0, 0, 1000, 1000], 'scope': SCAN}).status_code == 400  # rev
    assert site.get('/s/s2/a').text.count('data-action="print-save-scan"') == 1       # there, but hidden: no scan
    assert 'id="pa-save-scan" data-action="print-save-scan" title="the print area of every submission split from the same scan file" hidden' \
        in site.get('/s/s2/a').text


def test_the_exam_page_lists_every_scope(site):
    from examinator.core import overrides
    site.post('/exam/set', {'path': '@print_area', 'value': [5, 5, 995, 995], 'rev': None})
    site.post('/exam/set', {'path': 'a/pages', 'value': [1], 'rev': None, 'scope': OWN})
    html = site.get('/exam').text
    assert '@print_area: [0, 0, 1000, 1000] -&gt; [5, 5, 995, 995]' in html
    assert 'data-path="@print_area"' in html and 'cover' not in html                   # editable here
    assert 'data-action="scoped-revert" class="danger" data-scope="submission:s1" data-path="a/pages"' in html
    rev = site.ws.store.overrides(OWN)['a/pages'].rev
    assert site.post('/exam/clear', {'path': 'a/pages', 'rev': rev + 1, 'scope': OWN}).status_code == 409
    assert site.post('/exam/clear', {'path': 'a/pages', 'rev': rev, 'scope': OWN}).status_code == 200
    assert 'data-scope="submission:s1"' not in site.get('/exam').text
    with pytest.raises(ValueError):
        overrides.validate(site.exam, '@cover_pages', 2)


def test_settings_page_has_no_exam_data(site):
    html = site.get('/settings').text
    assert 'print area' not in html and 'id="keymap"' in html
    assert site.post('/settings', {'print_area': [1, 1, 999, 999]}).status_code == 405


# --- untrusted content ---

def test_student_text_is_escaped(site):
    site.run_all()
    store = site.ws.store
    blocks = [
        {'type': 'note', 'text': f"note {EVIL}", 'tone': 'bad'},
        {'type': 'table', 'columns': [f"col {EVIL}"], 'rows': [[EVIL, None, 3]]},
        {'type': 'code', 'text': f"x = 1\n{EVIL}", 'highlight': [2]},
        {'type': 'details', 'summary': f"more {EVIL}",
         'blocks': [Image(SVG, 'image/svg+xml', alt=EVIL).to_json()]},
        Image(PNG, title=f"plot {EVIL}").to_json(),
        {'type': 'image', 'media_type': 'text/html', 'data': base64.b64encode(EVIL.encode()).decode()},
        "not a block",
    ]
    row = store.get('s1', 'a/x', 'result')
    store.write('s1', 'a/x', 'result', {**row.body, 'blocks': blocks,
                                        'fields': {'x': {'outcome': None, 'note': EVIL, 'expected': EVIL}}},
                origin='auto')
    site.runner.set('s1', 'a', 'x', EVIL, author='t')
    site.runner.set('s1', 'a', 'y', '"><img src=x onerror=alert(1)>\n</textarea>' + EVIL, author='t')
    r = site.get('/s/s1/a')
    assert r.status_code == 200
    assert '<script>alert' not in r.text and '<img src=x' not in r.text and '</textarea><' not in r.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in r.text
    assert r.text.index('class="figure"') < r.text.index('class="note')       # images first
    png = re.search(r'<img class="figure" src="(/image/s1/a/x/4\?v=[0-9a-f]{16})"', r.text).group(1)
    svg = re.search(r'<img class="figure" src="(/image/s1/a/x/3-0\?v=[0-9a-f]{16})"', r.text).group(1)
    assert r.text.count('class="figure"') == 2                                 # not the text/html one
    for url, data, kind in ((png, PNG, 'image/png'), (svg, SVG, 'image/svg+xml')):
        got = site.get(url)
        assert got.status_code == 200 and got.content == data
        assert got.headers['content-type'] == kind and got.headers['x-content-type-options'] == 'nosniff'
        assert got.headers['content-security-policy'] == "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        assert 'immutable' in got.headers['cache-control']
    assert site.get('/image/s1/a/x/4?v=old').headers['cache-control'] == 'no-store'     # any other version
    for path in ('/image/s1/a/x/0', '/image/s1/a/x/3', '/image/s1/a/x/3-1', '/image/s1/a/x/5',
                 '/image/s1/a/x/9', '/image/s1/a/y/3-0'):
        assert site.get(path).status_code == 404, path
    assert '<script>alert' not in site.get('/').text


def test_names_are_escaped_in_the_grid(env):
    exam, ws, runner = env
    ws.store.put_submissions([Submission('s1', f"Eve {EVIL}", "", 's1.pdf')])
    site = Site(exam, ws, runner)
    for path in ('/', '/s/s1/a', '/settings'):
        assert '<script>alert' not in site.get(path).text
    assert 'Eve &lt;script&gt;' in site.get('/').text


def test_block_helpers():
    image = Image(PNG, alt="a").to_json()
    blocks = [{'type': 'details', 'summary': "d", 'blocks': [image]},
              {'type': 'code', 'text': "a\nb", 'highlight': [2]}, {'type': 'mystery'}]
    out = prepare_blocks(blocks)
    assert [b['type'] for b in out] == ['details', 'code']
    assert out[0]['blocks'] == [{'type': 'image', 'path': '0-0', 'v': out[0]['blocks'][0]['v'], 'alt': "a", 'title': ""}]
    assert out[1]['lines'] == [(1, 'a', False), (2, 'b', True)]
    assert find_image(blocks, '0-0') == image
    assert find_image(blocks, '0') is None and find_image(blocks, '') is None


def test_image_block():
    b = Image(PNG, title="t").to_json()
    assert b['type'] == 'image' and b['media_type'] == 'image/png' and base64.b64decode(b['data']) == PNG
    assert json.loads(json.dumps(b)) == b
    for kind in ('text/html', 'image/svg', 'application/octet-stream'):
        with pytest.raises(ValueError):
            Image(PNG, kind)
    with pytest.raises(ValueError):
        Image(b'x' * (MAX_IMAGE + 1))


# --- exam data: overrides ------------------------------------------------------

@pytest.fixture
def ksite(tmp_path):
    """The keyed exam (keys held as data), every value read and verified, every check run."""
    exam = make_keyed_exam()
    ws = workspace(tmp_path, exam)
    runner = Runner(exam, ws, model=FakeModel(dict(SCRIPT)))
    for sid in ('s1', 's2'):
        for qid in exam.grading:
            assert all(o.ok for o in runner.read(sid, qid))
        for k, v in runner.values(sid).items():
            runner.verify(sid, *k.split('/'), author='t', expect_rev=v.rev)
        runner.refresh(sid, questions=list(exam.grading))
    yield Site(exam, ws, runner)
    runner.close()
    ws.close()


def crop_box(html, path):
    m = re.search(rf'data-box="([^"]+)" data-region="{re.escape(path)}"', html)
    return m and m.group(1)


def test_exam_page_lists_entries(site):
    r = site.get('/exam')
    assert r.status_code == 200
    for path in ('a/pages', 'a/fields/x/region', 'p/fields/c1/region', 'p/fields/c1/read/reads',
                 'p/fields/c1/read/ask', 'a/read/notes', 'b/pages'):
        assert f'data-path="{path}"' in r.text, path
    assert '1 = one\n2 = two' in r.text          # a map, one reading per line
    html = r.text
    assert '<script>' not in html and ' style="' not in html and not re.search(r'\son\w+="', html)


def test_exam_page_of_the_toy_exam(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    runner = Runner(exam, ws)
    try:
        r = Site(exam, ws, runner).get('/exam')
        assert r.status_code == 200
        assert 'data-path="areas/checks/check/key/green/A.2"' in r.text
        assert 'data-path="parity/checks/check/key/B" data-kind="choice"' in r.text
        assert 'even\nE\noption 1' in r.text and 'value="2 400 300 900 420"' in r.text
    finally:
        runner.close()
        ws.close()


def test_set_and_clear_round_trip(site):
    path = 'p/fields/c1/read/ask'
    r = site.post('/exam/set', {'path': path, 'value': "the first cue, printed", 'rev': None})
    assert r.status_code == 200, r.text
    assert r.json()['reread'] == ['p/c1'] and r.json()['job'] is None        # offered: read again
    rev = r.json()['rev']
    assert site.exam.questions['p'].fields[0].read.ask == "the first cue, printed"
    page = site.get('/exam').text
    assert 'package: <code>first cue</code>' in page
    r = site.post('/exam/clear', {'path': path, 'rev': rev})
    assert r.status_code == 200 and r.json()['value'] == "first cue" and not r.json()['overridden']


def test_set_validation_conflict_and_token(site):
    path = 'a/fields/x/region'
    r = site.post('/exam/set', {'path': path, 'value': [1, 500, 0, 100, 100], 'rev': None})
    assert r.status_code == 400 and 'left < right' in r.json()['error']
    assert site.post('/exam/set', {'path': 'a/checks/x/fn', 'value': 1, 'rev': None}).status_code == 400
    assert site.post('/exam/set', {'path': path, 'value': [1, 0, 0, 10, 10]}).status_code == 400   # no rev
    assert site.client.post('/exam/set', json={'path': path, 'value': [1, 0, 0, 10, 10], 'rev': None}).status_code == 403
    ok = site.post('/exam/set', {'path': path, 'value': [1, 0, 0, 10, 10], 'rev': None})
    assert ok.status_code == 200 and ok.json()['checks'] == [] and ok.json()['job'] is None
    stale = site.post('/exam/set', {'path': path, 'value': [1, 0, 0, 20, 20], 'rev': None})
    assert stale.status_code == 409
    assert stale.json()['current'] == {'rev': ok.json()['rev'], 'value': [1, 0, 0, 10, 10], 'who': 'tester'}


def test_region_override_moves_the_crop_and_changes_no_value(ksite):
    path = 'a/fields/x/region'
    before = crop_box(ksite.get('/s/s1/a').text, path)
    assert before
    r = ksite.post('/exam/set', {'path': path, 'value': [1, 100, 300, 600, 450], 'rev': None, 'sub': 's1'})
    assert r.status_code == 200 and r.json()['job'] is None and r.json()['reread'] == ['a/x']
    after = crop_box(ksite.get('/s/s1/a').text, path)
    assert after != before
    assert ksite.state('s1', 'a', 'x').state == 'graded'


def test_key_override_outdates_and_recomputes(ksite):
    path = 'a/checks/x/key/one/x'
    page = ksite.get('/s/s1/a').text
    assert f'data-path="{path}"' in page and 'data-path="a/checks/x/key/two/x"' not in page
    assert '<summary>key x <span class="dim">one</span>' in page          # the key the printing picks
    r = ksite.post('/exam/set', {'path': path, 'value': '5', 'rev': None, 'sub': 's2'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['checks'] == ['a.x']
    info = ksite.wait(body['job'])
    assert info['state'] == 'done' and info['done'] == info['total'] == 2
    for sid in ('s1', 's2'):
        assert (ksite.state(sid, 'a', 'x').state, ksite.state(sid, 'a', 'x').value) == ('graded', False)
        assert ksite.state(sid, 'a', 'y').value is True and ksite.state(sid, 'b').value is True
    r = ksite.post('/exam/clear', {'path': path, 'rev': body['rev']})
    ksite.wait(r.json()['job'])
    assert (ksite.state('s1', 'a', 'x').state, ksite.state('s1', 'a', 'x').value) == ('graded', True)


def test_overrides_survive_a_new_app(tmp_path):
    exam = make_keyed_exam()
    ws = workspace(tmp_path, exam)
    runner = Runner(exam, ws)
    site = Site(exam, ws, runner)
    assert site.post('/exam/set', {'path': 'p/fields/c1/read/ask', 'value': "again", 'rev': None}).status_code == 200
    runner.close()
    ws.close()
    exam = make_keyed_exam()                 # a fresh package, nothing applied
    ws = Workspace(tmp_path / 'ws', exam)
    runner = Runner(exam, ws)
    try:
        site = Site(exam, ws, runner)
        assert exam.questions['p'].fields[0].read.ask == "again"
        assert 'package: <code>first cue</code>' in site.get('/exam').text
    finally:
        runner.close()
        ws.close()


def test_the_app_recomputes_what_a_code_change_outdated_when_it_starts(ksite):
    exam, ws, runner = ksite.exam, ksite.ws, ksite.runner
    exam.code_hash = 'changed'
    assert ksite.state('s1', 'a', 'x').state == 'outdated'
    Site(exam, ws, runner)                  # waits for its start-up job
    assert ksite.state('s1', 'a', 'x').state == 'graded'


# --- the focus ---------------------------------------------------------------------

def test_filter_page_and_values(site):
    site.run_all()
    r = site.get('/filter')
    assert r.status_code == 200 and 'id="fx-questions"' in r.text and '/static/filter.js' in r.text
    data = site.get('/focus/values').json()
    assert data['subs'] == ['s1', 's2']
    assert [q['id'] for q in data['questions']] == ['p', 'a', 'b']
    assert [f['id'] for f in data['questions'][0]['facets']] == ['p/state', 'p/field/c1', 'p/field/c2']
    ids = [f['id'] for f in data['questions'][1]['facets']]
    assert ids[:4] == ['a/grade:x', 'a/state:x', 'a/grade:y', 'a/state:y']
    assert data['values']['a/state:x'] == {'s1': ['unverified'], 's2': ['not_run']}
    assert data['values']['p/field/c1'] == {'s1': ['one']}
    one = site.get('/focus/values?sub=s2').json()
    assert one['subs'] == ['s2'] and one['values']['a/state:x'] == {'s2': ['not_run']}
    assert site.get('/focus/values?sub=nobody').status_code == 404


def test_grid_honours_a_focus_in_the_url(site):
    site.run_all()

    def shown(sel):
        html = site.get('/?focus=' + quote(json.dumps(sel))).text
        return [m.group(1) for m in re.finditer(r'<tr data-id="([^"]+)">', html)]

    assert shown({}) == ['s1', 's2']
    assert shown({'a/state:x': ['unverified']}) == ['s1']
    assert shown({'a/state:x': ['unverified', 'not_run']}) == ['s1', 's2']                   # OR within
    assert shown({'a/state:x': ['unverified'], 'b/state': ['graded']}) == []                 # AND across
    assert shown({'p/field/c1': ['one']}) == ['s1']                                         # the printing
    assert shown({'no/such': ['x']}) == ['s1', 's2']                                          # unknown: ignored
    assert site.get('/?focus=%7Bnot json').status_code == 200


def test_focus_values_follow_decisions(site):
    site.run_all()
    assert site.get('/focus/values').json()['values']['b/state']['s1'] == ['not_run']
    assert site.decide('s1', 'b', 'check', True).status_code == 200
    data = site.get('/focus/values').json()['values']
    assert data['b/state']['s1'] == ['decided'] and data['b/grade']['s1'] == ['✓']


def test_inverted_scans_filter_setting_and_csp(site):
    for path in ('/', '/s/s1/a', '/settings'):
        r = site.get(path)
        assert '<filter id="image-gamma"' in r.text
        assert "default-src 'none'" in r.headers['content-security-policy']
    assert 'id="invert-scans"' in site.get('/settings').text
