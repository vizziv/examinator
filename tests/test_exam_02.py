"""Exam 2 as an exam package. Everything here is synthetic. Tests that read the
instructor's blank PDFs skip unless `EXAM2_BLANKS` names their directory."""
import hashlib
import importlib
import os
import re
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from examinator import Context, load_exam, synth
from examinator.core.evidence import body_of
from examinator.core.runner import Runner
from examinator.core.workflow import part_status
from examinator.core.workspace import Workspace
from examinator.std import pseudocode

ROOT = Path(__file__).resolve().parents[1]
EXAM = ROOT / 'exams' / 'exam_02'
BLANKS = os.environ.get('EXAM2_BLANKS')
needs_blanks = pytest.mark.skipif(not BLANKS, reason="EXAM2_BLANKS is not set")
S = 'synthetic'


@pytest.fixture(scope='module')
def exam():
    return load_exam(EXAM)


@pytest.fixture(scope='module')
def pkg(exam):
    """The package's modules, by name."""
    return lambda name: importlib.import_module(f'{EXAM.name}.{name}')


def outcomes(exam, qid, parse, variant=None):
    """Run one question's check directly on a parse body: `{label: outcome}`, and the evidence."""
    q = exam.questions[qid]
    body = body_of(q.stage('evidence').fn(Context(
        submission=S, question=q, stage='evidence', variant=variant, inputs={'parse': parse})))
    return {label: f['outcome'] for label, f in body['data']['fields'].items()}, body


def grade(exam, qid, parse, variant=None):
    q = exam.questions[qid]
    _, body = outcomes(exam, qid, parse, variant)
    return q.stage('grade').fn(Context(submission=S, question=q, stage='grade', inputs={'evidence': body}))


# --- the fixtures end to end ------------------------------------------------------------

def test_selftest(exam, tmp_path):
    assert synth.selftest(exam, tmp_path / 'ws') == []


def test_definition(exam):
    assert list(exam.questions) == [f'q{i}' for i in range(1, 13)]
    assert exam.pages == 28 and exam.cover_pages == 1 and exam.scan_window == (60, 55, 920, 825)
    q = exam.questions
    assert not q['q11'].variants
    for qid in ('q7', 'q9'):
        assert list(q[qid].result) == ['answer', 'check']
        assert q[qid].result['check'].counts and not q[qid].result['answer'].counts
    assert list(q['q8'].result) == ['check', 'shiny'] and not q['q8'].result['shiny'].counts
    assert not any(spec.counts for qid in ('q10', 'q11', 'q12') for spec in q[qid].result.values())
    for question in q.values():
        used = {f.region.page for f in question.fields}
        assert used <= set(question.pages) <= set(question.export_pages), question.id
        if question.variants:       # two cues that can agree, however many are declared
            voting = [c for c in question.cues if c.fn is not None] or list(question.cues)
            assert len(voting) == 2, question.id
    # Question 1 is printed four ways but has one key: its check does not wait for the printing.
    assert len(q['q1'].variants) == 4 and not q['q1'].stage('evidence').uses_variant
    assert all(q[qid].stage('evidence').uses_variant for qid in q if qid not in ('q1', 'q11'))


# --- printings --------------------------------------------------------------------------

def test_versions_table(pkg):
    v = pkg('versions')
    assert set(v.VERSIONS) == set(range(8))
    for alt, table in v.VERSIONS.items():
        b1, b2, b4 = bool(alt & 1), bool(alt & 2), bool(alt & 4)
        assert table['q8'] == v.STATES[b1] and table['q9'] == v.STATES[b2]
        assert {table[q] for q in ('q2', 'q7', 'q10', 'q12')} == {v.STATES[b4]}
        assert table['q1'] == table['q3'] == ('left' if b1 else 'right') + ('+1' if b4 else '')
        assert table['q5'] == ('means' if b1 else 'totals') + ('/coordinate' if b4 else '/distance')
        assert table['q6'] == ('rising' if b2 else 'falling') + ('/half' if b4 else '/third')
    assert [v.VERSIONS[alt]['q4'] for alt in range(8)] == list('aabbcdcd')
    assert v.readings(0) == {'q5_totals': 'totals_all', 'q8_first': 'unequal', 'q6_density': '3/6',
                             'q9_variance': '4', 'q2_animal': 'koala', 'q7_quantile': 'median'}
    assert v.readings(7)['q7_quantile'] == 'upper quartile' and v.readings(2)['q9_variance'] == '9'


PRINTED = {     # cue -> (printed page, pattern, the text that marks the original printing)
    'q5_totals': (10, r'(totals_\w+) = ', 'totals_all'),
    'q8_first': (16, r'8\.1 Suppose 𝑚1 (≠|=) 𝑚2', '≠'),
    'q6_density': (12, r'(\d)\s+6\s+if 0 ≤ 𝑟 < 1', '3'),
    'q9_variance': (18, r'sample variance of (\d+)', '4'),
    'q2_animal': (4, r'function (\w+)\(\)', 'koala'),
    'q7_quantile': (14, r'its unknown (median|upper quartile)', 'median'),
}


@needs_blanks
def test_versions_match_the_printed_blanks(exam, pkg):
    """The alt index -> variants table agrees with the text and the pixels of each blank."""
    import pypdfium2
    v = pkg('versions')
    pages = {}      # alt -> printed page -> hash of its rendering
    for alt in v.VERSIONS:
        doc = pypdfium2.PdfDocument(str(v.blank(alt)))
        try:
            assert len(doc) == exam.pages
            for bit, cues in v.CUES.items():
                for cue in cues:
                    page, pattern, original = PRINTED[cue.name]
                    assert cue.region.page == page
                    found = re.search(pattern, doc[page - 1].get_textpage().get_text_range())
                    assert found, (alt, cue.name)
                    assert (found[1] != original) == bool(alt & bit), (alt, cue.name)
            pages[alt] = {p: hashlib.sha256(doc[p - 1].render(scale=0.5).to_pil().tobytes()).hexdigest()
                          for p in range(1, exam.pages + 1)}
        finally:
            doc.close()
    # Two printings show a question's pages identically exactly when they share its variant.
    for qid, q in exam.questions.items():
        seen = {}
        for alt in v.VERSIONS:
            look = tuple(pages[alt][p] for p in q.export_pages)
            seen.setdefault(v.VERSIONS[alt].get(qid), set()).add(look)
        assert all(len(looks) == 1 for looks in seen.values()), qid
        assert len({next(iter(looks)) for looks in seen.values()}) == len(seen), qid
    assert len({pages[alt][p] for alt in v.VERSIONS for p in exam.front + exam.extra}) == len(exam.front + exam.extra)


def test_cues_combine_into_a_printing(exam, pkg, tmp_path):
    """Question 5 depends on two bits: both pairs of cues must agree, and a person can choose."""
    f, v = pkg('fixtures'), pkg('versions')
    answers = {'q5': f.ANSWERS['q5'][v.VERSIONS[5]['q5']]}
    cases = [synth.Case('agree', variants=v.VERSIONS[5], answers=answers, readings=v.readings(5)),
             synth.Case('split', variants=v.VERSIONS[5], answers=answers,
                        readings={**v.readings(5), 'q7_quantile': 'median'})]
    root = synth.generate(exam, tmp_path / 'ws', cases)
    ws = Workspace(root, exam)
    ws.sync_roster()
    runner = Runner(exam, ws, model=synth.fake_model(root))
    try:
        q = exam.questions['q5']
        for sid in ('agree', 'split'):
            assert all(o.ok for o in runner.run_from(sid, 'q5', 'variant'))
            runner.verify(sid, 'q5', 'parse', author='ta', expect_rev=ws.store.get(sid, 'q5', 'parse').rev)
        body = ws.store.get('agree', 'q5', 'variant').body
        assert (body['resolved'], body['agree']) == ('means/coordinate', 2)
        assert body['cues']['q5_totals'] == {'reading': 'totals_few', 'variant': None}      # read, but no vote
        assert body['cues']['q5_first']['variant'] == body['cues']['q5_second']['variant'] == 'means/coordinate'
        st = part_status(q, '', ws.store.for_question('agree', 'q5'))
        assert (st.state, st.value) == ('graded', True)

        body = ws.store.get('split', 'q5', 'variant').body
        assert body['resolved'] is None and body['candidates'] == []
        assert body['cues']['q5_second']['variant'] == 'means/distance'
        st = part_status(q, '', ws.store.for_question('split', 'q5'))
        assert (st.value, st.counts, st.reason) == (None, False, "variant unresolved")
        runner.choose_variant('split', 'q5', 'means/coordinate', author='ta')
        runner.recheck('split', 'q5')
        st = part_status(q, '', ws.store.for_question('split', 'q5'))
        assert (st.state, st.value) == ('graded', True)
    finally:
        runner.close()
        ws.close()


# --- key entries ------------------------------------------------------------------------

def test_number_and_point_entries(pkg):
    keys = pkg('keys')
    quarter = keys.number('1/4')
    assert quarter('0.25') == ('correct', "") and quarter('1/3')[0] == 'incorrect'
    assert quarter('None')[0] == 'incorrect'                # "no value works" is an answer, and a wrong one
    assert keys.split('[1/4, 1/3]') == ['1/4', '1/3'] and keys.split('(sqrt(2), (1+1)/2)') == ['sqrt(2)', '(1+1)/2']
    assert keys.split('(1) + (2), 3') == ['(1) + (2)', '3'] and keys.split('7') == ['7']
    p = keys.point('-2', '1')
    assert p('[-2, 1]')[0] == p('(−2, 2/2)')[0] == p('-2, 1')[0] == 'correct'
    assert p('[2, 1]')[0] == p('[1, -2]')[0] == p('-2')[0] == p('none')[0] == 'incorrect'
    with pytest.raises(Exception):
        p('[a, 1]')                                         # unknown symbol: undecided, via the check
    assert p.label == '[-2, 1]'


def test_option_entry(pkg):
    keys = pkg('keys')
    fourth = keys.Option(4, ('<= 2 delta',))
    for text in ("It's ≤ 2δ", "it’s <= 2 delta", '≤2δ', '<= 2*delta', '4', 'option 4', 'D', '(d)'):
        assert fourth(text) == 'correct', text
    for text in ("It's ≤ δ", '<= delta', '>= 1 - 2 delta', '2', 'None of the above'):
        assert fourth(text) == 'incorrect', text


def test_either_order_and_question_2(exam, pkg):
    f = pkg('fixtures')
    right = f.ANSWERS['q2']['original']
    got, body = outcomes(exam, 'q2', right, 'original')
    assert set(got.values()) == {'correct'} and list(got) == f.P8
    swapped = {**right, '??_6': '[2, 1]', '??_7': '(-2, 1)'}
    assert set(outcomes(exam, 'q2', swapped, 'original')[0].values()) == {'correct'}
    twice = {**right, '??_7': '[-2, 1]'}                    # the same ear twice: one of the two is wrong
    got, _ = outcomes(exam, 'q2', twice, 'original')
    assert (got['??_6'], got['??_7']) == ('correct', 'incorrect')
    got, _ = outcomes(exam, 'q2', {**right, '??_6': None, '??_7': '[-2, 1]'}, 'original')
    assert (got['??_6'], got['??_7']) == ('blank', 'correct')
    got, _ = outcomes(exam, 'q2', {**right, '??_6': '[REVIEW: smudged]'}, 'original')
    assert got['??_6'] is None and got['??_7'] is None
    got, _ = outcomes(exam, 'q2', right, 'alternate')       # the other animal: radii, share and ears differ
    assert [label for label, o in got.items() if o == 'incorrect'] == ['??_1', '??_3', '??_5', '??_6', '??_7']
    assert "either order" in str(body['blocks'])
    # At least 7 of 8; an undecided field holds the grade open only when it could tip it.
    assert grade(exam, 'q2', {**right, '??_5': '1/2'}, 'original') == 'correct'
    assert grade(exam, 'q2', {**right, '??_5': '1/2', '??_2': '2'}, 'original') == 'incorrect'
    assert grade(exam, 'q2', {**right, '??_5': '1/2', '??_2': 'k'}, 'original') is None
    assert grade(exam, 'q2', {**right, '??_2': 'k'}, 'original') == 'correct'
    assert grade(exam, 'q2', dict.fromkeys(right), 'original') == 'blank'


DENSITY = {'falling': [Fraction(3, 6), Fraction(2, 6), Fraction(1, 6)],
           'rising': [Fraction(1, 6), Fraction(2, 6), Fraction(3, 6)]}
LEVELS = {'third': (Fraction(1, 3), Fraction(5, 6)), 'half': (Fraction(1, 2), Fraction(2, 3))}


def narrowest(density, level, step=Fraction(1, 12)):
    """Brute force over a grid: (least width, lowest start, highest start) of a noise
    interval [c, c + w] inside [0, 3] that holds at least `level` of the mass."""
    def mass(lo, hi):
        return sum(d * max(Fraction(0), min(hi, i + 1) - max(lo, i)) for i, d in enumerate(density))
    grid = [i * step for i in range(int(3 / step) + 1)]
    fits = [(hi - lo, lo) for lo in grid for hi in grid if hi > lo and mass(lo, hi) >= level]
    width = min(w for w, _ in fits)
    starts = [lo for w, lo in fits if w == width]
    return width, min(starts), max(starts)


def test_interval_keys_are_the_narrowest_intervals(exam, pkg):
    keys = pkg('keys')
    by_variant = exam.questions['q6'].stage('evidence').fn.key
    assert set(by_variant) == {f'{d}/{lv}' for d in DENSITY for lv in LEVELS}
    for variant, key in by_variant.items():
        shape, levels = variant.split('/')
        for entry, level in zip(key.values(), LEVELS[levels]):
            want = narrowest(DENSITY[shape], level)
            assert tuple(Fraction(t) for t in (entry.width, entry.lo, entry.hi)) == want, (variant, level)
    assert keys.Interval('2/3', '0', '1/3').label == "b - a = 2/3, -1/3 <= b <= 0"
    assert keys.Interval('2', '1', '1').label == "a = -3, b = -1"


def test_question_6(exam, pkg):
    f = pkg('fixtures')
    for variant, answers in f.ANSWERS['q6'].items():
        assert set(outcomes(exam, 'q6', answers, variant)[0].values()) == {'correct'}, variant
    base = f.ANSWERS['q6']['falling/third']

    def first(a, b):
        got, _ = outcomes(exam, 'q6', {**base, '6.1 a': a, '6.1 b': b}, 'falling/third')
        return got['6.1 a'], got['6.1 b']

    assert first('-1', '-1/3') == first('-5/6', '-1/6') == ('correct', 'correct')       # any equally narrow one
    assert first('-1', '0') == ('incorrect', 'incorrect')                               # each in range, too wide
    assert first('-2/3', '1/3') == ('correct', 'incorrect')
    assert first('0', '2/3') == ('incorrect', 'incorrect')                              # the noise's sign forgotten
    assert first(None, '0') == ('blank', 'correct')
    assert first('x', '0') == (None, 'correct')
    assert first('None', '0') == ('incorrect', 'correct')
    assert grade(exam, 'q6', base, 'falling/third') == 'correct'
    assert grade(exam, 'q6', base, 'rising/third') == 'incorrect'


# --- question 3 -------------------------------------------------------------------------

def test_every_accepted_form_has_the_right_law(pkg):
    """The forms the key accepts are checked by the grid test, for all four printings."""
    lshape = pkg('lshape')
    for name, shape in lshape.SHAPES.items():
        xs, ys = lshape.x_forms(shape), lshape.y_forms(shape)
        assert len(set(xs)) == 8 and len(set(ys)) == 4
        assert all(lshape.x_valid(shape, form) for form in xs), name
        assert all(lshape.y_valid(shape, form) for form in ys), name
        other = lshape.SHAPES[{'right': 'left', 'left': 'right', 'right+1': 'left+1', 'left+1': 'right+1'}[name]]
        assert not any(lshape.x_valid(other, form) for form in xs), name
        assert not any(lshape.y_valid(other, form) for form in ys), name
    right = lshape.SHAPES['right']
    assert ('1/2', '4*u', '2*u + 1') in lshape.x_forms(right) and ('2', 'v', '2*v') in lshape.y_forms(right)
    assert not lshape.x_valid(right, ('1/2', '4*u', '2*u'))             # lands on [1, 2] instead of [2, 3]
    assert not lshape.x_valid(right, ('1/3', '6*u', '3*u/2 + 3/2'))     # right support, wrong weights
    assert not lshape.y_valid(right, ('2', 'v', 'v'))
    assert not lshape.y_valid(right, ('2', 'v', '2*v**2'))              # right support, not uniform
    assert not lshape.x_valid(right, ('1/2', '1/(u - u)', '2*u + 1'))   # not a number


def test_evaluate(pkg):
    lshape = pkg('lshape')
    from examinator.std import math
    u = np.array([0.25, 0.5])
    expr = math.parse_expr('max(2u, 1) + sqrt(u) - abs(u - 1/2)/3 + pi - e^u', symbols=['u'])
    want = np.maximum(2 * u, 1) + np.sqrt(u) - np.abs(u - 0.5) / 3 + np.pi - np.e ** u
    assert np.allclose(lshape.evaluate(expr, {'u': u}), want)
    with pytest.raises(math.Unsupported):
        lshape.evaluate(math.parse_expr('u + v', symbols=['u', 'v']), {'u': u})


def test_question_3(exam, pkg):
    f = pkg('fixtures')
    for variant, answers in f.ANSWERS['q3'].items():
        assert set(outcomes(exam, 'q3', answers, variant)[0].values()) == {'correct'}, variant
    right = f.ANSWERS['q3']['right']

    def q3(**changed):
        answers = {**right, **{f'??_{k[1:]}': v for k, v in changed.items()}}
        got, _ = outcomes(exam, 'q3', answers, 'right')
        return list(got.values()), grade(exam, 'q3', answers, 'right')

    ok = 'correct'
    assert q3() == ([ok] * 6, 'correct')
    assert q3(p2='2 + 2u', p3='4u - 2') == ([ok] * 6, 'correct')                    # branches swapped
    assert q3(p2='2 - 4u', p5='1 - v') == ([ok] * 6, 'correct')                     # run downward
    assert q3(p5='abs(1 - 2v)', p6='2 max(v, 1 - v) - 2 min(v, 1 - v)')[1] == 'correct'    # no listed form: right law
    # A swapped form with one slip is scored against the swapped form: 5 of 6.
    assert q3(p2='2 + 2u', p3='4u') == ([ok, ok, 'incorrect', ok, ok, ok], 'correct')
    assert q3(p3='2u', p6='v') == ([ok, ok, 'incorrect', ok, ok, 'incorrect'], 'incorrect')
    assert q3(p1=None)[0][0] == 'blank' and q3(p1=None)[1] == 'correct'
    assert q3(p2='4w')[0][1] is None and q3(p2='4w')[1] == 'correct'                # unknown symbol: open, cannot tip it
    assert q3(p2='4w', p6='v')[1] is None
    assert q3(p2='[REVIEW: 4u or 4v]')[0][:3] == [None, None, None]
    assert grade(exam, 'q3', right, 'left') == 'incorrect'


# --- grade rules ------------------------------------------------------------------------

def evidence(**outcomes_):
    data = {label: {'outcome': o} for label, o in outcomes_.items()}
    return SimpleNamespace(outcomes=lambda: [f['outcome'] for f in data.values()],
                           outcome=lambda label: data[label]['outcome'])


def test_rules(pkg):
    rules = pkg('rules')
    c, i, b = 'correct', 'incorrect', 'blank'
    two = rules.at_least(2)
    assert two(evidence(a=c, b=c, c=i)) == 'correct' and two(evidence(a=c, b=i, c=b)) == 'incorrect'
    assert two(evidence(a=c, b=None, c=i)) is None and two(evidence(a=None, b=i, c=b)) == 'incorrect'
    assert two(evidence(a=b, b=b, c=b)) == 'blank'
    tiers = rules.tiers(check=['a', 'b'], shiny=['a', 'b', 'c'])
    assert tiers(evidence(a=c, b=c, c=i)) == {'check': 'correct', 'shiny': 'incorrect'}
    assert tiers(evidence(a=c, b=c, c=b)) == {'check': 'correct', 'shiny': 'incorrect'}
    assert tiers(evidence(a=c, b=c, c=None)) == {'check': 'correct', 'shiny': None}
    assert tiers(evidence(a=b, b=b, c=b)) == {'check': 'blank', 'shiny': 'blank'}
    worked = rules.with_work('check')
    got = worked(evidence(a=c, b=c))
    assert got['answer'] == 'correct' and (got['check'].value, got['check'].reason) == (None, rules.WORK)
    assert worked(evidence(a=c, b=i)) == {'answer': 'incorrect', 'check': 'incorrect'}
    assert worked(evidence(a=b, b=b)) == {'answer': 'blank', 'check': 'blank'}
    assert worked(evidence(a=c, b=None)) == {'answer': None, 'check': None}


def test_work_is_decided_by_a_person(exam, pkg, tmp_path):
    f, v = pkg('fixtures'), pkg('versions')
    case = synth.Case(S, variants=v.VERSIONS[0], answers={'q7': f.ANSWERS['q7']['original']}, readings=v.readings(0))
    root = synth.generate(exam, tmp_path / 'ws', [case])
    ws = Workspace(root, exam)
    ws.sync_roster()
    runner = Runner(exam, ws, model=synth.fake_model(root))
    try:
        q = exam.questions['q7']
        assert all(o.ok for o in runner.run_from(S, 'q7', 'variant'))
        runner.verify(S, 'q7', 'parse', author='ta', expect_rev=ws.store.get(S, 'q7', 'parse').rev)
        answer, check = (part_status(q, part, ws.store.for_question(S, 'q7')) for part in ('answer', 'check'))
        assert (answer.state, answer.value) == ('graded', True)
        assert (check.state, check.value, check.reason) == ('needs_review', None, "judge the work")
        runner.decide(S, 'q7', 'check', True, author='ta')
        check = part_status(q, 'check', ws.store.for_question(S, 'q7'))
        assert (check.state, check.value, check.counts) == ('manual', True, True)
    finally:
        runner.close()
        ws.close()


def test_choice_questions(exam, pkg):
    f = pkg('fixtures')
    for qid in ('q4', 'q5', 'q8'):
        for variant, answers in f.ANSWERS[qid].items():
            for other in f.ANSWERS[qid]:
                got = set(outcomes(exam, qid, answers, other)[0].values())
                assert (got == {'correct'}) == (other == variant or f.ANSWERS[qid][other] == answers), (qid, variant)
    assert grade(exam, 'q8', f.WRONG['q8'], 'original') == {'check': 'correct', 'shiny': 'incorrect'}
    assert outcomes(exam, 'q5', {'5.1': '100', '5.2': '10^(0)', '5.3': '10²'}, 'totals/distance')[0] == \
        {'5.1': 'correct', '5.2': 'correct', '5.3': None}
    got, _ = outcomes(exam, 'q1', {'1.1': 'None', '1.2': '.5', '1.3': '2 + 2', '1.4': '1/4, 1/3', '1.5': '[0, -1]'})
    assert got == {'1.1': 'incorrect', '1.2': 'correct', '1.3': 'correct', '1.4': 'correct', '1.5': 'incorrect'}


# --- pseudocode -------------------------------------------------------------------------

def test_point_arithmetic_powers_and_die(pkg):
    samplers = pkg('samplers')
    checks = importlib.import_module('examinator_kits.stochastic.checks')

    def run(source, n=1):
        program = pseudocode.parse(samplers.powers(source), samplers.LANGUAGE)
        return samplers.draw(program, 'f', n=n, seconds=5)

    def stopped(source):
        with pytest.raises(checks.Stop) as stop:
            run(source)
        return stop.value

    source = """\
def f():
    # points are lists
    p = [1, 2] + [10, 20]
    p -= [1, 1]
    q = 2 * p / 4
    k = 3 + 2^2 * 2
    k += 1
    return [q, -1/2 * k^2, die(3)]
"""
    assert samplers.powers(source).count('\n') == source.count('\n')      # line numbers are the student's own
    (q, k, d), = run(source)
    assert (q, k) == ([5.0, 10.5], -72.0) and d in (0, 1, 2)
    assert {v[2] for v in run(source, n=200)} == {0, 1, 2}                 # die(n) is 0 .. n-1 on this exam

    # Points times points and negated points work coordinate by coordinate, as on Exam 1.
    assert run("def f():\n    p = [1, 2]\n    return [p * [3, 4], -p, [6, 8] / p, -(2 * p)]\n") == \
        [[[3, 8], [-1, -2], [6, 4], [-2, -4]]]
    # Failures are reported at the line of the source as written.
    stop = stopped("def f():\n    # a comment\n\n    x = [1, 2]\n    return log(x[0] - 1)\n")
    assert stop.outcome == 'incorrect' and stop.note.startswith("line 5: ")
    # Point arithmetic the checker cannot do proves nothing about the student: undecided.
    for body in ("x = [1, 2]\n    return x + 1", "return [1, 2] + [1, 2, 3]"):
        stop = stopped(f"def f():\n    {body}\n")
        assert stop.outcome is None and stop.error.kind == 'type'
    stop = stopped("def f():\n\n    return polar(1, 2)\n")         # a helper the exam does not provide: incorrect
    assert stop.outcome == 'incorrect' and stop.note == "line 3: polar(1, 2): function 'polar' is not defined"
    assert stopped("def f():\n    return None + 1\n").outcome == 'incorrect'     # not point arithmetic
    with pytest.raises(pseudocode.Unsupported) as err:
        pseudocode.parse("def f():\n\n\n    import os\n", samplers.LANGUAGE)
    assert err.value.line == 4
    description = samplers.LANGUAGE.description
    assert "from 0 to n - 1" in description and "from 1 to n" not in description
    assert "`rotate(point, angle, center)`" in description and "Vectors:" in description
    assert samplers.rotate([1, 0], np.pi / 2, [0, 0]) == pytest.approx([0, 1])
    assert samplers.rotate([2, 1], np.pi, [1, 1]) == pytest.approx([0, 1])
    assert run("def f():\n    return rotate([1, 0], pi, [0, 0])\n")[0] == pytest.approx([-1, 0])


def test_snow_cone_regions(pkg):
    cone = pkg('snow_cone')
    for name, region in cone.REGIONS.items():
        ref = region.reference(20_000)
        assert region.inside(ref[:, 0], ref[:, 1]).all()
        # Corners of the triangle and the top of the arc are in; just outside them is out.
        top = region.center[1] + region.radius
        for x, y in [(1, 3), (3, 3), (2, 0), (2, top), region.center]:
            assert region.inside(np.float64(x), np.float64(y)), (name, x, y)
        for x, y in [(0.99, 3), (2, -0.01), (2, top + 0.01), (1.2, 3.2), (2.9, 2.5), (1.5, 1.4)]:
            assert not region.inside(np.float64(x), np.float64(y)), (name, x, y)
        # Area by Monte Carlo against sector + triangle - overlap.
        box = np.random.default_rng(1).uniform((1, 0), (3, top), size=(200_000, 2))
        area = region.inside(box[:, 0], box[:, 1]).mean() * 2 * top
        overlap = 3 - region.center[1]          # the covered triangle has height and area 3 - cy
        assert area == pytest.approx(region.half_angle * region.radius ** 2 + 3 - overlap, abs=0.03), name


def q10(exam, source, variant='original'):
    got, body = outcomes(exam, 'q10', source, variant)
    f = body['data']['fields']['snow_cone']
    return f['outcome'], f['note']


def test_question_10(exam, pkg):
    f = pkg('fixtures')
    for variant, source in f.Q10.items():
        assert q10(exam, source, variant) == ('correct', "")
    outcome, note = q10(exam, f.Q10['original'], 'alternate')
    assert outcome == 'incorrect' and "outside the region" in note
    assert q10(exam, f.Q10['alternate'], 'original')[0] == 'incorrect'      # inside the region, not uniform
    assert q10(exam, f.Q10_HALF)[0] == 'incorrect'
    assert q10(exam, f.Q10_LOOP) == (None, "passes, but loops: accept-reject?")
    recursive = "def snow_cone():\n    X = 1 + 2 * unif()\n    Y = 3 * unif()\n" \
                "    if Y >= 3 * abs(X - 2):\n        return [X, Y]\n    return snow_cone()\n"
    assert q10(exam, recursive)[0] == 'incorrect'           # the triangle alone: wrong whatever the method
    assert q10(exam, "def snow_cone():\n    return unif()\n") == ('incorrect', "a sample is not a point [x, y]")
    assert q10(exam, "def snow_cone():\n    return [2, 1] + polar(1)\n") == \
        ('incorrect', "line 2: function 'polar' is not defined")
    assert q10(exam, "def snow_cone():\n    return [2, 1 +\n")[0] is None
    assert q10(exam, "def snow_cone():\n    pass\n") == ('blank', "") == q10(exam, "")
    tuples = f.Q10['original'].replace('return [2, 0] + [X, Y]', 'return 2 + X, Y')
    assert q10(exam, tuples) == ('correct', "")             # a tuple is a point too


def q11(exam, source):
    got, body = outcomes(exam, 'q11', source)
    f = body['data']['fields']['standard_normal']
    return f['outcome'], f['note']


def test_question_11(exam, pkg):
    f = pkg('fixtures')
    assert q11(exam, f.Q11) == ('correct', "")
    only = f.Q11[f.Q11.index('def standard_normal'):]
    assert q11(exam, only) == ('correct', "")               # the printed g(t) is supplied if the parse left it out
    assert q11(exam, f.Q11.replace('2 * Y', '100 * Y')) == ('correct', "")      # a wasteful constant still passes
    assert q11(exam, f.Q11_ONE_SIDED)[0] == 'incorrect'
    assert q11(exam, f.Q11.replace('2 * Y', '1 * Y'))[0] == 'incorrect'         # the envelope is too low
    box_muller = "def standard_normal():\n    return sqrt(-2 * log(1 - unif())) * cos(2 * pi * unif())\n"
    assert q11(exam, box_muller) == ('correct', "")
    assert q11(exam, "def standard_normal():\n    return [unif(), unif()]\n")[0] == 'incorrect'
    assert q11(exam, "def standard_normal():\n    while True:\n        X = unif()\n")[0] == 'incorrect'    # never returns
    assert q11(exam, f.Q11_BLANK) == ('blank', "")


# --- answer keys never reach the model --------------------------------------------------

def test_no_answer_key_in_any_prompt(exam, pkg, tmp_path):
    f, v = pkg('fixtures'), pkg('versions')
    cases = [synth.Case(f'v{alt}', variants=v.VERSIONS[alt], answers=f.canonical(alt), readings=v.readings(alt))
             for alt in (0, 7)]
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
    assert {c.tag[2] for c in model.calls} == {'variant', 'transcript', 'parse'}
    prompts = "\n".join(c.prompt for c in model.calls)
    secrets = ['sqrt(5)', '27/64', '15/2', '[1/4', '1/3]', '2 delta', '2δ', 'None of the above', 'none of the above',
               '4*u', '2*u', '4u', '2v', 'hypot', 'norm.cdf', 'projected', 'half_angle', 'sqrt(2)', 'sqrt(3)',
               'exp(-X)', 'coin(1/2)', 'vee', 'sector']
    for qid in ('q2', 'q3', 'q6', 'q7', 'q8'):
        secrets += [a for answers in f.ANSWERS[qid].values() for a in answers.values() if len(a) > 3]
    for secret in secrets:
        assert secret not in prompts, secret
    # Short keys: no line of their own question's prompts holds one as a token, labels aside.
    for qid in f.FIELDS:
        q = exam.questions[qid]
        own = "\n".join(line for c in model.calls if c.tag[1] == qid and c.tag[2] != 'variant'
                        for line in c.prompt.splitlines() if not line.startswith(("Answer labels:", "Question:")))
        values = {a for answers in f.ANSWERS[qid].values() for a in answers.values() if len(a) <= 3}
        values -= {'v'} if qid == 'q3' else set()       # question 3's note names the variables u, v and x
        for value in values:
            assert not re.search(rf'(?<![\w./^-]){re.escape(value)}(?![\w./^])', own), (qid, value)
        assert q.labels and "Answer labels:" in "\n".join(c.prompt for c in model.calls if c.tag[1] == qid)
