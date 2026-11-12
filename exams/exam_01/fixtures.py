"""Synthetic cases for Exam 1. Every answer here was written for the fixtures;
none is student work. With `EXAM1_BLANKS` set, each case is drawn on the
matching blank PDF; without it, on placeholder pages."""
from examinator.synth import Case

from .versions import VERSIONS, blank, printed_on

Q2 = [f'2.{part}.{name}' for part in (1, 2, 3) for name in 'mc']
Q3 = [f'??{n}' for n in range(1, 7)]
MATH = ['q2', 'q3']
ALL = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6']

Q3_ANSWERS = dict(zip(Q3, ['s[i-1]', 's[i]', 's[i-1] / s[n]', 's[i] / s[n]', '(s[i] - s[i-1]) / s[n]',
                           'Uniform(0, 1)']))

SCOOPS = """
def scoop():
    R = sqrt(unif())
    T = 2*pi * unif()
    return [R * cos(T), R * sin(T)]

def half_scoop():
    [X, Y] = scoop()
    return [X, abs(Y)]

def cone():
    X = unif()
    Y = unif()
    if X > Y:
        [X, Y] = rotate([X, Y], pi/2, [0, 0])
    return [X, Y]
"""

# Canonical answers per printing, stated independently of the checks.
ANSWERS = {
    'original': {
        'q1': """\
def exponential(m):
    U = unif()
    return m * log(1 / U)
""",
        'q2': dict(zip(Q2, ['1', '3/2', '12', '4', '1/4', '4'])),
        'q3': Q3_ANSWERS,
        'q4': """\
def ice_cream():
    K = weighted_die([pi, pi/2, 3])
    if K == 1:
        return scoop() + [3, 4]
    elif K == 2:
        return half_scoop() + [3, 2]
    else:
        return cone() * [1, 3] + [3, -1]
""" + SCOOPS,
        'q5': """\
def unit_cone():
    Z = unif()**(1/3)
    [X, Y] = Z * unit_disc()
    return [X, Y, Z]

def unit_disc():
    return rotate([unif()**(1/2), 0], 2*pi * unif(), [0, 0])
""",
        'q6': """\
def spire():
    done = False
    while not done:
        [X, Y] = spire_bounding_area()
        done = Y < f(X)
    return X

def spire_bounding_area():
    X = 2 * unif()**(5/4)
    Y = unif() * 4 * X**(-1/5)
    return [X, Y]
""",
    },
    'alternate': {
        'q1': """\
def exponential(m):
    U = unif()
    return log(1 / U) / m
""",
        'q2': dict(zip(Q2, ['1', '1', '1/12', '4', '4', '1'])),
        'q3': Q3_ANSWERS,
        'q4': """\
def ice_cream():
    K = weighted_die([pi, pi, pi/2, 2])
    if K == 1:
        return scoop() + [2, 2]
    elif K == 2:
        return scoop() + [2, 0]
    elif K == 3:
        return half_scoop() + [2, -2]
    else:
        return cone() * [1, 2] + [2, -4]
""" + SCOOPS,
        'q5': """\
def twonit_cone():
    Z = 2 * unif()**(1/3)
    [X, Y] = Z * unit_disc()
    return [X, Y, Z]

def unit_disc():
    return rotate([unif()**(1/2), 0], 2*pi * unif(), [0, 0])
""",
        'q6': """\
def spire():
    done = False
    while not done:
        [X, Y] = spire_bounding_area()
        done = Y < f(X)
    return X

def spire_bounding_area():
    X = 4 * unif()**(3/2)
    Y = unif() * 6 * X**(-1/3)
    return [X, Y]
""",
    },
}

# The same original answers in other accepted forms.
OTHER_FORMS = {
    'q1': """\
def exponential(m):
    return -m * log(1 - unif())
""",
    'q2': dict(zip(Q2, ['1.0', '1.5', '12', '2 + 2', '0.25', '4.0'])),
    'q3': dict(zip(Q3, ['s_{i-1}', 's_i', 's_(i-1)/s_n', '(s_i)/(s_n)', 's[i]/s[n] - s[i-1]/s[n]', 'U(0,1)'])),
    # coordinates written out, indices shifted to 0-based by the parse, a number times a point
    'q4': """\
def ice_cream():
    centers = [[3, 4], [3, 2]]
    K = weighted_die([2, 1, 6/pi])
    if K == 3:
        U = sqrt(unif())
        V = unif()
        return [3, -1] + U * ((1 - V) * [-1, 3] + V * [1, 3])
    R = sqrt(unif())
    T = 2 * pi * unif()
    P = [R * cos(T), R * sin(T)]
    if K == 2:
        P[1] = abs(P[1])
    return centers[K - 1] + P
""",
    'q5': """\
def unit_cone():
    Z = unif() ** (1/3)
    R = Z * sqrt(unif())
    T = unif(0, 2 * pi)
    return [R * cos(T), R * sin(T), Z]
""",
    # the printed f copied along, a different loop shape, a looser envelope
    'q6': """\
def f(x):
    return (3 + sin(40 * x)) / x**(1/5) if 0 < x < 2 else 0

def spire():
    while True:
        X = 2 * unif()**1.25
        if coin(f(X) / (5 * X**(-0.2))):
            return X
""",
}

# One mistake in each question (original printing); two fields in question 2, the required blank in 3.
WRONG = {
    'q1': ANSWERS['alternate']['q1'],                                           # rate where the mean is asked
    'q2': dict(zip(Q2, ['1', '3/2', '1/12', '4', '4', '4'])),
    'q3': {**Q3_ANSWERS, '??6': 'Exponential(1)'},
    'q4': ANSWERS['original']['q4'].replace('R = sqrt(unif())', 'R = unif()'),  # points crowd the centres
    'q5': ANSWERS['original']['q5'].replace('Z = unif()**(1/3)', 'Z = unif()'),
    'q6': ANSWERS['original']['q6'].replace('done = Y < f(X)', 'done = True'),  # the envelope, never rejected
}

# One wrong field is still full credit under the "5 of 6" criteria.
FIVE_OF_SIX = {
    'q2': dict(zip(Q2, ['1', '3/2', '12', '4', '4', '4'])),
    'q3': {**Q3_ANSWERS, '??3': 's[i] / s[n]'},
}

# Answers a person must judge.
REVIEW = {
    # ??5 already simplified: accepted or not is the instructor's call; with ??1 wrong it decides the question.
    'q3': {**Q3_ANSWERS, '??1': 's[i]', '??5': 'w[i] / s[n]'},
    # The right distribution, by accept-reject.
    'q4': """\
def ice_cream():
    while True:
        X = unif(2, 4)
        Y = unif(-1, 5)
        if (X - 3)**2 + (Y - 4)**2 < 1:
            return [X, Y]
        if (X - 3)**2 + (Y - 2)**2 < 1 and Y > 2:
            return [X, Y]
        if Y < 2 and Y > -1 + 3 * abs(X - 3):
            return [X, Y]
""",
    'q5': """\
def unit_cone():
    X = unif(-1, 1)
    Y = unif(-1, 1)
    Z = unif()
    while X**2 + Y**2 >= Z**2:
        X = unif(-1, 1)
        Y = unif(-1, 1)
        Z = unif()
    return [X, Y, Z]
""",
}

# Code that runs and fails: proposed incorrect, for a grader to verify or to repair.
ERRORS = {
    # A 3-D offset on a 2-D point: points of different lengths.
    'q4': ANSWERS['original']['q4'].replace('scoop() + [3, 4]', 'scoop() + [3, 4, 0]')
                                   .replace('half_scoop() + [3, 2]', 'half_scoop() + [3, 2, 0]')
                                   .replace('+ [3, -1]', '+ [3, -1, 0]'),
    # The height added to the disc point as if it were a third coordinate.
    'q5': ANSWERS['original']['q5'].replace('return [X, Y, Z]', 'return [X, Y] + [0, 0, Z]'),
}

BLANK_CODE = {
    'q1': "def exponential(m):\n    pass\n", 'q4': "def ice_cream():\n    pass\n",
    'q5': "def unit_cone():\n    pass\n", 'q6': "def spire():\n    pass\n",
}

ALL_TRUE = {q: True for q in ALL}


def canonical(alt: int) -> dict:
    """Correct answers to every question of one printing."""
    return {q: ANSWERS[VERSIONS[alt].get(q, 'original')][q] for q in ALL}


def empty(answers: dict) -> dict:
    return {label: None for label in answers}


def cases():
    original = dict(printed=printed_on(0), blank=blank(0))
    alternate = dict(printed=printed_on(1), blank=blank(1))
    return [
        Case('alt0', **original, answers=canonical(0), expect=ALL_TRUE),
        Case('alt1', **alternate, answers=canonical(1), expect=ALL_TRUE),
        Case('forms', **original, answers=OTHER_FORMS, expect=ALL_TRUE),
        Case('wrong', **original, answers=WRONG, expect={q: False for q in ALL}),
        Case('five-of-six', **original, answers=FIVE_OF_SIX, expect={'q2': True, 'q3': True}),
        Case('review', **original, answers=REVIEW, expect={'q3': None, 'q4': None, 'q5': None}),
        Case('errors', **original, answers=ERRORS, expect={'q4': False, 'q5': False}),
        # The alternate printing with the original printing's answers. Question 3 is printed the
        # same; question 5 defines the other printing's function, which a person must look at.
        Case('crossed', **alternate, answers=canonical(0),
             expect={'q1': False, 'q2': False, 'q3': True, 'q4': False, 'q5': None, 'q6': False}),
        Case('empty', **alternate,
             answers={**BLANK_CODE, 'q2': empty(ANSWERS['original']['q2']), 'q3': empty(Q3_ANSWERS)},
             expect={q: 'blank' for q in ALL}),
        # One printed field is unreadable: the questions that read that field wait for a
        # person. The others read fields that were read as expected, and count.
        Case('unread', printed={'info': {**printed_on(0)['info'], 'q1_parameter': None}}, blank=blank(0),
             answers=canonical(0), expect={'q1': None, 'q2': None, 'q3': True, 'q4': True, 'q5': True, 'q6': True}),
        # Question 6's field contradicts question 5's: both are undecided; question 4's two fields agree.
        Case('disagree', printed={'info': {**printed_on(0)['info'], 'q6_sine_frequency': '20'}},
             blank=blank(0), answers=canonical(0),
             expect={'q1': True, 'q2': True, 'q3': True, 'q4': True, 'q5': None, 'q6': None}),
    ]
