"""Changing the exam while grading (MODEL.md): stored things are keyed by name,
so the package can change under a workspace. One test per row of the table.
Everything here is synthetic."""

from examinator import Bool, Check, Exam, Field, Region
from examinator.core import overrides
from examinator.core.runner import Runner
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.std import math

from conftest import Parse, workspace

SCRIPT_VALUES = {'a': '1', 'b': '2', 'c': '3'}
SCRIPT = {('q', 'parse'): '{"a": "1", "b": "2", "c": "3"}', ('r', 'parse'): '{"z": "9"}'}


def build(*, labels=('a', 'b'), key=None, points=Bool(), extra_check='', question_r=False):
    """One version of a small exam. Its checks hold their keys as data."""
    exam = Exam('changing', pages=1)
    checks = {'check': Check(math.keyed(key or {'a': '1'}), points, isolate=False)}
    if extra_check:
        checks['both'] = Check(math.keyed({x: SCRIPT_VALUES[x] for x in extra_check}), isolate=False)
    exam.question('q', pages=[1], fields=[Field(label) for label in labels], read=Parse(), checks=checks)
    if question_r:
        exam.question('r', pages=[1], fields=[Field('z')], read=Parse(), check=Check(math.keyed({'z': '9'}),
                                                                                       isolate=False))
    return exam


def graded(tmp_path, exam):
    """A workspace where q is read, verified and graded for 's1', with a decision on 's2'."""
    ws = workspace(tmp_path, exam)
    runner = Runner(ws, model=FakeModel(dict(SCRIPT)))
    for sub in ('s1', 's2'):
        runner.read(sub, 'q')
        for k, v in runner.values(sub).items():
            runner.verify(sub, *k.split('/'), author='ta', expect_rev=v.rev)
        runner.refresh(sub, questions=['q'])
    runner.decide('s2', 'q', 'check', False, author='ta')
    return ws, runner


def reopen(ws, runner, exam):
    """The same workspace under another version of the package."""
    runner.close()
    ws.close()
    ws = Workspace(ws.root, exam)
    return ws, Runner(ws, model=FakeModel(dict(SCRIPT)))


def snapshot(ws, sub):
    return {(r.kind, r.name): (r.rev, r.body) for r in ws.store.all(submission=sub)}


def st(runner, sub, qid, check):
    q = runner.ws.exam.questions[qid]
    return runner.sheet(sub).status(runner.ws.exam, q, q.check(check))


def test_a_check_whose_key_changes_is_recomputed_and_nothing_else_moves(tmp_path):
    ws, runner = graded(tmp_path, build())
    before = snapshot(ws, 's1')
    assert st(runner, 's1', 'q', 'check').value is True
    overrides.set(ws, 'q/checks/check/key/a', '2', author='ta')
    assert st(runner, 's1', 'q', 'check').state == 'outdated'
    assert st(runner, 's2', 'q', 'check').state == 'decided'              # decisions untouched
    assert [o.step for o in runner.refresh('s1')] == ['check']
    s = st(runner, 's1', 'q', 'check')
    assert (s.state, s.value) == ('graded', False)
    after = snapshot(ws, 's1')
    assert {k: v for k, v in after.items() if k[0] == 'value'} == {k: v for k, v in before.items() if k[0] == 'value'}
    assert ws.store.history('s1', 'q/check', 'result') == []              # results keep no history
    runner.close()
    ws.close()


def test_a_check_whose_code_changes_is_recomputed(tmp_path):
    ws, runner = graded(tmp_path, build())
    exam = build()
    exam.code_hash = 'another'
    ws, runner = reopen(ws, runner, exam)
    assert st(runner, 's1', 'q', 'check').state == 'outdated'
    assert st(runner, 's2', 'q', 'check').state == 'decided'
    runner.refresh('s1')
    assert st(runner, 's1', 'q', 'check').state == 'graded'
    runner.close()
    ws.close()


def test_a_question_or_check_added_disturbs_nothing(tmp_path):
    ws, runner = graded(tmp_path, build())
    before = snapshot(ws, 's1')
    ws, runner = reopen(ws, runner, build(question_r=True, extra_check='ab'))
    assert st(runner, 's1', 'q', 'check').state == 'graded'
    assert st(runner, 's1', 'q', 'both').state == 'not_run'
    assert st(runner, 's1', 'r', 'check').state == 'not_run'
    assert runner.refresh('s1') == []                                     # nothing outdated, nothing ran
    assert snapshot(ws, 's1') == before
    runner.close()
    ws.close()


def test_a_field_added_to_a_question(tmp_path):
    ws, runner = graded(tmp_path, build())
    before = snapshot(ws, 's1')
    ws, runner = reopen(ws, runner, build(labels=('a', 'b', 'c'), extra_check='ac'))
    values = runner.values('s1')
    assert values['q/a'].is_verified and values['q/b'].is_verified and 'q/c' not in values   # not read
    assert st(runner, 's1', 'q', 'check').state == 'graded'               # does not read it: unaffected
    assert snapshot(ws, 's1') == before
    runner.refresh('s1', questions=['q'])                                 # the new check reads it
    s = st(runner, 's1', 'q', 'both')
    assert (s.state, s.value) == ('unverified', None) and "c not read" in s.reason
    runner.read('s1', 'q', missing_only=True)                             # fills only the new field
    assert runner.values('s1')['q/a'].rev == values['q/a'].rev and runner.values('s1')['q/a'].is_verified
    assert st(runner, 's1', 'q', 'both').state == 'outdated'
    runner.refresh('s1')
    assert st(runner, 's1', 'q', 'both').state == 'unverified'            # waits for the new value
    runner.close()
    ws.close()


def test_a_field_removed_is_ignored_and_restoring_it_restores_its_value(tmp_path):
    ws, runner = graded(tmp_path, build())
    kept = runner.values('s1')['q/b']
    ws, runner = reopen(ws, runner, build(labels=('a',)))
    assert ws.store.get('s1', 'q/b', 'value') is not None                 # not deleted
    assert runner.page('s1', 'q') == ['q/a']                              # but not on the page
    assert st(runner, 's1', 'q', 'check').state == 'graded'
    ws, runner = reopen(ws, runner, build())
    assert runner.values('s1')['q/b'] == kept
    runner.close()
    ws.close()


def test_a_check_removed_and_restored(tmp_path):
    ws, runner = graded(tmp_path, build(extra_check='ab'))
    runner.refresh('s1', questions=['q'])
    both = ws.store.get('s1', 'q/both', 'result')
    ws, runner = reopen(ws, runner, build())
    assert runner.refresh('s1') == []
    ws, runner = reopen(ws, runner, build(extra_check='ab'))
    assert ws.store.get('s1', 'q/both', 'result') == both
    runner.close()
    ws.close()


def test_points_change_totals_follow_at_once(tmp_path):
    from examinator.core import grid
    ws, runner = graded(tmp_path, build())
    assert [r.points for r in grid.load(runner.ws.exam, ws.store)] == [1.0, 0.0]
    ws, runner = reopen(ws, runner, build(points=Bool(worth=3)))
    assert [r.points for r in grid.load(runner.ws.exam, ws.store)] == [3.0, 0.0]
    assert st(runner, 's1', 'q', 'check').state == 'graded'               # points are not the check's definition
    runner.close()
    ws.close()


def test_how_a_field_is_read_changes_and_no_value_changes(tmp_path):
    ws, runner = graded(tmp_path, build())
    before = snapshot(ws, 's1')
    change = overrides.set(ws, 'q/fields/a/region', [1, 0, 0, 500, 100], author='ta')
    assert change.checks == ()
    # the same below the exam: one submission's region, print area
    own = overrides.scope(overrides.SUBMISSION, 's1')
    for path, value in (('q/fields/b/region', [1, 0, 100, 500, 200]),
                        ('@print_area', [10, 10, 990, 990])):
        change = overrides.set(ws, path, value, scope=own, author='ta')
        assert change.checks == ()
    assert snapshot(ws, 's1') == before
    assert [st(runner, 's1', 'q', 'check').state, st(runner, 's2', 'q', 'check').state] == ['graded', 'decided']
    # a field removed from the package: its own region is ignored, not deleted; restoring the name restores it
    ws, runner = reopen(ws, runner, build(labels=('a',)))
    fields = ws.layers('s1').question('q').fields         # opening applies the exam's override of 'a'
    assert [(f.label, f.region) for f in fields] == [('a', Region(1, 0, 0, 500, 100))]
    ws, runner = reopen(ws, runner, build())
    assert ws.layers('s1').question('q').field('b').region.top == 100
    runner.close()
    ws.close()
