"""Synthetic cases for Exam 2. Every answer here was written for the fixtures;
none is student work. With `EXAM2_BLANKS` set, each case is drawn on the
matching blank PDF; without it, on placeholder pages."""
from examinator.synth import Case

from .versions import VERSIONS, blank, readings

P8 = [f'??_{i}' for i in range(1, 9)]
P6 = P8[:6]
Q1 = ['1.1', '1.2', '1.3', '1.4', '1.5']
Q4 = ['4.1', '4.2', '4.3', '4.4', '4.5']
Q5 = ['5.1', '5.2', '5.3']
Q6 = ['6.1 a', '6.1 b', '6.2 a', '6.2 b']
Q8 = ['8.1', '8.2', '8.3', '8.4']
Q12 = [f'12.{i}' for i in range(1, 7)]
FIELDS = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8', 'q9', 'q12']       # questions answered in boxes or bubbles

NONE_ABOVE, AT_MOST_2 = 'None of the above', "It's ≤ 2δ"

# Canonical answers per question and variant, stated independently of the keys in exam.py.
ANSWERS = {
    'q1': dict.fromkeys(['right', 'left', 'right+1', 'left+1'],
                        dict(zip(Q1, ['1/4', '1/2', '4', '[1/4, 1/3]', 'None']))),
    'q2': {'original':  dict(zip(P8, ['sqrt(5) - 3/2', '1/2', '3/2', '1/2',
                                      '2pi(sqrt(5)-3/2)^2 / (2pi(sqrt(5)-3/2)^2 + pi(3/2)^2)',
                                      '[-2, 1]', '[2, 1]', '[0, 0]'])),
           'alternate': dict(zip(P8, ['sqrt(5) - 5/3', '1/2', '5/3', '1/2',
                                      '2pi(sqrt(5)-5/3)^2 / (2pi(sqrt(5)-5/3)^2 + pi(5/3)^2)',
                                      '[-1, 2]', '[1, 2]', '[0, 0]']))},
    'q3': {'right':   dict(zip(P6, ['1/2', '4u', '2u + 1', '2', 'v', '2v'])),
           'left':    dict(zip(P6, ['1/2', '2u', '4u - 1', '1', '2v', 'v'])),
           'right+1': dict(zip(P6, ['1/2', '4u + 1', '2u + 2', '3', 'v', '2v'])),
           'left+1':  dict(zip(P6, ['1/2', '2u + 1', '4u', '2', '2v', 'v']))},
    'q4': {'a': dict(zip(Q4, ['False', 'True', 'False', 'False', 'False'])),
           'b': dict(zip(Q4, ['True', 'False', 'False', 'True', 'False'])),
           'c': dict(zip(Q4, ['True', 'False', 'True', 'False', 'False'])),
           'd': dict(zip(Q4, ['False', 'True', 'True', 'False', 'True']))},
    'q5': {'totals/distance':   dict(zip(Q5, ['10^2', '10^0', '10^1'])),
           'means/distance':    dict(zip(Q5, ['10^0', '10^2', '10^1'])),
           'totals/coordinate': dict(zip(Q5, ['10^1', '10^2', '10^1'])),
           'means/coordinate':  dict(zip(Q5, ['10^3', '10^0', '10^1']))},
    'q6': {'falling/third': dict(zip(Q6, ['-2/3', '0', '-2', '0'])),
           'rising/third':  dict(zip(Q6, ['-3', '-7/3', '-3', '-1'])),
           'falling/half':  dict(zip(Q6, ['-1', '0', '-3/2', '0'])),
           'rising/half':   dict(zip(Q6, ['-3', '-2', '-3', '-3/2']))},
    'q7': {'original': {'7.1': '3/8', '7.2': '3/4'}, 'alternate': {'7.1': '27/64', '7.2': '27/64'}},
    'q8': {'original':  dict(zip(Q8, [NONE_ABOVE, AT_MOST_2, NONE_ABOVE, AT_MOST_2])),
           'alternate': dict(zip(Q8, [AT_MOST_2, NONE_ABOVE, NONE_ABOVE, AT_MOST_2]))},
    'q9': {'original': {'n': '36', 't': '7.5'}, 'alternate': {'n': '81', 't': '7.5'}},
    'q12': {'original':  dict(zip(Q12, ['2', '1', '2', '2', '2', '3'])),
            'alternate': dict(zip(Q12, ['2', '1', '2', '2', '2', '5']))},
}

# Printing alt0's answers in other accepted forms.
OTHER_FORMS = {
    'q1': dict(zip(Q1, ['0.25', '2/4', '4.0', '(1/4, 1/3)', 'none'])),
    'q2': dict(zip(P8, ['√5 − 1.5', '0.5', '1.5', '.5', '1 - (9/4)/(2(sqrt(5)-3/2)^2 + 9/4)',
                        '(2, 1)', '(-2, 1)', '(0, 0)'])),
    'q3': dict(zip(P6, ['0.5', '2 + 2u', '4u - 2', '2', '1 - v', '2 - 2v'])),     # branches swapped, and run downward
    'q4': dict(zip(Q4, ['F', 'T', 'false', 'FALSE', 'f'])),
    'q5': dict(zip(Q5, ['100', '1', '10'])),
    'q6': dict(zip(Q6, ['-0.9', '-0.9 + 2/3', '-2.0', '0'])),                     # another equally narrow interval
    'q7': {'7.1': '3 (1/2)(1/2)^2', '7.2': '0.75'},
    'q8': dict(zip(Q8, ['none of the above', '<= 2 delta', 'E', '4'])),
    'q9': {'n': '9*4', 't': '15/2'},
    'q12': dict(zip(Q12, ['2.0', '1', '2', '4/2', '2', '3'])),
}

# Printing alt0 with mistakes: enough to lose the credit in each question.
WRONG = {
    'q1': dict(zip(Q1, ['1/4', '1/3', '4', '[1/4, 1/4]', 'None'])),               # 3 of 5
    'q2': dict(zip(P8, ['sqrt(5) - 3/2', '1/2', '3/2', '1/2', '1/2', '[-2, 1]', '[-2, 1]', '[0, 0]'])),   # 6 of 8
    'q3': dict(zip(P6, ['1/2', '4u', '2u', '2', 'v', 'v'])),                      # 4 of 6
    'q4': dict(zip(Q4, ['False', 'True', 'True', 'False', 'False'])),
    'q5': dict(zip(Q5, ['10^2', '10^1', '10^1'])),
    'q6': dict(zip(Q6, ['-2/3', '0', '-3', '-1'])),
    'q7': {'7.1': '3/8', '7.2': '7/8'},
    'q8': dict(zip(Q8, [NONE_ABOVE, AT_MOST_2, "It's ≤ δ", AT_MOST_2])),          # the first two still earn 'check'
    'q9': {'n': '16', 't': '7.5'},
    'q12': dict(zip(Q12, ['2', '2', '2', '2', '2', '3'])),
}

# One slip inside the threshold questions' allowance: still credit.
SLIPS = {
    'q1': dict(zip(Q1, ['1/4', '1/2', '3', '[1/4, 1/3]', 'None'])),               # 4 of 5
    'q2': dict(zip(P8, ['sqrt(5) - 3/2', '1/2', '3/2', '1/2', '1/2', '[2, 1]', '[-2, 1]', '[0, 0]'])),    # 7 of 8
    'q3': dict(zip(P6, ['1/2', '4u', '2u + 1', '2', 'v', 'v'])),                  # 5 of 6
}

SNOW_CONE = """\
def snow_cone():
    sector_area = pi * {radius}^2 * {fraction}
    vee_area = 3 - {overlap}
    if coin(sector_area / (sector_area + vee_area)):
        R = {radius} * sqrt(unif())
        Theta = pi/2 + 2 * pi * {fraction} * (unif() - 1/2)
        return {center} + rotate([R, 0], Theta, [0, 0])
    else:
        X = unif()
        Y = unif()
        if Y < X:
            [X, Y] = rotate([X, Y], pi/2, [0, 0])
        Y *= 3 - {overlap}
        Y += {overlap} * abs(X)
        return [2, 0] + [X, Y]
"""
# `overlap` is the height (and the area) of the triangle that the sector covers.
Q10 = {'original': SNOW_CONE.format(radius='sqrt(2)', fraction='(1/4)', center='[2, 2]', overlap='1'),
       'alternate': SNOW_CONE.format(radius='2', fraction='(1/6)', center='[2, 3 - sqrt(3)]', overlap='sqrt(3)')}
Q10_HALF = Q10['original'].replace('sector_area / (sector_area + vee_area)', '1/2')    # wrong weights
Q10_LENGTHS = Q10['original'].replace('return [2, 0] + [X, Y]', 'return [2] + [X, Y]')     # runs and fails
Q10_LOOP = """\
def snow_cone():
    while True:
        X = 1 + 2 * unif()
        Y = 4 * unif()
        if Y <= 3 and Y >= 3 * abs(X - 2):
            return [X, Y]
        if (X - 2)^2 + (Y - 2)^2 <= 2 and abs(X - 2) <= Y - 2:
            return [X, Y]
"""

Q11 = """\
def g(t):
    return exp(-1/2 * t^2)

def standard_normal():
    while True:
        X = -log(1 - unif())
        Y = unif() * exp(-X)
        if 2 * Y < g(X):
            return X if coin(1/2) else -X
"""
Q11_ONE_SIDED = Q11.replace('X if coin(1/2) else -X', 'X')
Q11_BLANK = "def standard_normal():\n    pass\n"

WORK = None     # a right answer that needs work: undecided until a person judges it
ALL_TRUE = {**{q: True for q in ('q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q10', 'q11')},
            'q7.answer': True, 'q7.check': WORK, 'q8.check': True, 'q8.shiny': True,
            'q9.answer': True, 'q9.check': WORK, 'q12.answer': True, 'q12.shiny': WORK}


def canonical(alt: int) -> dict:
    """Correct answers to every question of one printing."""
    v = VERSIONS[alt]
    return {**{q: ANSWERS[q][v[q]] for q in FIELDS}, 'q10': Q10[v['q10']], 'q11': Q11}


def empty(answers: dict) -> dict:
    return {label: None for label in answers}


def case(name, alt, answers, expect, **changed):
    return Case(name, variants=VERSIONS[alt], blank=blank(alt), answers=answers, expect=expect,
                readings={**readings(alt), **changed})


def cases():
    out = [case(f'alt{alt}', alt, canonical(alt), ALL_TRUE) for alt in VERSIONS]
    out += [
        case('forms', 0, {**OTHER_FORMS, 'q10': Q10_LOOP, 'q11': Q11},
             {**ALL_TRUE, 'q10': None}),                        # passes with a loop: a person decides
        case('wrong', 0, {**WRONG, 'q10': Q10_HALF, 'q11': Q11_ONE_SIDED},
             {**{q: False for q in ('q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q10', 'q11')},
              'q7.answer': False, 'q7.check': False, 'q8.check': True, 'q8.shiny': False,
              'q9.answer': False, 'q9.check': False, 'q12.answer': False, 'q12.shiny': False}),
        case('slips', 0, SLIPS, {'q1': True, 'q2': True, 'q3': True}),
        case('errors', 0, {'q10': Q10_LENGTHS}, {'q10': False}),   # points of different lengths: incorrect
        # Another printing with printing alt0's answers: the other keys apply.
        case('crossed', 7, {**{q: ANSWERS[q][VERSIONS[0][q]] for q in FIELDS}, 'q10': Q10['original']},
             {'q1': True, 'q2': False, 'q3': False, 'q4': False, 'q5': False, 'q6': False,
              'q7.answer': False, 'q8.check': False, 'q9.answer': False, 'q10': False, 'q12.answer': False}),
        case('empty', 5, {**{q: empty(ANSWERS[q][VERSIONS[5][q]]) for q in FIELDS}, 'q10': "", 'q11': Q11_BLANK},
             {**{q: 'blank' for q in ('q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q10', 'q11')},
              'q7.answer': 'blank', 'q7.check': 'blank', 'q8.check': 'blank', 'q8.shiny': 'blank',
              'q9.answer': 'blank', 'q9.check': 'blank', 'q12.answer': 'blank', 'q12.shiny': 'blank'}),
        # Bit 1: one cue unreadable. Bit 2: its two cues disagree. Bit 4: both agree.
        # Question 1's key does not depend on its printing, so it still counts.
        case('cues', 0, {q: ANSWERS[q][VERSIONS[0][q]] for q in FIELDS},
             {'q1': True, 'q2': True, 'q3': None, 'q4': None, 'q5': None, 'q6': None,
              'q7.answer': True, 'q8.check': None, 'q9.answer': None, 'q12.answer': True},
             q5_totals=None, q9_variance='9'),
    ]
    return out
