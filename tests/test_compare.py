"""`examinator compare`: several models on the same answers, nothing written to the store."""
import json
from pathlib import Path

from examinator import cli, compare, synth
from examinator.core.defs import load_exam
from examinator.core.workspace import Workspace
from examinator.model.client import Client

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'


class Echo:
    """A stand-in client: records its calls and answers with a fixed word."""

    def __init__(self, word):
        self.word, self.calls = word, []

    def complete(self, prompt, *, images=(), **kw):
        self.calls.append((prompt, len(images)))
        return self.word


def test_compare_runs_both_modes_and_leaves_the_store_alone(tmp_path):
    exam = load_exam(TOY)
    root = synth.generate(exam, tmp_path / 'ws', synth.load_cases(exam))
    ws = Workspace(root, exam)
    ws.sync()
    subs = [s.id for s in ws.store.submissions()][:2]
    qid = next(q.id for q in exam.questions.values() if any(f.region for f in q.fields))
    boxes = sum(1 for f in exam.questions[qid].fields if f.region and f.read is None)    # answer boxes, not printed fields
    before = ws.store.version()

    stage, ocr = compare.Entrant('a', ''), compare.Entrant('b', '', 'ocr', "Read:")
    clients = {stage: Echo("whole answer"), ocr: Echo("box")}
    lines = []
    results = compare.run(exam, ws, [stage, ocr], submissions=subs, questions=[qid],
                          make_client=clients.__getitem__, progress=lines.append)

    assert ws.store.version() == before                      # nothing written
    assert [r.model for r in results] == ['a', 'a', 'b (ocr)', 'b (ocr)'] and len(lines) == 4
    assert results[0].text == "whole answer" and results[0].calls == 1
    assert results[2].calls == boxes and results[2].text.count("box") == boxes
    assert all(call == ("Read:", 1) for call in clients[ocr].calls)        # one box, the bare prompt

    path = compare.write(exam, ws, results, tmp_path / 'out')
    page = path.read_text()
    assert "whole answer" in page and 'data:image/jpeg' in page and '<script' not in page
    data = json.loads((tmp_path / 'out' / 'results.json').read_text())
    assert [row['model'] for row in data['summary']] == ['a', 'b (ocr)']
    ws.close()


def test_compare_reports_failures_without_model_text(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(synth.generate(exam, tmp_path / 'ws', synth.load_cases(exam)), exam)
    ws.sync()

    class Broken:
        def complete(self, prompt, **kw):
            raise compare.ModelError("model server returned HTTP 500")

    sub = ws.store.submissions()[0].id
    r, = compare.run(exam, ws, [compare.Entrant('x', '')], submissions=[sub],
                     questions=[next(iter(exam.questions))], make_client=lambda e: Broken())
    assert r.text is None and r.error == "ModelError: model server returned HTTP 500"
    assert compare.summary([r], {})[0]['failed'] == 1

    class Odd:
        def complete(self, prompt, **kw):
            raise RuntimeError("could quote an answer")

    a, b = compare.run(exam, ws, [compare.Entrant('y', '')], submissions=[sub, sub],
                       questions=[next(iter(exam.questions))], make_client=lambda e: Odd())
    assert (a.text, a.error) == (None, "RuntimeError") and b.error == "RuntimeError"   # the run goes on
    ws.close()


def test_entrants_and_the_ocr_client():
    e = compare.parse_entrant('m@http://localhost:9000/v1', 'http://localhost:8241/v1', 'ocr')
    assert (e.name, e.url, e.label) == ('m', 'http://localhost:9000/v1', 'm (ocr)')
    assert compare.parse_entrant('m', 'http://localhost:8241/v1', 'stage').url == 'http://localhost:8241/v1'
    assert compare.parse_entrant('m@http://localhost:9000/v1::', '', 'ocr').prompt == ""
    assert compare.parse_entrant('m::Formula Recognition:', 'u', 'ocr').prompt == "Formula Recognition:"
    body = compare.client_for(e).body("OCR:")
    assert [m['role'] for m in body['messages']] == ['user'] and 'reasoning_effort' not in body
    assert [m['role'] for m in Client('http://localhost:8241/v1', 'm').body("p")['messages']] == ['system', 'user']


def test_cli_compare_with_the_scripted_model(tmp_path, capsys):
    exam = load_exam(TOY)
    root = synth.generate(exam, tmp_path / 'ws', synth.load_cases(exam))
    assert cli.main(['compare', str(TOY), '-w', str(root), '--fake', '--limit', '1', '--no-images']) == 0
    out = capsys.readouterr().out
    report = Path(out.strip().splitlines()[-1].removeprefix("report: "))
    assert report.is_file() and root / 'exports' in report.parents
    assert 'data:image' not in report.read_text()


def test_cli_closes_the_workspace(tmp_path, monkeypatch, capsys):
    exam = load_exam(TOY)
    root = synth.generate(exam, tmp_path / 'ws', synth.load_cases(exam))
    closed = []

    class Tracked(Workspace):
        def close(self):
            closed.append(1)
            super().close()
    monkeypatch.setattr(cli, 'Workspace', Tracked)
    assert cli.main(['status', str(TOY), '-w', str(root), '--fake']) == 0
    assert closed == [1]
