"""The scans page: upload, step-through edits, check, accept, names. Synthetic stacks only."""
import re
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from examinator import load_exam, synth
from examinator.core import identity
from examinator.core.runner import Runner
from examinator.core.workspace import Workspace
from examinator.web.app import create_app

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'
BASE = 'http://127.0.0.1:8580'


@pytest.fixture
def make_site(tmp_path):
    """`make_site(model=True)`: the app over a workspace with synthetic stacks, with
    the scripted model or none. Returns (client, token, workspace, stacks)."""
    opened = []

    def make(model=True):
        exam = load_exam(TOY)
        root = tmp_path / 'ws'
        paths = synth.stacks(exam, root)
        ws = Workspace(root, exam, create=True)
        runner = Runner(exam, ws, model=synth.fake_model(root) if model else None)
        opened.append((runner, ws))
        client = TestClient(create_app(exam, ws, runner, author='tester'), base_url=BASE)
        token = re.search(r'<meta name="token" content="([^"]+)"', client.get('/scans').text).group(1)
        return client, token, ws, paths
    yield make
    for runner, ws in opened:
        runner.close()
        ws.close()


@pytest.fixture
def site(make_site):
    return make_site()


def wait(client, job):
    for _ in range(500):
        state = client.get(f'/jobs/{job}').json()
        if state['state'] != 'running':
            return state
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def post(client, token, path, data):
    return client.post(path, json=data, headers={'X-Token': token})


def upload(client, token, path, name='stack.pdf', **headers):
    return client.post(f'/scans/upload?name={name}', content=path.read_bytes(),
                       headers={'Content-Type': 'application/pdf', **({'X-Token': token} if token else {}), **headers})


def test_upload_needs_the_token_and_a_pdf(site):
    client, token, ws, paths = site
    assert upload(client, None, paths[0]).status_code == 403
    assert upload(client, 'wrong', paths[0]).status_code == 403
    r = client.post('/scans/upload?name=x.pdf', content=b'hello', headers={'X-Token': token})
    assert r.status_code == 400 and ws.store.sources() == []
    assert upload(client, token, paths[0], Origin='http://evil.example').status_code == 403
    r = upload(client, token, paths[0], name='../../etc/stack-1.pdf')
    assert r.status_code == 200 and r.json()['new'] and r.json()['pages'] == 24
    assert ws.store.sources()[0].name == 'stack-1.pdf'
    assert not upload(client, token, paths[0]).json()['new']           # the same scan again
    assert not list((ws.root / 'sources').glob('.upload-*'))           # no temporary files left
    assert 'stack-1.pdf' in client.get('/scans').text


def check_and_accept(client, token, src):
    """Check the plan with the (scripted) model, turn the upside-down exam, accept."""
    plan = client.get(f'/scans/{src}/plan').json()
    wait(client, post(client, token, f'/scans/{src}/propose', {'check': True, 'rev': plan['rev']}).json()['job'])
    plan = client.get(f'/scans/{src}/plan').json()
    for _ in range(2):          # two quarter turns
        plan = post(client, token, f'/scans/{src}/edit', {'op': 'turn', 'k': 4, 'rev': plan['rev']}).json()['plan']
    return plan, post(client, token, f'/scans/{src}/accept', {'rev': plan['rev']})


def test_step_through_edits_and_undo(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    plan = client.get(f'/scans/{src}/plan').json()
    assert [e['start'] for e in plan['entries']] == [0, 4, 8, 12, 16, 20] and plan['cursor'] == 0
    # stale revision: refused
    assert post(client, token, f'/scans/{src}/edit', {'op': 'confirm', 'k': 0, 'rev': plan['rev'] - 1}).status_code == 409
    r = post(client, token, f'/scans/{src}/edit', {'op': 'confirm', 'k': 0, 'rev': plan['rev']})
    plan = r.json()['plan']
    r = post(client, token, f'/scans/{src}/edit', {'op': 'move', 'k': 1, 'delta': -1, 'rev': plan['rev']})
    plan = r.json()['plan']
    assert [e['start'] for e in plan['entries']][:3] == [0, 3, 7]
    assert post(client, token, f'/scans/{src}/edit', {'op': 'move', 'k': 1, 'delta': 5, 'rev': plan['rev']}).status_code == 400
    assert post(client, token, f'/scans/{src}/edit', {'op': 'nope', 'rev': plan['rev']}).status_code == 400
    plan = post(client, token, f'/scans/{src}/undo', {'rev': plan['rev']}).json()['plan']
    assert [e['start'] for e in plan['entries']][:2] == [0, 4]


def test_check_turn_and_accept(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    plan, r = check_and_accept(client, token, src)
    assert plan['checked'] and plan['failed'] == 0 and [e['start'] for e in plan['entries']] == [0, 4, 7, 12, 16, 20]
    assert plan['readings']['4'] == 'p.1'
    assert plan['sheet_o'][17] == 2 and plan['entries'][4]['own']
    assert r.status_code == 200 and len(r.json()['report']['new']) == 6
    assert len(list(ws.pdfs.glob('*.pdf'))) == 6
    assert client.get('/').status_code == 200 and 's0005' in client.get('/').text


def test_naming_and_a_forced_accept(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    check_and_accept(client, token, src)
    post(client, token, '/scans/roster', {'csv': (ws.root / 'roster.csv').read_text()})
    label = identity.label(identity.people(ws)[4])
    assert post(client, token, '/scans/who', {'sub': 's0005', 'text': label}).json()['who']['name'] == "Emery Sampleton"
    assert ws.store.submission('s0005').name == "Emery Sampleton"
    assert post(client, token, '/scans/who', {'sub': 'nobody', 'text': label}).status_code == 400
    # changing a written submission that has work: refused, then forced
    ws.store.write('s0002', 'parity/B', 'value', {'text': 'odd'}, origin='manual', author='t')
    plan = client.get(f'/scans/{src}/plan').json()
    plan = post(client, token, f'/scans/{src}/edit', {'op': 'drop', 'sheet': 6, 'rev': plan['rev']}).json()['plan']
    r = post(client, token, f'/scans/{src}/accept', {'rev': plan['rev']})
    assert r.status_code == 409 and r.json()['current']['busy'] == ['s0002']
    r = post(client, token, f'/scans/{src}/accept', {'rev': plan['rev'], 'force': True})
    assert r.json()['report']['rewritten'] == ['s0002']


def test_sheet_images(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[1]).json()['id']
    r = client.get(f'/sheet/{src}/0.jpg?o=2&w=140')
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'
    from io import BytesIO
    from PIL import Image
    assert Image.open(BytesIO(r.content)).width == 140
    for bad in ('/sheet/{s}/99.jpg', '/sheet/{s}/0.jpg?o=4', '/sheet/{s}/0.jpg?o=20', '/sheet/{s}/0.jpg?w=17', '/sheet/nosuch/0.jpg'):
        assert client.get(bad.format(s=src)).status_code == 404


# --- who wrote each submission ---

def test_roster_paste_preview_then_save(site):
    client, token, ws, paths = site
    text = "Ada Lovelace, 31415926\n27182818\tAlan Turing\nGrace Hopper\n"
    assert client.post('/scans/roster', json={'text': text, 'dry': True}).status_code == 403
    assert post(client, 'wrong', '/scans/roster', {'text': text, 'dry': True}).status_code == 403
    r = post(client, token, '/scans/roster', {'text': text, 'dry': True})
    body = r.json()
    assert r.status_code == 200 and body['people'] == 2 and body['problems'] == ["line 3: expected a name and an ID"]
    assert body['sample'][1] == {'name': "Alan Turing", 'id': "27182818", 'email': ""}
    assert identity.people(ws) == []                                    # a preview saves nothing
    assert post(client, token, '/scans/roster', {'text': text}).status_code == 400      # problems: not saved
    r = post(client, token, '/scans/roster', {'text': text.replace("Grace Hopper\n", "")})
    assert r.status_code == 200 and [p['name'] for p in identity.people(ws)] == ["Ada Lovelace", "Alan Turing"]
    r = client.post('/scans/roster', json={'text': text, 'dry': True},
                    headers={'X-Token': token, 'Origin': 'http://evil.example'})
    assert r.status_code == 403


def test_names_read_confirm_cycle_skip(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    check_and_accept(client, token, src)
    post(client, token, '/scans/roster', {'text': (ws.root / 'roster.csv').read_text()})
    view = client.get('/scans/names').json()
    assert view['left'] == 6 and {r['doubt'] for r in view['rows']} == {'unread'}
    assert [c['name'] for c in view['rows'][0]['crops']] == ['name', 'id']
    assert all(r['stale'] == ['name', 'id'] for r in view['rows'])
    # reading: a background job with the (scripted) model, refused without the token
    assert client.post('/scans/names/read', json={}).status_code == 403
    r = post(client, token, '/scans/names/read', {})
    assert r.json()['n'] == 6 and wait(client, r.json()['job'])['state'] == 'done'
    assert post(client, token, '/scans/names/read', {}).json()['n'] == 0     # nothing left to read
    view = client.get('/scans/names').json()
    rows = {r['sub']: r for r in view['rows']}
    assert view['rows'][0]['sub'] == 's0004' and rows['s0004']['doubt'] == 'disagree'
    assert rows['s0006']['doubt'] == 'nobody' and rows['s0002']['candidates'][0]['why'] == "ID 1 off, name exact"
    # confirm the top proposal (V): by key, against the revision seen (none yet)
    top = rows['s0001']['candidates'][0]
    r = post(client, token, '/scans/who', {'sub': 's0001', 'text': top['key'], 'rev': None})
    assert r.status_code == 200 and r.json()['who']['name'] == "Avery Testwell"
    assert post(client, token, '/scans/who', {'sub': 's0001', 'text': top['key'], 'rev': None}).status_code == 409
    # cycle to the second candidate (D), then confirm it
    second = rows['s0004']['candidates'][1]
    assert second['name'] == "Devon Fakely"
    post(client, token, '/scans/who', {'sub': 's0004', 'text': second['key'], 'rev': None})
    # skip (S) writes nothing; s0006 is not on the roster: typed free text
    post(client, token, '/scans/who', {'sub': 's0006', 'text': "Finley Dummond", 'rev': None})
    view = client.get('/scans/names').json()
    assert view['left'] == 3 and [r['sub'] for r in view['rows'][-3:]] == ['s0001', 's0004', 's0006']
    assert "Avery Testwell <90412873>" not in view['nobody'] and "Kit Absentia <90655318>" in view['nobody']
    # the confirmed name and ID are the submission's everywhere
    grid = client.get('/').text
    assert 'Devon Fakely' in grid and 'data-student="93681724"' in grid
    assert "93681724" in client.get('/s/s0004/parity').text


def test_roster_on_the_page_and_who_is_matched(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    check_and_accept(client, token, src)
    post(client, token, '/scans/roster', {'text': (ws.root / 'roster.csv').read_text()})
    html = client.get('/scans').text
    people = identity.people(ws)
    assert 'id="ro-rows"' in html and 'id="ro-filter"' in html
    # the roster goes to the page once, as data: the picker and the table filter it in the browser
    assert all(p['name'] in html and p['id'] in html for p in people)
    post(client, token, '/scans/who', {'sub': 's0005', 'text': people[4]['key']})
    post(client, token, '/scans/who', {'sub': 's0003', 'text': people[4]['key']})
    rows = {r['sub']: r for r in client.get('/scans/names').json()['rows']}
    # a person picked twice: both submissions carry the key, and are flagged
    assert rows['s0005']['who']['key'] == rows['s0003']['who']['key'] == people[4]['key']
    assert rows['s0005']['who']['twice'] and rows['s0003']['who']['twice']
    # what the page needs to show the first page without boxes: cover pages and print area
    assert rows['s0001']['cover'] == ws.layout('s0001').cover_pages and len(rows['s0001']['print_area']) == 4


def test_drawing_the_name_boxes_for_every_submission(site):
    """Identity fields' regions are exam data like any field's: '@identity/fields/<column>/region',
    for every submission or for one, on the Exam data page."""
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    check_and_accept(client, token, src)
    view = client.get('/scans/names').json()
    cols = {c['name']: c for c in view['columns']}
    assert list(cols) == ['name', 'id', 'email'] and cols['email']['value'] is None     # not placed: "+ email"
    assert cols['name']['path'] == '@identity/fields/name/region' and cols['name']['rev'] is None
    assert cols['name']['value'] == cols['name']['base'] == [1, 100, 200, 800, 280]
    wait(client, post(client, token, '/scans/names/read', {}).json()['job'])
    assert identity.unread(ws) == []
    before = {r['sub']: r['crops'] for r in view['rows']}
    # move the name box, for every submission; values read under the old box are read again
    path = '@identity/fields/name/region'
    assert client.post('/exam/set', json={'path': path, 'value': [1, 100, 190, 820, 290], 'rev': None}).status_code == 403
    r = post(client, token, '/exam/set', {'path': path, 'value': [1, 100, 190, 820, 290], 'rev': None})
    assert r.status_code == 200 and r.json()['overridden'] and r.json()['reread'] == ['@identity/name']
    rev = r.json()['rev']
    assert post(client, token, '/exam/set', {'path': path, 'value': [1, 100, 190, 820, 300], 'rev': None}).status_code == 409
    view = client.get('/scans/names').json()
    assert {c['name']: c['rev'] for c in view['columns']}['name'] == rev
    assert all(r['crops'][0]['box'] != before[r['sub']][0]['box'] for r in view['rows'])
    assert all(r['crops'][1]['box'] == before[r['sub']][1]['box'] for r in view['rows'])
    assert sorted(identity.unread(ws)) == sorted(before)                 # read again ...
    r = post(client, token, '/scans/names/read', {})
    assert r.json()['n'] == 6 and wait(client, r.json()['job'])['state'] == 'done'   # ... with the new box
    # one submission's own box: its crop alone moves, and it alone is read again
    own = {'path': path, 'value': [1, 100, 120, 800, 200], 'rev': None, 'scope': 'submission:s0003'}
    assert post(client, token, '/exam/set', own).status_code == 200
    rows = {r['sub']: r for r in client.get('/scans/names').json()['rows']}
    assert rows['s0003']['own']['name']['value'] == [1, 100, 120, 800, 200] and rows['s0002']['own'] == {}
    assert rows['s0003']['crops'][0]['box'] != rows['s0002']['crops'][0]['box']
    assert identity.unread(ws) == ['s0003']
    # on the Exam data page, with the package's value, as a line for the package, and the submission's own
    page = client.get('/exam').text
    assert 'data-path="@identity/fields/name/region"' in page and "@identity/fields/name/region: Region(1, " in page
    assert '/scans#sub=s0003' in page
    # checked like any region
    for bad in ({'path': path, 'value': [1, 500, 100, 400, 200]}, {'path': path, 'value': [9, 1, 1, 2, 2]},
                {'path': '@identity/fields/nick/region', 'value': [1, 1, 1, 2, 2]}, {'path': '@nothing/x', 'value': 1},
                {**own, 'value': None}):
        assert post(client, token, '/exam/set', {**bad, 'rev': None}).status_code == 400, bad
    # a column the package did not place, then the declared ID taken away (no region: not read)
    post(client, token, '/exam/set', {'path': '@identity/fields/email/region', 'value': [1, 10, 10, 300, 60], 'rev': None})
    assert [f.label for f in identity.placed(ws)] == ['name', 'id', 'email']
    post(client, token, '/exam/set', {'path': '@identity/fields/id/region', 'value': None, 'rev': None})
    assert [f.label for f in identity.placed(ws)] == ['name', 'email']
    for o in ws.store.all_overrides():
        assert post(client, token, '/exam/clear', {'path': o.path, 'rev': o.rev, 'scope': o.scope}).status_code == 200
    assert ws.exam.identity is ws.exam.base['@identity'] and ws.exam.overrides == {}
    assert 'data-action="names-read"' in client.get('/scans').text


def test_identity_values_set_and_verified_like_any_field(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    check_and_accept(client, token, src)
    post(client, token, '/scans/roster', {'text': (ws.root / 'roster.csv').read_text()})
    wait(client, post(client, token, '/scans/names/read', {}).json()['job'])
    row = {r['sub']: r for r in client.get('/scans/names').json()['rows']}['s0002']
    v = row['values']['id']
    assert v['text'] == "91786250" and v['settled'] is None
    r = post(client, token, '/s/s0002/@identity/save', {'values': {'@identity/id': {'text': "91736250", 'rev': v['rev']}}})
    assert r.status_code == 200
    row = {r['sub']: r for r in client.get('/scans/names').json()['rows']}['s0002']
    assert row['values']['id']['settled'] == 'person' and row['candidates'][0]['why'] == "ID exact, name exact"
    n = row['values']['name']
    assert post(client, token, '/s/s0002/@identity/save', {'verify': [{'key': '@identity/name', 'rev': n['rev']}]}).status_code == 200
    assert {r['sub']: r for r in client.get('/scans/names').json()['rows']}['s0002']['values']['name']['settled'] == 'person'


def test_the_group_is_not_graded_in_the_app(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    check_and_accept(client, token, src)
    wait(client, post(client, token, '/scans/names/read', {}).json()['job'])
    grid = client.get('/').text
    assert '@identity' not in grid
    assert '@identity' not in client.get('/runs').text
    assert all(q['id'] != '@identity' for q in client.get('/focus/values').json()['questions'])
    assert post(client, token, '/runs/plan', {'questions': ['@identity']}).status_code == 400
    first = next(iter(ws.exam.grading))
    assert 'data-action="prev-question"' not in client.get(f'/s/s0001/{first}').text


def test_no_boxes_left_means_nothing_to_read(site):
    client, token, ws, paths = site
    src = upload(client, token, paths[0]).json()['id']
    check_and_accept(client, token, src)
    for name in ('name', 'id'):
        post(client, token, '/exam/set', {'path': f'@identity/fields/{name}/region', 'value': None, 'rev': None})
    assert identity.placed(ws) == ()
    assert post(client, token, '/scans/names/read', {}).status_code == 400


def test_reading_names_needs_a_model(make_site):
    client, token, ws, paths = make_site(model=False)
    html = client.get('/scans').text
    assert 'data-model=""' in html and 'data-action="names-read" title="no model"' in html    # shown, dimmed
    assert post(client, token, '/scans/names/read', {}).status_code == 400


def test_serve_starts_a_new_workspace_on_the_scans_page(tmp_path, monkeypatch, capsys):
    """`serve` on a folder that does not exist yet makes the empty workspace (the README
    says to start from the Scans page) and says to open Scans."""
    import uvicorn
    from examinator import cli
    pages = {}

    def run(app, **kw):        # while the workspace is open
        client = TestClient(app, base_url=BASE)
        pages.update({path: client.get(path).status_code for path in ('/', '/scans')})
    monkeypatch.setattr(uvicorn, 'run', run)
    root = tmp_path / 'new' / 'ws'
    assert cli.main(['serve', str(TOY), '-w', str(root), '--port', '8599']) == 0
    assert (root / 'pdfs').is_dir()
    assert capsys.readouterr().out.startswith('http://127.0.0.1:8599/scans')
    assert pages == {'/': 200, '/scans': 200}
