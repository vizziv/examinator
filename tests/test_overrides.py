"""Overrides: live edits of exam data. Everything here is synthetic."""
from pathlib import Path

import pytest

from examinator import Check, Exam, Field, Printed, Region, load_exam
from examinator.core import overrides
from examinator.core.defs import IDENTITY
from examinator.core.as_python import describe_changes
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
        fields=[Field('c1', Region(1, 0, 0, 1000, 200), read=Printed("first cue")),
                Field('c2', Region(1, 0, 200, 1000, 400), read=Printed("second cue"))])
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
    runner = Runner(ws, model=FakeModel(dict(SCRIPT)))
    for sid in ('s1', 's2'):
        for qid in exam.questions:
            assert all(o.ok for o in runner.read(sid, qid))
        for k, v in runner.values(sid).items():
            runner.verify(sid, *k.split('/'), author='t', expect_rev=v.rev)
        runner.refresh(sid)
    yield ws.exam, ws, runner
    runner.close()
    ws.close()


def status(runner, sid, qid, check='check'):
    q = runner.ws.exam.questions[qid]
    st = runner.ledger(sid).status(runner.ws.exam, q, q.check(check))
    return st.state, st.value


# --- paths ------------------------------------------------------------------------

def test_toy_exam_paths():
    exam = load_exam(TOY)
    entries = {e.path: e for e in overrides.editable(exam)}
    assert entries['areas/fields/A.1/region'].kind == 'region'
    assert entries['areas/fields/A.1/region'].base == [2, 400, 300, 900, 420]
    assert entries['areas/fields/side/region'].base == [2, 0, 60, 1000, 240]
    assert entries['areas/fields/side/region'].nullable           # unset: simply not read
    assert entries['areas/fields/side/read/ask'].kind == 'text'
    assert entries['areas/fields/side/read/prompt'].kind == 'text'
    assert 'areas/pages' not in entries          # a question's pages are the package's
    assert entries['areas/fields/side/read/verify'].kind == 'bool'
    assert entries['areas/checks/check/key/2/A.2'].base == '4*x'
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
    live, skipped = overrides.apply(exam, {'areas/fields/A.1/region': [2, 400, 310, 900, 430],
                                           'areas/checks/check/key/2/A.2': '2*2*x',
                                           'parity/checks/check/key/B': ['odd']})
    assert skipped == []
    q = live.questions['areas']
    assert q.fields[1].region == Region(2, 400, 310, 900, 430)
    assert q.check('check').fn.key['2']['A.2'] == '2*2*x'
    assert list(q.check('check').fn.key) == ['2', '3', '5']       # order kept
    assert live.questions['parity'].check('check').fn.key['B'].accepted == ('odd',)
    assert live.base['parity'].check('check').fn.key['B'].accepted == ('even', 'E', 'option 1')
    assert base['areas'].fields[1].region == Region(2, 400, 300, 900, 420)    # nothing mutated
    assert exam.questions == base and exam.overrides == {} and hashes(exam) == before   # the package untouched
    assert hashes(live) != before
    entries = {e.path: e for e in overrides.editable(live)}
    assert entries['areas/fields/A.1/region'].overridden and entries['areas/fields/A.1/region'].current == [2, 400, 310, 900, 430]
    back, skipped = overrides.apply(live, {})
    assert skipped == [] and hashes(back) == before
    assert all(back.questions[qid] is base[qid] for qid in base)


def test_validation():
    exam = load_exam(TOY)
    ok = overrides.validate
    assert ok(exam, 'areas/fields/A.1/region', (2, 1, 2, 3, 4)) == [2, 1, 2, 3, 4]
    assert ok(exam, 'areas/fields/side/region', None) is None
    assert ok(exam, 'areas/fields/side/read/ask', "the side length, as a number") == "the side length, as a number"
    bad = [('areas/fields/side/read/reads', {'2': 'red'}), ('areas/pages', [2]),     # no such values
           ('areas/fields/A.1/region', [2, 500, 0, 400, 100]), ('areas/fields/A.1/region', [9, 0, 0, 10, 10]),
           ('areas/fields/A.1/region', [2, 0, 0, 10]), ('areas/fields/side/read/ask', 3), ('areas/read/notes', 3),
           ('areas/fields/side/read/ask', " "), ('areas/fields/side/read/prompt', "{other}"),
           ('areas/read/crops', 'yes'), ('parity/checks/check/key/B', []), ('parity/checks/check/key/B', 'odd'),
           ('areas/checks/check/by', 'x'), ('areas/checks/check/key/2/Z', '1'), ('nope/pages', [1])]
    for path, value in bad:
        with pytest.raises(ValueError):
            ok(exam, path, value)


def test_stale_overrides_are_skipped_and_reported():
    exam = load_exam(TOY)
    live, skipped = overrides.apply(exam, {'areas/fields/Z.9/region': [2, 0, 0, 10, 10], 'gone/pages': [1],
                                           'areas/pages': [7], 'areas/read/notes': "Answers are integers."})
    assert sorted(s.path for s in skipped) == ['areas/fields/Z.9/region', 'areas/pages', 'gone/pages']
    assert live.questions['areas'].read.notes == "Answers are integers."
    assert live.overrides == {'areas/read/notes': "Answers are integers."}


def other_value(e):
    """A value for an editable entry other than the package's, where one is plain to make."""
    if e.kind == 'region':
        page = e.base[0] if e.base else 1
        return [page, 10, 10, 500, 500] if e.base != [page, 10, 10, 500, 500] else [page, 20, 20, 600, 600]
    if e.kind == 'print_area':
        return [1, 1, 999, 999]
    if e.kind == 'text':
        return e.base + " "
    if e.kind == 'choice':
        return [*e.base, "another"]
    if e.kind == 'bool':
        return not e.base
    return e.base


def each_valid_alone(exam):
    """{path: value} with a value for every editable entry that is valid on its own."""
    out = {}
    for e in overrides.editable(exam):
        for value in (other_value(e), e.base):
            try:
                out[e.path] = overrides.validate(exam, e.path, value)
                break
            except ValueError:
                continue
        else:
            raise AssertionError(f"{e.path}: not even the package's value is valid alone")
    return out


EXAM3 = Path(__file__).resolve().parents[1] / 'exams' / 'exam_03'


@pytest.mark.parametrize('package', [TOY, pytest.param(EXAM3, marks=pytest.mark.skipif(
    not __import__('os').environ.get('EXAM3_BLANKS'), reason="EXAM3_BLANKS is not set"))])
def test_every_override_is_valid_alone_and_the_order_does_not_matter(package):
    """Every editable value takes a value valid on its own; all of them together, put in
    one by one in any order (each checked against what is in already), give one valid
    definition, the same as applying them at once, with none skipped."""
    import random
    from examinator.core.defs import describe
    exam = load_exam(package)
    alone = each_valid_alone(exam)
    changed = [p for p, v in alone.items() if v != overrides.entry(exam, p).base]
    assert len(changed) > len(alone) // 2          # most get a value of their own, not the package's
    live, skipped = overrides.apply(exam, alone)
    assert skipped == []
    live.validate()
    want = describe(live.roots())
    rng = random.Random(4580)
    for _ in range(3):
        order = list(alone)
        rng.shuffle(order)
        roots = exam.roots()
        for path in order:
            node, _ = overrides._change(exam, roots, path, alone[path], overrides.EXAM)
            roots = {**roots, overrides.split(path)[0]: node}
        assert describe(roots) == want


def test_skipped_overrides_are_reported_at_every_scope(tmp_path):
    exam = make_exam()
    ws = workspace(tmp_path, exam)
    try:
        ws.store.set_override('a/fields/x/region', [9, 0, 0, 10, 10], scope='submission:s1', author='ta')
        ws.store.set_override('gone/pages', [1], scope='submission:s2', author='ta')
        ws.store.set_override('@print_area', [5, 5, 4, 4], scope='source:src9', author='ta')
        ws.store.set_override('a/fields/y/region', [9, 0, 0, 10, 10], author='ta')
        ws.refresh()
        got = {(s.scope, s.path) for s in ws.skipped_overrides()}
        assert got == {('submission:s1', 'a/fields/x/region'), ('submission:s2', 'gone/pages'),
                       ('source:src9', '@print_area'), ('', 'a/fields/y/region')}
        assert ws.layers('s1').question('a').field('x').region == ws.exam.questions['a'].field('x').region
    finally:
        ws.close()


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
    assert '@identity/pages' not in entries
    base = exam.identity
    live, skipped = overrides.apply(exam, {'@identity/fields/name/region': [1, 100, 190, 820, 290],
                                           '@identity/fields/id/region': None,
                                           '@identity/fields/email/region': [1, 10, 10, 300, 60]})
    assert skipped == []
    g = live.identity
    assert g.field('name').region == Region(1, 100, 190, 820, 290) and g.field('name').read == base.field('name').read
    assert g.field('id').region is None                         # taken away: not read
    assert g.field('email').region == Region(1, 10, 10, 300, 60)    # a column the package left unplaced
    assert exam.base['@identity'] is base and exam.identity is base     # nothing mutated
    back, skipped = overrides.apply(live, {})
    assert skipped == [] and back.identity is base
    for path, value in [('@identity/fields/name/region', [1, 500, 0, 400, 100]), ('@identity/fields/nick/region', [1, 1, 1, 2, 2]),
                        ('@identity/fields/name/label', 'x'), ('@identity', 1), ('@landmarks/x', 1)]:
        with pytest.raises(ValueError):
            overrides.validate(exam, path, value)


def test_identity_field_changes_described_as_python(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    try:
        overrides.set(ws, '@identity/fields/id/region', [1, 100, 330, 600, 420], author='ta')
        change = overrides.set(ws, '@identity/fields/email/region', [1, 10, 10, 300, 60], author='ta')
        assert change.checks == ()
        lines = describe_changes(exam, ws.store)
        assert any(x.startswith('@identity/fields/id/region: Region(') and '-> Region(1, 100, 330, 600, 420)' in x
                   for x in lines)
        assert any(x.startswith('@identity/fields/email/region: None -> Region(1, 10, 10, 300, 60)') for x in lines)
        overrides.clear(ws, '@identity/fields/email/region')
        assert ws.exam.identity.field('email').region is None
    finally:
        ws.close()


# --- the store ----------------------------------------------------------------------

def test_store_conflicts(env):
    exam, ws, _ = env
    store = ws.store
    a = store.set_override('a/note', [1], author='ta', expect_rev=None)
    with pytest.raises(Conflict):
        store.set_override('a/note', [2], author='tb', expect_rev=None)
    assert store.set_override('a/note', [1], author='tb') == a        # the value it has: nothing changes
    b = store.set_override('a/note', [2], author='tb', expect_rev=a.rev)
    assert b.rev > a.rev and store.overrides()['a/note'].value == [2]
    with pytest.raises(Conflict):
        store.clear_override('a/note', expect_rev=a.rev)
    # another scope: the same path, its own row and revision
    own = store.set_override('a/note', [1], scope='submission:s1', author='tc', expect_rev=None)
    assert store.overrides()['a/note'].value == [2] and store.overrides('submission:s1')['a/note'] == own
    with pytest.raises(Conflict):
        store.clear_override('a/note', scope='submission:s1', expect_rev=b.rev)
    store.clear_override('a/note', expect_rev=b.rev)
    assert store.overrides() == {} and [o.scope for o in store.all_overrides()] == ['submission:s1']


def test_set_and_describe_changes(env):
    exam, ws, _ = env
    with pytest.raises(ValueError):
        overrides.set(ws, 'a/fields/x/region', [1, 9, 9, 1, 1], author='ta')
    change = overrides.set(ws, 'a/fields/x/region', [1, 400, 310, 900, 430], author='ta')
    assert change.checks == ()     # how it is read, not what checks see
    change = overrides.set(ws, 'p/fields/c1/read/ask', "the first cue", author='ta')
    assert change.checks == ()
    lines = describe_changes(exam, ws.store)
    assert lines[0].startswith('a/fields/x/region: None -> Region(1, 400, 310, 900, 430)  (ta, ')
    fresh = make_exam()                 # a fresh session sees the same exam: opening the workspace applies it
    other = Workspace(ws.root, fresh)
    assert other.skipped == [] and other.exam.questions['p'].fields[0].read.ask == "the first cue"
    other.close()
    overrides.clear(ws, 'p/fields/c1/read/ask')
    assert ws.exam.questions['p'].fields[0].read.ask == "first cue"


def test_choice_described_as_python(tmp_path):
    exam = load_exam(TOY)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    try:
        overrides.set(ws, 'parity/checks/check/key/B', ['odd', 'O'], author='ta')
        assert describe_changes(exam, ws.store)[0].startswith(
            "parity/checks/check/key/B: Choice('even', 'E', 'option 1') -> Choice('odd', 'O')  (ta, ")
    finally:
        ws.close()


# --- what an edit outdates ------------------------------------------------------------

def test_a_key_override_outdates_only_the_check_that_uses_it(kenv):
    exam, ws, runner = kenv
    assert status(runner, 's1', 'a', 'x') == ('graded', True)
    change = overrides.set(ws, 'a/checks/x/key/one/x', '5', author='ta')
    assert change.checks == (('a', 'x'),) and change.questions == ('a',)
    for sid in ('s1', 's2'):
        assert status(runner, sid, 'a', 'x')[0] == 'outdated'
        assert status(runner, sid, 'a', 'y') == ('graded', True)
        assert status(runner, sid, 'b') == ('graded', True)
        assert [o.step for o in runner.refresh(sid)] == ['x']
    assert status(runner, 's1', 'a', 'x') == ('graded', False)
    overrides.clear(ws, 'a/checks/x/key/one/x')
    runner.refresh('s1')
    assert status(runner, 's1', 'a', 'x') == ('graded', True)


def test_a_choice_key_override_regrades(kenv):
    exam, ws, runner = kenv
    overrides.set(ws, 'b/checks/check/key/x', ['no'], author='ta')
    assert status(runner, 's1', 'b')[0] == 'outdated'
    runner.refresh('s1')
    assert status(runner, 's1', 'b') == ('graded', False)


def test_changing_how_a_printed_field_is_read_changes_no_value_until_it_is_read_again(kenv):
    exam, ws, runner = kenv
    before = runner.values('s1')
    for f in ('c1', 'c2'):
        assert overrides.set(ws, f'p/fields/{f}/read/ask', "the cue", author='ta').checks == ()
        assert overrides.set(ws, f'p/fields/{f}/region', [1, 0, 0, 1000, 500], author='ta').checks == ()
    assert runner.values('s1') == before and status(runner, 's1', 'a', 'x') == ('graded', True)
    runner.read('s1', 'p')                    # a person verified them: skipped
    assert runner.values('s1') == before
    runner.model.script[('p', 'read:c1')] = runner.model.script[('p', 'read:c2')] = '{"value": "two"}'
    runner.read('s1', 'p', include_protected=True)
    assert runner.values('s1')['p/c1'].text == 'two' and runner.values('s1')['p/c1'].verified == 'reader'
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
    runner = Runner(ws)
    try:
        runner.set('s', 'k', 'x', '2', author='t')
        assert runner.compute('s', 'k', 'check').ok
        assert status(runner, 's', 'k') == ('graded', True)
        pids = [p.pid for p in runner.pool._idle]
        overrides.set(ws, 'k/checks/check/key/x', '3', author='ta')
        assert status(runner, 's', 'k')[0] == 'outdated'
        runner.refresh('s')
        assert status(runner, 's', 'k') == ('graded', False)
        overrides.clear(ws, 'k/checks/check/key/x')
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
    ws.store.set_made(Made('s1', 'src1', (0,), (0,), 'now', 's1.pdf'))
    (ws.made / 's1.pdf').write_bytes((ws.pdfs / 's1.pdf').read_bytes())     # intake's file
    return exam, ws


def test_scopes_are_stored_as_text():
    assert overrides.scope(overrides.EXAM) == '' and S1 == 'submission:s1' and SRC == 'source:src1'
    assert overrides.parse_scope(S1) == ('submission', 's1') and overrides.parse_scope('') == ('exam', '')
    for bad in ('s1', 'submission:', 'person:x', 'exam:x'):
        with pytest.raises(ValueError):
            overrides.parse_scope(bad)


def test_each_kind_resolves_submission_then_scan_then_exam_then_package(scoped):
    exam, ws = scoped
    put = lambda path, value, scope: overrides.set(ws, path, value, scope=scope, author='ta')
    region = lambda sid: ws.layers(sid).question('a').field('x').region
    base = exam.questions['a'].field('x').region
    # a region: the submission's, else the exam's, else the package's (no scan scope)
    assert region('s1') == region('s2') == base
    put('a/fields/x/region', [1, 1, 1, 9, 9], '')
    assert region('s1') == region('s2') == Region(1, 1, 1, 9, 9)
    put('a/fields/x/region', [1, 2, 2, 8, 8], S1)
    assert region('s1') == Region(1, 2, 2, 8, 8) and region('s2') == Region(1, 1, 1, 9, 9)
    with pytest.raises(ValueError):
        put('a/fields/x/region', [1, 2, 2, 8, 8], SRC)
    # the print area: the submission's, its scan's, the exam's, the package's
    layout = lambda sid: ws.layout(sid).print_area
    assert layout('s1') == layout('s2') == (0, 0, 1000, 1000)
    put('@print_area', [5, 5, 995, 995], '')
    assert layout('s1') == layout('s2') == (5, 5, 995, 995)
    put('@print_area', [10, 10, 990, 990], SRC)
    assert layout('s1') == (10, 10, 990, 990) and layout('s2') == (5, 5, 995, 995)   # s2: no scan
    put('@print_area', [20, 20, 980, 980], S1)
    put('@print_area', [30, 30, 970, 970], S2)
    assert layout('s1') == (20, 20, 980, 980) and layout('s2') == (30, 30, 970, 970)
    # dropping each level falls back to the next
    for scope, want in ((S1, (10, 10, 990, 990)), (SRC, (5, 5, 995, 995)), ('', (0, 0, 1000, 1000))):
        overrides.clear(ws, '@print_area', scope=scope)
        assert layout('s1') == want
    for scope, want in ((S1, Region(1, 1, 1, 9, 9)), ('', base)):
        overrides.clear(ws, 'a/fields/x/region', scope=scope)
        assert region('s1') == want
    # an identity field's region, for one submission too (a student who wrote their name elsewhere)
    put('@identity/fields/name/region', [1, 1, 1, 5, 5], S1)
    assert ws.layers('s1').question(IDENTITY).field('name').region == Region(1, 1, 1, 5, 5) and ws.layers('s2').question(IDENTITY).field('name').region is None
    with pytest.raises(ValueError, match="not set for one submission"):
        put('@identity/fields/name/read/ask', "x", S1)


def test_conflicts_at_each_scope(scoped):
    exam, ws = scoped
    for scope in ('', SRC, S1):
        o = overrides.set(ws, '@print_area', [5, 5, 995, 995], scope=scope, author='ta', expect_rev=None)
        with pytest.raises(Conflict):
            overrides.set(ws, '@print_area', [6, 6, 994, 994], scope=scope, author='tb', expect_rev=None)
        rev = ws.store.overrides(scope)['@print_area'].rev
        with pytest.raises(Conflict):
            overrides.clear(ws, '@print_area', scope=scope, expect_rev=rev + 1)
        overrides.set(ws, '@print_area', [6, 6, 994, 994], scope=scope, author='tb', expect_rev=rev)
        assert ws.store.overrides(scope)['@print_area'].author == 'tb' and o is not None


def test_one_validator_for_every_scope(scoped):
    exam, ws = scoped
    bad = [('a/fields/x/region', [1, 500, 0, 10, 10]), ('a/fields/x/region', [9, 0, 0, 10, 10]),
           ('a/fields/x/region', "1 0 0 10 10"), ('a/pages', [1]),
           ('@print_area', [0, 0, 1000]), ('@print_area', [500, 0, 400, 1000]), ('@print_area', [0, 0, 1001, 1000]),
           ('a/note', "x")]
    for path, value in bad:
        for scope in ('', SRC, S1):
            with pytest.raises(ValueError):
                overrides.set(ws, path, value, scope=scope, author='ta')
    assert ws.store.all_overrides() == []
    for path, value, scope in [('a/fields/x/region', [1, 0, 0, 5, 5], SRC), ('a/checks/check/key', 'x', S1),
                               ('@print_area', None, S1), ('a/fields/x/region', [1, 0, 0, 5, 5], 'nobody:x')]:
        with pytest.raises(ValueError):
            overrides.set(ws, path, value, scope=scope, author='ta')
    # a value stored below the exam that no longer fits is not applied, like one for the exam
    ws.store.set_override('a/fields/x/region', [9, 0, 0, 5, 5], scope=S1, author='ta')
    assert ws.layers('s1').question('a').field('x').region == exam.questions['a'].field('x').region


def test_changes_below_the_exam_outdate_no_check(scoped):
    exam, ws = scoped
    before = {qc: exam.check_hash(exam.questions[qc[0]], exam.questions[qc[0]].check(qc[1])) for qc in
              [(qid, c.name) for qid, q in exam.questions.items() for c in q.checks]}
    change = overrides.set(ws, 'a/fields/x/region', [1, 0, 0, 5, 5], scope=S1, author='ta')
    assert change.checks == ()
    change = overrides.set(ws, '@print_area', [5, 5, 995, 995], scope=SRC, author='ta')
    assert change.checks == ()
    change = overrides.set(ws, '@print_area', [9, 9, 991, 991], author='ta')     # every crop moves
    assert change.checks == ()
    live = ws.exam
    assert {qc: live.check_hash(live.questions[qc[0]], live.questions[qc[0]].check(qc[1])) for qc in before} == before


def test_the_change_list_has_the_print_area(scoped):
    exam, ws = scoped
    overrides.set(ws, '@print_area', [5, 5, 995, 995], author='ta')
    overrides.set(ws, 'a/fields/x/region', [2, 0, 0, 5, 5], scope=S1, author='ta')     # one submission's: not for the package
    lines = describe_changes(exam, ws.store)
    assert [x.split('  (')[0] for x in lines] == ['@print_area: (0, 0, 1000, 1000) -> (5, 5, 995, 995)']
    entries = {e.path: e for e in overrides.editable(ws.exam)}
    assert entries['@print_area'].current == [5, 5, 995, 995] and entries['@print_area'].overridden
    fresh = make_exam()
    other = Workspace(ws.root, fresh)
    assert other.skipped == [] and other.exam.print_area == (5, 5, 995, 995)
    assert fresh.base['@print_area'] == (0, 0, 1000, 1000)
    other.close()


def test_a_replaced_check_is_let_go(kenv):
    """The check-hash cache holds only the checks of the definition in force: a check an
    edit replaced is not kept alive by it."""
    import gc
    import weakref
    exam, ws, runner = kenv
    overrides.set(ws, 'a/checks/x/key/one/x', '2', author='ta')
    q = ws.exam.questions['a']
    ws.exam.check_hash(q, q.check('x'))
    gone = weakref.ref(q.check('x'))
    del q
    overrides.set(ws, 'a/checks/x/key/one/x', '3', author='ta')
    gc.collect()
    assert gone() is None
