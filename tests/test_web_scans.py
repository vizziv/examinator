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
    assert plan['sheet_o'][17] == '20' and plan['entries'][4]['own']
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
    r = client.get(f'/sheet/{src}/0.jpg?o=20&w=140')
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'
    from io import BytesIO
    from PIL import Image
    assert Image.open(BytesIO(r.content)).width == 140
    for bad in ('/sheet/{s}/99.jpg', '/sheet/{s}/0.jpg?o=40', '/sheet/{s}/0.jpg?w=17', '/sheet/nosuch/0.jpg'):
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
