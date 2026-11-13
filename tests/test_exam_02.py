"""Exam 2 as an exam package. Everything here is synthetic. Tests that read the
instructor's blank PDFs skip unless `EXAM2_BLANKS` names their directory."""
import hashlib
import importlib
import os
import re
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from examinator import Grade, Printed, Undecided, load_exam, synth
from examinator.core.context import CheckContext
from examinator.core.runner import Runner, try_check
from examinator.core.workspace import Workspace
from examinator.std import math, pseudocode

ROOT = Path(__file__).resolve().parents[1]
EXAM = ROOT / 'exams' / 'exam_02'
BLANKS = os.environ.get('EXAM2_BLANKS')
needs_blanks = pytest.mark.skipif(not BLANKS, reason="EXAM2_BLANKS is not set")
S = 'synthetic'
FRONT, EXTRA = (1,), (26, 27, 28)       # the front page and the extra-space pages


def extent(qid):
    """A question's printed pages: question k is on pages 2k and 2k + 1."""
    k = int(qid[1:])
    return {2 * k, 2 * k + 1}


@pytest.fixture(scope='module')
def exam():
    return load_exam(EXAM)


@pytest.fixture(scope='module')
def pkg(exam):
    """The package's modules, by name."""
    return lambda name: importlib.import_module(f'{EXAM.name}.{name}')


def info(alt):
    """The `info` values of printing `alt`: each printed field's value."""
    v = importlib.import_module(f'{EXAM.name}.versions')
    return {f.label: v.STATES[bool(alt & b)] for b, pair in v.FIELDS.items() for f in pair}


def values(exam, qid, answers, printing=None, body=None):
    """Field values for a check: the question's answers (`{label: text or None}`, or
    the source of its one code field), and `info` read as an alt index whose printing
    of `qid` is `printing` (or as `body`; or not read)."""
    v = importlib.import_module(f'{EXAM.name}.versions')
    answers = answers if isinstance(answers, dict) else {exam.questions[qid].written[0].label: answers}
    out = {f'{qid}/{label}': '' if text is None else text for label, text in answers.items()}
    if printing is not None:
        body = info(next(alt for alt, table in v.VERSIONS.items() if table.get(qid) == printing))
    out.update({f'info/{label}': text for label, text in (body or {}).items()})
    return out


def outcomes(exam, qid, answers, printing=None, check=None):
    """Run one of a question's checks (its first by default) directly on answers,
    with `info` read so that the question is on `printing`: `{label: outcome}`, and
    the result."""
    check = check or exam.questions[qid].checks[0].name
    body = try_check(exam, qid, check, values(exam, qid, answers, printing))
    return {label: f['outcome'] for label, f in body['fields'].items()}, body


def outcome(g: Grade):
    """A Bool grade as the outcome it came from."""
    return 'blank' if 'blank' in g.flags else {True: 'correct', False: 'incorrect', None: None}[g.value]


def grade(exam, qid, answers, printing=None, check=None):
    """The outcome a check grades the answers: 'correct', 'incorrect', 'blank' or None."""
    return outcome(Grade.from_json(outcomes(exam, qid, answers, printing, check)[1]['grade']))


def status(runner, sub, qid, check='check'):
    q = runner.ws.exam.questions[qid]
    return runner.sheet(sub).status(runner.ws.exam, q, q.check(check))


def read_and_verify(runner, sub, questions):
    """Read the questions' fields, verify every value as a grader would, and run their checks."""
    for qid in questions:
        assert all(o.ok for o in runner.read(sub, qid))
    for k, v in runner.values(sub).items():
        runner.verify(sub, *k.split('/'), author='ta', expect_rev=v.rev)
    runner.refresh(sub, questions=list(questions))


# --- the fixtures end to end ------------------------------------------------------------

def test_selftest(exam, tmp_path):
    assert synth.selftest(exam, tmp_path / 'ws') == []


def test_definition(exam, pkg):
    v = pkg('versions')
    assert list(exam.questions) == ['info'] + [f'q{i}' for i in range(1, 13)]
    assert exam.pages == 28 and exam.print_area == (0, 0, 1000, 1000)
    q = exam.questions
    assert q['info'].checks == () and q['info'].read is None
    assert all(isinstance(f.read, Printed) for f in q['info'].fields)
    assert [f.label for f in q['info'].fields] == [f.label for pair in v.FIELDS.values() for f in pair]
    assert {f.region.page for f in q['info'].fields} == set(q['info'].pages)

    def names(qid):
        return [c.name for c in q[qid].checks]

    for qid in ('q7', 'q9'):
        assert names(qid) == ['answer', 'check'] and q[qid].columns() == [f'{qid}.answer', f'{qid}.check']
        assert q[qid].check('check').points.counts and not q[qid].check('answer').points.counts
    assert names('q8') == ['check', 'shiny'] and not q['q8'].check('shiny').points.counts
    assert names('q12') == ['answer', 'shiny']
    assert not any(c.points.counts for qid in ('q10', 'q11', 'q12') for c in q[qid].checks)
    assert all(names(qid) == ['check'] for qid in q if qid not in ('info', 'q7', 'q8', 'q9', 'q12'))
    # q8's 'check' reads only the two answers it judges; 'shiny' all four.
    assert {label for k in q['q8'].check('check').fn.key.values() for label in k} == {'8.1', '8.2'}
    assert {label for k in q['q8'].check('shiny').fn.key.values() for label in k} == {'8.1', '8.2', '8.3', '8.4'}
    for question in q.values():
        if question.id == 'info':
            continue
        used = {f.region.page for f in question.fields}
        assert used <= set(question.pages) <= extent(question.id), question.id
    # Each question with several keys picks one by the printing read from info, with a key per printing.
    # Question 1 is printed four ways but has one key: its check reads nothing.
    picking = {qid for qid in q if any(getattr(c.fn, 'by', None) is not None for c in q[qid].checks)}
    assert picking == set(v.PRINTINGS) - {'q1'} - {'q10'}
    for qid in picking:
        for c in q[qid].checks:
            if c.fn is None:
                continue
            assert set(c.fn.key) == set(v.names(qid)), qid
            assert c.fn.by == v.printing(qid), qid
    assert v.printing('q10') == v.bit(4) and v.printing('q4') == v.Printing('q4')


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


PRINTED = {     # printed field -> (pattern, the text that marks the original printing)
    'q5_totals': (r'(totals_\w+) = ', 'totals_all'),
    'q8_first': (r'8\.1 Suppose 𝑚1 (≠|=) 𝑚2', '≠'),
    'q6_density': (r'(\d)\s+6\s+if 0 ≤ 𝑟 < 1', '3'),
    'q9_variance': (r'sample variance of (\d+)', '4'),
    'q2_animal': (r'function (\w+)\(\)', 'koala'),
    'q7_quantile': (r'its unknown (median|upper quartile)', 'median'),
}


@needs_blanks
def test_versions_match_the_printed_blanks(exam, pkg):
    """The alt index -> printing table agrees with the text and the pixels of each blank,
    and each printed field of `info` holds its printed text in its region."""
    import pypdfium2
    v = pkg('versions')
    assert {f.label for pair in v.FIELDS.values() for f in pair} == set(PRINTED)
    pages = {}      # alt -> printed page -> hash of its rendering
    for alt in v.VERSIONS:
        doc = pypdfium2.PdfDocument(str(v.blank(alt)))
        try:
            assert len(doc) == exam.pages
            for bit, pair in v.FIELDS.items():
                for f in pair:
                    pattern, original = PRINTED[f.label]
                    page = doc[f.region.page - 1]
                    found = re.search(pattern, page.get_textpage().get_text_range())
                    assert found, (alt, f.label)
                    assert (found[1] != original) == bool(alt & bit), (alt, f.label)
                    w, h = page.get_size()
                    r = f.region
                    inside = page.get_textpage().get_text_bounded(
                        left=w * r.left / 1000, right=w * r.right / 1000,
                        top=h * (1 - r.top / 1000), bottom=h * (1 - r.bottom / 1000))
                    assert ' '.join(found[1].split()) in ' '.join(inside.split()), (alt, f.label, "outside the region")
            pages[alt] = {p: hashlib.sha256(doc[p - 1].render(scale=0.5).to_pil().tobytes()).hexdigest()
                          for p in range(1, exam.pages + 1)}
        finally:
            doc.close()
    # Two printings show a question's pages identically exactly when they share its printing.
    for qid, q in exam.questions.items():
        if qid == 'info':
            continue
        seen = {}
        for alt in v.VERSIONS:
            look = tuple(pages[alt][p] for p in sorted(extent(qid)))
            seen.setdefault(v.VERSIONS[alt].get(qid), set()).add(look)
        assert all(len(looks) == 1 for looks in seen.values()), qid
        assert len({next(iter(looks)) for looks in seen.values()}) == len(seen), qid
    assert len({pages[alt][p] for alt in v.VERSIONS for p in FRONT + EXTRA}) == len(FRONT + EXTRA)


def test_two_bits_combine_into_a_printing(exam, pkg, tmp_path):
    """Question 5 depends on two bits: both pairs of printed fields must agree, and a
    grader correcting the one that disagrees decides it."""
    f, v = pkg('fixtures'), pkg('versions')
    answers = {'q5': f.ANSWERS['q5'][v.VERSIONS[5]['q5']]}
    cases = [synth.Case('agree', printed={'info': v.readings(5)}, answers=answers),
             synth.Case('split', printed={'info': {**v.readings(5), 'q7_quantile': 'median'}}, answers=answers)]
    root = synth.generate(exam, tmp_path / 'ws', cases)
    ws = Workspace(root, exam)
    ws.sync()
    runner = Runner(ws, model=synth.fake_model(root))

    def printed(sid):
        return {k.split('/')[1]: v.text for k, v in runner.values(sid).items() if k.startswith('info/')}

    try:
        for sid in ('agree', 'split'):
            read_and_verify(runner, sid, ('info', 'q5'))
        assert printed('agree') == info(5)
        reads = runner.sheet('agree').results['q5/check'].reads
        assert {k for k in reads if k.startswith('info/')} == \
            {f'info/{f.label}' for b in (1, 4) for f in v.FIELDS[b]}      # bits 1 and 4, not bit 2
        assert (status(runner, 'agree', 'q5').state, status(runner, 'agree', 'q5').value) == ('graded', True)

        assert printed('split') == {**info(5), 'q7_quantile': 'original'}
        st = status(runner, 'split', 'q5')
        assert (st.state, st.value, st.counts) == ('to_decide', None, False) and 'disagree' in st.reason
        rev = runner.values('split')['info/q7_quantile'].rev
        runner.set('split', 'info', 'q7_quantile', 'alternate', author='ta', expect_rev=rev)
        assert status(runner, 'split', 'q5').state == 'outdated'
        runner.refresh('split')
        assert (status(runner, 'split', 'q5').state, status(runner, 'split', 'q5').value) == ('graded', True)
    finally:
        runner.close()
        ws.close()


def test_printing_reads_bits(exam, pkg):
    """`Printing` names a question's printing from the bits it reads; undecided while one is."""
    v = pkg('versions')
    q = exam.questions['q4']
    every = exam.fields()

    def context(body):
        return CheckContext(submission=S, question=q, check='check', fields=every,
                            values={k: (1, text) for k, text in values(exam, 'q4', {}, body=body).items()})

    for alt in v.VERSIONS:
        ctx = context(info(alt))
        assert v.Printing('q4')(ctx) == v.VERSIONS[alt]['q4']
        assert set(ctx.reads) == {f'info/{label}' for label in info(alt)}      # all three bits
    with pytest.raises(Undecided):
        v.Printing('q4')(context({**info(0), 'q9_variance': '[REVIEW: not read]'}))
    with pytest.raises(Undecided):
        v.Printing('q4')(context(None))


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
    ears = exam.questions['q2'].check('check').fn.key['original'][('??_6', '??_7')]
    assert math.show_entry(ears) == "[-2, 1] and [2, 1], either order"     # how graders see the pair's key
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
    by_printing = exam.questions['q6'].check('check').fn.key
    assert set(by_printing) == {f'{d}/{lv}' for d in DENSITY for lv in LEVELS}
    for printing, key in by_printing.items():
        shape, levels = printing.split('/')
        for entry, level in zip(key.values(), LEVELS[levels]):
            want = narrowest(DENSITY[shape], level)
            assert tuple(Fraction(t) for t in (entry.width, entry.lo, entry.hi)) == want, (printing, level)
    assert keys.Interval('2/3', '0', '1/3').label == "b - a = 2/3, -1/3 <= b <= 0"
    assert keys.Interval('2', '1', '1').label == "a = -3, b = -1"


def test_question_6(exam, pkg):
    f = pkg('fixtures')
    for printing, answers in f.ANSWERS['q6'].items():
        assert set(outcomes(exam, 'q6', answers, printing)[0].values()) == {'correct'}, printing
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
    u = np.array([0.25, 0.5])
    expr = math.parse_expr('max(2u, 1) + sqrt(u) - abs(u - 1/2)/3 + pi - e^u', symbols=['u'])
    want = np.maximum(2 * u, 1) + np.sqrt(u) - np.abs(u - 0.5) / 3 + np.pi - np.e ** u
    assert np.allclose(lshape.evaluate(expr, {'u': u}), want)
    with pytest.raises(math.Unsupported):
        lshape.evaluate(math.parse_expr('u + v', symbols=['u', 'v']), {'u': u})


def test_question_3(exam, pkg):
    f = pkg('fixtures')
    for printing, answers in f.ANSWERS['q3'].items():
        assert set(outcomes(exam, 'q3', answers, printing)[0].values()) == {'correct'}, printing
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

def test_rules():
    c, i, b = 'correct', 'incorrect', 'blank'
    two = math.at_least(2)      # the threshold questions' rule, as this exam relies on it
    assert two(dict(a=c, b=c, c=i)) == 'correct' and two(dict(a=c, b=i, c=b)) == 'incorrect'
    assert two(dict(a=c, b=None, c=i)) is None and two(dict(a=None, b=i, c=b)) == 'incorrect'
    assert two(dict(a=b, b=b, c=b)) == 'blank'


def test_worked_questions(exam, pkg):
    """q7, q9 and q12: 'answer' follows the boxes; the credit has no check function."""
    f = pkg('fixtures')
    for qid, credit in (('q7', 'check'), ('q9', 'check'), ('q12', 'shiny')):
        assert exam.questions[qid].check(credit).fn is None
        for printing, answers in f.ANSWERS[qid].items():
            assert grade(exam, qid, answers, printing, 'answer') == 'correct', (qid, printing)
        wrong = f.WRONG[qid]
        assert grade(exam, qid, wrong, 'original', 'answer') == 'incorrect'
        assert grade(exam, qid, dict.fromkeys(wrong), 'original', 'answer') == 'blank'


def test_work_is_decided_by_a_person(exam, pkg, tmp_path):
    f, v = pkg('fixtures'), pkg('versions')
    case = synth.Case(S, printed={'info': v.readings(0)}, answers={'q7': f.ANSWERS['q7']['original']})
    root = synth.generate(exam, tmp_path / 'ws', [case])
    ws = Workspace(root, exam)
    ws.sync()
    runner = Runner(ws, model=synth.fake_model(root))
    try:
        read_and_verify(runner, S, ('info', 'q7'))
        answer, check = status(runner, S, 'q7', 'answer'), status(runner, S, 'q7', 'check')
        assert (answer.state, answer.value) == ('graded', True)
        assert (check.state, check.value, check.counts) == ('to_decide', None, False)
        runner.decide(S, 'q7', 'check', True, author='ta')
        check = status(runner, S, 'q7', 'check')
        assert (check.state, check.value, check.counts) == ('decided', True, True)
    finally:
        runner.close()
        ws.close()


def test_choice_questions(exam, pkg):
    f = pkg('fixtures')
    for qid, check in (('q4', 'check'), ('q5', 'check'), ('q8', 'shiny')):
        for printing, answers in f.ANSWERS[qid].items():
            for other in f.ANSWERS[qid]:
                got = set(outcomes(exam, qid, answers, other, check)[0].values())
                assert (got == {'correct'}) == (other == printing or f.ANSWERS[qid][other] == answers), (qid, printing)

    def q8(answers):
        return {check: grade(exam, 'q8', answers, 'original', check) for check in ('check', 'shiny')}

    assert q8(f.WRONG['q8']) == {'check': 'correct', 'shiny': 'incorrect'}
    right = f.ANSWERS['q8']['original']
    assert q8({**right, '8.3': None}) == {'check': 'correct', 'shiny': 'incorrect'}
    assert q8({**right, '8.3': '[REVIEW: smudged]'}) == {'check': 'correct', 'shiny': None}
    assert q8(dict.fromkeys(right)) == {'check': 'blank', 'shiny': 'blank'}
    reads = outcomes(exam, 'q8', right, 'original', 'check')[1]['reads']
    assert {k for k in reads if k.startswith('q8/')} == {'q8/8.1', 'q8/8.2'}      # only what it judges
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
    # Point arithmetic that fails is a failure like any other: incorrect.
    for body in ("x = [1, 2]\n    return x + 1", "return [1, 2] + [1, 2, 3]"):
        stop = stopped(f"def f():\n    {body}\n")
        assert stop.outcome == 'incorrect' and stop.error.kind == 'type'
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


def q10(exam, source, printing='original'):
    got, body = outcomes(exam, 'q10', source, printing)
    f = body['fields']['snow_cone']
    return f['outcome'], f.get('note', "")


def test_question_10(exam, pkg):
    f = pkg('fixtures')
    for printing, source in f.Q10.items():
        assert q10(exam, source, printing) == ('correct', "")
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
    outcome, note = q10(exam, f.Q10_LENGTHS)                # fails on one branch only: still incorrect
    assert outcome == 'incorrect' and "different lengths (1 and 2)" in note
    assert q10(exam, "def snow_cone():\n    return [2, 1 +\n")[0] is None
    assert q10(exam, "def snow_cone():\n    pass\n") == ('blank', "") == q10(exam, "")
    tuples = f.Q10['original'].replace('return [2, 0] + [X, Y]', 'return 2 + X, Y')
    assert q10(exam, tuples) == ('correct', "")             # a tuple is a point too


def q11(exam, source):
    got, body = outcomes(exam, 'q11', source)
    f = body['fields']['standard_normal']
    return f['outcome'], f.get('note', "")


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
    assert q11(exam, "def standard_normal():\n    while True:\n        X = unif()\n")[0] is None    # never returns
    assert q11(exam, f.Q11_BLANK) == ('blank', "")


# --- answer keys never reach the model --------------------------------------------------

def test_no_answer_key_in_any_prompt(exam, pkg, tmp_path):
    f, v = pkg('fixtures'), pkg('versions')
    cases = [synth.Case(f'v{alt}', printed={'info': v.readings(alt)}, answers=f.canonical(alt)) for alt in (0, 7)]
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
        own = "\n".join(line for c in model.calls if c.tag[1] == qid
                        for line in c.prompt.splitlines() if not line.startswith(("Answer labels:", "Question:")))
        values = {a for answers in f.ANSWERS[qid].values() for a in answers.values() if len(a) <= 3}
        values -= {'v'} if qid == 'q3' else set()       # question 3's note names the variables u, v and x
        for value in values:
            assert not re.search(rf'(?<![\w./^-]){re.escape(value)}(?![\w./^])', own), (qid, value)
        assert q.labels and "Answer labels:" in "\n".join(c.prompt for c in model.calls if c.tag[1] == qid)
