"""Overrides: live edits of exam data. Everything here is synthetic."""
from pathlib import Path

import pytest

from examinator import Check, Exam, Field, Identity, Printed, Region, load_exam
from examinator.core import overrides
from examinator.core.runner import Runner
from examinator.core.store import Conflict
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.std import fields, math

from conftest import SCRIPT, Parse, make_exam, workspace

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'


def hashes(exam):
    return {(qid, c.name): exam.check_hash(q, c) for qid, q in exam.questions.items() for c in q.checks}


def make_keyed_exam():
    """Like the mini exam, but its checks hold their keys as data."""
    exam = Exam('keyed', pages=2)
    exam.question(
        'p', pages=[1],
        fields=[Field('c1', Region(1, 0, 0, 1000, 200), read=Printed("first cue", reads={'1': 'one', '2': 'two'})),
                Field('c2', Region(1, 0, 200, 1000, 400), read=Printed("second cue", reads={'i': 'one', 'ii': 'two'}))])
    by = fields.agree(fields.value('c1', question='p'), fields.value('c2', question='p'))
    exam.question(
        'a', pages=[1], fields=[Field('x', Region(1, 0, 500, 1000, 600)), Field('y')], read=Parse(),
        checks={'x': Check(math.keyed({'one': {'x': '1'}, 'two': {'x': '10'}}, by=by), isolate=False),
                'y': Check(math.keyed({'one': {'y': '2'}, 'two': {'y': '20'}}, by=by), isolate=False)})
    exam.question('b', pages=[2], fields=[Field('x')], read=Parse(),
                  check=Check(math.keyed({'x': math.Choice('yes')}), isolate=False))
    return exam


@pytest.fixture
def kenv(tmp_path):
    """(exam, workspace, runner) for the keyed exam, every value read and verified, every check run."""
    exam = make_keyed_exam()
    ws = workspace(tmp_path, exam)
    runner = Runner(exam, ws, model=FakeModel(dict(SCRIPT)))
    for sid in ('s1', 's2'):
        for qid in exam.questions:
            assert all(o.ok for o in runner.read(sid, qid))
        for k, v in runner.values(sid).items():
            runner.verify(sid, *k.split('/'), author='t', expect_rev=v.rev)
        runner.refresh(sid, questions=list(exam.questions))
    yield exam, ws, runner
    runner.close()
    ws.close()


def status(runner, sid, qid, check='check'):
    q = runner.exam.questions[qid]
    st = runner.sheet(sid).status(runner.exam, q, q.check(check))
    return st.state, st.value


# --- paths ------------------------------------------------------------------------

def test_toy_exam_paths():
    exam = load_exam(TOY)
    entries = {e.path: e for e in overrides.editable(exam)}
    assert entries['areas/pages'].kind == 'pages' and entries['areas/pages'].base == [2]
    assert entries['areas/fields/A.1/region'].kind == 'region'
    assert entries['areas/fields/A.1/region'].base == [2, 400, 300, 900, 420]
    assert entries['areas/fields/side/region'].base == [2, 0, 60, 1000, 240]
    assert entries['areas/fields/side/region'].nullable           # unset: simply not read
    assert entries['areas/fields/side/read/ask'].kind == 'text'
    assert entries['areas/fields/side/read/reads'].kind == 'map'
    assert entries['areas/fields/side/read/verify'].kind == 'bool'
    assert entries['areas/checks/check/key/red/A.2'].base == '4*x'
    assert entries['areas/read/notes'].kind == 'text'
    assert entries['areas/read/parse_prompt'].kind == 'text'
    assert entries['parity/checks/check/key/B'].kind == 'choice'
    assert entries['parity/checks/check/key/B'].base == ['even', 'E', 'option 1']
    assert not any(e.overridden for e in entries.values())
    # functions, names and wiring are not data to edit
    assert not any(p.endswith(('/fn', '/name', '/label', '/by', '/grade', '/points')) for p in entries)


def test_escaping():
    assert overrides.join('q', 'fields', '1/2', 'region') == 'q/fields/1%2F2/region'
    assert overrides.split('q/fields/1%2F2%25/region') == ('q', 'fields', '1/2%', 'region')


def test_apply_and_clear_restore_the_base_exactly():
    exam = load_exam(TOY)
    before = hashes(exam)
    base = dict(exam.questions)
    skipped = overrides.apply(exam, {'areas/fields/A.1/region': [2, 400, 310, 900, 430],
                                     'areas/checks/check/key/red/A.2': '2*2*x',
                                     'parity/checks/check/key/B': ['odd'],
                                     'areas/pages': [2, 3]})
    assert skipped == []
    q = exam.questions['areas']
    assert q.fields[1].region == Region(2, 400, 310, 900, 430) and q.pages == (2, 3)
    assert q.check('check').fn.key['red']['A.2'] == '2*2*x'
    assert list(q.check('check').fn.key) == ['red', 'green', 'blue']       # order kept
    assert exam.questions['parity'].check('check').fn.key['B'].accepted == ('odd',)
    assert exam.base['parity'].check('check').fn.key['B'].accepted == ('even', 'E', 'option 1')
    assert base['areas'].fields[1].region == Region(2, 400, 300, 900, 420)    # nothing mutated
    assert hashes(exam) != before
    entries = {e.path: e for e in overrides.editable(exam)}
    assert entries['areas/pages'].overridden and entries['areas/pages'].current == [2, 3]
    assert overrides.apply(exam, {}) == []
    assert hashes(exam) == before
    assert all(exam.questions[qid] is base[qid] for qid in base)


def test_validation():
    exam = load_exam(TOY)
    ok = overrides.validate
    assert ok(exam, 'areas/fields/A.1/region', (2, 1, 2, 3, 4)) == [2, 1, 2, 3, 4]
    assert ok(exam, 'areas/fields/side/region', None) is None
    assert ok(exam, 'areas/fields/side/read/reads', {'2': 'purple'}) == {'2': 'purple'}
    bad = [('areas/fields/A.1/region', [2, 500, 0, 400, 100]), ('areas/fields/A.1/region', [9, 0, 0, 10, 10]),
           ('areas/fields/A.1/region', [2, 0, 0, 10]), ('areas/pages', []), ('areas/pages', [5]),
           ('areas/pages', [2, 2]), ('areas/fields/side/read/ask', 3), ('areas/read/notes', 3),
           ('areas/read/crops', 'yes'), ('areas/fields/side/read/reads', {'2': 3}),
           ('parity/checks/check/key/B', []), ('parity/checks/check/key/B', 'odd'),
           ('areas/checks/check/by', 'x'), ('areas/checks/check/key/red/Z', '1'), ('nope/pages', [1])]
    for path, value in bad:
        with pytest.raises(ValueError):
            ok(exam, path, value)


def test_stale_overrides_are_skipped_and_reported():
    exam = load_exam(TOY)
    skipped = overrides.apply(exam, {'areas/fields/Z.9/region': [2, 0, 0, 10, 10], 'gone/pages': [1],
                                     'areas/pages': [7], 'areas/read/notes': "Answers are integers."})
    assert sorted(s.path for s in skipped) == ['areas/fields/Z.9/region', 'areas/pages', 'gone/pages']
    assert exam.questions['areas'].read.notes == "Answers are integers."
    assert exam.overrides == {'areas/read/notes': "Answers are integers."}


# --- the exam's lists: identity boxes ----------------------------------------------------

def test_identity_boxes_are_exam_data():
    exam = load_exam(TOY)
    entries = {e.path: e for e in overrides.editable(exam)}
    declared = {f.name: f for f in exam.identity}
    for name, f in declared.items():
        e = entries[f'@identity/{name}/region']
        assert e.kind == 'region' and e.nullable and e.base == [f.region.page, f.region.left, f.region.top,
                                                               f.region.right, f.region.bottom]
        assert entries[f'@identity/{name}/ask'].kind == 'text'
        assert entries[f'@identity/{name}/roster'].kind == 'text' and entries[f'@identity/{name}/roster'].nullable
    assert not any(p.startswith('@identity') and p.endswith('/name') for p in entries)     # the address
    assert {e.path for e in overrides.editable(exam, '@identity')} == {p for p in entries if p.startswith('@')}
    base = exam.identity
    added = {'region': [1, 10, 10, 300, 60], 'ask': "the email", 'roster': 'email'}
    skipped = overrides.apply(exam, {'@identity/name/region': [1, 100, 190, 820, 290], '@identity/id/region': None,
                                     '@identity/netid': added})
    assert skipped == []
    assert [f.name for f in exam.identity] == ['name', 'id', 'netid']
    assert exam.identity[0].region == Region(1, 100, 190, 820, 290) and exam.identity[0].ask == base[0].ask
    assert exam.identity[1].region is None                      # taken away: not used
    assert exam.identity[2] == Identity('netid', Region(1, 10, 10, 300, 60), "the email", 'email')
    assert exam.base_lists['identity'] == base                  # nothing mutated
    entries = {e.path: e for e in overrides.editable(exam)}
    assert entries['@identity/netid'].kind == 'item' and entries['@identity/netid'].base is None
    assert entries['@identity/netid'].current == added and entries['@identity/id/region'].current is None
    assert overrides.apply(exam, {}) == [] and exam.identity == base
    # checked like any value, and by the item's own class
    for path, value in [('@identity/name/region', [1, 500, 0, 400, 100]), ('@identity/name/roster', 'phone'),
                        ('@identity/name/name', 'x'), ('@identity/name', {'region': [1, 1, 1, 2, 2], 'ask': "?"}),
                        ('@identity/new', {'region': [1, 1, 1, 2, 2]}), ('@identity/new', None),
                        ('@identity/new', {'region': [1, 1, 1, 2, 2], 'ask': "?", 'roster': 'phone'}),
                        ('@identity/new', {'region': [1, 1, 1, 2, 2], 'ask': "?", 'colour': 'red'}),
                        ('@identity', 1), ('@landmarks/x', 1)]:
        with pytest.raises(ValueError):
            overrides.validate(exam, path, value)
    # one the package now declares itself is skipped, not applied twice
    skipped = overrides.apply(exam, {'@identity/name': {'region': [1, 1, 1, 2, 2], 'ask': "?"}})
    assert [s.path for s in skipped] == ['@identity/name'] and exam.identity == base
    with pytest.raises(ValueError):
        exam.question('@identity', pages=[1])


def test_identity_box_changes_described_as_python(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    try:
        overrides.set(exam, ws.store, '@identity/id/region', [1, 100, 330, 600, 410], author='ta')
        overrides.set(exam, ws.store, '@identity/netid', {'region': [1, 10, 10, 300, 60], 'ask': "the email",
                                                          'roster': 'email'}, author='ta')
        lines = overrides.describe_changes(exam, ws.store)
        assert any(x.startswith('@identity/id/region: Region(') and '-> Region(1, 100, 330, 600, 410)' in x for x in lines)
        assert any(x.startswith("@identity/netid: (added) -> Identity('netid', region=Region(1, 10, 10, 300, 60), "
                                "ask='the email', roster='email')") for x in lines)
        change = overrides.clear(exam, ws.store, '@identity/netid')
        assert change.checks == () and [f.name for f in exam.identity] == ['name', 'id']
    finally:
        ws.close()


# --- the store ----------------------------------------------------------------------

def test_store_conflicts_and_history(env):
    exam, ws, _ = env
    store = ws.store
    a = store.set_override('a/pages', [1], author='ta', expect_rev=None)
    with pytest.raises(Conflict):
        store.set_override('a/pages', [2], author='tb', expect_rev=None)
    b = store.set_override('a/pages', [2], author='tb', expect_rev=a.rev)
    assert b.rev > a.rev and store.overrides()['a/pages'].value == [2]
    with pytest.raises(Conflict):
        store.clear_override('a/pages', expect_rev=a.rev)
    store.clear_override('a/pages', expect_rev=b.rev)
    assert store.overrides() == {}
    assert [(h.value, h.author) for h in store.override_history('a/pages')] == [([2], 'tb'), ([1], 'ta')]


def test_set_and_describe_changes(env):
    exam, ws, _ = env
    with pytest.raises(ValueError):
        overrides.set(exam, ws.store, 'a/fields/x/region', [1, 9, 9, 1, 1], author='ta')
    change = overrides.set(exam, ws.store, 'a/fields/x/region', [1, 400, 310, 900, 430], author='ta')
    assert change.checks == () and change.fields == (('a', 'x'),)     # how it is read, not what checks see
    change = overrides.set(exam, ws.store, 'p/fields/c1/read/reads', {'1': 'one'}, author='ta')
    assert change.checks == () and change.fields == (('p', 'c1'),)
    lines = overrides.describe_changes(exam, ws.store)
    assert lines[0].startswith('a/fields/x/region: None -> Region(1, 400, 310, 900, 430)  (ta, ')
    fresh = make_exam()                 # a fresh session sees the same exam once it refreshes from the store
    assert overrides.refresh(fresh, ws.store) == []
    assert fresh.questions['p'].fields[0].read.reads == {'1': 'one'}
    overrides.clear(exam, ws.store, 'p/fields/c1/read/reads')
    assert exam.questions['p'].fields[0].read.reads == {'1': 'one', '2': 'two'}


def test_choice_described_as_python(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    try:
        overrides.set(exam, ws.store, 'parity/checks/check/key/B', ['odd', 'O'], author='ta')
        assert overrides.describe_changes(exam, ws.store)[0].startswith(
            "parity/checks/check/key/B: Choice('even', 'E', 'option 1') -> Choice('odd', 'O')  (ta, ")
    finally:
        ws.close()


# --- what an edit outdates ------------------------------------------------------------

def test_a_key_override_outdates_only_the_check_that_uses_it(kenv):
    exam, ws, runner = kenv
    assert status(runner, 's1', 'a', 'x') == ('graded', True)
    change = overrides.set(exam, ws.store, 'a/checks/x/key/one/x', '5', author='ta')
    assert change.checks == (('a', 'x'),) and change.questions == ('a',)
    for sid in ('s1', 's2'):
        assert status(runner, sid, 'a', 'x')[0] == 'outdated'
        assert status(runner, sid, 'a', 'y') == ('graded', True)
        assert status(runner, sid, 'b') == ('graded', True)
        assert [o.step for o in runner.refresh(sid)] == ['x']
    assert status(runner, 's1', 'a', 'x') == ('graded', False)
    overrides.clear(exam, ws.store, 'a/checks/x/key/one/x')
    runner.refresh('s1')
    assert status(runner, 's1', 'a', 'x') == ('graded', True)


def test_a_choice_key_override_regrades(kenv):
    exam, ws, runner = kenv
    overrides.set(exam, ws.store, 'b/checks/check/key/x', ['no'], author='ta')
    assert status(runner, 's1', 'b')[0] == 'outdated'
    runner.refresh('s1')
    assert status(runner, 's1', 'b') == ('graded', False)


def test_changing_how_a_printed_field_is_read_changes_no_value_until_it_is_read_again(kenv):
    exam, ws, runner = kenv
    before = runner.values('s1')
    for i, f in enumerate(('c1', 'c2')):
        flipped = {k: ('two' if v == 'one' else 'one') for k, v in exam.questions['p'].fields[i].read.reads.items()}
        assert overrides.set(exam, ws.store, f'p/fields/{f}/read/reads', flipped, author='ta').checks == ()
    assert runner.values('s1') == before and status(runner, 's1', 'a', 'x') == ('graded', True)
    runner.read('s1', 'p')                    # a person verified them: skipped
    assert runner.values('s1') == before
    runner.read('s1', 'p', include_protected=True, fresh=True)
    assert runner.values('s1')['p/c1'].text == 'two' and runner.values('s1')['p/c1'].settled == 'reader'
    assert status(runner, 's1', 'a', 'x')[0] == 'outdated'
    runner.refresh('s1')
    assert status(runner, 's1', 'a', 'x') == ('graded', False)            # the other printing's key


# --- isolated workers --------------------------------------------------------------------

EXAM = '''
from examinator import Exam, Field, Printed, Region
from examinator.std import fields, math

exam = Exam('ovr', pages=1)
exam.question('info', pages=[1], fields=[Field('w', Region(1, 0, 0, 1000, 100), read=Printed("the letter"))])
exam.question('k', pages=[1], fields=[Field('x')], check=math.keyed({'x': '2'}))
exam.question('m', pages=[1], fields=[Field('x')],
              check=math.keyed({'a': {'x': '1'}, 'b': {'x': '2'}}, by=fields.value('w', question='info')))
'''


def test_isolated_worker_sees_overrides(tmp_path):
    pkg = tmp_path / 'ovr_exam'
    pkg.mkdir()
    (pkg / 'exam.py').write_text(EXAM)
    exam = load_exam(pkg)
    assert exam.questions['k'].check('check').isolate
    (tmp_path / 'ws' / 'pdfs').mkdir(parents=True)
    ws = Workspace(tmp_path / 'ws', exam)
    runner = Runner(exam, ws)
    try:
        runner.set('s', 'k', 'x', '2', author='t')
        assert runner.compute('s', 'k', 'check').ok
        assert status(runner, 's', 'k') == ('graded', True)
        pids = [p.pid for p in runner.pool._idle]
        overrides.set(exam, ws.store, 'k/checks/check/key/x', '3', author='ta')
        assert status(runner, 's', 'k')[0] == 'outdated'
        runner.refresh('s')
        assert status(runner, 's', 'k') == ('graded', False)
        overrides.clear(exam, ws.store, 'k/checks/check/key/x')
        runner.refresh('s')
        assert status(runner, 's', 'k') == ('graded', True)
        assert [p.pid for p in runner.pool._idle] == pids        # same warm worker throughout
        # a check in a worker reads another question's field; the read is recorded
        runner.set('s', 'm', 'x', '2', author='t')
        assert runner.compute('s', 'm', 'check').ok
        body = ws.store.get('s', 'm/check', 'result').body
        assert body['reads'] == {'info/w': None} and body['grade']['reason'] == "info w not read"
        runner.set('s', 'info', 'w', 'b', author='t')
        runner.refresh('s')
        assert status(runner, 's', 'm') == ('graded', True)
    finally:
        runner.close()
        ws.close()
