"""Exam 3: nine questions on 22 printed pages, in eight printed versions.

Three independent binary choices each switch a group of questions between an
`original` and an `alternate` printing; every question in a group reads the
same two printed cues, which must agree.
"""
from examinator import Bool, Cue, Exam, Field, Region
from examinator.std import math, stages

from . import huge_constants, language, notes, point_process, rectangles, weibullish

exam = Exam('exam_03', title="Exam 3", pages=22,
            scan_window=[60, 55, 920, 825], cover_pages=1)


# --- helpers local to this exam -------------------------------------------

def boxes(page, labels, tops, left, right, height):
    return [Field(label, region=Region(page, left, top, right, top + height))
            for label, top in zip(labels, tops)]


def band(page, top, bottom):
    return Region(page, 0, top, 1000, bottom)


def variants(*cues):
    return dict(variants=['original', 'alternate'], cues=list(cues))


def numbered(n, *accepted):
    """A choice that is the n-th printed option: also its number, 'option n' and its letter."""
    return math.Choice(*accepted, str(n), f'option {n}', 'ABCDEF'[n - 1])


OPTIONS = ['increases', 'stays the same', 'decreases', 'not enough info']


def option(n):
    return numbered(n, OPTIONS[n - 1], *(['same', 'unchanged'] if n == 2 else []))


def letter(c):
    return numbered(ord(c) - 64)


def marks(**parts):
    """Each named mark follows one field's outcome."""
    def rule(evidence):
        return {name: evidence.outcome(label) for name, label in parts.items()}
    return rule


TWO_MARKS = {'check': Bool(), 'shiny': Bool(counts=False)}


# --- cues: two printed clues per group of questions -----------------------

BIT1 = variants(
    Cue('q5_confidence', region=band(10, 208, 474),
        ask="the overall confidence percent printed in question 5's task sentence, as a number without the % sign",
        reads={'95': 'original', '90': 'alternate'}),
    Cue('q9_observations', region=band(18, 318, 649),
        ask="the three observed numbers printed in question 9, in order, as a JSON list",
        reads={'[14, 12, 15]': 'original', '[14, 11, 15]': 'alternate'}))
# On the alternate printing explanations C and D swap, so both sentences are on the page:
# the cue asks for one literally printed word of the item labelled C, and its band stops
# above the answer boxes (which also print "C" and "D").
BIT2 = variants(
    Cue('q6_option_c', region=band(13, 40, 275),
        ask="the single word printed immediately after 'They' in the list item labelled 'C.' "
            "(the item labelled C only, not item D)",
        reads={'use': 'original', 'do': 'alternate'}),
    Cue('q3_first_height', region=band(7, 70, 170),
        ask="the single italic word printed immediately before the word 'height' in the sentence labelled 3.1",
        reads={'base': 'original', 'spike': 'alternate'}))
BIT4 = variants(
    Cue('q8_first_n', region=band(17, 0, 273),
        ask="the value labelled n in the expression labelled 8.1",
        reads={'3': 'original', '4': 'alternate'}),
    Cue('q4_negative_branch', region=band(8, 0, 487),
        ask="question 4's density expression for -1 <= r < 0, in plain ASCII",
        reads={'r+1': 'original', '1+r': 'original', '-r': 'alternate'}))


# --- questions, in printed order ---------------------------------------------
# Questions 2 and 7 are pseudocode: custom checks, two independent marks each.

def right_right(p, q):
    """Question 1's key when the printed region is [p, 1] x [0, q]."""
    return {'1.1': rectangles.key('0', q, p, '1'),
            '1.2': rectangles.scalar(f'2*{q}*(1-{p})'),
            '1.3': rectangles.scalar(f'{q}*(1-{p})'),
            '1.4': rectangles.scalar('1/2')}


exam.question(
    'q1', title="Right right", pages=[3], result=Bool(), **BIT1,
    fields=boxes(3, ['1.1', '1.2', '1.3', '1.4'], [96, 207, 318, 430], 500, 885, 100),
    stages=stages.math(symbols=rectangles.SYMBOLS, functions=rectangles.FUNCTIONS, notes=notes.RECTANGLE,
                       key={'original': right_right('a', 'b'), 'alternate': right_right('b', 'a')}))

exam.question(
    'q2', title="Feeling Weibullish", pages=[4, 5], result=TWO_MARKS,
    fields=[Field('2.1', region=band(4, 290, 930)), Field('2.2', region=band(5, 300, 930))],
    stages=stages.pseudocode(['weibull', 'weibullish'], weibullish.check, language=language.SCALAR,
                             notes=notes.KEEP_CONSTANTS,
                             then=huge_constants.rewrite, grade_rule=marks(check='2.1', shiny='2.2'),
                             timeout=100))

Q3 = ['3.1', '3.2', '3.3', '3.4']
exam.question(
    'q3', title="A tale of two heights", pages=[7], result=Bool(), **BIT2,
    fields=boxes(7, Q3, [98, 308, 518, 728], 650, 900, 110),
    stages=stages.math(key={'original':  dict(zip(Q3, map(option, [2, 2, 2, 1]))),
                            'alternate': dict(zip(Q3, map(option, [2, 3, 2, 2])))}))

Q4 = [f'4.{part}.{bound}' for part in (1, 2) for bound in 'abcd']
exam.question(
    'q4', title="Sawtooth GPS", pages=[9], result=Bool(), **BIT4,
    fields=boxes(9, Q4, [99, 199, 298, 397, 519, 618, 718, 817], 500, 885, 92),
    stages=stages.math(key={
        'original':  dict(zip(Q4, ['-1', '-1/2', '0', '1/2', '-1', '-1/4', '0', '3/4'])),
        'alternate': dict(zip(Q4, ['-1/2', '0', '1/2', '1', '-3/4', '0', '1/4', '1']))}))

exam.question(
    'q5', title="Mmm, measuring multiple means", pages=[11],
    result=Bool(), **BIT1,
    fields=boxes(11, ['5'], [98], 710, 900, 150),
    stages=stages.math(key={'original':  {'5': numbered(4, '30')},
                            'alternate': {'5': numbered(3, '15')}}))

Q6 = ['6.1', '6.2', '6.3', '6.4']
exam.question(
    'q6', title="Poisson un, poisson deux, poisson rouge, poisson bleu",
    pages=[13], result=Bool(), **BIT2,
    fields=boxes(13, Q6, [295, 456, 617, 777], 710, 900, 110),
    stages=stages.math(key={'original':  dict(zip(Q6, map(letter, 'CDAB'))),
                            'alternate': dict(zip(Q6, map(letter, 'DCAB')))}))

exam.question(
    'q7', title="It's v Poisson", pages=[14, 15], result=TWO_MARKS,
    fields=[Field('7.1', region=band(14, 320, 930)), Field('7.2', region=band(15, 235, 930))],
    stages=stages.pseudocode(['poisson_process', 'poisson_dist'], point_process.check,
                             language=point_process.LANGUAGE, notes=notes.POINT_PROCESS,
                             grade_rule=marks(check='7.1', shiny='7.2'), timeout=40))

exam.question(
    'q8', title="Triangular likelihood", pages=[17], result=Bool(), **BIT4,
    fields=boxes(17, ['8.1', '8.2'], [98, 518], 500, 885, 110),
    stages=stages.math(key={'original':  {'8.1': '6/25**3', '8.2': '0'},
                            'alternate': {'8.1': '0', '8.2': '6/25**3'}}))

exam.question(
    'q9', title="Capital L likelihood", pages=[19], result=Bool(), **BIT1,
    fields=boxes(19, ['9.a', '9.b'], [96, 207], 500, 885, 100),
    stages=stages.math(key={'original':  {'9.a': '12', '9.b': '16'},
                            'alternate': {'9.a': '11', '9.b': '19'}}))
