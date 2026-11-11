"""Overrides: live edits of exam data. Everything here is synthetic."""
import dataclasses
from pathlib import Path

import pytest
from PIL import Image

from examinator import Bool, Cue, Exam, Field, Region, Stage, load_exam
from examinator.core import overrides, variants
from examinator.core.defs import stage_hash
from examinator.core.runner import Runner
from examinator.core.store import Conflict, Submission
from examinator.core.workflow import part_status
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.std import math, stages

from conftest import SCRIPT, make_exam, parse

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'


def hashes(exam):
    return {qid: {s.name: stage_hash(q, s) for s in q.stages} for qid, q in exam.questions.items()}


def make_keyed_exam():
    """Like the mini exam, but its checks hold their keys as data."""
    exam = Exam('keyed', pages=2)
    exam.question(
        'a', pages=[1], result={'x': Bool(), 'y': Bool(counts=False)}, variants=['one', 'two'],
        cues=[Cue('c1', region=Region(1, 0, 0, 1000, 200), ask="first cue", reads={'1': 'one', '2': 'two'}),
              Cue('c2', region=Region(1, 0, 200, 1000, 400), ask="second cue", reads={'i': 'one', 'ii': 'two'})],
        fields=[Field('x', region=Region(1, 0, 500, 1000, 600)), Field('y')],
        stages=[stages.transcribe(),
                Stage('parse', parse, inputs=('transcript',), uses_model=True, editable=True),
                stages.check_math(key={'one': {'x': '1', 'y': '2'}, 'two': {'x': '10', 'y': '20'}}),
                stages.grade(lambda ev: {'x': ev.outcome('x'), 'y': ev.outcome('y')})])
    exam.question(
        'b', pages=[2], result=Bool(), fields=[Field('x')],
        stages=[stages.transcribe(),
                Stage('parse', parse, inputs=('transcript',), uses_model=True, editable=True),
                stages.check_math(key={'x': math.Choice('yes')}),
                stages.grade(math.all_correct)])
    return exam


@pytest.fixture
def kenv(tmp_path):
    """(exam, workspace, runner) for the keyed exam, every question graded and verified."""
    exam = make_keyed_exam()
    root = tmp_path / 'ws'
    (root / 'pdfs').mkdir(parents=True)
    pages = [Image.new('RGB', (200, 260), 'white') for _ in range(2)]
    for sid in ('s1', 's2'):
        pages[0].save(root / 'pdfs' / f'{sid}.pdf', 'PDF', save_all=True, append_images=pages[1:])
    ws = Workspace(root, exam)
    ws.store.put_submissions([Submission(s, s, "", f'{s}.pdf') for s in ('s1', 's2')])
    runner = Runner(exam, ws, model=FakeModel(dict(SCRIPT)))
    for sid in ('s1', 's2'):
        for qid, q in exam.questions.items():
            assert all(o.ok for o in runner.run_from(sid, qid, q.stages[0].name))
            runner.verify(sid, qid, 'parse', author='t', expect_rev=ws.store.get(sid, qid, 'parse').rev)
    yield exam, ws, runner
    runner.close()
    ws.close()


def status(exam, ws, sid, qid, part=''):
    st = part_status(exam.questions[qid], part, ws.store.for_question(sid, qid))
    return st.state, st.value


# --- paths ------------------------------------------------------------------------

def test_toy_exam_paths():
    exam = load_exam(TOY)
    entries = {e.path: e for e in overrides.editable(exam)}
    assert entries['areas/pages'].kind == 'pages' and entries['areas/pages'].base == [2]
    assert entries['areas/fields/A.1/region'].kind == 'region'
    assert entries['areas/fields/A.1/region'].base == [2, 400, 300, 900, 420]
    assert entries['areas/cues/areas_side/region'].base == [2, 0, 60, 1000, 240]
    assert entries['areas/cues/areas_side/ask'].kind == 'text'
    assert entries['areas/cues/areas_side/reads'].kind == 'map'
    assert entries['areas/stages/evidence/key/red/A.2'].base == '4*x'
    assert entries['areas/stages/transcript/notes'].kind == 'text'
    assert entries['areas/stages/parse/prompt'].kind == 'text'
    assert entries['parity/stages/evidence/key/B'].kind == 'choice'
    assert entries['parity/stages/evidence/key/B'].base == ['even', 'E', 'option 1']
    assert not any(e.overridden for e in entries.values())
    # functions, names, variants and stage wiring are not data to edit
    assert not any('/grade/' in p or p.endswith(('/fn', '/name', '/label', '/variants', '/inputs'))
                   for p in entries)


def test_mini_exam_paths():
    paths = {e.path for e in overrides.editable(make_exam())}
    assert {'a/pages', 'a/require', 'a/trust', 'a/fields/x/region', 'a/cues/c1/reads', 'a/cues/c2/ask',
            'a/stages/transcript/notes', 'a/stages/variant/prompt', 'b/pages'} <= paths
    assert not any(p.startswith(('a/stages/evidence', 'a/stages/parse', 'a/stages/grade')) for p in paths)
    assert 'b/require' not in paths      # no variants


def test_escaping():
    assert overrides.join('q', 'fields', '1/2', 'region') == 'q/fields/1%2F2/region'
    assert overrides.split('q/fields/1%2F2%25/region') == ('q', 'fields', '1/2%', 'region')
    exam = Exam('e', pages=1)
    exam.question('q', pages=[1], result=Bool(), fields=[Field('1/2'), Field('4.1.a')],
                  stages=[stages.check(lambda ctx: None, inputs=())])
    paths = {e.path for e in overrides.editable(exam)}
    assert {'q/fields/1%2F2/region', 'q/fields/4.1.a/region'} <= paths
    assert overrides.apply(exam, {'q/fields/1%2F2/region': [1, 0, 0, 10, 10]}) == []
    assert exam.questions['q'].fields[0].region == Region(1, 0, 0, 10, 10)


# --- applying -----------------------------------------------------------------------

def test_apply_and_clear_restore_the_base_exactly():
    exam = load_exam(TOY)
    before = hashes(exam)
    base = dict(exam.questions)
    skipped = overrides.apply(exam, {'areas/fields/A.1/region': [2, 400, 310, 900, 430],
                                     'areas/stages/evidence/key/red/A.2': '2*2*x',
                                     'parity/stages/evidence/key/B': ['odd'],
                                     'areas/pages': [2, 3]})
    assert skipped == []
    q = exam.questions['areas']
    assert q.fields[0].region == Region(2, 400, 310, 900, 430) and q.pages == (2, 3)
    assert q.stage('evidence').fn.key['red']['A.2'] == '2*2*x'
    assert list(q.stage('evidence').fn.key) == ['red', 'green', 'blue']       # order kept
    assert exam.questions['parity'].stage('evidence').fn.key['B'].accepted == ('odd',)
    assert exam.base['parity'].stage('evidence').fn.key['B'].accepted == ('even', 'E', 'option 1')
    assert base['areas'].fields[0].region == Region(2, 400, 300, 900, 420)    # nothing mutated
    after = hashes(exam)
    assert after['areas']['evidence'] != before['areas']['evidence']
    assert after['areas']['variant'] == before['areas']['variant']
    entries = {e.path: e for e in overrides.editable(exam)}
    assert entries['areas/pages'].overridden and entries['areas/pages'].current == [2, 3]
    assert entries['areas/pages'].base == [2]

    assert overrides.apply(exam, {}) == []
    assert hashes(exam) == before
    assert all(exam.questions[qid] is base[qid] for qid in base)
    # applying an equal value gives an equal hash
    overrides.apply(exam, {'areas/fields/A.1/region': [2, 400, 300, 900, 420]})
    assert hashes(exam) == before


def test_validation():
    exam = load_exam(TOY)
    ok = overrides.validate
    assert ok(exam, 'areas/fields/A.1/region', (2, 1, 2, 3, 4)) == [2, 1, 2, 3, 4]
    assert ok(exam, 'areas/require', None) is None
    bad = [('areas/fields/A.1/region', [2, 500, 0, 400, 100]),      # left >= right
           ('areas/fields/A.1/region', [2, 0, 0, 1001, 100]),
           ('areas/fields/A.1/region', [9, 0, 0, 10, 10]),           # no such page
           ('areas/fields/A.1/region', [2, 0, 0, 10]),
           ('areas/cues/areas_side/region', None),
           ('areas/pages', []), ('areas/pages', [5]), ('areas/pages', [2, 2]), ('areas/pages', ['2']),
           ('areas/trust', True), ('areas/trust', -1),
           ('areas/stages/transcript/notes', 3), ('areas/stages/transcript/crops', 'yes'),
           ('areas/cues/areas_side/reads', {'2': 'purple'}), ('areas/cues/areas_side/reads', ['2']),
           ('parity/stages/evidence/key/B', []), ('parity/stages/evidence/key/B', 'odd'),
           ('areas/stages/grade/fn', 'x'), ('areas/variants', ['a']), ('areas/stages/evidence/key/red/Z', '1'),
           ('nope/pages', [1])]
    for path, value in bad:
        with pytest.raises(ValueError):
            ok(exam, path, value)


def test_stale_overrides_are_skipped_and_reported():
    exam = load_exam(TOY)
    skipped = overrides.apply(exam, {'areas/fields/Z.9/region': [2, 0, 0, 10, 10],
                                     'gone/pages': [1],
                                     'areas/pages': [7],
                                     'areas/stages/transcript/notes': "Answers are integers."})
    assert sorted(s.path for s in skipped) == ['areas/fields/Z.9/region', 'areas/pages', 'gone/pages']
    assert exam.questions['areas'].stage('transcript').fn.notes == "Answers are integers."
    assert exam.overrides == {'areas/stages/transcript/notes': "Answers are integers."}


# --- the store ----------------------------------------------------------------------

def test_store_conflicts_and_history(env):
    exam, ws, _ = env
    store = ws.store
    a = store.set_override('a/pages', [1], author='ta', expect_rev=None)
    with pytest.raises(Conflict):
        store.set_override('a/pages', [2], author='tb', expect_rev=None)
    with pytest.raises(Conflict):
        store.set_override('a/pages', [2], author='tb', expect_rev=a.rev - 1)
    b = store.set_override('a/pages', [2], author='tb', expect_rev=a.rev)
    assert b.rev > a.rev and store.overrides()['a/pages'].value == [2]
    with pytest.raises(Conflict):
        store.clear_override('a/pages', expect_rev=a.rev)
    store.clear_override('a/pages', expect_rev=b.rev)
    assert store.overrides() == {}
    assert [(h.value, h.author) for h in store.override_history('a/pages')] == [([2], 'tb'), ([1], 'ta')]
    assert all(h.replaced_at for h in store.override_history('a/pages'))


def test_set_and_describe_changes(env):
    exam, ws, _ = env
    with pytest.raises(ValueError):
        overrides.set(exam, ws.store, 'a/fields/x/region', [1, 9, 9, 1, 1], author='ta')
    assert ws.store.overrides() == {}
    change = overrides.set(exam, ws.store, 'a/fields/x/region', [1, 400, 310, 900, 430], author='ta')
    assert change.questions == ()          # a crop region is not part of any stage's definition
    overrides.set(exam, ws.store, 'a/cues/c1/reads', {'1': 'one'}, author='ta')
    lines = overrides.describe_changes(exam, ws.store)
    assert lines[0] == ("a/cues/c1/reads: {'1': 'one', '2': 'two'} -> {'1': 'one'}  (ta, "
                        + ws.store.overrides()['a/cues/c1/reads'].updated_at[:10] + ")")
    assert lines[1].startswith('a/fields/x/region: None -> Region(1, 400, 310, 900, 430)  (ta, ')
    # a fresh session sees the same exam once it refreshes from the store
    fresh = make_exam()
    assert overrides.refresh(fresh, ws.store) == []
    assert hashes(fresh) == hashes(exam)
    overrides.clear(exam, ws.store, 'a/cues/c1/reads')
    assert exam.questions['a'].cues[0].reads == {'1': 'one', '2': 'two'}


def test_choice_described_as_python(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    try:
        overrides.set(exam, ws.store, 'parity/stages/evidence/key/B', ['odd', 'O'], author='ta')
        assert overrides.describe_changes(exam, ws.store)[0].startswith(
            "parity/stages/evidence/key/B: Choice('even', 'E', 'option 1') -> Choice('odd', 'O')  (ta, ")
    finally:
        ws.close()


# --- workflow ------------------------------------------------------------------------

def test_key_override_outdates_only_that_question(kenv):
    exam, ws, runner = kenv
    assert status(exam, ws, 's1', 'a', 'x') == ('graded', True)
    assert status(exam, ws, 's1', 'b') == ('graded', True)
    change = overrides.set(exam, ws.store, 'a/stages/evidence/key/one/x', '5', author='ta')
    assert change.questions == ('a',)
    for sid in ('s1', 's2'):
        assert status(exam, ws, sid, 'a', 'x')[0] == 'outdated'
        assert status(exam, ws, sid, 'b') == ('graded', True)
    for sid in ('s1', 's2'):
        for qid in change.questions:
            runner.recheck(sid, qid)
    assert status(exam, ws, 's1', 'a', 'x') == ('graded', False)
    assert status(exam, ws, 's1', 'a', 'y') == ('graded', True)
    overrides.clear(exam, ws.store, 'a/stages/evidence/key/one/x')
    assert status(exam, ws, 's1', 'a', 'x')[0] == 'outdated'
    runner.recheck('s1', 'a')
    assert status(exam, ws, 's1', 'a', 'x') == ('graded', True)


def test_choice_key_override_regrades(kenv):
    exam, ws, runner = kenv
    overrides.set(exam, ws.store, 'b/stages/evidence/key/x', ['no'], author='ta')
    assert status(exam, ws, 's1', 'b')[0] == 'outdated'
    runner.recheck('s1', 'b')
    assert status(exam, ws, 's1', 'b') == ('graded', False)


def test_region_and_notes_overrides_do_not_outdate_checks(kenv):
    exam, ws, runner = kenv
    before = hashes(exam)
    overrides.set(exam, ws.store, 'a/fields/x/region', [1, 0, 510, 1000, 610], author='ta')
    assert hashes(exam) == before
    overrides.set(exam, ws.store, 'a/stages/transcript/notes', "Answers are integers.", author='ta')
    after = hashes(exam)
    assert after['a']['transcript'] != before['a']['transcript']
    assert {k: v for k, v in after['a'].items() if k != 'transcript'} == \
        {k: v for k, v in before['a'].items() if k != 'transcript'}
    assert status(exam, ws, 's1', 'a', 'x') == ('graded', True)


# --- cues -------------------------------------------------------------------------------

def variant_body(ws, sid='s1'):
    return ws.store.get(sid, 'a', 'variant').body


def test_changed_reads_renarrow_from_stored_readings(kenv):
    exam, ws, runner = kenv
    assert variant_body(ws)['resolved'] == 'one'
    model_calls = len(runner.model.calls)
    overrides.set(exam, ws.store, 'a/cues/c1/reads', {'1': 'two', '2': 'one'}, author='ta')
    overrides.set(exam, ws.store, 'a/cues/c2/reads', {'i': 'two', 'ii': 'one'}, author='ta')
    runner.recheck('s1', 'a')
    body = variant_body(ws)
    assert body['resolved'] == 'two'
    assert {n: c['reading'] for n, c in body['cues'].items()} == {'c1': '1', 'c2': 'i'}
    assert len(runner.model.calls) == model_calls       # no new reading
    assert status(exam, ws, 's1', 'a', 'x') == ('graded', False)     # checked against variant two's key


@pytest.mark.parametrize('path, value', [('a/cues/c1/region', [1, 0, 0, 1000, 250]),
                                         ('a/cues/c1/ask', "the first cue, printed")])
def test_changed_crop_or_question_leaves_the_cue_unread(kenv, path, value):
    exam, ws, runner = kenv
    overrides.set(exam, ws.store, path, value, author='ta')
    runner.recheck('s1', 'a')
    body = variant_body(ws)
    assert body['resolved'] is None
    assert {n: c['reading'] for n, c in body['cues'].items()} == {'c1': None, 'c2': 'i'}
    assert status(exam, ws, 's1', 'a', 'x')[1] is None
    assert variant_body(ws, 's2')['resolved'] == 'one'          # not rechecked yet
    # a rerun of the variant stage reads the cue again
    assert runner.run_stage('s1', 'a', 'variant').ok
    assert variant_body(ws)['resolved'] == 'one'


def test_readings_without_a_basis_are_trusted():
    q = make_exam().questions['a']
    old = {'cues': {'c1': {'reading': '1', 'variant': 'one'}, 'c2': {'reading': 'i', 'variant': 'one'}}}
    assert variants.stored_readings(q, old) == {'c1': '1', 'c2': 'i'}
    moved = dataclasses.replace(q, cues=(dataclasses.replace(q.cues[0], ask="other"), q.cues[1]))
    fresh = variants.narrow(q, {'c1': '1', 'c2': 'i'})
    assert variants.stored_readings(moved, fresh) == {'c2': 'i'}


# --- isolated workers --------------------------------------------------------------------

EXAM = '''
from examinator import Bool, Exam, Field
from examinator.std import math, stages

exam = Exam('ovr', pages=1)
exam.question('k', pages=[1], result=Bool(), fields=[Field('x')],
              stages=[stages.transcribe(), stages.parse_math(),
                      stages.check_math(key={'x': '2'}), stages.grade(math.all_correct)])
'''


def test_isolated_worker_sees_overrides(tmp_path):
    pkg = tmp_path / 'ovr_exam'
    pkg.mkdir()
    (pkg / 'exam.py').write_text(EXAM)
    exam = load_exam(pkg)
    assert exam.questions['k'].stage('evidence').isolate
    (tmp_path / 'ws' / 'pdfs').mkdir(parents=True)
    ws = Workspace(tmp_path / 'ws', exam)
    runner = Runner(exam, ws)
    try:
        ws.store.write('s', 'k', 'parse', {'x': '2'}, origin='manual', author='t', verified=True)
        assert runner.run_stage('s', 'k', 'evidence').ok
        assert status(exam, ws, 's', 'k') == ('graded', True)
        pids = [p.pid for p in runner.pool._idle]

        overrides.set(exam, ws.store, 'k/stages/evidence/key/x', '3', author='ta')
        assert status(exam, ws, 's', 'k')[0] == 'outdated'
        runner.recheck('s', 'k')
        assert status(exam, ws, 's', 'k') == ('graded', False)

        overrides.clear(exam, ws.store, 'k/stages/evidence/key/x')
        runner.recheck('s', 'k')
        assert status(exam, ws, 's', 'k') == ('graded', True)
        assert [p.pid for p in runner.pool._idle] == pids        # same warm worker throughout
    finally:
        runner.close()
        ws.close()
