"""The scans page: upload, step-through edits, check, accept, names. Synthetic stacks only."""
import re
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from examinator import load_exam, synth
from examinator.core import intake
from examinator.core.runner import Runner
from examinator.core.workspace import Workspace
from examinator.web.app import create_app

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'
BASE = 'http://127.0.0.1:8580'


@pytest.fixture
def site(tmp_path):
    exam = load_exam(TOY)
    root = tmp_path / 'ws'
    paths = synth.stacks(exam, root)
    ws = Workspace(root, exam, create=True)
    runner = Runner(exam, ws, model=synth.fake_model(root))
    client = TestClient(create_app(exam, ws, runner, author='tester'), base_url=BASE)
    token = re.search(r'<meta name="token" content="([^"]+)"', client.get('/scans').text).group(1)
    yield client, token, ws, paths
    runner.close()
    ws.close()


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


def test_step_through_and_accept(site):
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
    # check with the (scripted) model in the background
    r = post(client, token, f'/scans/{src}/propose', {'check': True, 'rev': plan['rev']})
    for _ in range(300):
        if client.get(f"/jobs/{r.json()['job']}").json()['state'] != 'running':
            break
        time.sleep(0.02)
    plan = client.get(f'/scans/{src}/plan').json()
    assert plan['checked'] and [e['start'] for e in plan['entries']] == [0, 4, 7, 12, 16, 20]
    assert plan['entries'][0]['confirmed'] and plan['readings']['4'] == 'p.1'
    plan = post(client, token, f'/scans/{src}/edit', {'op': 'turn', 'k': 4, 'rev': plan['rev']}).json()['plan']
    assert plan['sheet_o'][17] == '20' and plan['entries'][4]['own']
    r = post(client, token, f'/scans/{src}/accept', {'rev': plan['rev']})
    assert r.status_code == 200 and len(r.json()['report']['new']) == 6
    assert len(list(ws.pdfs.glob('*.pdf'))) == 6
    assert client.get('/').status_code == 200 and 's0005' in client.get('/').text
    # names
    post(client, token, '/scans/roster', {'csv': (ws.root / 'roster.csv').read_text()})
    label = intake.label(intake.people(ws)[4])
    assert post(client, token, '/scans/who', {'sub': 's0005', 'text': label}).json()['who']['name'] == "Synthetic Student 05"
    assert ws.store.submission('s0005').name == "Synthetic Student 05"
    assert post(client, token, '/scans/who', {'sub': 'nobody', 'text': label}).status_code == 400
    # changing a written submission that has work: refused, then forced
    ws.store.write('s0002', 'parity', 'parse', {'B': 'odd'}, origin='manual', author='t')
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
