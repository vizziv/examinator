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


def _left() -> dict[str, list[str]]:
    """The left-hand preset: action name -> combos."""
    js = _actions_js()
    start = js.index('const LEFT = {')
    body = js[start:js.index('};', start)]
    return {m[1]: re.findall(r"'([^']*)'", m[2]) for m in re.finditer(r"'?([\w-]+)'?\s*:\s*\[([^\]]*)\]", body)}


def _mirror():
    """The right-hand preset's mapping of one combo, as actions.js computes it."""
    js = _actions_js()
    table = dict(re.findall(r"(\w):\s*'(\w)'", re.search(r'const MIRROR = \{([^}]*)\}', js)[1]))

    def mirror(c: str) -> str:
        if c in table:
            return table[c]
        if c.startswith('Shift+') and c[6:] in table:
            return 'Shift+' + table[c[6:]]
        return c
    return mirror


def _registered() -> set[str]:
    """Action names the browser code registers: `register('name', ...)` (Actions' own or a
    page's wrapper) and object literals of specs (`name: {run ...}`) registered in a loop."""
    names = set()
    for f in STATIC.glob('*.js'):
        js = f.read_text()
        names |= set(re.findall(r"register\(\s*'([\w-]+)'", js))
        names |= set(re.findall(r"(?:^|[\s{,])'?([\w-]+)'?\s*:\s*\{\s*(?:available|run)\b", js, re.M))
    return names


def test_every_data_action_names_an_action():
    known = set(_catalog()) | _registered()
    used = {(f.name, name) for f in TEMPLATES.glob('*.html')
            for name in re.findall(r'data-action="([^"{}]+)"', f.read_text())}
    unknown = sorted(f"{f}: {name}" for f, name in used if name not in known)
    assert not unknown, "\n".join(unknown)


def test_every_preset_key_names_a_catalog_action():
    catalog = _catalog()
    assert catalog and _left()
    assert sorted(set(_left()) - set(catalog)) == []


def test_no_two_actions_of_a_group_share_a_single_key():
    catalog, left = _catalog(), _left()
    by_group: dict[tuple[str, str], list[str]] = {}
    for name, combos in left.items():
        for c in combos:
            if not c.startswith(('Mod+', 'Alt+')):
                by_group.setdefault((catalog.get(name, ''), c), []).append(name)
    shared = {k: v for k, v in by_group.items() if len(v) > 1}
    assert not shared, shared


def test_the_right_hand_preset_keeps_keys_apart():
    """Mirroring never sends two different left-hand keys of one group to the same key."""
    catalog, left, mirror = _catalog(), _left(), _mirror()
    groups: dict[str, set[str]] = {}
    for name, combos in left.items():
        groups.setdefault(catalog.get(name, ''), set()).update(combos)
    for group, combos in groups.items():
        images: dict[str, list[str]] = {}
        for c in combos:
            images.setdefault(mirror(c), []).append(c)
        clashes = {k: sorted(v) for k, v in images.items() if len(v) > 1}
        assert not clashes, (group, clashes)
