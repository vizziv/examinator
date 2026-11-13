"""Exam 3: nine questions on 22 printed pages, in eight printed versions.

Three independent binary choices each switch a group of questions between an
`original` and an `alternate` printing. The first question, `info`, has no
checks: it holds the printed fields that tell the printing, two per choice. The
questions of a group read that choice's two fields, which must agree.
"""
from examinator import Bool, Check, Exam, Field, Printed, Region
from examinator.std import fields, math, readers

from . import huge_constants, language, notes, point_process, rectangles, weibullish

exam = Exam('exam_03', title="Exam 3", pages=22)


# --- helpers local to this exam -------------------------------------------

def boxes(page, labels, tops, left, right, height):
    return [Field(label, region=Region(page, left, top, right, top + height))
            for label, top in zip(labels, tops)]


def band(page, top, bottom):
    return Region(page, 0, top, 1000, bottom)


def numbered(n, *accepted):
    """A choice that is the n-th printed option: also its number, 'option n' and its letter."""
    return math.Choice(*accepted, str(n), f'option {n}', 'ABCDEF'[n - 1])


OPTIONS = ['increases', 'stays the same', 'decreases', 'not enough info']


def option(n):
    return numbered(n, OPTIONS[n - 1], *(['same', 'unchanged'] if n == 2 else []))


def letter(c):
    return numbered(ord(c) - 64)


SHINY = Bool(counts=False)       # the second part of a pseudocode question does not count


# --- the printing: two printed fields per choice, in a question of their own -----

# printed field -> {a text it may show: the printing it shows it on}, the first text per printing as printed
TEXTS: dict[str, dict[str, str]] = {}


def printed(label, region, ask, original, alternate):
    TEXTS[label] = {**{text: 'original' for text in original}, **{text: 'alternate' for text in alternate}}
    return Field(label, region=region, read=Printed(ask))


# On the alternate printing explanations C and D swap, so both sentences are on the page:
# `q6_option_c` asks for one literally printed word of the item labelled C, and its band
# stops above the answer boxes (which also print "C" and "D").
exam.question(
    'info', title="Printing", pages=[7, 8, 10, 13, 17, 18],
    fields=[printed('q5_confidence', band(10, 208, 474),
                    "the overall confidence percent printed in question 5's task sentence, "
                    "as a number without the % sign", ['95'], ['90']),
            printed('q9_observations', band(18, 318, 649),
                    "the three observed numbers printed in question 9, in order, as a JSON list",
                    ['[14, 12, 15]'], ['[14, 11, 15]']),
            printed('q6_option_c', band(13, 40, 275),
                    "the single word printed immediately after 'They' in the list item labelled 'C.' "
                    "(the item labelled C only, not item D)", ['use'], ['do']),
            printed('q3_first_height', band(7, 70, 170),
                    "the single italic word printed immediately before the word 'height' in the "
                    "sentence labelled 3.1", ['base'], ['spike']),
            printed('q8_first_n', band(17, 0, 273),
                    "the value labelled n in the expression labelled 8.1", ['3'], ['4']),
            printed('q4_negative_branch', band(8, 0, 487),
                    "question 4's density expression for -1 <= r < 0, in plain ASCII", ['r+1', '1+r'], ['-r'])])


def bit(*labels):
    """One choice: its printed fields, which must agree."""
    return fields.agree(*(fields.table(fields.value(label, question='info'), TEXTS[label]) for label in labels))


BIT1 = bit('q5_confidence', 'q9_observations')
BIT2 = bit('q6_option_c', 'q3_first_height')
BIT4 = bit('q8_first_n', 'q4_negative_branch')


# --- questions, in printed order ---------------------------------------------
# Questions 2 and 7 are pseudocode: one code field per function, read in one go, and
# one check per field ('check' counts, 'shiny' does not).

def right_right(p, q):
    """Question 1's key when the printed region is [p, 1] x [0, q]."""
    return {'1.1': rectangles.key('0', q, p, '1'),
            '1.2': rectangles.scalar(f'2*{q}*(1-{p})'),
            '1.3': rectangles.scalar(f'{q}*(1-{p})'),
            '1.4': rectangles.scalar('1/2')}


exam.question(
    'q1', title="Right right", pages=[3],
    fields=boxes(3, ['1.1', '1.2', '1.3', '1.4'], [96, 207, 318, 430], 500, 885, 100),
    read=readers.answers(symbols=rectangles.SYMBOLS, functions=rectangles.FUNCTIONS, notes=notes.RECTANGLE),
    check=math.keyed(by=BIT1, symbols=rectangles.SYMBOLS, functions=rectangles.FUNCTIONS,
                     key={'original': right_right('a', 'b'), 'alternate': right_right('b', 'a')}))

exam.question(
    'q2', title="Feeling Weibullish", pages=[4, 5],
    fields=[Field('2.1', region=band(4, 290, 930)), Field('2.2', region=band(5, 300, 930)),
            Field(huge_constants.NAME, default='10')],          # a grader may set another value
    read=readers.code({'2.1': ['weibull'], '2.2': ['weibullish']}, language=language.SCALAR,
                      notes=notes.KEEP_CONSTANTS, then=huge_constants.rewrite),
    checks={'check': Check(weibullish.check_21, timeout=100),
            'shiny': Check(weibullish.check_22, SHINY, timeout=100)})

Q3 = ['3.1', '3.2', '3.3', '3.4']
exam.question(
    'q3', title="A tale of two heights", pages=[7],
    fields=boxes(7, Q3, [98, 308, 518, 728], 650, 900, 110),
    read=readers.answers(),
    check=math.keyed(by=BIT2, key={'original':  dict(zip(Q3, map(option, [2, 2, 2, 1]))),
                                   'alternate': dict(zip(Q3, map(option, [2, 3, 2, 2])))}))

Q4 = [f'4.{part}.{bound}' for part in (1, 2) for bound in 'abcd']
exam.question(
    'q4', title="Sawtooth GPS", pages=[9],
    fields=boxes(9, Q4, [99, 199, 298, 397, 519, 618, 718, 817], 500, 885, 92),
    read=readers.answers(),
    check=math.keyed(by=BIT4, key={
        'original':  dict(zip(Q4, ['-1', '-1/2', '0', '1/2', '-1', '-1/4', '0', '3/4'])),
        'alternate': dict(zip(Q4, ['-1/2', '0', '1/2', '1', '-3/4', '0', '1/4', '1']))}))

exam.question(
    'q5', title="Mmm, measuring multiple means", pages=[11],
    fields=boxes(11, ['5'], [98], 710, 900, 150),
    read=readers.answers(),
    check=math.keyed(by=BIT1, key={'original':  {'5': numbered(4, '30')},
                                   'alternate': {'5': numbered(3, '15')}}))

Q6 = ['6.1', '6.2', '6.3', '6.4']
exam.question(
    'q6', title="Poisson un, poisson deux, poisson rouge, poisson bleu", pages=[13],
    fields=boxes(13, Q6, [295, 456, 617, 777], 710, 900, 110),
    read=readers.answers(),
    check=math.keyed(by=BIT2, key={'original':  dict(zip(Q6, map(letter, 'CDAB'))),
                                   'alternate': dict(zip(Q6, map(letter, 'DCAB')))}))

exam.question(
    'q7', title="It's v Poisson", pages=[14, 15],
    fields=[Field('7.1', region=band(14, 320, 930)), Field('7.2', region=band(15, 235, 930))],
    read=readers.code({'7.1': ['poisson_process'], '7.2': ['poisson_dist']}, language=point_process.LANGUAGE,
                      notes=notes.POINT_PROCESS),
    checks={'check': Check(point_process.check_71, timeout=40),
            'shiny': Check(point_process.check_72, SHINY, timeout=40)})

exam.question(
    'q8', title="Triangular likelihood", pages=[17],
    fields=boxes(17, ['8.1', '8.2'], [98, 518], 500, 885, 110),
    read=readers.answers(),
    check=math.keyed(by=BIT4, key={'original':  {'8.1': '6/25**3', '8.2': '0'},
                                   'alternate': {'8.1': '0', '8.2': '6/25**3'}}))

exam.question(
    'q9', title="Capital L likelihood", pages=[19],
    fields=boxes(19, ['9.a', '9.b'], [96, 207], 500, 885, 100),
    read=readers.answers(),
    check=math.keyed(by=BIT1, key={'original':  {'9.a': '12', '9.b': '16'},
                                   'alternate': {'9.a': '11', '9.b': '19'}}))
