"""Exam 1: six questions on 16 printed pages, in two printings.

Questions 1, 4, 5 and 6 are pseudocode samplers; 2 is six numbers; 3 is six
blanks of a proof. Every question is all-or-nothing (`Bool`), two of them
under a "k of n" credit criterion. The printings differ in every question
except 3: the `info` question (no checks) holds five printed fields, and each
varied question's check reads two of them, which must agree (`versions.py`).
"""
from examinator import Bool, Check, Exam, Field, Region
from examinator.std import math, readers

from . import blanks, cone, exponential, ice_cream, language, notes, spire
from .versions import FIELDS, PRINTING

exam = Exam('exam_01', title="Exam 1", pages=16, cover_pages=1)

SHINY = Bool(counts=False)      # the course's "shiny" questions do not count toward the total


# --- helpers local to this exam ---------------------------------------------------

def boxes(page, labels, tops, left=606, right=852, height=88, inset=4):
    """Printed boxes with their top edges at `tops`. The boxes are stacked closer than
    the padding added around a crop, so each region is inset to keep its neighbours out."""
    return [Field(label, Region(page, left, top + inset, right, top + height - inset))
            for label, top in zip(labels, tops)]


def band(page, top, bottom):
    return Region(page, 0, top, 1000, bottom)


def code(entries, *, language, parse_notes=""):
    """How a sampler's answer is read: pseudocode asking for `entries`."""
    return readers.code(entries, language=language, notes=f"{parse_notes} {notes.FORMULAS}".strip())


# --- questions, in printed order -------------------------------------------------------

exam.question('info', title="Printing", pages=[2, 4, 8, 10, 12], fields=FIELDS)

exam.question(
    'q1', title="Exponential sampling", pages=[3], fields=[Field('1', band(3, 90, 930))],
    read=code(['exponential'], language=language.SCALAR, parse_notes=notes.SCALAR),
    check=exponential.check)

Q2 = [f'2.{part}.{name}' for part in (1, 2, 3) for name in 'mc']
exam.question(
    'q2', title="Comprehensive coverage", pages=[5], fields=boxes(5, Q2, [91, 188, 371, 468, 652, 749]),
    read=readers.answers(notes=notes.NUMBERS),
    check=math.keyed(by=PRINTING['q2'], grade=math.at_least(5), key={
        'original':  dict(zip(Q2, ['1', '3/2', '12', '4', '1/4', '4'])),
        'alternate': dict(zip(Q2, ['1', '1', '1/12', '4', '4', '1']))}))

Q3 = [f'??{n}' for n in range(1, 7)]
exam.question(
    'q3', title="Weighty choices", pages=[7], fields=boxes(7, Q3, [91, 188, 285, 381, 479, 575]),
    read=readers.answers(notes=notes.SUBSCRIPTS),
    check=math.keyed(grade=math.at_least(5, required=('??6',)), key=dict(zip(Q3, [
        blanks.indexed('s[i-1]'), blanks.indexed('s[i]'),
        blanks.indexed('s[i-1] / s[n]'), blanks.indexed('s[i] / s[n]'),
        blanks.indexed('(s[i] - s[i-1]) / s[n]', 'w[i] / s[n]'),
        blanks.StandardUniform()]))))

exam.question(
    'q4', title="For which we all scream", pages=[9], fields=[Field('4', band(9, 90, 930))],
    read=code(['ice_cream'], language=language.POINTS, parse_notes=notes.POINTS),
    check=ice_cream.check)

exam.question(
    'q5', title="Radical acceptance... in space!", pages=[11], fields=[Field('5', band(11, 90, 930))],
    read=code(list(cone.ENTRIES.values()), language=language.POINTS, parse_notes=notes.POINTS),
    check=Check(cone.check, SHINY))

exam.question(
    'q6', title="Sample the spire", pages=[13], fields=[Field('6', band(13, 90, 930))],
    read=code(['spire'], language=language.COLLECTIONS, parse_notes=notes.SCALAR + " " + notes.GIVEN_F),
    check=Check(spire.check, SHINY, timeout=90))
