"""Mistakes in an exam's definition are caught when it is defined, naming the question."""
from pathlib import Path

import pytest

from examinator import Bool, Cue, Exam, Field, Identity, Landmark, Region, load_exam
from examinator.std import stages

EXAMS = Path(__file__).resolve().parents[1] / 'exams'


def cue(name='c', reads=None, region=Region(1, 0, 0, 1000, 100), **kw):
    return Cue(name, region=region, ask="the printed letter", reads=reads or {'a': 'x', 'b': 'y'}, **kw)


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


def test_cue_landmark_and_identity_regions_are_checked():
    with pytest.raises(ValueError, match="q: cue 'c'"):
        define(variants=['x', 'y'], cues=[cue(region=Region(1, 0, 0, 1000, 1001))], trust=1)
    with pytest.raises(ValueError, match="landmark 'title'"):
        Exam('t', pages=2, landmarks=[Landmark('title', Region(0, 0, 0, 10, 10), ask="the title")])
    with pytest.raises(ValueError, match="identity 'name'"):
        Exam('t', pages=2, identity=[Identity('name', Region(1, 10, 10, 5, 20), ask="the name")])


def test_question_pages_are_printed_pages():
    with pytest.raises(ValueError, match="q: page 4"):
        define(pages=[1, 4])


def test_a_cue_reading_to_a_name_that_is_not_a_variant():
    with pytest.raises(ValueError, match=r"q: cue 'c' reads to \['z'\]"):
        define(variants=['x', 'y'], cues=[cue(reads={'a': 'x', 'b': 'y', 'c': 'z'})], trust=1)


def test_a_cue_voting_for_nothing_is_allowed_only_beside_a_computed_cue():
    helper = cue('h', reads={'a': 'on', 'b': 'off'})
    with pytest.raises(ValueError, match="q: cue 'h' reads to none of the variants"):
        define(variants=['x', 'y'], cues=[cue(), helper], require=1, trust=1)
    computed = Cue('f', fn=lambda ctx: None)
    define(variants=['x', 'y'], cues=[helper, computed], require=1, trust=1)     # exam_02's pattern


def test_a_variant_no_cue_can_select():
    with pytest.raises(ValueError, match="q: variant 'z' can be selected by 0 cue"):
        define(variants=['x', 'y', 'z'], cues=[cue()], trust=1)
    with pytest.raises(ValueError, match="variant 'y' can be selected by 1 cue.* needs 2"):
        define(variants=['x', 'y'], cues=[cue(), cue('d', reads={'a': 'x', 'b': 'x'})])
    define(variants=['x', 'y'], cues=[cue(), cue('d', reads={'a': 'x', 'b': 'x'})], require=1)


def test_a_map_cue_needs_a_region_and_a_question():
    with pytest.raises(ValueError, match="q: cue 'c' needs a region"):
        define(variants=['x', 'y'], cues=[Cue('c', reads={'a': 'x', 'b': 'y'})], trust=1)


def test_require_beyond_the_cues_is_never_reached():
    with pytest.raises(ValueError, match="q: require=3 but the question has 2 cue.*set require=2"):
        define(variants=['x', 'y'], cues=[cue(), cue('d')], require=3)
    define(variants=['x', 'y'], cues=[cue()])      # trust 2 > 1 cue: a person verifies every variant
    define(variants=['x', 'y'])                    # no cues: a person chooses every variant


def test_duplicate_labels_variants_and_cues():
    f = Field('1', region=Region(1, 0, 200, 500, 300))
    with pytest.raises(ValueError, match=r"q: duplicate field labels \['1'\]"):
        define(fields=[f, f])
    with pytest.raises(ValueError, match="q: duplicate variants"):
        define(variants=['x', 'x', 'y'], cues=[cue()], trust=1)
    with pytest.raises(ValueError, match="q: duplicate cue names"):
        define(variants=['x', 'y'], cues=[cue(), cue()])


def test_a_key_per_variant_names_the_questions_variants():
    key = {'x': {'1': '1'}, 'z': {'1': '2'}}
    with pytest.raises(ValueError, match=r"q: the key is one per variant.*\['y'\] have no key.*\['z'\] are not"):
        define(variants=['x', 'y'], cues=[cue()], trust=1, stages=stages.math(key=key))
    with pytest.raises(ValueError, match="q: the key is one per variant"):
        define(stages=stages.math(key={'x': {'1': '1'}}))      # no variants at all
    define(variants=['x', 'y'], cues=[cue()], trust=1, stages=stages.math(key={'x': {'1': '1'}, 'y': {'1': '2'}}))


def test_a_check_writer_needs_only_the_top_level_names():
    import examinator
    from examinator.core.context import Unavailable
    from examinator.core.focus import Facet
    assert examinator.Unavailable is Unavailable and examinator.Facet is Facet
    assert {'Blocked', 'BadOutput', 'Unavailable', 'Facet', 'Evidence'} <= set(examinator.__all__)
