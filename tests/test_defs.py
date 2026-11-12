"""Mistakes in an exam's definition are caught when it is defined, naming the question."""
from pathlib import Path

import pytest

from examinator import Bool, Exam, Field, Identity, Landmark, Printed, Region, load_exam
from examinator.std import fields, stages

EXAMS = Path(__file__).resolve().parents[1] / 'exams'


def define(**kw):
    exam = Exam('t', pages=2)
    kw = {'pages': [1], 'result': Bool(), 'fields': [Field('1', region=Region(1, 0, 200, 500, 300))],
          'stages': [], **kw}
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
    return Field(label, region=region, read=Printed(ask, reads={'a': 'x', 'b': 'y'} if reads is None else reads))


READ = [stages.read_fields()]


def test_printed_landmark_and_identity_regions_are_checked():
    with pytest.raises(ValueError, match="q: field 'side'"):
        define(fields=[printed(region=Region(1, 0, 0, 1000, 1001))], stages=READ, result=None)
    with pytest.raises(ValueError, match="landmark 'title'"):
        Exam('t', pages=2, landmarks=[Landmark('title', Region(0, 0, 0, 10, 10), ask="the title")])
    with pytest.raises(ValueError, match="identity 'name'"):
        Exam('t', pages=2, identity=[Identity('name', Region(1, 10, 10, 5, 20), ask="the name")])


def test_question_pages_are_printed_pages():
    with pytest.raises(ValueError, match="q: page 4"):
        define(pages=[1, 4])


def test_a_printed_field_needs_a_region_an_ask_and_text_readings():
    with pytest.raises(ValueError, match="q: field 'side' has a reader but no region"):
        define(fields=[printed(region=None)], stages=READ, result=None)
    with pytest.raises(ValueError, match="q: field 'side': a printed field needs an `ask`"):
        define(fields=[printed(ask=" ")], stages=READ, result=None)
    with pytest.raises(ValueError, match="q: field 'side': `reads` maps readings"):
        define(fields=[printed(reads={'a': 1})], stages=READ, result=None)


def test_a_printed_field_lives_in_a_parse_of_fields():
    """Its value sits in the question's parse, so the question needs a parse of
    fields: not none, and not source text."""
    with pytest.raises(ValueError, match="q: a field with a reader is part of a parse of fields"):
        define(fields=[printed()], stages=[], result=None)
    with pytest.raises(ValueError, match="q: a field with a reader is part of a parse of fields"):
        define(fields=[printed()], stages=stages.pseudocode(['f'], lambda ctx: None, grade_rule=lambda e: None))
    define(fields=[printed(), Field('1', region=Region(1, 0, 200, 500, 300))], stages=stages.math(key={'1': '1'}))
    q = define(fields=[printed()], stages=READ, result=None)
    assert q.result == {} and q.written == () and q.labels == ('side',)


def test_a_question_without_a_result_has_no_grade_stage():
    with pytest.raises(ValueError, match="q: a grade stage needs a `result`"):
        define(result=None, stages=stages.math(key={'1': '1'}))


def test_duplicate_labels():
    f = Field('1', region=Region(1, 0, 200, 500, 300))
    with pytest.raises(ValueError, match=r"q: duplicate field labels \['1'\]"):
        define(fields=[f, f])
    with pytest.raises(ValueError, match=r"q: duplicate field labels \['side'\]"):
        define(fields=[printed(), printed()], stages=READ, result=None)


ANSWER = Field('1', region=Region(1, 0, 200, 500, 300))


def test_a_key_per_value_is_picked_by_a_field_whose_readings_it_names():
    fields_ = [printed(), ANSWER]
    with pytest.raises(ValueError, match=r"q: the key is one per value of side.*\['y'\] have no key.*\['z'\] are not"):
        define(fields=fields_, stages=stages.math(key={'x': {'1': '1'}, 'z': {'1': '2'}}, by='side'))
    with pytest.raises(ValueError, match="q: a key per value needs `by=`"):
        define(stages=stages.math(key={'x': {'1': '1'}}))
    with pytest.raises(ValueError, match="q: `by=` picks one of several keys"):
        define(fields=fields_, stages=stages.math(key={'1': '1'}, by='side'))
    with pytest.raises(ValueError, match="q: q has no field 'width'"):
        define(fields=fields_, stages=stages.math(key={'x': {'1': '1'}}, by='width'))
    define(fields=fields_, stages=stages.math(key={'X': {'1': '1'}, 'y': {'1': '2'}}, by='side'))   # names fold case
    # a field without `reads` (or the exam's own function) does not say what it reads to
    define(fields=[printed(reads={}), ANSWER], stages=stages.math(key={'7': {'1': '1'}}, by='side'))
    define(stages=stages.math(key={'7': {'1': '1'}}, by=lambda ctx: '7'))


def test_a_key_picked_by_another_questions_field():
    exam = Exam('t', pages=2)
    by = fields.agree(fields.value('one', question='info'), fields.value('two', question='info'))
    key = {'x': {'1': '1'}, 'y': {'1': '2'}}
    with pytest.raises(ValueError, match="q: no question 'info' .declare it before"):
        exam.question('q', pages=[1], result=Bool(), fields=[ANSWER], stages=stages.math(key=key, by=by))
    exam.question('info', pages=[1], stages=READ,
                  fields=[printed('one'), printed('two', region=Region(1, 0, 100, 1000, 200),
                                                  reads={'c': 'x', 'd': 'y', 'e': 'w'})])
    exam.question('q', pages=[1], result=Bool(), fields=[ANSWER], stages=stages.math(key=key, by=by))
    with pytest.raises(ValueError, match="q2: info has no field 'three'"):
        exam.question('q2', pages=[1], result=Bool(), fields=[ANSWER],
                      stages=stages.math(key=key, by=fields.value('three', question='info')))
    with pytest.raises(ValueError, match=r"q3: the key is one per value of info two.*\['w'\] have no key"):
        exam.question('q3', pages=[1], result=Bool(), fields=[ANSWER],
                      stages=stages.math(key=key, by=fields.value('two', question='info')))


def test_a_check_writer_needs_only_the_top_level_names():
    import examinator
    from examinator.core.context import Unavailable
    from examinator.core.focus import Facet
    assert examinator.Unavailable is Unavailable and examinator.Facet is Facet
    assert {'Blocked', 'BadOutput', 'Unavailable', 'Facet', 'Evidence'} <= set(examinator.__all__)
