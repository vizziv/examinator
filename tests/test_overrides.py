"""Overrides: live edits of exam data. Everything here is synthetic."""
from pathlib import Path

import pytest

from examinator import Check, Exam, Field, Printed, Region, load_exam
from examinator.core import overrides
from examinator.core.as_python import describe_changes
from examinator.core.runner import Runner
from examinator.core.store import Conflict
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.std import fields, math

from conftest import SCRIPT, Parse, make_exam, workspace

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'


def hashes(exam):
    return {(qid, c.name): exam.check_hash(q, c) for qid, q in exam.grading.items() for c in q.checks}


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
        for qid in exam.grading:
            assert all(o.ok for o in runner.read(sid, qid))
        for k, v in runner.values(sid).items():
            runner.verify(sid, *k.split('/'), author='t', expect_rev=v.rev)
        runner.refresh(sid, questions=list(exam.grading))
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


# --- the identity fields: a group like any other ------------------------------------------

def test_identity_fields_are_exam_data():
    exam = load_exam(TOY)
    entries = {e.path: e for e in overrides.editable(exam)}
    for f in exam.identity.fields:
        e = entries[f'@identity/fields/{f.label}/region']
        assert e.kind == 'region' and e.nullable
        assert e.base == (None if f.region is None else [f.region.page, f.region.left, f.region.top,
                                                         f.region.right, f.region.bottom])
        assert entries[f'@identity/fields/{f.label}/read/ask'].kind == 'text'
    assert '@identity/pages' not in entries and '@identity/note' not in entries     # no page to show
    base = exam.identity
    skipped = overrides.apply(exam, {'@identity/fields/name/region': [1, 100, 190, 820, 290],
                                     '@identity/fields/id/region': None,
                                     '@identity/fields/email/region': [1, 10, 10, 300, 60]})
    assert skipped == []
    g = exam.identity
    assert g.field('name').region == Region(1, 100, 190, 820, 290) and g.field('name').read == base.field('name').read
    assert g.field('id').region is None                         # taken away: not read
    assert g.field('email').region == Region(1, 10, 10, 300, 60)    # a column the package left unplaced
    assert exam.base['@identity'] is base                       # nothing mutated
    assert overrides.apply(exam, {}) == [] and exam.identity is base
    for path, value in [('@identity/fields/name/region', [1, 500, 0, 400, 100]), ('@identity/fields/nick/region', [1, 1, 1, 2, 2]),
                        ('@identity/fields/name/label', 'x'), ('@identity', 1), ('@landmarks/x', 1)]:
        with pytest.raises(ValueError):
            overrides.validate(exam, path, value)


def test_identity_field_changes_described_as_python(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    try:
        overrides.set(exam, ws.store, '@identity/fields/id/region', [1, 100, 330, 600, 420], author='ta')
        change = overrides.set(exam, ws.store, '@identity/fields/email/region', [1, 10, 10, 300, 60], author='ta')
        assert change.checks == () and change.fields == (('@identity', 'email'),)     # offered to read again
        lines = describe_changes(exam, ws.store)
        assert any(x.startswith('@identity/fields/id/region: Region(') and '-> Region(1, 100, 330, 600, 420)' in x
                   for x in lines)
        assert any(x.startswith('@identity/fields/email/region: None -> Region(1, 10, 10, 300, 60)') for x in lines)
        overrides.clear(exam, ws.store, '@identity/fields/email/region')
        assert exam.identity.field('email').region is None
    finally:
        ws.close()


# --- the store ----------------------------------------------------------------------

def test_store_conflicts_and_history(env):
    exam, ws, _ = env
    store = ws.store
    a = store.set_override('a/pages', [1], author='ta', expect_rev=None)
    with pytest.raises(Conflict):
        store.set_override('a/pages', [2], author='tb', expect_rev=None)
    assert store.set_override('a/pages', [1], author='tb') == a        # the value it has: nothing changes
    b = store.set_override('a/pages', [2], author='tb', expect_rev=a.rev)
    assert b.rev > a.rev and store.overrides()['a/pages'].value == [2]
    with pytest.raises(Conflict):
        store.clear_override('a/pages', expect_rev=a.rev)
    # another scope: the same path, its own row and revision
    own = store.set_override('a/pages', [1], scope='submission:s1', author='tc', expect_rev=None)
    assert store.overrides()['a/pages'].value == [2] and store.overrides('submission:s1')['a/pages'] == own
    with pytest.raises(Conflict):
        store.clear_override('a/pages', scope='submission:s1', expect_rev=b.rev)
    store.clear_override('a/pages', expect_rev=b.rev)
    assert store.overrides() == {} and [o.scope for o in store.all_overrides()] == ['submission:s1']
    assert [(h.value, h.author) for h in store.override_history('a/pages')] == [([2], 'tb'), ([1], 'ta')]
    assert store.override_history('a/pages', 'submission:s1') == []


def test_set_and_describe_changes(env):
    exam, ws, _ = env
    with pytest.raises(ValueError):
        overrides.set(exam, ws.store, 'a/fields/x/region', [1, 9, 9, 1, 1], author='ta')
    change = overrides.set(exam, ws.store, 'a/fields/x/region', [1, 400, 310, 900, 430], author='ta')
    assert change.checks == () and change.fields == (('a', 'x'),)     # how it is read, not what checks see
    change = overrides.set(exam, ws.store, 'p/fields/c1/read/reads', {'1': 'one'}, author='ta')
    assert change.checks == () and change.fields == (('p', 'c1'),)
    lines = describe_changes(exam, ws.store)
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
        assert describe_changes(exam, ws.store)[0].startswith(
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


# --- scopes: the exam, a source scan, a submission -------------------------------------------

S1, S2, SRC = (overrides.scope(overrides.SUBMISSION, 's1'), overrides.scope(overrides.SUBMISSION, 's2'),
               overrides.scope(overrides.SOURCE, 'src1'))


@pytest.fixture
def scoped(env):
    """The mini exam's workspace, s1 split from scan 'src1', s2 handed in split."""
    from examinator.core.store import Made
    exam, ws, _ = env
    ws.store.set_made(Made('s1', 'src1', (0,), {}, 'now'))
    return exam, ws


def test_scopes_are_stored_as_text():
    assert overrides.scope(overrides.EXAM) == '' and S1 == 'submission:s1' and SRC == 'source:src1'
    assert overrides.parse_scope(S1) == ('submission', 's1') and overrides.parse_scope('') == ('exam', '')
    for bad in ('s1', 'submission:', 'person:x', 'exam:x'):
        with pytest.raises(ValueError):
            overrides.parse_scope(bad)


def test_each_kind_resolves_submission_then_scan_then_exam_then_package(scoped):
    exam, ws = scoped
    put = lambda path, value, scope: overrides.set(exam, ws.store, path, value, scope=scope, author='ta')
    region = lambda sid: ws.region(sid, exam.questions['a'], 'x')
    base = exam.questions['a'].field('x').region
    # a region: the submission's, else the exam's, else the package's (no scan scope)
    assert region('s1') == region('s2') == base
    put('a/fields/x/region', [1, 1, 1, 9, 9], '')
    assert region('s1') == region('s2') == Region(1, 1, 1, 9, 9)
    put('a/fields/x/region', [1, 2, 2, 8, 8], S1)
    assert region('s1') == Region(1, 2, 2, 8, 8) and region('s2') == Region(1, 1, 1, 9, 9)
    with pytest.raises(ValueError):
        put('a/fields/x/region', [1, 2, 2, 8, 8], SRC)
    # pages and the note that goes with them
    pages = lambda sid: ws.selected_pages(sid, exam.questions['a'])
    put('a/pages', [1, 2], ''), put('a/note', "overleaf", S1)
    assert pages('s1') == ((1, 2), "overleaf") and pages('s2') == ((1, 2), "")
    put('a/pages', [2], S1)
    assert pages('s1') == ((2,), "overleaf") and pages('s2') == ((1, 2), "")
    # the print area and the cover pages: the submission's, its scan's, the exam's, the package's
    layout = lambda sid: (ws.layout(sid).cover_pages, ws.layout(sid).print_area)
    assert layout('s1') == layout('s2') == (0, (0, 0, 1000, 1000))
    put('@print_area', [5, 5, 995, 995], ''), put('@cover_pages', 1, '')
    assert layout('s1') == layout('s2') == (1, (5, 5, 995, 995))
    put('@print_area', [10, 10, 990, 990], SRC), put('@cover_pages', 2, SRC)
    assert layout('s1') == (2, (10, 10, 990, 990)) and layout('s2') == (1, (5, 5, 995, 995))   # s2: no scan
    put('@print_area', [20, 20, 980, 980], S1), put('@cover_pages', 3, S1)
    put('@print_area', [30, 30, 970, 970], S2)
    assert layout('s1') == (3, (20, 20, 980, 980)) and layout('s2') == (1, (30, 30, 970, 970))
    # dropping each level falls back to the next
    for scope, want in ((S1, (2, (10, 10, 990, 990))), (SRC, (1, (5, 5, 995, 995))), ('', (0, (0, 0, 1000, 1000)))):
        overrides.clear(exam, ws.store, '@print_area', scope=scope)
        overrides.clear(exam, ws.store, '@cover_pages', scope=scope)
        assert layout('s1') == want
    for scope, want in ((S1, Region(1, 1, 1, 9, 9)), ('', base)):
        overrides.clear(exam, ws.store, 'a/fields/x/region', scope=scope)
        assert region('s1') == want
    overrides.clear(exam, ws.store, 'a/pages', scope=S1)
    assert pages('s1') == ((1, 2), "overleaf")
    overrides.clear(exam, ws.store, 'a/note', scope=S1)
    overrides.clear(exam, ws.store, 'a/pages')
    assert pages('s1') == ((1,), "")
    # an identity field's region, for one submission too (a student who wrote their name elsewhere)
    assert overrides.validate(load_exam(TOY), '@identity/fields/name/region', [1, 1, 1, 5, 5], S1) == [1, 1, 1, 5, 5]
    with pytest.raises(ValueError):
        overrides.validate(load_exam(TOY), '@identity/fields/name/read/ask', "x", S1)


def test_conflicts_at_each_scope(scoped):
    exam, ws = scoped
    for scope in ('', SRC, S1):
        o = overrides.set(exam, ws.store, '@print_area', [5, 5, 995, 995], scope=scope, author='ta', expect_rev=None)
        with pytest.raises(Conflict):
            overrides.set(exam, ws.store, '@print_area', [6, 6, 994, 994], scope=scope, author='tb', expect_rev=None)
        rev = ws.store.overrides(scope)['@print_area'].rev
        with pytest.raises(Conflict):
            overrides.clear(exam, ws.store, '@print_area', scope=scope, expect_rev=rev + 1)
        overrides.set(exam, ws.store, '@print_area', [6, 6, 994, 994], scope=scope, author='tb', expect_rev=rev)
        assert [h.author for h in ws.store.override_history('@print_area', scope)] == ['ta'] and o is not None


def test_one_validator_for_every_scope(scoped):
    exam, ws = scoped
    bad = [('a/fields/x/region', [1, 500, 0, 10, 10]), ('a/fields/x/region', [9, 0, 0, 10, 10]),
           ('a/fields/x/region', "1 0 0 10 10"), ('a/pages', []), ('a/pages', [0]), ('a/pages', [1, 1]),
           ('@print_area', [0, 0, 1000]), ('@print_area', [500, 0, 400, 1000]), ('@print_area', [0, 0, 1001, 1000]),
           ('@cover_pages', -1), ('@cover_pages', 1.5), ('@cover_pages', True), ('a/note', 3), ('a/note', 'x' * 2001)]
    for path, value in bad:
        for scope in ('', SRC, S1):
            with pytest.raises(ValueError):
                overrides.set(exam, ws.store, path, value, scope=scope, author='ta')
    assert ws.store.all_overrides() == []
    for path, value, scope in [('a/note', "x", ''), ('a/pages', [1], SRC), ('a/checks/check/key', 'x', S1),
                               ('@print_area', None, S1), ('a/fields/x/region', [1, 0, 0, 5, 5], 'nobody:x')]:
        with pytest.raises(ValueError):
            overrides.set(exam, ws.store, path, value, scope=scope, author='ta')
    # a value stored below the exam that no longer fits is not applied, like one for the exam
    ws.store.set_override('a/fields/x/region', [9, 0, 0, 5, 5], scope=S1, author='ta')
    assert ws.region('s1', exam.questions['a'], 'x') == exam.questions['a'].field('x').region


def test_changes_below_the_exam_outdate_no_check(scoped):
    exam, ws = scoped
    before = {qc: exam.check_hash(exam.questions[qc[0]], exam.questions[qc[0]].check(qc[1])) for qc in
              [(qid, c.name) for qid, q in exam.grading.items() for c in q.checks]}
    change = overrides.set(exam, ws.store, 'a/fields/x/region', [1, 0, 0, 5, 5], scope=S1, author='ta')
    assert change.checks == () and change.fields == (('a', 'x'),)
    change = overrides.set(exam, ws.store, '@print_area', [5, 5, 995, 995], scope=SRC, author='ta')
    assert change.checks == () and ('a', 'x') in change.fields and ('p', 'c1') in change.fields
    change = overrides.set(exam, ws.store, '@cover_pages', 1, author='ta')       # every crop moves
    assert change.checks == () and ('a', 'x') in change.fields
    assert {qc: exam.check_hash(exam.questions[qc[0]], exam.questions[qc[0]].check(qc[1])) for qc in before} == before


def test_the_change_list_has_the_print_area_and_cover_pages(scoped):
    exam, ws = scoped
    overrides.set(exam, ws.store, '@print_area', [5, 5, 995, 995], author='ta')
    overrides.set(exam, ws.store, '@cover_pages', 1, author='ta')
    overrides.set(exam, ws.store, 'a/pages', [2], scope=S1, author='ta')         # one submission's: not for the package
    lines = describe_changes(exam, ws.store)
    assert [x.split('  (')[0] for x in lines] == ['@cover_pages: 0 -> 1', '@print_area: [0, 0, 1000, 1000] -> [5, 5, 995, 995]']
    entries = {e.path: e for e in overrides.editable(exam)}
    assert entries['@print_area'].current == [5, 5, 995, 995] and entries['@print_area'].overridden
    assert entries['@cover_pages'].base == 0 and not any(p.endswith('/note') for p in entries)
    fresh = make_exam()
    assert overrides.refresh(fresh, ws.store) == [] and fresh.print_area == (5, 5, 995, 995) and fresh.cover_pages == 1
    assert fresh.base_attrs == {'print_area': (0, 0, 1000, 1000), 'cover_pages': 0}
