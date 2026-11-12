"""Exam 2: twelve questions on 28 printed pages, in eight printings.

Three independent binary choices switch groups of questions; several questions
depend on two of them, and question 4 on all three (see `versions.py`). The
first question, `info`, has no checks: it holds the six printed fields that tell
the choices, and the other questions' checks read it to pick their keys.
Questions 10 and 11 are pseudocode. Questions 7, 9 and 12 ask for work, which
a person judges: their credit is never settled automatically for a right answer.
"""
from examinator import Bool, Check, Exam, Field, Region
from examinator.std import math, readers

from . import keys, lshape, normal, notes, rules, samplers, snow_cone
from .keys import number, point
from .versions import FIELDS, band, names, printing

exam = Exam('exam_02', title="Exam 2", pages=28,
            scan_window=[60, 55, 920, 825], cover_pages=1)


# --- helpers local to this exam ---------------------------------------------

BOX = (583, 853, 95)        # left, right and height of every printed answer box


def boxes(page, labels, tops):
    left, right, height = BOX
    return [Field(label, Region(page, left, top, right, top + height)) for label, top in zip(labels, tops)]


def bubbles(page, labels, tops, height, left=750, right=880):
    return [Field(label, Region(page, left, top, right, top + height)) for label, top in zip(labels, tops)]


def per_printing(qid, key):
    """`key(name)` for each of the question's printings."""
    return {name: key(name) for name in names(qid)}


def restricted(key, labels):
    """A key per printing, keeping only `labels`."""
    return {name: {label: entry for label, entry in k.items() if label in labels} for name, k in key.items()}


def placeholders(n):
    return [f'??_{i}' for i in range(1, n + 1)]


TRUE, FALSE = math.Choice('True', 'T'), math.Choice('False', 'F')


def truths(pattern):
    return [TRUE if c == 'T' else FALSE for c in pattern]


OPTIONS = ['>= 1 - delta', '>= 1 - 2 delta', '<= delta', '<= 2 delta', 'None of the above']
EXTRA = {5: ['none']}


def option(n):
    return keys.Option(n, (OPTIONS[n - 1], *EXTRA.get(n, [])))


SHINY = Bool(counts=False)
ANSWER = Bool(counts=False)        # the boxed answers alone, before the work is judged


def worked(key, *, by, mark='check', points=Bool()):
    """The checks of a question whose credit also needs work: 'answer' follows the
    boxed answers; `mark`, the credit, leaves a right answer to a person
    (`rules.with_work`)."""
    return {'answer': Check(math.keyed(key, by=by, functions=keys.FUNCTIONS), ANSWER),
            mark: Check(math.keyed(key, by=by, functions=keys.FUNCTIONS, grade=rules.with_work), points)}


# --- questions, in printed order --------------------------------------------------

exam.question('info', title="Printing", pages=[4, 10, 12, 14, 16, 18],
              fields=[f for pair in FIELDS.values() for f in pair])

# The same key on all four printings of the L piece: it reads no printed field.
Q1 = ['1.1', '1.2', '1.3', '1.4', '1.5']
exam.question(
    'q1', title="Catching Ls", pages=[2, 3],
    fields=boxes(2, Q1[:2], [515, 724]) + boxes(3, Q1[2:], [93, 385, 696]),
    read=readers.answers(functions=keys.FUNCTIONS, notes=notes.PLACEHOLDERS),
    check=math.keyed(functions=keys.FUNCTIONS, grade=math.at_least(4),
                     key=dict(zip(Q1, [number('1/4'), number('1/2'), number('4'), point('1/4', '1/3'),
                                       math.Choice('None')]))))


def discs(face, ears):
    """Question 2's key: the face's radius, and where the two ears sit."""
    ear = f'sqrt(5) - {face}'
    share = f'2*({ear})**2 / (2*({ear})**2 + ({face})**2)'
    return {'??_1': number(ear), '??_2': number('1/2'), '??_3': number(face), '??_4': number('1/2'),
            '??_5': number(share), ('??_6', '??_7'): keys.EitherOrder(*ears), '??_8': point('0', '0')}


exam.question(
    'q2', title="That's no mouse—it's a marsupial!", pages=[5],
    fields=boxes(5, placeholders(8), [89, 193, 296, 400, 504, 608, 711, 815]),
    read=readers.answers(functions=keys.FUNCTIONS, notes=notes.PLACEHOLDERS),
    check=math.keyed(by=printing('q2'), functions=keys.FUNCTIONS, grade=math.at_least(7),
                     key={'original': discs('3/2', (point('-2', '1'), point('2', '1'))),
                          'alternate': discs('5/3', (point('-1', '2'), point('1', '2')))}))

exam.question(
    'q3', title="CooL", pages=[7],
    fields=boxes(7, placeholders(6), [89, 193, 296, 400, 504, 608]),
    read=readers.answers(symbols=['u', 'v', 'x'], functions=keys.FUNCTIONS, notes=notes.EXPRESSIONS),
    check=math.keyed(per_printing('q3', lshape.key), by=printing('q3'), symbols=['u', 'v', 'x'],
                     functions=keys.FUNCTIONS, grade=math.at_least(5)))

Q4 = ['4.1', '4.2', '4.3', '4.4', '4.5']
exam.question(
    'q4', title="ID'ing i.i.d.", pages=[8, 9],
    fields=bubbles(8, Q4[:2], [380, 667], 65) + bubbles(9, Q4[2:], [115, 415, 700], 65),
    read=readers.answers(notes=notes.TRUE_FALSE),
    check=math.keyed(by=printing('q4'),
                     key={'a': dict(zip(Q4, truths('FTFFF'))), 'b': dict(zip(Q4, truths('TFFTF'))),
                          'c': dict(zip(Q4, truths('TFTFF'))), 'd': dict(zip(Q4, truths('FTTFT')))}))

Q5 = ['5.1', '5.2', '5.3']
exam.question(
    'q5', title="Histogrametry", pages=[11],
    fields=bubbles(11, Q5, [115, 399, 683], 190),
    read=readers.answers(functions=[], notes=notes.POWERS),
    check=math.keyed(by=printing('q5'), functions=[],
                     key={'totals/distance':   dict(zip(Q5, ['10**2', '10**0', '10**1'])),
                          'means/distance':    dict(zip(Q5, ['10**0', '10**2', '10**1'])),
                          'totals/coordinate': dict(zip(Q5, ['10**1', '10**2', '10**1'])),
                          'means/coordinate':  dict(zip(Q5, ['10**3', '10**0', '10**1']))}))


def narrowest(first, second):
    """Question 6's key from the noise interval behind each part: (width, lowest start, highest start)."""
    return {('6.1 a', '6.1 b'): keys.Interval(*first), ('6.2 a', '6.2 b'): keys.Interval(*second)}


exam.question(
    'q6', title="Blocky GPS", pages=[13],
    fields=boxes(13, ['6.1 a', '6.1 b', '6.2 a', '6.2 b'], [94, 198, 514, 618]),
    read=readers.answers(functions=keys.FUNCTIONS, notes=notes.BOXED),
    check=math.keyed(by=printing('q6'), functions=keys.FUNCTIONS, key={
        'falling/third': narrowest(('2/3', '0', '1/3'), ('2', '0', '0')),
        'rising/third':  narrowest(('2/3', '2', '7/3'), ('2', '1', '1')),
        'falling/half':  narrowest(('1', '0', '0'), ('3/2', '0', '0')),
        'rising/half':   narrowest(('1', '2', '2'), ('3/2', '3/2', '3/2'))}))

exam.question(
    'q7', title="Three samples", pages=[14, 15],
    fields=boxes(14, ['7.1'], [506]) + boxes(15, ['7.2'], [151]),
    read=readers.answers(functions=keys.FUNCTIONS, notes=notes.BOXED),
    checks=worked(by=printing('q7'), key={'original':  {'7.1': '3/8', '7.2': '3/4'},
                                          'alternate': {'7.1': '27/64', '7.2': '27/64'}}))

# 'check' needs the first two answers; 'shiny' all four.
Q8 = ['8.1', '8.2', '8.3', '8.4']
Q8_KEY = {'original':  dict(zip(Q8, map(option, [5, 4, 5, 4]))),
          'alternate': dict(zip(Q8, map(option, [4, 5, 5, 4])))}
exam.question(
    'q8', title="2 confident 2 curious", pages=[16, 17],
    fields=bubbles(16, Q8[:2], [337, 652], 120, 135, 480) + bubbles(17, Q8[2:], [132, 567], 120, 135, 480),
    read=readers.answers(notes=notes.OPTIONS),
    checks={'check': math.keyed(restricted(Q8_KEY, Q8[:2]), by=printing('q8')),
            'shiny': Check(math.keyed(Q8_KEY, by=printing('q8')), SHINY)})

exam.question(
    'q9', title="Zzz score", pages=[19],
    fields=boxes(19, ['n', 't'], [143, 247]),
    read=readers.answers(functions=keys.FUNCTIONS, notes=notes.BOXED),
    checks=worked(by=printing('q9'), key={'original':  {'n': '36', 't': '15/2'},
                                          'alternate': {'n': '81', 't': '15/2'}}))

exam.question(
    'q10', title="For which we all moan", pages=[21],
    fields=[Field(snow_cone.LABEL, band(21, 125, 930))],
    read=readers.code([snow_cone.LABEL], language=samplers.LANGUAGE, notes=notes.SNOW_CONE),
    check=Check(snow_cone.check, SHINY, timeout=60))

exam.question(
    'q11', title="Ordinary regular", pages=[23],
    fields=[Field(normal.LABEL, band(23, 85, 930))],
    read=readers.code([normal.LABEL], language=samplers.LANGUAGE, notes=notes.NORMAL),
    check=Check(normal.check, SHINY, timeout=60))

Q12 = [f'12.{i}' for i in range(1, 7)]
exam.question(
    'q12', title="Maximum mystery", pages=[25],
    fields=boxes(25, Q12, [169, 296, 424, 552, 679, 807]),
    read=readers.answers(functions=keys.FUNCTIONS, notes=notes.BOXED),
    checks=worked(by=printing('q12'), mark='shiny', points=SHINY,
                  key={'original':  dict(zip(Q12, ['2', '1', '2', '2', '2', '3'])),
                       'alternate': dict(zip(Q12, ['2', '1', '2', '2', '2', '5']))}))
