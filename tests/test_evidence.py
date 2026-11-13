"""Evidence: one stored shape, enforced where a check's return becomes a result
(`result.settle`). Everything here is synthetic."""
import json

import numpy as np
import pytest

from examinator import Check, Exam, Field, Result, load_exam
from examinator.core.defs import Grade, Number
from examinator.core.result import Judgement
from examinator.core.result import BadResult, Record, image_digest, settle
from examinator.core.runner import Runner
from examinator.core.workspace import Workspace
from examinator.std.blocks import MAX_EVIDENCE, MAX_IMAGE, Code, Details, Image, Note, Table

from conftest import workspace

PNG = b'\x89PNG\r\n\x1a\n' + b'synthetic'
POINTS = Check(lambda ctx: None).points


def blocks_of(*blocks):
    return settle(POINTS, Result(None, blocks=list(blocks)))


def test_every_block_is_one_shape_however_written():
    as_objects = blocks_of(Note("n", 'good'), Table(["a"], [[1, None]]), Code("x\ny", [2]),
                           Details("d", Note("inner")), Image(PNG, alt="a", title="t"))
    as_dicts = blocks_of(*[{**b} for b in (Note("n", 'good').to_json(), Table(["a"], [[1, None]]).to_json(),
                                           Code("x\ny", [2]).to_json(),
                                           {'type': 'details', 'summary': "d", 'blocks': [Note("inner").to_json()]},
                                           Image(PNG, alt="a", title="t").to_json())])
    assert as_objects == as_dicts
    digest = image_digest('image/png', PNG)
    assert as_objects.images == {digest: ('image/png', PNG)}
    first = as_objects.body['blocks'][0]       # top-level images first
    assert first == {'type': 'image', 'digest': digest, 'media_type': 'image/png', 'alt': "a", 'title': "t"}
    assert 'data' not in json.dumps(as_objects.body)
    assert Record.of_json(json.loads(json.dumps(as_objects.to_json()))) == as_objects      # across a pipe


def test_numbers_in_tables_become_json():
    got = blocks_of(Table([np.int64(1)], [[np.float32(0.5), np.int64(2), float('nan'), np.bool_(True)]]))
    table = got.body['blocks'][0]
    assert table == {'type': 'table', 'columns': ['1'], 'rows': [[0.5, 2, 'nan', True]]}
    assert json.loads(json.dumps(got.body, allow_nan=False)) == got.body


@pytest.mark.parametrize('block', [
    {'type': 'image', 'media_type': 'image/png', 'data': b'x' * (MAX_IMAGE + 1)},    # too large
    {'type': 'image', 'media_type': 'image/png', 'data': 'aGVsbG8='},                # base64 text, not bytes
    {'type': 'image', 'media_type': 'text/html', 'data': b'<script>'},               # not an image type
    Image(PNG, 'image/svg'),
    {'type': 'mystery'}, "not a block", {'type': 'note', 'text': 3}, {'type': 'note', 'text': "x", 'tone': 'red'},
    {'type': 'note', 'text': "x", 'colour': 'red'}, {'type': 'table', 'columns': [], 'rows': [[object()]]},
    {'type': 'code', 'text': "x", 'highlight': ["1"]}, {'type': 'details', 'summary': "d", 'blocks': "no"},
])
def test_a_block_that_breaks_the_grammar_is_a_check_error(block):
    with pytest.raises(BadResult, match="^the check returned"):
        blocks_of(block)


def test_a_judgement_is_text():
    with pytest.raises(BadResult, match="^the check returned a judgement"):
        settle(POINTS, Result(None, {'x': {'outcome': None, 'note': np.float64(1.5)}}))


NUMBER = Number(min=0, max=3)


@pytest.mark.parametrize('points, out', [
    (NUMBER, float('nan')), (NUMBER, float('inf')), (Number(), float('nan')), (NUMBER, 4),
    (NUMBER, Grade(1, (float('inf'),))), (NUMBER, Grade(1, reason=5)), (POINTS, Grade(True, ('ok', 2))),
    (POINTS, "maybe"), (POINTS, 1.0), (POINTS, Result(True, reason=3)),
    (POINTS, Result(True, {'x': ['correct']})), (POINTS, Result(True, {'x': ('correct', "n", "extra")})),
    (POINTS, Result(True, {'x': 'right'})), (POINTS, Result(True, {'x': {'outcome': 'correct', 'score': 1}})),
    (POINTS, Result(True, {1: 'correct'})), (POINTS, Result(True, {'x': ('correct', 3)})),
])
def test_settle_is_the_boundary_for_the_whole_result(points, out):
    """Value a bool or a finite number its points accept, flags text, reason text
    or None, judgements of their shape: anything else is a structural `BadResult`."""
    with pytest.raises(BadResult, match="^the check returned"):
        settle(points, out)


def test_what_fits_the_boundary_is_stored_in_normal_form():
    got = settle(NUMBER, Result(Grade(2, ['blank']), {'x': ('incorrect', ""), 'y': None, 'z': Judgement('correct')}))
    assert got.body['grade'] == {'value': 2.0, 'flags': ['blank'], 'reason': None}
    assert got.body['fields'] == {'x': {'outcome': 'incorrect'}, 'y': {'outcome': None}, 'z': {'outcome': 'correct'}}
    assert settle(POINTS, None).body['grade'] == {'value': None, 'flags': [], 'reason': "undecided"}


def test_a_malformed_return_is_undecided_with_the_structural_reason(tmp_path):
    exam = Exam('ev', pages=1)
    exam.question('q', pages=[1], fields=[Field('x', default='1')], checks={
        'nan': Check(lambda ctx: float('nan'), points=NUMBER, isolate=False),
        'shape': Check(lambda ctx: Result(True, {'x': ['correct']}), isolate=False)})
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(exam, ws)
    try:
        for name in ('nan', 'shape'):
            assert runner.compute('s1', 'q', name).ok
            grade = ws.store.get('s1', f'q/{name}', 'result').body['grade']
            assert grade['value'] is None and grade['reason'].startswith("the check returned")
    finally:
        runner.close()
        ws.close()


def test_deep_details_and_total_evidence_are_bounded():
    deep = Note("bottom")
    for _ in range(20):
        deep = Details("d", deep)
    with pytest.raises(BadResult):
        blocks_of(deep)
    many = [Image(bytes([i]) * (MAX_IMAGE - 1)) for i in range(MAX_EVIDENCE // MAX_IMAGE + 1)]
    with pytest.raises(BadResult, match="bytes of evidence"):
        blocks_of(*many)
    blocks_of(*many[:2])                        # within the limits


def bad_image(ctx):
    return Result(True, blocks=[{'type': 'image', 'media_type': 'image/png', 'data': b'x' * (MAX_IMAGE + 1)}])


def plots(ctx):
    return Result(True, blocks=[Note("text"), Image(PNG), Details("more", Image(PNG + b'2'))])


def test_a_bad_result_is_stored_as_undecided_with_the_reason(tmp_path):
    exam = Exam('ev', pages=1)
    exam.question('q', pages=[1], fields=[Field('x', default='1')],
                  checks={'bad': Check(bad_image, isolate=False), 'good': Check(plots, isolate=False)})
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(exam, ws)
    try:
        assert runner.compute('s1', 'q', 'bad').ok
        body = ws.store.get('s1', 'q/bad', 'result').body
        assert body['grade']['value'] is None and body['grade']['reason'].startswith("the check returned an image")
        assert all(b['type'] != 'image' for b in body['blocks'])
        assert runner.compute('s1', 'q', 'good').ok
        body = ws.store.get('s1', 'q/good', 'result').body
        assert [b['type'] for b in body['blocks']] == ['image', 'note', 'details']
        digests = [body['blocks'][0]['digest'], body['blocks'][2]['blocks'][0]['digest']]
        assert [ws.store.image('s1', 'q/good', d)[1] for d in digests] == [PNG, PNG + b'2']
        ws.store.delete_submission('s1')        # its results go, and their images
        assert ws.store.get('s1', 'q/good', 'result') is None
        assert all(ws.store.image('s1', 'q/good', d) is None for d in digests)
    finally:
        runner.close()
        ws.close()


ISOLATED = '''
from examinator import Check, Exam, Field, Result
from examinator.std.blocks import Image, Table
import numpy as np

def figure(ctx):
    return Result(True, blocks=[Table(["n"], [[np.int64(3)]]), Image(b'png bytes', title="t")])

def bad(ctx):
    return Result(True, blocks=[{'type': 'image', 'media_type': 'text/html', 'data': b'<b>'}])

exam = Exam('iso', pages=1)
exam.question('q', pages=[1], fields=[Field('x', default='1')],
              checks={'figure': Check(figure), 'bad': Check(bad)})
'''


def test_an_isolated_check_passes_the_same_shape(tmp_path):
    pkg = tmp_path / 'iso_exam'
    pkg.mkdir()
    (pkg / 'exam.py').write_text(ISOLATED)
    exam = load_exam(pkg)
    (tmp_path / 'ws' / 'pdfs').mkdir(parents=True)
    ws = Workspace(tmp_path / 'ws', exam)
    runner = Runner(exam, ws)
    try:
        assert runner.compute('s', 'q', 'figure').ok and runner.compute('s', 'q', 'bad').ok
        body = ws.store.get('s', 'q/figure', 'result').body
        image = body['blocks'][0]
        assert body['blocks'][1]['rows'] == [[3]]
        assert ws.store.image('s', 'q/figure', image['digest']) == ('image/png', b'png bytes')
        bad = ws.store.get('s', 'q/bad', 'result').body
        assert bad['grade']['value'] is None and bad['grade']['reason'].startswith("the check returned an image type")
    finally:
        runner.close()
        ws.close()
