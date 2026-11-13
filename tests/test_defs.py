"""Mistakes in an exam's definition are caught when it is defined, naming the question."""
from pathlib import Path

import pytest

from examinator import Ask, Bool, Check, Exam, Field, Landmark, Number, Printed, Region, load_exam
from examinator.core.defs import IDENTITY
from examinator.std import fields, math, readers

EXAMS = Path(__file__).resolve().parents[1] / 'exams'
ANSWER = Field('1', Region(1, 0, 200, 500, 300))


def define(**kw):
    exam = Exam('t', pages=2)
    kw = {'pages': [1], 'fields': [ANSWER], 'read': readers.answers(), **kw}
    return exam.question('q', **kw)


@pytest.mark.parametrize('name', ['toy_exam', 'exam_01', 'exam_02', 'exam_03'])
def test_the_example_exams_pass_every_rule(name):
    assert load_exam(EXAMS / name).questions


@pytest.mark.parametrize('region, said', [
    (Region(3, 0, 0, 100, 100), "page 3 is not a printed page"),
    (Region(1, 0, 0, 1200, 100), "0 <= left < right <= 1000"),
    (Region(1, 500, 0, 400, 100), "0 <= left < right <= 1000"),
    (Region(1, 0, 300, 100, 300), "top < bottom")])
def test_a_field_region_off_the_printed_page(region, said):
    with pytest.raises(ValueError, match="q: field '1'") as e:
        define(fields=[Field('1', region=region)])
    assert said in str(e.value)


def printed(label='side', region=Region(1, 0, 0, 1000, 100), reads=None, ask="the printed letter"):
    return Field(label, region, read=Printed(ask, reads={'a': 'x', 'b': 'y'} if reads is None else reads))


def test_printed_landmark_and_identity_regions_are_checked():
    with pytest.raises(ValueError, match="q: field 'side'"):
        define(fields=[printed(region=Region(1, 0, 0, 1000, 1001))])
    with pytest.raises(ValueError, match="landmark 'title'"):
        Exam('t', pages=2, landmarks=[Landmark('title', Region(0, 0, 0, 10, 10), ask="the title")])
    with pytest.raises(ValueError, match="identity 'name'"):
        Exam('t', pages=2, identity={'name': Field('name', Region(1, 10, 10, 5, 20))})


def test_identity_maps_roster_columns_to_ordinary_fields():
    """Any subset of the roster's columns; the group always has a field per column."""
    for mapping in ({}, None, {'id': Field('id', Region(1, 0, 0, 9, 9))},
                    {'email': Field('email', None, read=Printed("the printed email")), 'name': Field('name')}):
        exam = Exam('t', pages=1, identity=mapping)
        g = exam.identity
        assert g.id == IDENTITY and g.apart and g.checks == () and exam.questions[IDENTITY] is g
        assert list(exam.grading) == [] and IDENTITY not in exam.grading
        mapped = list(mapping or {})
        assert list(g.labels) == mapped + [c for c in ('name', 'id', 'email') if c not in mapped]
        for f in g.fields:
            if f.label in mapped:
                assert f == mapping[f.label] or (mapping[f.label].read is None and f.region == mapping[f.label].region)
            else:
                assert f.region is None        # not placed: drawn in the app
            assert isinstance(f.read, Ask)     # handwriting unless the field says otherwise
    assert isinstance(Exam('t', pages=1, identity={'email': Field('email', read=Printed("x"))}).identity.field('email').read, Printed)
    with pytest.raises(ValueError, match="roster column"):
        Exam('t', pages=1, identity={'nickname': Field('nickname')})
    with pytest.raises(ValueError, match="labelled 'name'"):
        Exam('t', pages=1, identity={'name': Field('who')})
    with pytest.raises(ValueError):
        Exam('t', pages=1).question(IDENTITY, pages=[1])


def test_question_pages_are_printed_pages():
    with pytest.raises(ValueError, match="q: page 4"):
        define(pages=[1, 4])


def test_a_printed_field_needs_an_ask_and_text_readings_and_may_wait_for_its_region():
    with pytest.raises(ValueError, match="q: field 'side': a field read by asking needs an `ask`"):
        define(fields=[printed(ask=" ")])
    with pytest.raises(ValueError, match="q: field 'side': `reads` maps readings"):
        define(fields=[printed(reads={'a': 1})])
    q = define(fields=[printed(region=None)])          # not read until its region is set
    assert q.labels == ('side',) and q.written == () and q.groups()[0][1][0].region is None


def test_who_reads_what():
    q = define(fields=[printed(), ANSWER, Field('2')])
    assert [(type(r).__name__, [f.label for f in fs]) for r, fs in q.groups()] == \
        [('Printed', ['side']), ('Answers', ['1', '2'])]
    assert define(read=None).groups() == []             # typed by a person only


def test_checks_and_their_columns():
    q = define()
    assert q.checks == () and q.columns() == ['q']
    q = define(check=lambda ctx: None)
    assert [c.name for c in q.checks] == ['check'] and q.columns() == ['q']
    assert q.check('check').points == Bool()
    q = define(checks={'check': lambda ctx: None, 'shiny': Check(None, Bool(counts=False))})
    assert q.columns() == ['q.check', 'q.shiny'] and q.check('shiny').fn is None
    with pytest.raises(ValueError, match="q: give `check` or `checks`"):
        define(check=lambda ctx: None, checks={'x': lambda ctx: None})
    with pytest.raises(ValueError, match="check name 'a.b'"):
        define(checks={'a.b': lambda ctx: None})


def test_duplicate_labels():
    with pytest.raises(ValueError, match=r"q: duplicate field labels \['1'\]"):
        define(fields=[ANSWER, ANSWER])
    with pytest.raises(ValueError, match=r"q: duplicate field labels \['side'\]"):
        define(fields=[printed(), printed()])
    with pytest.raises(ValueError, match="no '/'"):
        define(fields=[Field('a/b')])


def test_a_key_names_the_questions_fields():
    with pytest.raises(ValueError, match=r"q: the key names \['2'\], which are not its fields"):
        define(check=math.keyed({'1': '1', '2': '2'}))


def test_a_key_per_value_is_picked_by_a_field_whose_readings_it_names():
    fields_ = [printed(), ANSWER]
    with pytest.raises(ValueError, match=r"q: the key is one per value of side.*\['y'\] have no key.*\['z'\] are not"):
        define(fields=fields_, check=math.keyed({'x': {'1': '1'}, 'z': {'1': '2'}}, by='side'))
    with pytest.raises(ValueError, match="q: a key per value needs `by=`"):
        define(check=math.keyed({'x': {'1': '1'}}))
    with pytest.raises(ValueError, match="q: `by=` picks one of several keys"):
        define(fields=fields_, check=math.keyed({'1': '1'}, by='side'))
    with pytest.raises(ValueError, match="q: q has no field 'width'"):
        define(fields=fields_, check=math.keyed({'x': {'1': '1'}}, by='width'))
    define(fields=fields_, check=math.keyed({'X': {'1': '1'}, 'y': {'1': '2'}}, by='side'))   # names fold case
    # a field without `reads` (or the exam's own function) does not say what it reads to
    define(fields=[printed(reads={}), ANSWER], check=math.keyed({'7': {'1': '1'}}, by='side'))
    define(check=math.keyed({'7': {'1': '1'}}, by=lambda ctx: '7'))


def test_a_key_picked_by_another_questions_field():
    exam = Exam('t', pages=2)
    by = fields.agree(fields.value('one', question='info'), fields.value('two', question='info'))
    key = {'x': {'1': '1'}, 'y': {'1': '2'}}
    with pytest.raises(ValueError, match="q: no question 'info' .declare it before"):
        exam.question('q', pages=[1], fields=[ANSWER], check=math.keyed(key, by=by))
    assert 'q' not in exam.questions                    # a question that fails is not added
    exam.question('info', pages=[1], fields=[printed('one'), printed('two', region=Region(1, 0, 100, 1000, 200),
                                                                     reads={'c': 'x', 'd': 'y', 'e': 'w'})])
    exam.question('q', pages=[1], fields=[ANSWER], check=math.keyed(key, by=by))
    with pytest.raises(ValueError, match="q2: info has no field 'three'"):
        exam.question('q2', pages=[1], fields=[ANSWER], check=math.keyed(key, by=fields.value('three', question='info')))
    with pytest.raises(ValueError, match=r"q3: the key is one per value of info two.*\['w'\] have no key"):
        exam.question('q3', pages=[1], fields=[ANSWER], check=math.keyed(key, by=fields.value('two', question='info')))


def test_points():
    assert Bool().coerce('blank').flags == ('blank',) and Bool(worth=2).points(True) == 2
    assert Number(int, 0, 3).coerce(2).value == 2
    with pytest.raises(ValueError):
        Number(int, 0, 3).coerce(4)


def test_a_check_writer_needs_only_the_top_level_names():
    import examinator
    from examinator.core.context import Unavailable
    from examinator.core.focus import Facet
    assert examinator.Unavailable is Unavailable and examinator.Facet is Facet
    assert {'BadOutput', 'Unavailable', 'Facet', 'Result', 'Judgement', 'Undecided', 'Check'} <= set(examinator.__all__)
