"""Synthetic cases for Exam 3. Every answer here was written for the fixtures;
none is student work. With `EXAM3_BLANKS` set, each case is drawn on the
matching blank PDF; without it, on placeholder pages."""
from examinator.synth import Case

from .versions import VERSIONS, blank, printed

Q3 = ['3.1', '3.2', '3.3', '3.4']
Q4 = [f'4.{part}.{bound}' for part in (1, 2) for bound in 'abcd']
Q6 = ['6.1', '6.2', '6.3', '6.4']
MATH = ['q1', 'q3', 'q4', 'q5', 'q6', 'q8', 'q9']

# Canonical answers per printing, stated independently of the keys in exam.py.
ANSWERS = {
    'original': {
        'q1': {'1.1': '[0, b] x [a, 1]', '1.2': '2*b*(1-a)', '1.3': 'b*(1-a)', '1.4': '1/2'},
        'q3': dict(zip(Q3, ['stays the same', 'stays the same', 'stays the same', 'increases'])),
        'q4': dict(zip(Q4, ['-1', '-1/2', '0', '1/2', '-1', '-1/4', '0', '3/4'])),
        'q5': {'5': '30'},
        'q6': dict(zip(Q6, 'CDAB')),
        'q8': {'8.1': '6/25**3', '8.2': '0'},
        'q9': {'9.a': '12', '9.b': '16'},
    },
    'alternate': {
        'q1': {'1.1': '[0, a] x [b, 1]', '1.2': '2*a*(1-b)', '1.3': 'a*(1-b)', '1.4': '1/2'},
        'q3': dict(zip(Q3, ['stays the same', 'decreases', 'stays the same', 'stays the same'])),
        'q4': dict(zip(Q4, ['-1/2', '0', '1/2', '1', '-3/4', '0', '1/4', '1'])),
        'q5': {'5': '15'},
        'q6': dict(zip(Q6, 'DCAB')),
        'q8': {'8.1': '0', '8.2': '6/25**3'},
        'q9': {'9.a': '11', '9.b': '19'},
    },
}

# The same original answers in other accepted notations.
OTHER_FORMS = {
    'q1': {'1.1': '[0,b]×[a,1]', '1.2': '2b(1-a)', '1.3': 'b - ab', '1.4': '0.5'},
    'q3': dict(zip(Q3, ['B', '2', 'same', 'option 1'])),
    'q4': dict(zip(Q4, ['-1', '-0.5', '0', '.5', '-1', '-.25', '0', '0.75'])),
    'q5': {'5': 'D'},
    'q6': dict(zip(Q6, ['3', 'option 4', 'a', '(B)'])),
    'q8': {'8.1': '(1/25)(2/25)(3/25)', '8.2': '0'},
    'q9': {'9.a': '12.0', '9.b': '16'},
}

# One field changed in each question (original printing).
ONE_WRONG = {
    'q1': {'1.1': '[0, b] x [a, 1]', '1.2': '2*b*(1-a)', '1.3': 'a*(1-b)', '1.4': '1/2'},
    'q3': dict(zip(Q3, ['stays the same', 'decreases', 'stays the same', 'increases'])),
    'q4': dict(zip(Q4, ['-1', '-1/2', '0', '1/2', '-1', '-1/4', '0', '1'])),
    'q5': {'5': '60'},
    'q6': dict(zip(Q6, 'CDBA')),
    'q8': {'8.1': '6/25**2', '8.2': '0'},
    'q9': {'9.a': '12', '9.b': '18'},
}

WEIBULL = """\
def weibull(a, b):
    return (-log(1 - unif()) / b) ** (1 / a)
"""
WEIBULLISH = """\
def weibullish():
    while True:
        t = weibull(2, 4)
        if t >= 1 and coin(t**2 / huge_constant):
            return t
"""
POISSON_DIST = """\
def poisson_dist(m):
    n = 0
    total = -log(1 - unif())
    while total < m:
        n += 1
        total += -log(1 - unif())
    return n
"""
POISSON_PROCESS = """\
def poisson_process():
    events = []
    for i in range(poisson_dist(25)):
        u = unif()
        if u < 0.5:
            events.append(-sqrt(25 - 50*u))
        else:
            events.append(sqrt(50*u - 25))
    return events
"""

Q2 = WEIBULL + "\n" + WEIBULLISH
Q7 = POISSON_DIST + "\n" + POISSON_PROCESS

Q2_LITERAL = Q2.replace('huge_constant', '10**1000')            # the rewrite restores the name
Q2_WRONG_WEIBULL = Q2.replace('** (1 / a)', '** a')             # fails 2.1 only
Q2_ONLY_WEIBULL = WEIBULL + "\ndef weibullish():\n    pass\n"   # 2.2 blank
Q7_OFF_BY_ONE = Q7.replace('n = 0', 'n = 1')                    # fails 7.2 only
Q7_ONLY_PROCESS = POISSON_PROCESS                               # 7.2 blank (function absent)

ALL_TRUE = {**{q: True for q in MATH},
            'q2.check': True, 'q2.shiny': True, 'q7.check': True, 'q7.shiny': True}


def canonical(alt: int) -> dict:
    """Correct answers to every question of one printed version."""
    return {**{q: ANSWERS[VERSIONS[alt][q]][q] for q in MATH}, 'q2': Q2, 'q7': Q7}


def empty(answers: dict) -> dict:
    return {label: None for label in answers}


def cases():
    out = [Case(f'alt{alt}', printed=printed(alt), blank=blank(alt), answers=canonical(alt), expect=ALL_TRUE)
           for alt in VERSIONS]
    out += [
        Case('forms', printed=printed(0), blank=blank(0),
             answers={**OTHER_FORMS, 'q2': Q2_LITERAL, 'q7': Q7}, expect=ALL_TRUE),
        Case('one-wrong', printed=printed(0), blank=blank(0),
             answers={**ONE_WRONG, 'q2': Q2_WRONG_WEIBULL, 'q7': Q7_OFF_BY_ONE},
             expect={**{q: False for q in MATH},
                     'q2.check': False, 'q2.shiny': True, 'q7.check': True, 'q7.shiny': False}),
        # The alternate printing with the original printing's answers: the alternate key applies.
        Case('crossed', printed=printed(7), blank=blank(7),
             answers={q: ANSWERS['original'][q] for q in MATH}, expect={q: False for q in MATH}),
        Case('empty', printed=printed(5), blank=blank(5),
             answers={**{q: empty(ANSWERS['original'][q]) for q in MATH},
                      'q2': Q2_ONLY_WEIBULL, 'q7': Q7_ONLY_PROCESS},
             expect={**{q: 'blank' for q in MATH},
                     'q2.check': True, 'q2.shiny': 'blank', 'q7.check': True, 'q7.shiny': 'blank'}),
        # One printed field unreadable: the printing's parse waits for a person, and every question
        # that reads it with it; the pseudocode questions read nothing and count.
        Case('unread', printed={'info': {**printed(0)['info'], 'q5_confidence': None}}, blank=blank(0),
             answers={**{q: ANSWERS['original'][q] for q in MATH}, 'q2': Q2, 'q7': Q7},
             expect={**{q: None for q in MATH},
                     'q2.check': True, 'q2.shiny': True, 'q7.check': True, 'q7.shiny': True}),
        # Bit 2's two fields disagree: its questions are undecided; bits 1 and 4 agree.
        Case('disagree', printed={'info': {**printed(0)['info'], 'q6_option_c': 'do'}}, blank=blank(0),
             answers={q: ANSWERS['original'][q] for q in MATH},
             expect={'q1': True, 'q5': True, 'q9': True, 'q3': None, 'q6': None, 'q4': True, 'q8': True}),
    ]
    return out
