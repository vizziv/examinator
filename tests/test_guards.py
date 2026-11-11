"""Guards against overfitting and leaks, and the toy exam end to end."""
import re
from pathlib import Path

from examinator import load_exam, synth
from examinator.core.runner import Runner
from examinator.core.workflow import part_status
from examinator.core.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / 'exams' / 'toy_exam'


def test_library_contains_no_exam_identifiers():
    """The library knows nothing about any particular exam."""
    pattern = re.compile(r'\bq\d+\b|weibull|poisson|huge|shiny', re.I)
    hits = [f"{f.relative_to(ROOT)}:{n}: {line.strip()}"
            for f in (ROOT / 'src' / 'examinator').rglob('*')
            if f.suffix in ('.py', '.html', '.js', '.css')
            for n, line in enumerate(f.read_text().splitlines(), 1) if pattern.search(line)]
    assert not hits, "\n".join(hits)


def test_library_never_imports_an_exam():
    for f in (ROOT / 'src' / 'examinator').rglob('*.py'):
        assert 'toy_exam' not in f.read_text() and 'exam_03' not in f.read_text(), f


def test_toy_exam_selftest(tmp_path):
    assert synth.selftest(load_exam(TOY), tmp_path / 'ws') == []


def test_no_answer_key_in_any_prompt(tmp_path):
    exam = load_exam(TOY)
    root = synth.generate(exam, tmp_path / 'ws', synth.load_cases(exam))
    ws = Workspace(root, exam)
    ws.sync_roster()
    model = synth.fake_model(root)
    runner = Runner(exam, ws, model=model)
    try:
        for sub in ws.store.submissions():
            for qid, q in exam.questions.items():
                runner.run_from(sub.id, qid, q.stages[0].name)
    finally:
        runner.close()
        ws.close()
    assert model.calls
    prompts = "\n".join(c.prompt for c in model.calls)
    for secret in ('10*x', '5*x**2', '6*x', '2*x**2', 'option 1', 'even'):
        assert secret not in prompts, secret


EXAM = '''
import time
from examinator import Bool, Exam, Field, Stage, Evidence
from examinator.std import stages

def slow(ctx):
    time.sleep(30)

def boom(ctx):
    raise RuntimeError("kaboom")

def fine(ctx):
    print("stray output must not break the worker protocol")
    return Evidence({'fields': {'x': {'outcome': 'correct'}}})

exam = Exam('iso', pages=1)
for name, fn in [('slow', slow), ('boom', boom), ('fine', fine)]:
    exam.question(name, pages=[1], result=Bool(), fields=[Field('x')],
                  stages=[Stage('parse', lambda ctx: {}, editable=True),
                          stages.check(fn, timeout=1.0), stages.grade(lambda ev: ev.outcome('x'))])
'''


def test_isolated_checks_time_out_and_crash_as_undecided(tmp_path):
    pkg = tmp_path / 'iso_exam'
    pkg.mkdir()
    (pkg / 'exam.py').write_text(EXAM)
    exam = load_exam(pkg)
    (tmp_path / 'ws' / 'pdfs').mkdir(parents=True)
    ws = Workspace(tmp_path / 'ws', exam)
    runner = Runner(exam, ws)
    try:
        for qid in exam.questions:
            ws.store.write('s', qid, 'parse', {}, origin='manual', author='t', verified=True)
            assert runner.run_stage('s', qid, 'evidence').ok

        def st(qid):
            return part_status(exam.questions[qid], '', ws.store.for_question('s', qid))

        assert (st('slow').state, st('slow').value) == ('needs_review', None)
        assert 'timed out' in st('slow').reason
        assert (st('boom').state, st('boom').value) == ('needs_review', None)
        assert 'kaboom' in str(ws.store.get('s', 'boom', 'evidence').body)     # traceback kept for graders
        assert (st('fine').state, st('fine').value) == ('graded', True)
    finally:
        runner.close()
        ws.close()
