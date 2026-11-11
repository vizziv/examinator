"""A deliberately small exam, unlike any real one: it exists to show that the
library is generic. One question scored as a number in a range with three
variants, and one boolean question with no variants."""
from examinator import Bool, Cue, Exam, Field, Grade, Number, Region
from examinator.std import math, stages

exam = Exam('toy', title="Toy exam", pages=4, front=[1], extra=[4],
            scan_window=[50, 40, 950, 900], cover_pages=1)


def count_correct(evidence):
    """One point per correct field; undecided if any field is."""
    outcomes = evidence.outcomes()
    if None in outcomes:
        return None
    if all(o == 'blank' for o in outcomes):
        return Grade(0, ('blank',))
    return outcomes.count('correct')


AREAS = ['A.1', 'A.2', 'A.3']
exam.question(
    'areas', title="Areas", pages=[2], result=Number(int, min=0, max=3),
    variants=['red', 'green', 'blue'], trust=1,
    cues=[Cue('areas_side', region=Region(2, 0, 60, 1000, 240),
              ask="the printed side length of the square, as a number",
              reads={'2': 'red', '3': 'green', '5': 'blue'})],
    fields=[Field(label, region=Region(2, 400, top, 900, top + 120))
            for label, top in zip(AREAS, [300, 480, 660])],
    stages=[stages.transcribe(),
            stages.parse_math(symbols=['x']),
            stages.check_math(symbols={'x': 'positive'}, key={
                'red':   dict(zip(AREAS, ['4', '4*x', '2*x**2'])),
                'green': dict(zip(AREAS, ['9', '6*x', '3*x**2'])),
                'blue':  dict(zip(AREAS, ['25', '10*x', '5*x**2']))}),
            stages.grade(count_correct)])

exam.question(
    'parity', title="Parity", pages=[3], result=Bool(),
    fields=[Field('B', region=Region(3, 400, 300, 900, 420))],
    stages=stages.math(key={'B': math.Choice('even', 'E', 'option 1')}))
