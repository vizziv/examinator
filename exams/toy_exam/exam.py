"""A deliberately small exam, unlike any real one: it exists to show that the
library is generic. One question scored as a number in a range, printed in
three versions: a printed field in the question says which, and picks the key.
One boolean question printed the same for everyone."""
from examinator import Check, Exam, Field, Grade, Identity, Landmark, Number, Printed, Region
from examinator.std import math, readers

exam = Exam('toy', title="Toy exam", pages=4,
            print_area=[50, 40, 950, 900], cover_pages=1,
            # intake: how to tell a scanned sheet's printed page, and where students write who they are
            landmarks=[Landmark('title', region=Region(1, 300, 90, 700, 150),
                                ask="the title printed in this box", reads={'TOY EXAM': 1}),
                       Landmark('page_number', region=Region(1, 900, 940, 990, 990),
                                ask="the printed page number, as a number")],
            identity=[Identity('name', Region(1, 100, 200, 800, 280), roster='name',
                               ask="the student's full name"),
                      Identity('id', Region(1, 100, 330, 600, 410), roster='id',
                               ask="the student ID number")])


def count_correct(outcomes):
    """One point per correct field; undecided if any field is."""
    values = list(outcomes.values())
    if None in values:
        return None
    if all(o == 'blank' for o in values):
        return Grade(0, ('blank',))
    return values.count('correct')


AREAS = ['A.1', 'A.2', 'A.3']
exam.question(
    'areas', title="Areas", pages=[2],
    fields=[Field('side', Region(2, 0, 60, 1000, 240),         # printed, not written: the version
                  read=Printed("the printed side length of the square, as a number",
                               reads={'2': 'red', '3': 'green', '5': 'blue'}))]
           + [Field(label, Region(2, 400, top, 900, top + 120)) for label, top in zip(AREAS, [300, 480, 660])],
    read=readers.answers(symbols=['x']),
    check=Check(math.keyed(symbols={'x': 'positive'}, by='side', grade=count_correct, key={
                    'red':   dict(zip(AREAS, ['4', '4*x', '2*x**2'])),
                    'green': dict(zip(AREAS, ['9', '6*x', '3*x**2'])),
                    'blue':  dict(zip(AREAS, ['25', '10*x', '5*x**2']))}),
                Number(int, min=0, max=3)))

exam.question(
    'parity', title="Parity", pages=[3],
    fields=[Field('B', Region(3, 400, 300, 900, 420))],
    read=readers.answers(),
    check=math.keyed({'B': math.Choice('even', 'E', 'option 1')}))
