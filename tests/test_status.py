"""The grade of a check: one test per row of the status table (MODEL.md), on
plain stored values. Everything here is synthetic."""
from examinator import Bool, Check, Exam, Field, Grade, Number
from examinator.core.defs import DEFAULT
from examinator.core.status import Decision, Stored, Value, fields_state, status, total

CHECK = Check(lambda ctx: None)
DEFN, CODE = 'd1', 'c1'


def values(**settled):
    """'q/<name>' values at revision 1; True: settled by a person, 'reader', False: unsettled."""
    return {f'q/{k}': Value("1", 1, {True: 'person', 'reader': 'reader', False: None}[s]) for k, s in settled.items()}


def stored(value=True, reads=None, defn=DEFN, code=CODE, flags=()):
    return Stored(Grade(value, tuple(flags), None if value is not None else "cannot decide"),
                  {'q/a': 1} if reads is None else reads, defn, code, rev=10)


def st(check=CHECK, vals=None, result=None, decision=None):
    return status(check, values(a=True) if vals is None else vals, result, decision, defn=DEFN, code=CODE)


# --- one test per row, in order ---

def test_decided_when_its_fields_are_unchanged():
    d = Decision(Grade(False), {'q/a': 1}, 'ta', rev=11)
    s = st(result=stored(True), decision=d)
    assert (s.state, s.value, s.author, s.counts) == ('decided', False, 'ta', True)


def test_decided_counts_even_on_unsettled_or_marked_fields():
    vals = {'q/a': Value("[REVIEW: 1 or 7]", 1)}
    assert st(vals=vals, decision=Decision(Grade(True), {'q/a': 1}, 'ta', 11)).counts


def test_decided_outdated_when_a_field_changed_since():
    vals = {'q/a': Value("2", 5, 'person')}
    s = st(vals=vals, result=stored(True, reads={'q/a': 5}), decision=Decision(Grade(True), {'q/a': 1}, 'ta', 11))
    assert (s.state, s.value, s.counts) == ('decided_outdated', True, False)


def test_decided_outdated_when_a_field_seen_as_not_read_is_read_now():
    d = Decision(Grade(True), {'q/a': 1, 'q/b': None}, 'ta', 11)
    assert st(vals=values(a=True, b=True), decision=d).state == 'decided_outdated'


def test_to_decide_without_a_check_function():
    s = st(check=Check(None))
    assert (s.state, s.counts) == ('to_decide', False)


def test_to_decide_when_current_settled_and_undecided():
    s = st(result=stored(None))
    assert (s.state, s.value, s.reason, s.counts) == ('to_decide', None, "cannot decide", False)


def test_to_decide_when_the_check_read_no_field():
    s = st(result=stored(True, reads={}))
    assert (s.state, s.counts) == ('to_decide', False)
    assert s.value is True              # the proposal is still shown


def test_not_run():
    assert st().state == 'not_run'


def test_outdated_when_a_field_it_read_changed():
    vals = {'q/a': Value("2", 7, 'person')}
    s = st(vals=vals, result=stored(True))
    assert (s.state, s.value, s.counts) == ('outdated', True, False)


def test_outdated_when_a_field_it_did_not_find_is_read_now():
    assert st(vals=values(a=True, b=True), result=stored(None, reads={'q/a': 1, 'q/b': None})).state == 'outdated'


def test_outdated_when_the_check_or_the_code_changed():
    assert st(result=stored(True, defn='d0')).state == 'outdated'
    assert st(result=stored(True, code='c0')).state == 'outdated'


def test_unverified_when_a_field_it_read_is_not_settled():
    s = st(vals=values(a=False), result=stored(True))
    assert (s.state, s.value, s.waiting, s.counts) == ('unverified', True, ('q/a',), False)


def test_unverified_when_it_read_a_field_that_is_not_read():
    s = st(vals={}, result=stored(None, reads={'q/a': None}))
    assert s.state == 'unverified' and s.waiting == ('q/a',)


def test_unverified_comes_before_an_undecided_proposal():
    assert st(vals=values(a=False), result=stored(None)).state == 'unverified'


def test_graded():
    s = st(result=stored(True))
    assert (s.state, s.value, s.counts) == ('graded', True, True)


def test_settled_by_the_reader_is_settled():
    assert st(vals=values(a='reader'), result=stored(True)).state == 'graded'


def test_a_field_read_at_its_default_is_settled_while_it_has_no_value():
    at_default = stored(True, reads={'q/a': 1, 'q/k': DEFAULT})
    assert st(result=at_default).state == 'graded'
    # a value set since (by a person, or one nobody settled) outdates it; dropped again, it is current
    assert st(vals={**values(a=True), 'q/k': Value("20", 4, 'person')}, result=at_default).state == 'outdated'
    assert st(vals={**values(a=True), 'q/k': Value("20", 4)}, result=at_default).state == 'outdated'
    assert st(vals=values(a=True), result=at_default).state == 'graded'


def test_only_the_fields_read_matter():
    vals = {**values(a=True), 'q/b': Value("x", 2), 'r/c': Value("y", 3)}       # unsettled, but not read
    assert st(vals=vals, result=stored(False)).state == 'graded'


# --- totals and fields ---

def test_total_counts_what_counts_and_follows_points():
    exam = Exam('t', pages=1)
    exam.question('q', pages=[1], fields=[Field('a')],
                  checks={'one': Check(None, Bool()), 'two': Check(None, Number(int, 0, 5)),
                          'shiny': Check(None, Bool(counts=False))})
    from examinator.core.status import Status
    statuses = {('q', 'one'): Status('graded', Grade(True)), ('q', 'two'): Status('decided', Grade(4)),
                ('q', 'shiny'): Status('graded', Grade(True))}
    assert total(exam, statuses) == (5.0, True)
    statuses[('q', 'two')] = Status('unverified', Grade(4))
    assert total(exam, statuses) == (1.0, False)


def test_fields_state_of_a_question_without_checks():
    exam = Exam('t', pages=1)
    q = exam.question('q', pages=[1], fields=[Field('a'), Field('b')])
    assert fields_state(q, {}) == 'not_read'
    assert fields_state(q, values(a=True)) == 'not_read'
    assert fields_state(q, values(a=True, b=False)) == 'unverified'
    assert fields_state(q, {'q/a': Value("1", 1, 'reader'), 'q/b': Value("", 2, 'person')}) == 'verified'
    q = exam.question('r', pages=[1], fields=[Field('a'), Field('k', default='10')])
    assert fields_state(q, {'r/a': Value("1", 1, 'person')}) == 'verified'          # k: its default
