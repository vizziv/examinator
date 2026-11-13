"""Guards against overfitting and leaks, and the toy exam end to end."""
import re
from pathlib import Path

from examinator import load_exam, synth
from examinator.core.runner import Runner
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


def test_nothing_mentions_cover_pages():
    """Printed page p is a submission's PDF page p: there are no cover sheets to skip."""
    pattern = re.compile(r'cover[ _]?(page|sheet)', re.I)
    files = [*(ROOT / 'src').rglob('*'), *(ROOT / 'exams').rglob('*.py'), *(ROOT / 'agent_docs').glob('*.md'),
             ROOT / 'README.md']
    hits = [f"{f.relative_to(ROOT)}:{n}: {line.strip()}" for f in files
            if f.suffix in ('.py', '.html', '.js', '.css', '.md') and f.name != 'LESSONS.md'
            for n, line in enumerate(f.read_text().splitlines(), 1) if pattern.search(line)]
    assert not hits, "\n".join(hits)


def test_no_dead_names():
    """Names an earlier rename or removal retired (`verified`, the Read page at /read, `multiline`)."""
    pattern = re.compile(r"\bis_settled\b|\bSETTLERS\b|\.settled\b|'settled'|scan_window|/runs\b|needs_review|"
                         r"\.field\.code\b|\bf\.code\b")
    files = [f for f in (*(ROOT / 'src').rglob('*'), *(ROOT / 'exams').rglob('*.py'))
             if f.suffix in ('.py', '.html', '.js', '.css')]
    hits = [f"{f.relative_to(ROOT)}:{n}: {line.strip()}" for f in files
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
    ws.sync()
    model = synth.fake_model(root)
    runner = Runner(ws, model=model)
    try:
        for sub in ws.store.submissions():
            for qid in exam.questions:
                runner.read(sub.id, qid)
            runner.refresh(sub.id, questions=list(exam.questions))
    finally:
        runner.close()
        ws.close()
    assert model.calls
    prompts = "\n".join(c.prompt for c in model.calls)
    for secret in ('10*x', '5*x**2', '6*x', '2*x**2', 'option 1', 'even'):
        assert secret not in prompts, secret


EXAM = '''
import time
from examinator import Check, Exam, Field
from examinator.std import math

def slow(ctx):
    time.sleep(30)

def boom(ctx):
    raise RuntimeError("kaboom")

def fine(ctx):
    print("stray output must not break the worker protocol")
    return math.keyed({'x': '1'})(ctx)

exam = Exam('iso', pages=1)
for name, fn in [('slow', slow), ('boom', boom), ('fine', fine)]:
    exam.question(name, pages=[1], fields=[Field('x')], check=Check(fn, timeout=1.0))
'''


def test_isolated_checks_time_out_and_crash_as_undecided(tmp_path):
    pkg = tmp_path / 'iso_exam'
    pkg.mkdir()
    (pkg / 'exam.py').write_text(EXAM)
    exam = load_exam(pkg)
    (tmp_path / 'ws' / 'pdfs').mkdir(parents=True)
    ws = Workspace(tmp_path / 'ws', exam)
    runner = Runner(ws)
    try:
        for qid in exam.questions:
            runner.set('s', qid, 'x', "1", author='t')
            assert runner.compute('s', qid, 'check').ok

        def st(qid):
            q = exam.questions[qid]
            return runner.sheet('s').status(exam, q, q.check('check'))

        assert (st('slow').state, st('slow').value) == ('to_decide', None)
        assert 'timed out' in st('slow').reason
        assert (st('boom').state, st('boom').value) == ('to_decide', None)
        assert 'kaboom' in str(ws.store.get('s', 'boom/check', 'result').body)     # traceback kept for graders
        assert (st('fine').state, st('fine').value) == ('graded', True)
        assert set(ws.store.get('s', 'fine/check', 'result').body['reads']) == {'fine/x'}   # recorded in the worker
    finally:
        runner.close()
        ws.close()


def test_no_browser_function_is_declared_twice():
    """In JavaScript a later `function f` silently replaces an earlier one in the same scope.
    Nothing runs the browser code in tests, so this catches it by reading: a name declared
    twice at the same depth in one file."""
    declared = re.compile(r'^(\s*)(?:async\s+)?function\s+(\w+)\s*\(', re.M)
    for f in (ROOT / 'src' / 'examinator' / 'web' / 'static').glob('*.js'):
        seen = [m.groups() for m in declared.finditer(f.read_text())]
        twice = sorted({name for depth, name in seen if seen.count((depth, name)) > 1})
        assert not twice, f"{f.name}: {', '.join(twice)}"


# --- the browser's actions and keys, checked by reading (nothing runs the browser code here) ---

STATIC = ROOT / 'src' / 'examinator' / 'web' / 'static'
TEMPLATES = ROOT / 'src' / 'examinator' / 'web' / 'templates'


def _actions_js() -> str:
    return (STATIC / 'actions.js').read_text()


def _catalog() -> dict[str, str]:
    """CATALOG in actions.js: action name -> its group ('' for the default group)."""
    js = _actions_js()
    body = js[js.index('const CATALOG = ['):js.index('];', js.index('const CATALOG = ['))]
    return {m[1]: m[2] or '' for m in re.finditer(r"\['([\w-]+)',\s*\"[^\"]*\"(?:,\s*'(\w+)')?\]", body)}


def _keys() -> dict[str, list[str]]:
    """The default key map: action name -> combos."""
    js = _actions_js()
    start = js.index('const KEYS = {')
    body = js[start:js.index('};', start)]
    return {m[1]: re.findall(r"'([^']*)'", m[2]) for m in re.finditer(r"'?([\w-]+)'?\s*:\s*\[([^\]]*)\]", body)}


def _registered() -> set[str]:
    """Action names the browser code registers: `register('name', ...)` (Actions' own or a
    page's wrapper) and object literals of specs (`name: {run ...}`) registered in a loop."""
    names = set()
    for f in STATIC.glob('*.js'):
        js = f.read_text()
        names |= set(re.findall(r"register\(\s*'([\w-]+)'", js))
        names |= set(re.findall(r"(?:^|[\s{,])'?([\w-]+)'?\s*:\s*(?:\{\s*(?:available|run)\b|follow\()", js, re.M))
    return names


def test_every_data_action_names_an_action():
    known = set(_catalog()) | _registered()
    used = {(f.name, name) for f in TEMPLATES.glob('*.html')
            for name in re.findall(r'data-action="([^"{}]+)"', f.read_text())}
    unknown = sorted(f"{f}: {name}" for f, name in used if name not in known)
    assert not unknown, "\n".join(unknown)


def test_every_default_key_names_a_catalog_action():
    catalog = _catalog()
    assert catalog and _keys()
    assert sorted(set(_keys()) - set(catalog)) == []
    assert 'MIRROR' not in _actions_js() and 'preset' not in _actions_js()      # one key map


def test_every_catalog_action_is_registered_somewhere():
    assert sorted(set(_catalog()) - _registered()) == []


def test_no_two_actions_of_a_group_share_a_single_key():
    catalog, keys = _catalog(), _keys()
    by_group: dict[tuple[str, str], list[str]] = {}
    for name, combos in keys.items():
        for c in combos:
            if not c.startswith(('Mod+', 'Alt+')):
                by_group.setdefault((catalog.get(name, ''), c), []).append(name)
    shared = {k: v for k, v in by_group.items() if len(v) > 1}
    assert not shared, shared


def test_a_batch_reads_everything_before_any_check(env):
    """A check that reads the printing (another question) finds it."""
    from examinator.core import batch
    exam, ws, runner = env
    plan = batch.plan(ws, batch.Selection(('s1',), tuple(exam.questions)), model_available=True)
    run = batch.BatchRun(runner, plan, parallel=1)
    outs = run.run()
    steps = [o.step for o in outs]
    assert steps.index('x') > max(i for i, s in enumerate(steps) if s == 'read')
    assert all(o.ok for o in outs)
