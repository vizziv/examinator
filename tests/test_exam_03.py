"""Exam 3 as an exam package: the library's integration test (EXAM3.md section 7).

Everything here is synthetic. Tests that read the instructor's blank PDFs skip
unless `EXAM3_BLANKS` names their directory. Tests marked `slow` take more than
about 20 s (`-m "not slow"` deselects them).
"""
import importlib
import os
import re
from pathlib import Path

import pytest

from examinator import Context, load_exam, synth
from examinator.core import batch
from examinator.core.evidence import body_of
from examinator.core.runner import Runner
from examinator.core.workflow import part_status
from examinator.core.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
EXAM = ROOT / 'exams' / 'exam_03'
BLANKS = os.environ.get('EXAM3_BLANKS')
needs_blanks = pytest.mark.skipif(not BLANKS, reason="EXAM3_BLANKS is not set")


@pytest.fixture(scope='module')
def exam():
    return load_exam(EXAM)


@pytest.fixture(scope='module')
def pkg(exam):
    """The package's modules, by name."""
    return lambda name: importlib.import_module(f'{EXAM.name}.{name}')


def outcomes(exam, qid, parse, variant=None, **params):
    """Run one question's check directly on a parse body: `{label: outcome}`."""
    q = exam.questions[qid]
    body = body_of(q.stage('evidence').fn(Context(
        submission='synthetic', question=q, stage='evidence', variant=variant,
        inputs={'parse': parse}, params=params)))
    return {label: f['outcome'] for label, f in body['data']['fields'].items()}, body


# --- acceptance checks 1-5: the fixtures end to end -----------------------------

def test_selftest(exam, tmp_path):
    assert synth.selftest(exam, tmp_path / 'ws') == []


def test_definition(exam):
    assert list(exam.questions) == [f'q{i}' for i in range(1, 10)]
    assert exam.pages == 22 and exam.cover_pages == 1 and exam.scan_window == (60, 55, 920, 825)
    for qid in ('q2', 'q7'):
        q = exam.questions[qid]
        assert list(q.result) == ['check', 'shiny'] and not q.variants
        assert q.result['check'].counts and not q.result['shiny'].counts
    groups = {}
    for q in exam.questions.values():
        if q.variants:
            assert q.variants == ('original', 'alternate') and len(q.cues) == 2
            groups.setdefault(tuple(c.name for c in q.cues), []).append(q.id)
    assert sorted(groups.values()) == [['q1', 'q5', 'q9'], ['q3', 'q6'], ['q4', 'q8']]


@needs_blanks
def test_versions_match_the_printed_blanks(exam, pkg):
    """The alt index -> variants table agrees with the cues printed in each blank."""
    import pypdfium2
    versions = pkg('versions')
    printed = {      # cue -> (printed page, pattern, text that marks the original printing)
        'q5_confidence': (10, r'at least (\d+)%', '95'),
        'q9_observations': (18, r'observing ([\d, and]+)\.', '14, 12, and 15'),
        'q6_option_c': (13, r'C\. They (use two different|do the exact same)', 'use two different'),
        'q3_first_height': (7, r'3\.1 .*?the (\w+) height', 'base'),
        'q8_first_n': (17, r'8\.1 .*?𝑛=(\d)', '3'),
        'q4_negative_branch': (8, r'(\S+(?: \+ 1)?) if − 1 ≤ 𝑟 < 0', '𝑟 + 1'),
    }
    for alt, variants in versions.VERSIONS.items():
        doc = pypdfium2.PdfDocument(str(versions.blank(alt)))
        try:
            assert len(doc) == exam.pages
            for q in exam.questions.values():
                for cue in q.cues:
                    page, pattern, original = printed[cue.name]
                    assert cue.region.page == page
                    text = doc[page - 1].get_textpage().get_text_range()
                    found = re.search(pattern, text, re.S)
                    assert found, (alt, cue.name)
                    want = 'original' if found[1] == original else 'alternate'
                    assert variants[q.id] == want, (alt, q.id, cue.name)
        finally:
            doc.close()


# --- acceptance check 5: the variant selects the key ----------------------------

def test_alternate_variant_selects_the_alternate_key(exam, pkg):
    answers = pkg('fixtures').ANSWERS
    for qid in ('q1', 'q3', 'q4', 'q5', 'q6', 'q8', 'q9'):
        for printed in ('original', 'alternate'):
            for written in ('original', 'alternate'):
                got, _ = outcomes(exam, qid, answers[written][qid], variant=printed)
                assert all(o == 'correct' for o in got.values()) == (printed == written), (qid, printed, written)
        assert exam.questions[qid].stage('evidence').uses_variant


# --- rectangles ---------------------------------------------------------------------

def test_rectangle_key(pkg):
    r = pkg('rectangles')
    key = r.key('0', 'b', 'a', '1')
    for text in ('[0,b] x [a,1]', '[0, b] × [a, 1]', '[0,b]*[a,1]', r'[0, b] \times [a, 1]',
                 '[0, 2b/2] x [a, (1)]', '[a-a, b] X [a, 1]'):
        assert key(text) == ('correct', ""), text
    assert key('[a,1] x [0,b]')[0] == 'incorrect'           # the two intervals are ordered
    assert key('[0,a] x [b,1]') == ('incorrect', "bound 2, 3")
    assert key('[0,b] x [a,2]') == ('incorrect', "bound 4")
    for text in ('0 <= u <= b and a <= v <= 1', '[0,b]', '[0,b] x [a,1] x [0,1]', '[0,b,1] x [a,1]'):
        assert key(text) == (None, "not a product of two intervals"), text
    assert key('[0, b/(a-a+1)] x [a, 1]')[0] == 'correct'
    assert key('[0, b] x [a, a/(1-a)]') == (None, "denominator may be zero")
    assert key.label == "[0, b] x [a, 1]" and r.scalar('2*b*(1-a)').label == '2*b*(1-a)'
    assert r.split('[0, max(a,b)] x [(1, 2), c]') == ['0', ' max(a,b)', '(1, 2)', ' c']


def test_question_1_scalars(exam):
    def q1(**changed):
        answers = {'1.1': '[0,b] x [a,1]', '1.2': '2b(1-a)', '1.3': 'b - ab', '1.4': '1/2', **changed}
        return outcomes(exam, 'q1', answers, variant='original')[0]

    assert set(q1().values()) == {'correct'}
    assert q1(**{'1.3': 'a(1-b)'})['1.3'] == 'incorrect'
    assert q1(**{'1.3': 'b(1-a)^2/(1-a)'})['1.3'] == 'correct'       # cancels symbolically
    assert q1(**{'1.2': '2b/(1-a)'})['1.2'] is None                    # 1 - a is not provably nonzero
    assert q1(**{'1.2': '2b(1-a)/a'})['1.2'] == 'incorrect'            # a > 0: a real mismatch
    assert q1(**{'1.2': 'sqrt(b)'})['1.2'] is None                     # no functions in this question
    assert q1(**{'1.4': None})['1.4'] == 'blank'
    assert q1(**{'1.1': '[0,b] x [a,1] [REVIEW: second bound?]'})['1.1'] is None


# --- the huge_constant rewrite --------------------------------------------------------

LOOP = """\
def weibullish():
    while True:
        t = weibull(2, 4)
        if t >= 1 and {accept}:
            return t
"""


def test_rewrite_single_envelope_constant(pkg):
    rewrite = pkg('huge_constants').rewrite
    want = LOOP.format(accept='coin(t**2 / huge_constant)')
    for literal in ('100', '1000', '10**1000', '1e6', '10 ** 3 ** 2'):
        assert rewrite(LOOP.format(accept=f'coin(t**2 / {literal})')) == want, literal
    assert rewrite(want) == want                                          # idempotent
    for small in ('5', '99', '10**1', 'k'):
        source = LOOP.format(accept=f'coin(t**2 / {small})')
        assert rewrite(source) == source, small
    # through a variable, and as a comparison with a uniform
    source = LOOP.format(accept='unif() < t**2 / M').replace('    while', '    M = 10**1000\n    while')
    assert 'M = huge_constant\n' in rewrite(source)
    assert rewrite(LOOP.format(accept='1000 * unif() <= t**2')) == LOOP.format(accept='huge_constant * unif() <= t**2')
    # comments and layout survive
    source = LOOP.format(accept='coin(t**2 / 10**1000)') + "# é: [note] kept\n"
    assert rewrite(source) == want + "# é: [note] kept\n"
    assert rewrite("def f(:\n") == "def f(:\n"                            # not valid syntax: untouched


def test_rewrite_reciprocal_used_consistently_in_two_branches(pkg):
    rewrite = pkg('huge_constants').rewrite
    source = """\
def weibullish():
    while True:
        t = weibull(2, 4)
        if t >= 2:
            if coin(0.001 * t**2):
                return t
        elif t >= 1:
            if unif() < t**2 * 10**-3:
                return t
"""
    out = rewrite(source)
    assert 'coin((1 / huge_constant) * t**2)' in out and 'unif() < t**2 * (1 / huge_constant)' in out
    assert '0.001' not in out and '10**-3' not in out


def test_rewrite_leaves_ambiguous_scales_and_parameters_alone(pkg):
    rewrite = pkg('huge_constants').rewrite
    two = """\
def weibullish():
    while True:
        t = weibull(2, 4)
        if t >= 2 and coin(t**2 / 1000):
            return t
        if 1 <= t < 2 and coin(t**2 / 10**1000):
            return t
"""
    assert rewrite(two) == two                    # two different huge constants: for a person
    parameters = """\
def weibullish():
    while True:
        t = weibull(2, 400) + unif(0, 1000)
        if t >= 100 and coin(exp(-400 * t**2) * t / 5) and t < 10**6:
            return 1000 * t
"""
    assert rewrite(parameters) == parameters      # large numbers that are not envelope scales


# --- question 2: more synthetic samplers ---------------------------------------------

def q2(exam, pkg, weibullish, weibull=None, **params):
    f = pkg('fixtures')
    return outcomes(exam, 'q2', (f.WEIBULL if weibull is None else weibull) + "\n" + weibullish, **params)


def test_q2_recorded_results(exam, pkg):
    f = pkg('fixtures')
    got, body = outcomes(exam, 'q2', f.Q2)
    assert got == {'2.1': 'correct', '2.2': 'correct'}
    tests = next(b for b in body['blocks'] if b.get('summary') == "2.2 tests")['blocks'][0]['rows']
    assert [(name, result) for name, result, _ in tests] == [
        ('huge_constant = 1', 'incorrect'), ('huge_constant = 10', 'correct')]
    assert 'not in [0, 1]' in tests[0][2]          # 1 is rejected as an invalid acceptance probability
    assert body['blocks'][0]['type'] == 'plot'     # plots first
    assert all(b['type'] != 'code' for b in body['blocks'])       # dumps and source only inside details

    rewritten = pkg('huge_constants').rewrite(f.Q2_LITERAL)
    assert rewritten == f.Q2
    assert outcomes(exam, 'q2', f.Q2_WRONG_WEIBULL)[0] == {'2.1': 'incorrect', '2.2': 'correct'}
    assert outcomes(exam, 'q2', f.Q2_ONLY_WEIBULL)[0] == {'2.1': 'correct', '2.2': 'blank'}
    assert outcomes(exam, 'q2', "")[0] == {'2.1': 'blank', '2.2': 'blank'}


def test_q2_undecided_versus_incorrect(exam, pkg):
    # No cutoff at 1, idealized: a finite substitute proves nothing, so undecided.
    no_cutoff = LOOP.format(accept='coin(t**2 / huge_constant)').replace('t >= 1 and ', '')
    assert q2(exam, pkg, no_cutoff)[0]['2.2'] is None
    # The same mistake without huge_constant is a demonstrated mismatch.
    assert q2(exam, pkg, LOOP.format(accept='coin(t**2 / 20)').replace('t >= 1 and ', ''))[0]['2.2'] == 'incorrect'
    # A literal run-time failure in code without huge_constant is incorrect...
    assert q2(exam, pkg, LOOP.format(accept='coin(t**2)'))[0]['2.2'] == 'incorrect'
    # ...and so is an unknown name; an exhausted budget and unsupported syntax are undecided.
    assert q2(exam, pkg, "def weibullish():\n    while True:\n        t = 1\n")[0]['2.2'] is None
    assert q2(exam, pkg, "def weibullish():\n    return 1 + helper()\n")[0]['2.2'] == 'incorrect'
    assert q2(exam, pkg, "import numpy\n")[0] == {'2.1': None, '2.2': None}
    # die(n) is part of the language: this runs, and is a demonstrated mismatch.
    assert q2(exam, pkg, "def weibullish():\n    return die(6)\n")[0]['2.2'] == 'incorrect'


def test_q2_reference_weibull_and_efficient_sampler(exam, pkg):
    # T given T >= 1 by memorylessness of T**2, then the envelope: correct, even with a wrong 2.1.
    direct = """\
def weibullish():
    while True:
        t = sqrt(1 + weibull(1, 4))
        if coin(t**2 / huge_constant):
            return t
"""
    wrong = "def weibull(a, b):\n    return unif()\n"
    assert q2(exam, pkg, direct, weibull=wrong)[0] == {'2.1': 'incorrect', '2.2': 'correct'}


def test_q2_custom_huge_constant(exam, pkg):
    f = pkg('fixtures')
    got, body = outcomes(exam, 'q2', f.Q2, huge_constant=20)
    assert got['2.2'] == 'correct' and body['data']['fields']['2.2']['note'] == 'huge_constant = 20'
    assert outcomes(exam, 'q2', f.Q2, huge_constant='12')[0]['2.2'] == 'correct'      # typed by a grader
    for bad in (0, -3, 1e7, 'many'):
        got, body = outcomes(exam, 'q2', f.Q2, huge_constant=bad)
        assert got == {'2.1': 'correct', '2.2': None}, bad
        assert 'at most 1e+06' in body['data']['fields']['2.2']['note']


@pytest.mark.slow
def test_q2_time_budget_is_undecided(exam, pkg):
    """A substitute too large for the one-sample-at-a-time interpreter: a budget runs out
    (up to 20 s), and the part is undecided."""
    got, body = outcomes(exam, 'q2', pkg('fixtures').Q2, huge_constant=1e6)
    assert got == {'2.1': 'correct', '2.2': None}
    (_, result, note), = next(b for b in body['blocks'] if b.get('summary') == "2.2 tests")['blocks'][0]['rows']
    assert result == '?' and ('time limit' in note or 'steps' in note)


# --- question 7: more synthetic samplers ---------------------------------------------

def q7(exam, pkg, process):
    return outcomes(exam, 'q7', pkg('fixtures').POISSON_DIST + "\n" + process)[0]


def test_q7_recorded_results(exam, pkg):
    f = pkg('fixtures')
    got, body = outcomes(exam, 'q7', f.Q7)
    assert got == {'7.1': 'correct', '7.2': 'correct'}
    kinds = [b['type'] for b in body['blocks']]
    assert kinds[0] == 'plot' and 'plot' not in kinds[kinds.index('table'):]      # plots first
    titles = [b['title'] for b in body['blocks'] if b['type'] == 'plot']
    assert titles[:4] == ["number of events", "event locations", "number of events", "event locations"]
    assert outcomes(exam, 'q7', f.Q7_OFF_BY_ONE)[0] == {'7.1': 'correct', '7.2': 'incorrect'}
    assert outcomes(exam, 'q7', f.Q7_ONLY_PROCESS)[0] == {'7.1': 'correct', '7.2': 'blank'}
    assert outcomes(exam, 'q7', f.POISSON_DIST)[0] == {'7.1': 'blank', '7.2': 'correct'}


CORRECT_PROCESSES = {
    'direct, two halves': """\
def poisson_process():
    events = set()
    for i in range(poisson_dist(12.5)):
        events.add(5 * sqrt(unif()))
    for i in range(poisson_dist(12.5)):
        events.add(-5 * sqrt(unif()))
    return events
""",
    'thinning a rate-5 process': """\
def poisson_process():
    points = [unif(-5, 5) for i in range(poisson_dist(50))]
    return [t for t in points if coin(abs(t) / 5)]
""",
    'concatenation': """\
def half():
    return [5 * sqrt(unif()) for i in range(poisson_dist(25 / 2))]

def poisson_process():
    return half() + [-t for t in half()]
""",
}

WRONG_PROCESSES = {
    'wrong count mean': """\
def poisson_process():
    return [(2 * coin(0.5) - 1) * 5 * sqrt(unif()) for i in range(poisson_dist(20))]
""",
    'uniform locations': """\
def poisson_process():
    return [unif(-5, 5) for i in range(poisson_dist(25))]
""",
    'dependent locations': """\
def poisson_process():
    side = 1 if coin(0.5) else -1
    return [side * 5 * sqrt(unif()) for i in range(poisson_dist(25))]
""",
    'locations depend on the count': """\
def poisson_process():
    n = poisson_dist(25)
    if n > 25:
        return [5 * sqrt(unif()) for i in range(n)]
    return [-5 * sqrt(unif()) for i in range(n)]
""",
    'repeated events': """\
def poisson_process():
    t = (2 * coin(0.5) - 1) * 5 * sqrt(unif())
    return [t for i in range(poisson_dist(25))]
""",
    'outside the interval': """\
def poisson_process():
    return [10 * sqrt(unif()) - 5 for i in range(poisson_dist(25))]
""",
    'not a collection': """\
def poisson_process():
    return poisson_dist(25)
""",
}


@pytest.mark.parametrize('name', CORRECT_PROCESSES)
def test_q7_correct_processes(exam, pkg, name):
    assert q7(exam, pkg, CORRECT_PROCESSES[name]) == {'7.1': 'correct', '7.2': 'correct'}


@pytest.mark.parametrize('name', WRONG_PROCESSES)
def test_q7_wrong_processes(exam, pkg, name):
    assert q7(exam, pkg, WRONG_PROCESSES[name]) == {'7.1': 'incorrect', '7.2': 'correct'}


def test_q7_uses_a_trusted_poisson_dist(exam, pkg):
    """A wrong, a looping and a missing 7.2 leave 7.1 alone."""
    f = pkg('fixtures')
    looping = "def poisson_dist(m):\n    while True:\n        m += 1\n"
    assert outcomes(exam, 'q7', looping + "\n" + f.POISSON_PROCESS)[0] == {'7.1': 'correct', '7.2': None}
    failure, body = outcomes(exam, 'q7', f.Q7.replace('events.append(sqrt(50*u - 25))', 'events.append(sqrt(25 - 50*u))'))
    assert failure == {'7.1': 'incorrect', '7.2': 'correct'}
    assert 'sqrt' in body['data']['fields']['7.1']['note']
    assert outcomes(exam, 'q7', "def poisson_process():\n    return {1: 2}\n")[0] == {'7.1': None, '7.2': None}


# --- acceptance check 6: workflow rules on this exam ----------------------------------

@pytest.fixture
def env(exam, pkg, tmp_path):
    """(workspace, runner) with one synthetic submission of the original printing."""
    f = pkg('fixtures')
    case = synth.Case('synthetic', variants=pkg('versions').VERSIONS[0], answers=f.canonical(0))
    root = synth.generate(exam, tmp_path / 'ws', [case])
    ws = Workspace(root, exam)
    ws.sync_roster()
    runner = Runner(exam, ws, model=synth.fake_model(root))
    yield ws, runner
    runner.close()
    ws.close()


S = 'synthetic'


def test_workflow_rules(exam, pkg, env):
    ws, runner = env
    f = pkg('fixtures')

    def status(qid, part=''):
        return part_status(exam.questions[qid], part, ws.store.for_question(S, qid))

    def verify(qid, kind='parse'):
        runner.verify(S, qid, kind, author='ta', expect_rev=ws.store.get(S, qid, kind).rev)

    for qid in ('q9', 'q2'):
        assert all(o.ok for o in runner.run_from(S, qid, exam.questions[qid].stages[0].name))

    # Unverified results do not count.
    st = status('q9')
    assert (st.state, st.value, st.counts, st.waiting) == ('unverified', True, False, ('parse',))
    assert status('q2', 'check').state == status('q2', 'shiny').state == 'unverified'
    verify('q9')
    verify('q2')
    assert (status('q9').state, status('q9').value, status('q9').counts) == ('graded', True, True)
    assert (status('q2', 'check').value, status('q2', 'shiny').value) == (True, True)

    # Editing a verified parse outdates its grade, until the check runs again.
    runner.save(S, 'q9', 'parse', {'9.a': '12', '9.b': '17'}, author='ta', verify=True)
    assert (status('q9').state, status('q9').counts) == ('outdated', False)
    runner.recheck(S, 'q9')
    assert (status('q9').state, status('q9').value) == ('graded', False)
    runner.save(S, 'q2', 'parse', f.Q2_WRONG_WEIBULL, author='ta')
    assert status('q2', 'check').state == 'outdated'
    runner.recheck(S, 'q2')
    assert (status('q2', 'check').state, status('q2', 'check').value) == ('unverified', False)

    # A variant resolved as alternate selects the alternate key.
    runner.save(S, 'q9', 'parse', f.ANSWERS['alternate']['q9'], author='ta', verify=True)
    runner.recheck(S, 'q9')
    assert status('q9').value is False
    runner.choose_variant(S, 'q9', 'alternate', author='ta')
    assert status('q9').state == 'outdated'
    runner.recheck(S, 'q9')
    assert (status('q9').state, status('q9').value) == ('graded', True)

    # A grader's rerun with one custom value reaches the check through ctx.params.
    assert runner.run_stage(S, 'q2', 'evidence', params={'huge_constant': 20}, deliberate=True).ok
    assert ws.store.get(S, 'q2', 'evidence').body['data']['fields']['2.2']['note'] == 'huge_constant = 20'

    # A manual grade survives a batch run.
    runner.decide(S, 'q9', '', False, author='ta')
    runner.decide(S, 'q2', 'shiny', False, author='ta')
    selection = batch.Selection((S,), ('q9', 'q2'), tuple(batch.stage_order(exam)))
    plan = batch.plan(exam, ws.store, selection, model_available=True)
    batch.BatchRun(runner, plan).run()
    assert (status('q9').state, status('q9').value, status('q9').author) == ('manual', False, 'ta')
    assert (status('q2', 'shiny').state, status('q2', 'shiny').value) == ('manual', False)
    # The batch used no custom value.
    assert 'huge_constant = 10' in ws.store.get(S, 'q2', 'evidence').body['data']['fields']['2.2']['note']


def test_unresolved_variant_is_undecided_until_a_grader_chooses(exam, pkg, tmp_path):
    f = pkg('fixtures')
    case = synth.Case(S, variants=pkg('versions').VERSIONS[0], answers={'q5': f.ANSWERS['original']['q5']},
                      readings={'q9_observations': '[14, 11, 15]'})        # disagrees with the other cue
    root = synth.generate(exam, tmp_path / 'ws', [case])
    ws = Workspace(root, exam)
    ws.sync_roster()
    runner = Runner(exam, ws, model=synth.fake_model(root))
    try:
        q = exam.questions['q5']
        assert all(o.ok for o in runner.run_from(S, 'q5', 'variant'))
        variant = ws.store.get(S, 'q5', 'variant').body
        assert variant['resolved'] is None and variant['candidates'] == []
        runner.verify(S, 'q5', 'parse', author='ta', expect_rev=ws.store.get(S, 'q5', 'parse').rev)
        st = part_status(q, '', ws.store.for_question(S, 'q5'))
        assert (st.value, st.counts, st.reason) == (None, False, "variant undecided")
        runner.choose_variant(S, 'q5', 'original', author='ta')
        runner.recheck(S, 'q5')
        st = part_status(q, '', ws.store.for_question(S, 'q5'))
        assert (st.state, st.value) == ('graded', True)
    finally:
        runner.close()
        ws.close()


# --- answer keys never reach the model --------------------------------------------------

def test_no_answer_key_in_any_prompt(exam, pkg, tmp_path):
    f, versions = pkg('fixtures'), pkg('versions')
    cases = [synth.Case(f'v{alt}', variants=versions.VERSIONS[alt], answers=f.canonical(alt)) for alt in (0, 7)]
    root = synth.generate(exam, tmp_path / 'ws', cases)
    ws = Workspace(root, exam)
    ws.sync_roster()
    model = synth.fake_model(root)
    runner = Runner(exam, ws, model=model)
    try:
        for sub in ws.store.submissions():
            for qid, q in exam.questions.items():
                assert all(o.ok for o in runner.run_from(sub.id, qid, q.stages[0].name))
    finally:
        runner.close()
        ws.close()
    stages = {c.tag[2] for c in model.calls}
    assert stages == {'variant', 'transcript', 'parse'}
    prompts = "\n".join(c.prompt for c in model.calls)
    secrets = ['2*b*(1-a)', 'b*(1-a)', '2*a*(1-b)', '[0, b]', '[0, a]', '6/25', '-1/4', '3/4', '-3/4',
               'stays the same', 'not enough info', 'increases', 'decreases', 'unchanged', 'option 4', 'option 3',
               'standard_exponential', 'expm1', 'sqrt(12.5)', '1 / a', '50*u']
    for answers in f.ANSWERS.values():
        secrets += [v for q in ('q1', 'q3', 'q8') for v in answers[q].values() if len(v) > 3]
    for secret in secrets:
        assert secret not in prompts, secret
    for qid in ('q5', 'q9', 'q4', 'q6'):       # short keys: check the prompts of their own question
        own = "\n".join(c.prompt for c in model.calls if c.tag[1] == qid and c.tag[2] != 'variant')
        for answers in f.ANSWERS.values():
            for label, value in answers[qid].items():
                assert not re.search(rf'(?<![\w.]){re.escape(value)}(?![\w.])', own.replace(label, '')), (qid, value)
