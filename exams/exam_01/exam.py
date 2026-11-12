"""Exam 1: six questions on 16 printed pages, in two printings.

Questions 1, 4, 5 and 6 are pseudocode samplers; 2 is six numbers; 3 is six
blanks of a proof. Every question is all-or-nothing (`Bool`), two of them
under a "k of n" credit criterion. The printings differ in every question
except 3: the `info` question (no grade) holds five printed fields, and each varied
question's check reads two of them, which must agree (`versions.py`).
"""
from examinator import Bool, Exam, Field, Region
from examinator.std import prompts, stages

from . import blanks, cone, exponential, ice_cream, language, notes, rules, spire
from .versions import FIELDS, PRINTING

exam = Exam('exam_01', title="Exam 1", pages=16,
            scan_window=[60, 55, 920, 825], cover_pages=1)

SHINY = Bool(counts=False)      # the course's "shiny" questions do not count toward the total


# --- helpers local to this exam ---------------------------------------------------

def boxes(page, labels, tops, left=606, right=852, height=88, inset=4):
    """Printed boxes with their top edges at `tops`. The boxes are stacked closer than
    the padding added around a crop, so each region is inset to keep its neighbours out."""
    return [Field(label, region=Region(page, left, top + inset, right, top + height - inset))
            for label, top in zip(labels, tops)]


def band(page, top, bottom):
    return Region(page, 0, top, 1000, bottom)


def sampler(module, entries, *, language, parse_notes="", timeout=60):
    """A pseudocode question with one answer, whose check reads the printing."""
    return [stages.transcribe(prompts.TRANSCRIBE_PSEUDOCODE, notes=parse_notes),
            stages.parse_pseudocode(entries, language=language, notes=f"{parse_notes} {notes.FORMULAS}".strip()),
            stages.check(module.check, timeout=timeout),
            stages.grade(rules.single(module.LABEL))]


# --- questions, in printed order -------------------------------------------------------

exam.question('info', title="Printing", pages=[2, 4, 8, 10, 12], fields=FIELDS, stages=[stages.read_fields()])

exam.question(
    'q1', title="Exponential sampling", pages=[3], result=Bool(),
    fields=[Field('1', region=band(3, 90, 930))],
    stages=sampler(exponential, ['exponential'], language=language.SCALAR, parse_notes=notes.SCALAR))

Q2 = [f'2.{part}.{name}' for part in (1, 2, 3) for name in 'mc']
exam.question(
    'q2', title="Comprehensive coverage", pages=[5], result=Bool(),
    fields=boxes(5, Q2, [91, 188, 371, 468, 652, 749]),
    stages=stages.math(by=PRINTING['q2'], notes=notes.NUMBERS, grade_rule=rules.at_least(5), key={
        'original':  dict(zip(Q2, ['1', '3/2', '12', '4', '1/4', '4'])),
        'alternate': dict(zip(Q2, ['1', '1', '1/12', '4', '4', '1']))}))

Q3 = [f'??{n}' for n in range(1, 7)]
exam.question(
    'q3', title="Weighty choices", pages=[7], result=Bool(),
    fields=boxes(7, Q3, [91, 188, 285, 381, 479, 575]),
    stages=[stages.transcribe(notes=notes.SUBSCRIPTS),
            stages.parse_math(notes=notes.SUBSCRIPTS),
            stages.check_math(key=dict(zip(Q3, [
                blanks.indexed('s[i-1]'), blanks.indexed('s[i]'),
                blanks.indexed('s[i-1] / s[n]'), blanks.indexed('s[i] / s[n]'),
                blanks.indexed('(s[i] - s[i-1]) / s[n]', 'w[i] / s[n]'),
                blanks.StandardUniform()]))),
            stages.grade(rules.at_least(5, required=('??6',)))])

exam.question(
    'q4', title="For which we all scream", pages=[9], result=Bool(),
    fields=[Field('4', region=band(9, 90, 930))],
    stages=sampler(ice_cream, ['ice_cream'], language=language.POINTS, parse_notes=notes.POINTS))

exam.question(
    'q5', title="Radical acceptance... in space!", pages=[11], result=SHINY,
    fields=[Field('5', region=band(11, 90, 930))],
    stages=sampler(cone, list(cone.ENTRIES.values()), language=language.POINTS,
                   parse_notes=notes.POINTS))

exam.question(
    'q6', title="Sample the spire", pages=[13], result=SHINY,
    fields=[Field('6', region=band(13, 90, 930))],
    stages=sampler(spire, ['spire'], language=language.COLLECTIONS,
                   parse_notes=notes.SCALAR + " " + notes.GIVEN_F, timeout=90))
