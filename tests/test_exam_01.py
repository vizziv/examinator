"""Exam 1 as an exam package: a real exam the library was not designed against.

Everything here is synthetic. Tests that read the instructor's blank PDFs skip
unless `EXAM1_BLANKS` names their directory.
"""
import importlib
import math
import os
import re
from pathlib import Path

import numpy as np
import pytest

from examinator import Bool, Grade, Printed, load_exam, synth
from examinator.core.runner import Runner, try_check
from examinator.core.workspace import Workspace
from examinator.std import pseudocode
from examinator_kits.stochastic import checks

ROOT = Path(__file__).resolve().parents[1]
EXAM = ROOT / 'exams' / 'exam_01'
BLANKS = os.environ.get('EXAM1_BLANKS')
needs_blanks = pytest.mark.skipif(not BLANKS, reason="EXAM1_BLANKS is not set")
VARIED = ('q1', 'q2', 'q4', 'q5', 'q6')
SAMPLERS = {'q1': '1', 'q4': '4', 'q5': '5', 'q6': '6'}


@pytest.fixture(scope='module')
def exam():
    return load_exam(EXAM)


@pytest.fixture(scope='module')
def pkg(exam):
    """The package's modules, by name."""
    return lambda name: importlib.import_module(f'{EXAM.name}.{name}')


def values(exam, qid, answers, info=None):
    """Field values for a check: the question's answers (`{label: text}`, or the
    source of its one code field), and `info`'s printed fields (or none read)."""
    answers = answers if isinstance(answers, dict) else {exam.questions[qid].written[0].label: answers}
    out = {f'{qid}/{label}': '' if text is None else text for label, text in answers.items()}
    out.update({f'info/{label}': v for label, v in (info or {}).items()})
    return out


def outcomes(exam, qid, answers, printing=None):
    """Run one question's check directly on answers, with every printed field of
    `info` read as `printing` (or not read): `({label: outcome}, result)`."""
    info = None if printing is None else {f.label: printing for f in exam.questions['info'].fields}
    body = try_check(exam, qid, 'check', values(exam, qid, answers, info))
    return {label: f['outcome'] for label, f in body['fields'].items()}, body


def verdict(exam, qid, source, printing='original'):
    """A sampler question's single outcome and its note."""
    _, body = outcomes(exam, qid, source, printing)
    f = body['fields'][SAMPLERS[qid]]
    return f['outcome'], f.get('note', "")


def grade(exam, qid, answers, printing=None):
    return Grade.from_json(outcomes(exam, qid, answers, printing)[1]['grade'])


def status(runner, sub, qid):
    q = runner.exam.questions[qid]
    return runner.sheet(sub).status(runner.exam, q, q.check('check'))


# --- the fixtures end to end ----------------------------------------------------------

def test_selftest(exam, tmp_path):
    assert synth.selftest(exam, tmp_path / 'ws') == []


def test_definition(exam, pkg):
    versions = pkg('versions')
    assert list(exam.grading) == ['info'] + [f'q{i}' for i in range(1, 7)]
    assert exam.pages == 16 and exam.print_area == (0, 0, 1000, 1000)
    info = exam.questions['info']
    assert info.checks == () and list(info.fields) == versions.FIELDS
    assert all(isinstance(f.read, Printed) and set(f.read.reads.values()) == {'original', 'alternate'}
               for f in info.fields)
    labels = {f.label: f for f in info.fields}
    assert set(versions.PRINTING) == set(VARIED)
    for qid, q in exam.grading.items():
        for f in q.fields:
            assert f.region.page in q.pages
        if qid == 'info':
            continue
        assert [c.name for c in q.checks] == ['check'] and q.columns() == [qid]
        assert q.check('check').points == Bool(counts=qid not in ('q5', 'q6'))  # the shiny questions do not count
        if qid in VARIED:       # two printed fields of info, one printed on or just before its own page
            refs = versions.PRINTING[qid].refs()
            assert len(refs) == 2 and all(r.question == 'info' and r.label in labels for r in refs)
            assert any(labels[r.label].region.page in (q.pages[0] - 1, q.pages[0]) for r in refs), qid
    assert exam.questions['q2'].check('check').fn.by == versions.PRINTING['q2']
    assert {q.id: q.pages for q in exam.grading.values()} == {
        'info': (2, 4, 8, 10, 12), 'q1': (3,), 'q2': (5,), 'q3': (7,), 'q4': (9,), 'q5': (11,), 'q6': (13,)}


@needs_blanks
def test_versions_match_the_printed_blanks(exam, pkg):
    """What each printed field of `info` reads to agrees with the text printed in each
    blank, and the field's region holds that text."""
    import pypdfium2
    versions = pkg('versions')
    printed = {      # field -> (pattern in the page's text, the match that marks the original printing)
        'q1_parameter': (r'exponential distribution with (\w+)', 'mean'),
        'q2_m_goal': (r'Answer with the (\w+) possible positive value of 𝑚', 'smallest'),
        'q4_y_axis_top': (r'𝑦\s+(−1 1 2 3 4|−1 1 2 3\s)', '−1 1 2 3 4'),
        'q5_function': (r'for a function (\w+)\(\)', 'unit_cone'),
        'q6_sine_frequency': (r'sin\((\d+)𝑥\)', '40'),
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
                found = re.search(pattern, page.get_textpage().get_text_range(), re.S)
                assert found, (alt, f.label)
                want = 'original' if found[1].strip() == original else 'alternate'
                assert versions.printed_on(alt)['info'][f.label] == want, (alt, f.label)
                w, h = page.get_size()
                r = f.region
                inside = page.get_textpage().get_text_bounded(
                    left=w * r.left / 1000, right=w * r.right / 1000,
                    top=h * (1 - r.top / 1000), bottom=h * (1 - r.bottom / 1000))
                assert ' '.join(found[1].split()) in ' '.join(inside.split()), (alt, f.label, "outside the region")
        finally:
            doc.close()


@needs_blanks
def test_field_regions_hold_ink_on_the_blanks(exam, pkg, tmp_path):
    """Every region lands on printed content (a printed field's text, a box's frame) in both printings."""
    from examinator.pdf.layout import Layout
    from examinator.pdf.render import Renderer
    renderer, versions = Renderer(tmp_path / 'cache', dpi=72), pkg('versions')
    for alt in versions.VERSIONS:
        for q in exam.grading.values():
            for region in [f.region for f in q.fields]:
                crop = Layout().crop(renderer.page(versions.blank(alt), region.page - 1), region)
                assert np.asarray(crop.convert('L')).min() < 128, (alt, q.id, region)


def test_no_answer_key_in_any_prompt(exam, pkg, tmp_path):
    f, versions = pkg('fixtures'), pkg('versions')
    cases = [synth.Case(f'v{alt}', printed=versions.printed_on(alt), answers=f.canonical(alt)) for alt in (0, 1)]
    root = synth.generate(exam, tmp_path / 'ws', cases)
    ws = Workspace(root, exam)
    ws.sync()
    model = synth.fake_model(root)
    runner = Runner(exam, ws, model=model)
    try:
        for sub in ws.store.submissions():
            for qid in exam.grading:
                assert all(o.ok for o in runner.read(sub.id, qid))
    finally:
        runner.close()
        ws.close()
    assert {c.tag[2].split(':')[0] for c in model.calls} == {'read', 'transcribe', 'parse'}
    prompts = "\n".join(c.prompt for c in model.calls)
    for secret in ['log(1', 'log(U', 'expm1', 'sqrt(unif', '1/3)', '5/4', '3/2)', 'pi/2', '[3, 4]', '[2, 2]',
                   'x**(1/5)', 'x**(-1/5)', 'sin(40', 'sin(20', 'unit_disc', 'scoop', 'niform(0']:
        assert secret not in prompts, secret

    def own(qid):       # short keys: check the prompts of their own question
        return "\n".join(c.prompt for c in model.calls if c.tag[1] == qid)

    for secret in ['s[', 's_', 'w[', 'w_', 'niform', '/ s']:
        assert secret not in own('q3'), secret
    numbered = re.sub(r'2\.[123]\.[mc]', '', own('q2'))
    for answers in f.ANSWERS.values():
        for value in answers['q2'].values():
            assert not re.search(rf'(?<![\w/.]){re.escape(value)}(?![\w/.])', numbered), value


# --- question 2: numbers, five of six ---------------------------------------------------

def test_q2_the_printing_read_from_info_selects_the_key(exam, pkg):
    answers = pkg('fixtures').ANSWERS
    for printed in ('original', 'alternate'):
        for written in ('original', 'alternate'):
            got, _ = outcomes(exam, 'q2', answers[written]['q2'], printing=printed)
            assert all(o == 'correct' for o in got.values()) == (printed == written)
            assert grade(exam, 'q2', answers[written]['q2'], printed).value == (printed == written)
    # Info not read, or its two fields disagreeing: undecided.
    body = try_check(exam, 'q2', 'check', values(exam, 'q2', answers['original']['q2']))
    assert body['grade']['value'] is None and "info q1_parameter not read" in body['grade']['reason']
    split = {f.label: 'original' for f in exam.questions['info'].fields} | {'q2_m_goal': 'alternate'}
    body = try_check(exam, 'q2', 'check', values(exam, 'q2', answers['original']['q2'], split))
    assert body['grade']['value'] is None and "disagree" in body['grade']['reason']
    assert set(body['reads']) == {'info/q1_parameter', 'info/q2_m_goal'}       # what it read, nothing more


def test_disagreeing_printed_fields_wait_for_a_grader(exam, pkg, tmp_path):
    """q4 and q5 read q5_function; when it disagrees with its neighbours, both are
    undecided until a grader corrects that one value."""
    f, versions = pkg('fixtures'), pkg('versions')
    printed = {'info': {**versions.printed_on(0)['info'], 'q5_function': 'twonit_cone'}}
    case = synth.Case('split', printed=printed, answers={q: f.ANSWERS['original'][q] for q in ('q2', 'q4', 'q5')})
    root = synth.generate(exam, tmp_path / 'ws', [case])
    ws = Workspace(root, exam)
    ws.sync()
    runner = Runner(exam, ws, model=synth.fake_model(root))
    try:
        for qid in ('info', 'q2', 'q4', 'q5'):
            assert all(o.ok for o in runner.read('split', qid))
        for k, v in runner.values('split').items():
            runner.verify('split', *k.split('/'), author='ta', expect_rev=v.rev)
        runner.refresh('split', questions=['q2', 'q4', 'q5'])
        assert {k: v.text for k, v in runner.values('split').items() if k.startswith('info/')} == \
            {f'info/{k}': v for k, v in {**versions.printed_on(0)['info'], 'q5_function': 'alternate'}.items()}
        assert (status(runner, 'split', 'q2').state, status(runner, 'split', 'q2').value) == ('graded', True)
        for qid in ('q4', 'q5'):
            st = status(runner, 'split', qid)
            assert (st.state, st.value, st.counts) == ('to_decide', None, False), qid
        rev = runner.values('split')['info/q5_function'].rev
        runner.set('split', 'info', 'q5_function', 'original', author='ta', expect_rev=rev)
        assert status(runner, 'split', 'q4').state == status(runner, 'split', 'q5').state == 'outdated'
        assert status(runner, 'split', 'q2').state == 'graded'          # it never read that field
        runner.refresh('split')
        for qid in ('q2', 'q4', 'q5'):
            assert (status(runner, 'split', qid).state, status(runner, 'split', qid).value) == ('graded', True)
    finally:
        runner.close()
        ws.close()


# --- question 3: subscripted expressions and a distribution's name -----------------------

def test_subscripted_expressions(pkg):
    blanks = pkg('blanks')
    assert blanks.read('s[i-1]') == blanks.read('S_{-1 + i}') != blanks.read('s_i')
    key = blanks.indexed('(s[i] - s[i-1]) / s[n]', 'w[i] / s[n]')
    for same in ('(s_i - s_{i-1})/s_n', 's[i]/s[n] - s[i-1]/s[n]', '(s[i]-s[-1+i])/s[n]', '(S_i − S_(i−1)) / S_n',
                 '\\frac{1}{s_n} * (s_i - s_{i-1})'.replace('\\frac{1}{s_n}', '(1/s_n)')):
        assert key(same) == ('correct', ""), same
    for wrong in ('s_i/s_n', '(s_k - s_{k-1})/s_n', 's[i] - s[i-1]', '1/n', '(s[i+1] - s[i])/s[n]'):
        assert key(wrong)[0] == 'incorrect', wrong
    outcome, note = key('w_i/s_n')
    assert outcome is None and 'w[i] / s[n]' in note                  # equal only by the next line's algebra
    for unclear in ('w_1 + ... + w_i', 's_i - s_i-1', 'P(U < s_i)', 's_{i-1'):
        with pytest.raises(Exception) as e:
            key(unclear)
        assert type(e.value).__name__ == 'Unsupported', unclear       # the standard check makes this undecided
    assert blanks.indexed('s[i-1]')('s_(i-1)') == ('correct', "")
    with pytest.raises(Exception):
        blanks.indexed('s[i')


def test_standard_uniform(pkg):
    entry = pkg('blanks').StandardUniform()
    for good in ('Uniform(0, 1)', 'uniform', 'U(0,1)', 'Unif[0, 1]', 'standard uniform', 'continuous uniform.',
                 'the uniform distribution on (0,1)'):
        assert entry(good) == ('correct', ""), good
    for unsure in ('Uniform(0, s_n)', 'uniform(a, b)', 'U[0, s_n]'):
        assert entry(unsure)[0] is None, unsure
    for bad in ('exponential', 'Exponential(1)', 'normal(0,1)', 'U', 'geometric'):
        assert entry(bad)[0] == 'incorrect', bad


def test_q3_credit_and_review(exam, pkg):
    f = pkg('fixtures')
    got, _ = outcomes(exam, 'q3', f.Q3_ANSWERS)
    assert set(got.values()) == {'correct'}
    assert grade(exam, 'q3', f.OTHER_FORMS['q3']).value is True
    assert grade(exam, 'q3', f.FIVE_OF_SIX['q3']).value is True
    assert grade(exam, 'q3', f.WRONG['q3']).value is False                 # ??6 is required
    assert grade(exam, 'q3', {**f.Q3_ANSWERS, '??5': 'w[i] / s[n]'}).value is True      # five others carry it
    undecided = grade(exam, 'q3', f.REVIEW['q3'])
    assert undecided.value is None and '??5' in undecided.reason
    assert grade(exam, 'q3', {**f.Q3_ANSWERS, '??2': 'w_1 + ... + w_i', '??4': '1'}).value is None
    blank = grade(exam, 'q3', dict.fromkeys(f.Q3_ANSWERS))
    assert blank.value is False and blank.flags == ('blank',)


# --- points: this course's pseudocode conventions ---------------------------------------

def run(pkg, source, entry='f', n=1):
    language = pkg('language')
    draws = language.Draws()
    program = pseudocode.parse(source, language.POINTS)
    return program.sample(entry, n=n, seed=1, rng=draws), draws


def test_point_arithmetic_and_zero_based_indexing(pkg):
    language = pkg('language')
    out, _ = run(pkg, """
def f():
    p = [1, 2]
    q = p + [10, 20]
    q = 2 * q - [1, 1]
    q = q * [1, 3] / 2
    q += [0.5, 0]
    xs = [[5, 6], [7, 8]]
    xs[0] = -xs[1]
    return [q, xs[0][1], rotate([1, 0], pi/2, [0, 0]), rotate([2, 1], pi, [1, 1]), 3 - 1 + 2 * 5]
""")
    q, last, quarter, half, scalar = out[0]
    assert q == [11.0, 64.5] and last == -8 and scalar == 12
    assert quarter == pytest.approx([0, 1]) and half == pytest.approx([0, 1])
    # Every run-time failure, point arithmetic and a primitive's limit included, is a mistake.
    for source, undecided in [("def f():\n    return [1, 2] + [1, 2, 3]\n", False),
                              ("def f():\n    return [1, 2] + 1\n", False),
                              ("def f():\n    return 1 / [1, 2]\n", False),
                              ("def f():\n    return rotate([1, 2, 3], 1, [0, 0])\n", False),
                              ("def f():\n    return None + 1\n", False),
                              ("def f():\n    p = [1, 2]\n    return p[2]\n", False)]:
        with pytest.raises(pseudocode.Failure) as e:
            run(pkg, source)
        assert checks.undecided_by_default(e.value) is undecided, source
    description = language.POINTS.description
    assert "Vectors:" in description and "`weighted_die(w)`" in description and "`rotate(" in description
    assert "from 1 to n" in description and "from 1 to n" in language.SCALAR.description


def test_random_calls_are_counted_per_sample(pkg):
    language = pkg('language')
    source = """
def f():
    k = weighted_die([0, 3, 1])
    total = 0
    for i in range(k):
        total += unif() + coin(0.5) + die(6)
    return [k, total]
"""
    out, draws = run(pkg, source, n=400)
    ks = [k for k, _ in out]
    assert set(ks) == {2, 3} and 0.65 < ks.count(2) / 400 < 0.85
    assert draws.most == 10                                              # 1 + 3 * 3, not the 400-sample total
    over = "def f():\n    for i in range(1001):\n        unif()\n    return 1\n"
    with pytest.raises(pseudocode.Failure) as e:
        run(pkg, over)
    assert e.value.kind == 'limit' and str(language.LIMIT) in str(e.value)
    assert run(pkg, over.replace('1001', '1000'))[1].most == 1000
    # The counting generator draws the same numbers as the default one.
    plain = pseudocode.parse("def f():\n    return unif()\n", language.POINTS)
    assert plain.sample('f', n=5, seed=1) == plain.sample('f', n=5, seed=1, rng=language.Draws())


def test_loops_that_may_hide_accept_reject(pkg):
    language = pkg('language')
    checks = importlib.import_module('examinator_kits.stochastic.checks')

    def why(source, entry='f'):
        return checks.accept_reject(pseudocode.parse(source, language.POINTS), entry)

    assert why("def f():\n    for i in range(3):\n        unif()\n    return g()\ndef g():\n    return 1\n") == ""
    assert why("def f():\n    return g()\ndef g():\n    while unif() < 0.5:\n        pass\n    return 1\n") == "while loop"
    assert why("def f():\n    return 1\ndef unused():\n    while True:\n        pass\n") == ""
    assert why("def f():\n    return g()\ndef g():\n    return f() if coin(0.5) else 1\n") == "recursion"


# --- the samplers --------------------------------------------------------------------------

def test_canonical_samplers_pass_only_their_own_printing(exam, pkg):
    answers = pkg('fixtures').ANSWERS
    for qid in SAMPLERS:
        for printed in ('original', 'alternate'):
            for written in ('original', 'alternate'):
                outcome, note = verdict(exam, qid, answers[written][qid], printed)
                if printed == written:
                    assert outcome == 'correct', (qid, printed, note)
                elif qid == 'q5':                       # the other printing's function name: a person looks
                    assert outcome is None and 'check the printing' in note
                elif (qid, written) == ('q6', 'alternate'):
                    # It calls the supplied f under an envelope that also covers the original's: a
                    # slower but correct sampler. (The original's envelope does not cover the alternate f.)
                    assert outcome == 'correct', note
                else:
                    assert outcome == 'incorrect', (qid, printed, written, note)


Q1_WRONG = {
    'no log': "def exponential(m):\n    return m * unif()\n",
    'sign': "def exponential(m):\n    return m * log(unif())\n",
    'squared': "def exponential(m):\n    return m * log(1 / unif()) ** 2\n",
}

Q4_WRONG = {
    # equal weights: the pieces are not chosen by area
    'weights': lambda a: a.replace('weighted_die([pi, pi/2, 3])', 'weighted_die([1, 1, 1])'),
    # a full disc where the half disc goes
    'half': lambda a: a.replace('return half_scoop() + [3, 2]', 'return scoop() + [3, 2]'),
    # the triangle sampled as a product of uniforms: inside it, but not uniform
    'triangle': lambda a: a.replace('return cone() * [1, 3] + [3, -1]',
                                    'Y = unif()\n        return [3 + (2*unif() - 1) * Y, -1 + 3 * Y]'),
}

Q5_WRONG = {
    'height': lambda a: a.replace('Z = unif()**(1/3)', 'Z = unif()**(1/2)'),
    'disc': lambda a: a.replace('unif()**(1/2), 0]', 'unif(), 0]'),
    'cylinder': lambda a: a.replace('[X, Y] = Z * unit_disc()', '[X, Y] = unit_disc()'),
    'two numbers': lambda a: a.replace('return [X, Y, Z]', 'return [X, Y]'),
}

Q6_WRONG = {
    # a flat envelope that does not cover f near 0
    'flat': lambda a: a.replace('X = 2 * unif()**(5/4)', 'X = 2 * unif()').replace('4 * X**(-1/5)', '4'),
    # the right envelope shape under a constant that is too small
    'low': lambda a: a.replace('unif() * 4 * X', 'unif() * 3 * X'),
    'uniform x': lambda a: a.replace('X = 2 * unif()**(5/4)', 'X = 2 * unif()'),
}


def test_wrong_samplers_are_incorrect(exam, pkg):
    answers = pkg('fixtures').ANSWERS['original']
    for name, source in Q1_WRONG.items():
        assert verdict(exam, 'q1', source)[0] == 'incorrect', name
    for qid, table in (('q4', Q4_WRONG), ('q5', Q5_WRONG), ('q6', Q6_WRONG)):
        for name, change in table.items():
            source = change(answers[qid])
            assert source != answers[qid], (qid, name)
            assert verdict(exam, qid, source)[0] == 'incorrect', (qid, name)


def test_q6_the_envelope_alone_passes_ks_but_not_the_phase_test(exam, pkg):
    """Why the check looks inside a period: the unrejected envelope is within KS noise of f."""
    _, body = outcomes(exam, 'q6', pkg('fixtures').WRONG['q6'], 'original')
    tests = next(b for b in body['blocks'] if b.get('summary') == "Tests")['blocks'][0]['rows']
    assert body['grade']['value'] is False and body['fields']['6']['outcome'] == 'incorrect'
    by_name = {name: outcome for name, outcome, _ in tests}
    assert by_name == {'support': 'correct', 'CDF': 'correct', 'position within a period': 'incorrect'}


def test_undecided_is_not_incorrect(exam, pkg):
    f = pkg('fixtures')
    for qid, source, needle in [
            ('q1', "def exponential(m):\n    return m * [1, 2]\n", 'list'),                       # outside the base language
            ('q4', f.REVIEW['q4'], 'accept-reject'),
            ('q5', f.REVIEW['q5'], 'accept-reject'),
            ('q6', "def spire():\n    import random\n", 'import')]:
        outcome, note = verdict(exam, qid, source)
        assert outcome is None and needle in note, (qid, note)
    # A name nobody provides: the program does not run, so incorrect (a grader may override).
    outcome, note = verdict(exam, 'q1', "def exponential(m):\n    return m * mystery()\n")
    assert outcome == 'incorrect' and 'not defined' in note
    outcome, note = verdict(exam, 'q4', "def ice_cream():\n    p = [3, 4]\n    return p + [0, 1, 2]\n")
    assert outcome == 'incorrect' and 'different lengths' in note
    for qid, source in f.ERRORS.items():                    # the same failure in whole answers
        outcome, note = verdict(exam, qid, source)
        assert outcome == 'incorrect' and 'different lengths (2 and 3)' in note, (qid, note)
    # Over the exam's limit on random calls is a demonstrated mistake, not an exhausted budget.
    slow = "def ice_cream():\n    for i in range(1001):\n        unif()\n    return [3, 4]\n"
    outcome, note = verdict(exam, 'q4', slow)
    assert outcome == 'incorrect' and '1000 random calls' in note
    for qid, source in f.BLANK_CODE.items():
        assert verdict(exam, qid, source)[0] == 'blank'
        assert verdict(exam, qid, "")[0] == 'blank'


def test_q6_uses_the_printed_f_not_a_copied_one(exam, pkg):
    """A miscopied `f` in the parse does not change the verdict: the printed one is supplied."""
    good = pkg('fixtures').ANSWERS['original']['q6']
    miscopied = "def f(x):\n    return 1\n\n" + good
    assert verdict(exam, 'q6', miscopied)[0] == 'correct'


def test_reference_geometry(pkg):
    ice, cone = pkg('ice_cream'), pkg('cone')
    assert ice.REGIONS['original'].areas == pytest.approx([math.pi, math.pi / 2, 3])
    assert ice.REGIONS['alternate'].areas == pytest.approx([math.pi, math.pi, math.pi / 2, 2])
    for region in ice.REGIONS.values():
        p = region.sample(20_000, 7)
        piece = region.piece(p)
        assert (piece >= 0).all()
        share = np.bincount(piece, minlength=len(region.areas)) / len(p)
        assert share == pytest.approx(region.areas / region.areas.sum(), abs=0.015)
    orig, alt = ice.REGIONS['original'], ice.REGIONS['alternate']
    inside = lambda region, *pt: region.piece(np.array([pt], dtype=float))[0] >= 0      # noqa: E731
    assert inside(orig, 3, 4.9) and inside(orig, 3, -0.9) and inside(orig, 2.1, 2.1) and inside(orig, 3.9, 1.9)
    assert not inside(orig, 2.1, 1.0) and not inside(orig, 2.1, 4.9) and not inside(orig, 3, 5.1)
    assert inside(alt, 2, 0) and inside(alt, 2, -3.9) and inside(alt, 1.1, -1.9) and not inside(alt, 1.1, -3)
    for printing, h in cone.HEIGHTS.items():
        x, y, z = cone.reference(h, 20_000, 3).T
        assert (x * x + y * y <= z * z).all() and 0 < z.min() and z.max() < h
        assert np.mean(z) == pytest.approx(0.75 * h, abs=0.01)             # E[z] = 3h/4


def test_spire_density_tables(pkg):
    from scipy import integrate
    for density in pkg('spire').DENSITIES.values():
        total, _ = integrate.quad(density, 0, density.hi, limit=2000, points=[0])
        for x in (0.01, 0.3, 1.0, 1.7):
            part, _ = integrate.quad(density, 0, x, limit=2000, points=[0])
            assert float(density.cdf(x)) == pytest.approx(part / total, abs=1e-5)
        assert density.cdf(-1) == 0 and density.cdf(density.hi + 1) == 1
        pmf = density.phase_pmf
        assert pmf.sum() == pytest.approx(1) and pmf.max() > 1.2 / len(pmf) and pmf.min() < 0.8 / len(pmf)
        assert density(0) == 0 and density(density.hi) == 0 and density(1.0) > 0
