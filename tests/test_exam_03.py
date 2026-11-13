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

from examinator import Grade, Printed, load_exam, synth
from examinator.core import batch
from examinator.core.defs import DEFAULT
from examinator.core.runner import Runner, try_check
from examinator.core.workspace import Workspace
from examinator.std import readers

ROOT = Path(__file__).resolve().parents[1]
EXAM = ROOT / 'exams' / 'exam_03'
BLANKS = os.environ.get('EXAM3_BLANKS')
needs_blanks = pytest.mark.skipif(not BLANKS, reason="EXAM3_BLANKS is not set")
S = 'synthetic'


@pytest.fixture(scope='module')
def exam():
    return load_exam(EXAM)


@pytest.fixture(scope='module')
def pkg(exam):
    """The package's modules, by name."""
    return lambda name: importlib.import_module(f'{EXAM.name}.{name}')


def info(exam, printing):
    """The `info` values of a submission whose every choice is `printing`: what its printed fields read."""
    versions = importlib.import_module(f'{EXAM.name}.versions')
    return versions.printed(0 if printing == 'original' else 7)['info']


def values(exam, qid, answers, printing=None, body=None):
    """Field values for a check: the question's answers (`{label: text or None}`),
    and `info` read as `printing` everywhere (or as `body`; or not read)."""
    out = {f'{qid}/{label}': '' if text is None else text for label, text in answers.items()}
    if printing is not None:
        body = info(exam, printing)
    out.update({f'info/{label}': text for label, text in (body or {}).items()})
    return out


def outcomes(exam, qid, answers, printing=None, *, check='check', body=None):
    """Run one check of a question directly on answers, with `info` read as
    `printing` (or as `body`, or not read): `({label: outcome}, result)`."""
    got = try_check(exam, qid, check, values(exam, qid, answers, printing, body))
    return {label: f['outcome'] for label, f in got['fields'].items()}, got


def both(exam, qid, answers):
    """Both checks of a pseudocode question (q2, q7) on `{label: source}`: the
    outcome of each part, and each check's result by name."""
    got, results = {}, {}
    for check in ('check', 'shiny'):
        o, results[check] = outcomes(exam, qid, answers, check=check)
        got.update(o)
    return got, results


def status(runner, sub, qid, check='check'):
    q = runner.ws.exam.questions[qid]
    return runner.sheet(sub).status(runner.ws.exam, q, q.check(check))


def text_of(runner, sub, qid):
    """A question's stored values, `{label: text}`."""
    return {k.split('/', 1)[1]: v.text for k, v in runner.values(sub).items() if k.startswith(f'{qid}/')}


def verify(runner, sub, qid, labels=None):
    """Verify a question's values (or some of them) as a grader would."""
    for k, v in runner.values(sub).items():
        q, label = k.split('/', 1)
        if q == qid and (labels is None or label in labels) and not v.is_verified:
            runner.verify(sub, q, label, author='ta', expect_rev=v.rev)


# --- acceptance checks 1-5: the fixtures end to end -----------------------------

def test_selftest(exam, tmp_path):
    assert synth.selftest(exam, tmp_path / 'ws') == []


def test_definition(exam, pkg):
    assert list(exam.questions) == ['info'] + [f'q{i}' for i in range(1, 10)]
    assert exam.pages == 22 and exam.print_area == (0, 0, 1000, 1000)
    info = exam.questions['info']
    assert info.checks == () and info.read is None and all(isinstance(f.read, Printed) for f in info.fields)
    assert {label for labels in pkg('versions').FIELDS.values() for label in labels} == {f.label for f in info.fields}
    for f in info.fields:
        assert set(pkg('exam').TEXTS[f.label].values()) == {'original', 'alternate'}, f.label
    # Questions 2 and 7: one code field per function, one reader for both, one check per field.
    for qid, functions in (('q2', ('weibull', 'weibullish')), ('q7', ('poisson_process', 'poisson_dist'))):
        q = exam.questions[qid]
        assert [c.name for c in q.checks] == ['check', 'shiny'] and q.columns() == [f'{qid}.check', f'{qid}.shiny']
        assert q.check('check').points.counts and not q.check('shiny').points.counts
        assert all(getattr(c.fn, 'by', None) is None for c in q.checks)
        assert isinstance(q.read, readers.Code) and [f.label for f in q.written] == list(q.read.entries)
        assert [q.read.wanted(f.label) for f in q.written] == [(fn,) for fn in functions]
    assert exam.questions['q2'].read.then is pkg('huge_constants').rewrite
    assert exam.questions['q7'].read.then is None
    groups = {}
    for q in exam.questions.values():
        if q.id in ('info', 'q2', 'q7'):
            continue
        assert [c.name for c in q.checks] == ['check'] and q.columns() == [q.id] and q.check('check').points.counts
        by = q.check('check').fn.by
        assert set(q.check('check').fn.key) == {'original', 'alternate'}
        assert all(v.question == 'info' for v in by.refs()) and len(by.refs()) == 2
        groups.setdefault(tuple(v.label for v in by.refs()), []).append(q.id)
    assert sorted(groups.values()) == [['q1', 'q5', 'q9'], ['q3', 'q6'], ['q4', 'q8']]
    assert sorted(groups) == sorted(pkg('versions').FIELDS.values())


@needs_blanks
def test_versions_match_the_printed_blanks(exam, pkg):
    """Each printed field of `info` lies on its page, its region holds the printed
    text, and what it reads to agrees with the alt index -> printing table."""
    import pypdfium2
    versions = pkg('versions')
    printed = {      # field -> (pattern, text that marks the original printing)
        'q5_confidence': (r'at least (\d+)%', '95'),
        'q9_observations': (r'observing ([\d, and]+)\.', '14, 12, and 15'),
        'q6_option_c': (r'C\. They (use two different|do the exact same)', 'use two different'),
        'q3_first_height': (r'3\.1 .*?the (\w+) height', 'base'),
        'q8_first_n': (r'8\.1 .*?𝑛=(\d)', '3'),
        'q4_negative_branch': (r'(\S+(?: \+ 1)?) if − 1 ≤ 𝑟 < 0', '𝑟 + 1'),
    }
    fields = exam.questions['info'].fields
    assert {f.label for f in fields} == set(printed)
    for alt in versions.VERSIONS:
        doc = pypdfium2.PdfDocument(str(versions.blank(alt)))
        try:
            assert len(doc) == exam.pages
            for f in fields:
                pattern, original = printed[f.label]
                page = doc[f.region.page - 1]
                text = page.get_textpage().get_text_range()
                found = re.search(pattern, text, re.S)
                assert found, (alt, f.label)
                want = 'original' if found[1] == original else 'alternate'
                assert pkg('exam').TEXTS[f.label][versions.printed(alt)['info'][f.label]] == want, (alt, f.label)
                w, h = page.get_size()
                r = f.region
                inside = page.get_textpage().get_text_bounded(
                    left=w * r.left / 1000, right=w * r.right / 1000,
                    top=h * (1 - r.top / 1000), bottom=h * (1 - r.bottom / 1000))
                assert ' '.join(found[1].split()) in ' '.join(inside.split()), (alt, f.label, "outside the region")
        finally:
            doc.close()


# --- acceptance check 5: the printing read from info selects the key ---------------

def test_the_printing_read_from_info_selects_the_key(exam, pkg):
    answers = pkg('fixtures').ANSWERS
    for qid in ('q1', 'q3', 'q4', 'q5', 'q6', 'q8', 'q9'):
        for printed in ('original', 'alternate'):
            for written in ('original', 'alternate'):
                got, _ = outcomes(exam, qid, answers[written][qid], printing=printed)
                assert all(o == 'correct' for o in got.values()) == (printed == written), (qid, printed, written)
        # With `info` not read, or its two fields disagreeing, the check is undecided.
        labels = [v.label for v in exam.questions[qid].check('check').fn.by.refs()]
        _, got = outcomes(exam, qid, answers['original'][qid])
        assert got['grade']['value'] is None and f"info {labels[0]} not read" in got['grade']['reason']
        _, got = outcomes(exam, qid, answers['original'][qid], body={**info(exam, 'original'), labels[0]: info(exam, 'alternate')[labels[0]]})
        assert got['grade']['value'] is None and "disagree" in got['grade']['reason']
        assert {k for k in got['reads'] if k.startswith('info/')} == {f'info/{label}' for label in labels}, qid


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
        return outcomes(exam, 'q1', answers, printing='original')[0]

    assert Grade.from_json(outcomes(exam, 'q1', {'1.1': '[0,b] x [a,1]', '1.2': '2b(1-a)', '1.3': 'a(1-b)',
                                                 '1.4': '1/2'}, 'original')[1]['grade']).value is False

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

def q2(exam, pkg, weibullish, weibull=None):
    """Both parts of question 2, with the fixtures' `weibull` unless given."""
    f = pkg('fixtures')
    return both(exam, 'q2', {'2.1': f.WEIBULL if weibull is None else weibull, '2.2': weibullish})


def rows_of(result):
    """A sampler check's test rows: `[test, outcome, note]`."""
    return next(b for b in result['blocks'] if b.get('summary') == "Tests")['blocks'][0]['rows']


def test_q2_recorded_results(exam, pkg):
    f = pkg('fixtures')
    got, results = both(exam, 'q2', f.Q2)
    assert got == {'2.1': 'correct', '2.2': 'correct'}
    assert [results[c]['grade']['value'] for c in ('check', 'shiny')] == [True, True]
    tests = rows_of(results['shiny'])
    assert [(name, result) for name, result, _ in tests] == [('huge_constant = 10', 'correct')]   # the default
    for result in results.values():
        assert result['blocks'][0]['type'] == 'image'                 # plots first
        assert all(b['type'] != 'code' for b in result['blocks'])     # dumps and source only inside details
    # Each check reads its own field only, and 2.2 the constant (at its default).
    assert set(results['check']['reads']) == {'q2/2.1'}
    assert results['shiny']['reads']['q2/huge_constant'] == DEFAULT and set(results['shiny']['reads']) == {'q2/2.2', 'q2/huge_constant'}

    rewritten = {label: pkg('huge_constants').rewrite(source) for label, source in f.Q2_LITERAL.items()}
    assert rewritten == f.Q2
    assert both(exam, 'q2', f.Q2_WRONG_WEIBULL)[0] == {'2.1': 'incorrect', '2.2': 'correct'}
    assert both(exam, 'q2', f.Q2_ONLY_WEIBULL)[0] == {'2.1': 'correct', '2.2': 'blank'}
    got, results = both(exam, 'q2', {'2.1': None, '2.2': None})
    assert got == {'2.1': 'blank', '2.2': 'blank'}
    assert all('blank' in r['grade']['flags'] for r in results.values())


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
    assert q2(exam, pkg, "import numpy\n", weibull="import numpy\n")[0] == {'2.1': None, '2.2': None}
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


def test_q2_huge_constant_set_by_a_grader(exam, pkg):
    f = pkg('fixtures')
    got, results = both(exam, 'q2', {**f.Q2, 'huge_constant': '20'})
    assert got['2.2'] == 'correct' and results['shiny']['fields']['2.2']['note'] == 'huge_constant = 20'
    got, results = both(exam, 'q2', {**f.Q2, 'huge_constant': '1'})
    assert got['2.2'] is None and 'not in [0, 1]' in rows_of(results['shiny'])[0][2]   # an invalid probability
    for bad in ('0', '-3', '1e7', 'many'):
        got, results = both(exam, 'q2', {**f.Q2, 'huge_constant': bad})
        assert got == {'2.1': 'correct', '2.2': None}, bad
        assert 'at most 1e+06' in results['shiny']['fields']['2.2']['note']
    # code without the constant does not read it
    _, results = both(exam, 'q2', {**f.Q2, '2.2': f.Q2['2.2'].replace('huge_constant', '10')})
    assert set(results['shiny']['reads']) == {'q2/2.2'}


@pytest.mark.slow
def test_q2_time_budget_is_undecided(exam, pkg):
    """A substitute too large for the one-sample-at-a-time interpreter: a budget runs out
    (up to 20 s), and the part is undecided."""
    got, result = outcomes(exam, 'q2', {**pkg('fixtures').Q2, 'huge_constant': '1e6'}, check='shiny')
    assert got == {'2.2': None} and result['grade']['value'] is None
    (_, outcome, note), = rows_of(result)
    assert outcome == '?' and ('time limit' in note or 'steps' in note)


# --- question 7: more synthetic samplers ---------------------------------------------

def q7(exam, pkg, process):
    return both(exam, 'q7', {'7.1': process, '7.2': pkg('fixtures').POISSON_DIST})[0]


def test_q7_recorded_results(exam, pkg):
    f = pkg('fixtures')
    got, results = both(exam, 'q7', f.Q7)
    assert got == {'7.1': 'correct', '7.2': 'correct'}
    kinds = [b['type'] for b in results['check']['blocks']]
    assert kinds[0] == 'image' and 'image' not in kinds[kinds.index('details'):]    # plots first
    titles = [b['title'] for b in results['check']['blocks'] if b['type'] == 'image']
    assert titles[:4] == ["number of events", "event locations", "number of events", "event locations"]
    assert set(results['check']['reads']) == {'q7/7.1'} and set(results['shiny']['reads']) == {'q7/7.2'}
    assert both(exam, 'q7', f.Q7_OFF_BY_ONE)[0] == {'7.1': 'correct', '7.2': 'incorrect'}
    assert both(exam, 'q7', f.Q7_ONLY_PROCESS)[0] == {'7.1': 'correct', '7.2': 'blank'}
    assert both(exam, 'q7', {'7.1': None, '7.2': f.POISSON_DIST})[0] == {'7.1': 'blank', '7.2': 'correct'}


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
    assert both(exam, 'q7', {'7.1': f.POISSON_PROCESS, '7.2': looping})[0] == {'7.1': 'correct', '7.2': None}
    broken = f.POISSON_PROCESS.replace('events.append(sqrt(50*u - 25))', 'events.append(sqrt(25 - 50*u))')
    failure, results = both(exam, 'q7', {**f.Q7, '7.1': broken})
    assert failure == {'7.1': 'incorrect', '7.2': 'correct'}
    assert 'sqrt' in results['check']['fields']['7.1']['note']
    unsupported = "def poisson_process():\n    return {1: 2}\n"     # outside the language: undecided
    assert both(exam, 'q7', {'7.1': unsupported, '7.2': unsupported})[0] == {'7.1': None, '7.2': None}


# --- acceptance check 6: workflow rules on this exam ----------------------------------

@pytest.fixture
def env(exam, pkg, tmp_path):
    """(workspace, runner) with one synthetic submission of the original printing."""
    f = pkg('fixtures')
    case = synth.Case(S, printed=pkg('versions').printed(0), answers=f.canonical(0))
    root = synth.generate(exam, tmp_path / 'ws', [case])
    ws = Workspace(root, exam)
    ws.sync()
    runner = Runner(ws, model=synth.fake_model(root))
    yield ws, runner
    runner.close()
    ws.close()


def test_workflow_rules(exam, pkg, env):
    ws, runner = env
    f, versions = pkg('fixtures'), pkg('versions')

    def st(qid, check='check'):
        return status(runner, S, qid, check)

    def put(qid, answers):
        """A grader types a question's values (which verifies them)."""
        for label, text in answers.items():
            runner.set(S, qid, label, text, author='ta', expect_rev=runner.values(S)[f'{qid}/{label}'].rev)

    for qid in ('info', 'q9', 'q2'):
        assert all(o.ok for o in runner.read(S, qid))
    runner.refresh(S, questions=['q9', 'q2'])
    assert text_of(runner, S, 'info') == versions.printed(0)['info']
    assert text_of(runner, S, 'q2') == f.Q2

    # Unverified results do not count. The printing was read as expected: nothing to verify there.
    assert all(runner.values(S)[f'info/{label}'].verified == 'reader' for label in versions.printed(0)['info'])
    s9 = st('q9')
    assert (s9.state, s9.value, s9.counts, set(s9.waiting)) == ('unverified', True, False, {'q9/9.a', 'q9/9.b'})
    assert st('q2', 'check').state == st('q2', 'shiny').state == 'unverified'
    assert st('q2', 'check').waiting == ('q2/2.1',)          # pseudocode reads no printing, and only its own field
    verify(runner, S, 'q9')
    verify(runner, S, 'q2', ['2.1'])
    assert (st('q2', 'check').state, st('q2', 'check').value) == ('graded', True)
    assert st('q2', 'shiny').state == 'unverified'           # waits for 2.2 only
    verify(runner, S, 'q2')
    assert (st('q2', 'shiny').state, st('q2', 'shiny').value) == ('graded', True)
    assert (st('q9').state, st('q9').value, st('q9').counts) == ('graded', True, True)

    # Editing a value outdates the grades that read it, until the check runs again.
    put('q9', {'9.b': '17'})
    assert (st('q9').state, st('q9').counts) == ('outdated', False)
    runner.refresh(S)
    assert (st('q9').state, st('q9').value) == ('graded', False)
    put('q2', {'2.1': f.Q2_WRONG_WEIBULL['2.1']})
    assert st('q2', 'check').state == 'outdated' and st('q2', 'shiny').state == 'graded'
    runner.refresh(S)
    assert (st('q2', 'check').state, st('q2', 'check').value) == ('graded', False)

    # The printing read from info selects the key: a grader correcting info decides q9 again.
    put('q9', f.ANSWERS['alternate']['q9'])
    runner.refresh(S)
    assert (st('q9').state, st('q9').value) == ('graded', False)
    put('info', {label: info(exam, 'alternate')[label] for label in versions.FIELDS[1]})
    assert st('q9').state == 'outdated' and st('q2', 'check').state == 'graded'
    runner.refresh(S)
    assert (st('q9').state, st('q9').value) == ('graded', True)

    # The constant at its default is verified; a grader's value outdates the result, and
    # dropping it goes back to the default.
    assert st('q2', 'shiny').state == 'graded' and 'q2/huge_constant' not in runner.values(S)
    runner.set(S, 'q2', 'huge_constant', '20', author='ta')
    assert st('q2', 'shiny').state == 'outdated'
    runner.refresh(S)
    assert (st('q2', 'shiny').state, runner.sheet(S).results['q2/shiny'].fields['2.2']['note']) == \
        ('graded', 'huge_constant = 20')
    runner.unset(S, 'q2', 'huge_constant')
    assert st('q2', 'shiny').state == 'outdated'
    runner.refresh(S)
    assert st('q2', 'shiny').state == 'graded'
    runner.set(S, 'q2', 'huge_constant', '20', author='ta')
    runner.refresh(S)

    # A decision survives a batch run.
    runner.decide(S, 'q9', 'check', False, author='ta')
    runner.decide(S, 'q2', 'shiny', False, author='ta')
    plan = batch.plan(ws, batch.Selection((S,), ('q9', 'q2')), model_available=True)
    batch.BatchRun(runner, plan).run()
    assert (st('q9').state, st('q9').value, st('q9').author) == ('decided', False, 'ta')
    assert (st('q2', 'shiny').state, st('q2', 'shiny').value) == ('decided', False)
    assert text_of(runner, S, 'q9') == f.ANSWERS['alternate']['q9']         # what a person set stays
    # A batch only reads: the result with the grader's constant stays.
    assert 'huge_constant = 20' in runner.sheet(S).results['q2/shiny'].fields['2.2']['note']


def test_disagreeing_printed_fields_are_undecided_until_a_grader_corrects_info(exam, pkg, tmp_path):
    f, versions = pkg('fixtures'), pkg('versions')
    printed = {'info': {**versions.printed(0)['info'], 'q9_observations': '[14, 11, 15]'}}   # the alternate's
    case = synth.Case(S, printed=printed, answers={'q5': f.ANSWERS['original']['q5']})
    root = synth.generate(exam, tmp_path / 'ws', [case])
    ws = Workspace(root, exam)
    ws.sync()
    runner = Runner(ws, model=synth.fake_model(root))
    try:
        for qid in ('info', 'q5'):
            assert all(o.ok for o in runner.read(S, qid))
            verify(runner, S, qid)
        runner.refresh(S, questions=['q5'])
        got = text_of(runner, S, 'info')
        assert (got['q5_confidence'], got['q9_observations']) == ('95', '[14, 11, 15]')
        st = status(runner, S, 'q5')
        assert (st.state, st.value, st.counts) == ('to_decide', None, False)
        assert 'disagree' in st.grade.reason
        rev = runner.values(S)['info/q9_observations'].rev
        runner.set(S, 'info', 'q9_observations', '[14, 12, 15]', author='ta', expect_rev=rev)
        assert status(runner, S, 'q5').state == 'outdated'
        runner.refresh(S)
        st = status(runner, S, 'q5')
        assert (st.state, st.value, st.counts) == ('graded', True, True)
    finally:
        runner.close()
        ws.close()


# --- answer keys never reach the model --------------------------------------------------

def test_no_answer_key_in_any_prompt(exam, pkg, tmp_path):
    f, versions = pkg('fixtures'), pkg('versions')
    cases = [synth.Case(f'v{alt}', printed=versions.printed(alt), answers=f.canonical(alt)) for alt in (0, 7)]
    root = synth.generate(exam, tmp_path / 'ws', cases)
    ws = Workspace(root, exam)
    ws.sync()
    model = synth.fake_model(root)
    runner = Runner(ws, model=model)
    try:
        for sub in ws.store.submissions():
            for qid in exam.questions:
                assert all(o.ok for o in runner.read(sub.id, qid))
    finally:
        runner.close()
        ws.close()
    assert {c.tag[2].split(':')[0] for c in model.calls} == {'read', 'transcribe', 'parse'}
    prompts = "\n".join(c.prompt for c in model.calls)
    secrets = ['2*b*(1-a)', 'b*(1-a)', '2*a*(1-b)', '[0, b]', '[0, a]', '6/25', '-1/4', '3/4', '-3/4',
               'stays the same', 'not enough info', 'increases', 'decreases', 'unchanged', 'option 4', 'option 3',
               'standard_exponential', 'expm1', 'sqrt(12.5)', '1 / a', '50*u']
    for answers in f.ANSWERS.values():
        secrets += [v for q in ('q1', 'q3', 'q8') for v in answers[q].values() if len(v) > 3]
    for secret in secrets:
        assert secret not in prompts, secret
    for qid in ('q5', 'q9', 'q4', 'q6'):       # short keys: check the prompts of their own question
        own = "\n".join(c.prompt for c in model.calls if c.tag[1] == qid)
        for answers in f.ANSWERS.values():
            for label, value in answers[qid].items():
                assert not re.search(rf'(?<![\w.]){re.escape(value)}(?![\w.])', own.replace(label, '')), (qid, value)
