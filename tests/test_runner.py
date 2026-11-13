"""The small changes: read, set, verify, unverify; compute; decide, clear.
Everything here is synthetic."""
import pytest

from examinator import Check, Exam, Field, Printed, Region
from examinator.core import overrides
from examinator.core.runner import Runner
from examinator.core.store import Conflict
from examinator.model.fake import FakeModel

from conftest import SCRIPT, make_exam, workspace


def value(ws, sub, k):
    from examinator.core.status import Value
    row = ws.store.get(sub, k, 'value')
    return Value.of(row) if row else None


def state(runner, sub, qid, check):
    q = runner.exam.questions[qid]
    return runner.sheet(sub).status(runner.exam, q, q.check(check))


def read_all(runner, sub):
    for qid in runner.exam.grading:
        assert all(o.ok for o in runner.read(sub, qid)), qid


# --- read ---

def test_read_writes_values_unsettled_unless_the_reader_vouches(env, tmp_path):
    exam, ws, runner = env
    read_all(runner, 's1')
    assert value(ws, 's1', 'a/x').text == '1' and not value(ws, 's1', 'a/x').is_settled
    assert value(ws, 's1', 'p/c1').text == 'one' and value(ws, 's1', 'p/c1').settled is None   # verify=True
    trusted = make_exam(trust=True)
    ws2 = workspace(tmp_path / 't', trusted)
    r2 = Runner(trusted, ws2, model=FakeModel(dict(SCRIPT)))
    read_all(r2, 's1')
    assert value(ws2, 's1', 'p/c1').settled == 'reader'           # one of a closed set: vouched for
    r2.close()
    ws2.close()


def test_reading_again_the_same_text_changes_nothing(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    before = value(ws, 's1', 'a/x')
    runner.verify('s1', 'a', 'x', author='ta', expect_rev=before.rev)
    version = ws.store.version()
    assert all(o.ok for o in runner.read('s1', 'a', include_protected=True))
    after = value(ws, 's1', 'a/x')
    assert (after.rev, after.settled) == (before.rev, 'person') and ws.store.version() == version


def test_a_read_never_replaces_what_a_person_settled_unless_told(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    runner.set('s1', 'a', 'x', "7", author='ta')
    runner.read('s1', 'a')
    assert value(ws, 's1', 'a/x').text == '7'
    runner.read('s1', 'a', include_protected=True)
    assert value(ws, 's1', 'a/x').text == '1' and value(ws, 's1', 'a/x').settled is None
    assert [h.body['text'] for h in ws.store.history('s1', 'a/x', 'value')][:1] == ['7']      # kept in history


def test_a_read_can_fill_only_the_fields_with_no_value(env):
    exam, ws, runner = env
    runner.set('s1', 'a', 'x', "7", author='ta')
    runner.verify('s1', 'a', 'x', author='ta', expect_rev=value(ws, 's1', 'a/x').rev, verified=False)
    runner.read('s1', 'a', missing_only=True)
    assert value(ws, 's1', 'a/x').text == '7' and value(ws, 's1', 'a/y').text == '2'
    calls = [c for c in runner.model.calls if c.tag[1] == 'a']
    assert calls and "parse" in calls[-1].prompt


def test_a_field_without_a_region_is_not_read_until_it_has_one(tmp_path):
    exam = Exam('r', pages=1)
    exam.question('info', pages=[1], fields=[Field('c', read=Printed("the cue", reads={'1': 'one'}))])
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(exam, ws, model=FakeModel({('info', 'read:c'): '{"value": "1"}'}))
    outs = runner.read('s1', 'info')
    assert [(o.status, o.detail) for o in outs] == [('blocked', "no region: c")] and value(ws, 's1', 'info/c') is None
    overrides.set(exam, ws.store, 'info/fields/c/region', [1, 0, 0, 500, 100], scope='submission:s1',
                  author='ta')                                                  # this submission only
    assert [o.status for o in runner.read('s1', 'info')] == ['done'] and value(ws, 's1', 'info/c').text == 'one'
    runner.close()
    ws.close()


def test_a_printed_reading_is_remembered_per_crop_and_a_new_print_area_reads_again(env):
    exam, ws, _ = env
    replies = {'read:c1': '{"value": "1"}', 'read:c2': '{"value": "i"}'}
    runner = Runner(exam, ws, model=FakeModel(lambda call: replies[call.tag[2]]))

    def calls():
        return len([c for c in runner.model.calls if c.tag[1] == 'p'])
    assert all(o.ok for o in runner.read('s1', 'p'))
    assert calls() == 2 and value(ws, 's1', 'p/c1').text == 'one'
    assert all(o.ok for o in runner.read('s1', 'p')) and calls() == 2        # the same crops: remembered
    replies.update({'read:c1': '{"value": "2"}', 'read:c2': '{"value": "ii"}'})
    own = lambda path, value: (overrides.set(exam, ws.store, path, value, scope='submission:s1', author='ta')
                               if value is not None else overrides.clear(exam, ws.store, path, scope='submission:s1'))
    own('@print_area', [50, 50, 950, 950])                                       # another crop
    assert all(o.ok for o in runner.read('s1', 'p'))
    assert calls() == 4 and value(ws, 's1', 'p/c1').text == 'two'
    own('@print_area', None)                                                     # the first crop again
    assert all(o.ok for o in runner.read('s1', 'p')) and calls() == 4
    assert value(ws, 's1', 'p/c1').text == 'one'


def test_a_failed_read_writes_nothing_and_says_why_structurally(env):
    exam, ws, runner = env
    runner.model.script.pop(('a', 'parse'))
    outs = runner.read('s1', 'a')
    assert [(o.status, o.detail) for o in outs] == [('failed', "no scripted response")]
    assert value(ws, 's1', 'a/x') is None


def test_no_model_blocks_reads(env):
    exam, ws, runner = env
    runner.model = None
    assert [(o.status, o.detail) for o in runner.read('s1', 'a')] == [('blocked', "model unavailable")]


# --- set, verify, unverify ---

def test_a_person_who_sets_a_value_settles_it_unless_it_carries_a_marker(env):
    exam, ws, runner = env
    v = runner.set('s1', 'a', 'x', "1", author='ta')
    assert (v.settled, v.by, v.author) == ('person', 'ta', 'ta')
    v = runner.set('s1', 'a', 'x', "1 [REVIEW: or 7]", author='ta', expect_rev=v.rev)
    assert v.settled is None
    with pytest.raises(ValueError, match="review markers"):
        runner.verify('s1', 'a', 'x', author='ta', expect_rev=v.rev)
    with pytest.raises(Conflict):
        runner.set('s1', 'a', 'x', "2", author='tb', expect_rev=v.rev - 1)


def test_blank_is_a_value_and_differs_from_not_read(env):
    exam, ws, runner = env
    v = runner.set('s1', 'a', 'x', "", author='ta')
    assert v.text == '' and v.is_settled and value(ws, 's1', 'a/y') is None


def test_a_value_is_one_line_unless_its_field_is_multiline(env):
    from examinator.std import readers
    exam, ws, runner = env
    assert runner.set('s1', 'a', 'x', "1 +\n  2\n", author='ta').text == '1 + 2'
    q = exam.questions['a']
    assert not q.multiline('x')
    code = Exam('e', pages=1).question('c', pages=[1], fields=[Field('f'), Field('g', multiline=False)],
                                       read=readers.code(['f']))
    assert code.multiline('f') and not code.multiline('g')         # the reader's default; the field's own word


def test_verify_and_unverify_keep_the_revision(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    v = value(ws, 's1', 'a/x')
    w = runner.verify('s1', 'a', 'x', author='ta', expect_rev=v.rev)
    assert (w.rev, w.settled, w.by) == (v.rev, 'person', 'ta')
    u = runner.verify('s1', 'a', 'x', author='ta', expect_rev=v.rev, verified=False)
    assert (u.rev, u.settled, u.text) == (v.rev, None, '1')
    assert len(ws.store.history('s1', 'a/x', 'value')) == 2         # both are history


def test_setting_the_same_text_is_a_verify(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    v = value(ws, 's1', 'a/x')
    w = runner.set('s1', 'a', 'x', "1", author='ta', expect_rev=v.rev)
    assert (w.rev, w.settled) == (v.rev, 'person')


# --- compute ---

def test_compute_records_what_it_read(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    assert runner.compute('s1', 'a', 'x').ok
    from examinator.core.status import Stored
    reads = Stored.of(ws.store.get('s1', 'a/x', 'result')).reads
    assert set(reads) == {'p/c1', 'p/c2', 'a/x'}             # not 'a/y': it never read it
    assert state(runner, 's1', 'a', 'x').state == 'unverified'


def test_a_check_that_reads_a_field_not_read_yet_is_undecided_and_outdated_once_it_is(env):
    exam, ws, runner = env
    runner.read('s1', 'a')
    assert runner.compute('s1', 'a', 'x').ok
    st = state(runner, 's1', 'a', 'x')
    assert (st.state, st.value) == ('unverified', None) and "not read" in st.reason
    runner.read('s1', 'p')
    assert state(runner, 's1', 'a', 'x').state == 'outdated'
    assert [o.step for o in runner.refresh('s1')] == ['x']
    for k, v in runner.values('s1').items():
        runner.verify('s1', *k.split('/'), author='ta', expect_rev=v.rev)
    assert (state(runner, 's1', 'a', 'x').state, state(runner, 's1', 'a', 'x').value) == ('graded', True)


def test_a_result_computed_from_values_that_changed_meanwhile_is_discarded(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    original = exam.questions['a'].check('x').fn

    def sneaky(ctx):
        out = original(ctx)
        runner.set('s1', 'a', 'x', "10", author='ta')      # a person edits while the check runs
        return out
    import dataclasses
    q = exam.questions['a']
    exam.questions['a'] = dataclasses.replace(q, checks=(dataclasses.replace(q.check('x'), fn=sneaky), q.check('y')))
    out = runner.compute('s1', 'a', 'x')
    assert out.status == 'discarded' and ws.store.get('s1', 'a/x', 'result') is None


def test_refresh_recomputes_only_what_is_outdated(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    assert [o.step for o in runner.refresh('s1')] == []                       # nothing ran yet: not outdated
    assert sorted(o.step for o in runner.refresh('s1', questions=['a', 'b'])) == ['check', 'x', 'y']
    assert runner.refresh('s1') == []
    runner.set('s1', 'a', 'y', "3", author='ta')
    assert [o.step for o in runner.refresh('s1')] == ['y']                    # only the check that read it


def test_a_check_with_no_function_is_decided_by_a_person(tmp_path):
    exam = Exam('m', pages=1)
    exam.question('w', pages=[1], fields=[Field('x')], check=Check(None))
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(exam, ws)
    assert runner.compute('s1', 'w', 'check').status == 'blocked'
    assert state(runner, 's1', 'w', 'check').state == 'to_decide'
    runner.decide('s1', 'w', 'check', True, author='ta')
    assert state(runner, 's1', 'w', 'check').state == 'decided'
    runner.close()
    ws.close()


# --- decide, clear ---

def test_a_decision_is_recorded_against_the_fields_on_the_page(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    runner.compute('s1', 'a', 'x')
    page = runner.page('s1', 'a', check='x')
    assert page == ['a/x', 'a/y', 'p/c1', 'p/c2']             # its own fields, then those it read
    seen = {k: value(ws, 's1', k).rev for k in page}
    d = runner.decide('s1', 'a', 'x', False, author='ta', seen=seen)
    assert d.seen == seen and state(runner, 's1', 'a', 'x').state == 'decided'
    assert state(runner, 's1', 'a', 'x').value is False


def test_a_decision_on_a_page_that_changed_is_refused(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    runner.compute('s1', 'a', 'x')
    seen = {k: value(ws, 's1', k).rev for k in runner.page('s1', 'a', check='x')}
    runner.set('s1', 'p', 'c1', "two", author='tb')
    with pytest.raises(Conflict):
        runner.decide('s1', 'a', 'x', True, author='ta', seen=seen)
    with pytest.raises(Conflict):                               # a field missing from what was seen
        runner.decide('s1', 'a', 'x', True, author='ta', seen={'a/x': seen['a/x']})


def test_a_decision_is_outdated_when_a_field_changes_and_clearing_it_brings_the_result_back(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    runner.compute('s1', 'a', 'x')
    d = runner.decide('s1', 'a', 'x', True, author='ta')
    runner.set('s1', 'a', 'y', "5", author='tb')               # on the page, though the check did not read it
    assert state(runner, 's1', 'a', 'x').state == 'decided_outdated'
    with pytest.raises(Conflict):
        runner.clear('s1', 'a', 'x', expect_rev=d.rev + 100)
    runner.clear('s1', 'a', 'x', expect_rev=d.rev)
    assert state(runner, 's1', 'a', 'x').state == 'unverified'


def test_a_decision_needs_a_value_that_fits(env):
    exam, ws, runner = env
    with pytest.raises(ValueError):
        runner.decide('s1', 'a', 'x', None, author='ta')
    with pytest.raises(ValueError):
        runner.decide('s1', 'a', 'x', 3, author='ta')


# --- batch ---

def test_a_cancelled_batch_stops_queued_reads(tmp_path):
    from examinator.core import batch
    exam = Exam('r', pages=1)
    exam.question('info', pages=[1], fields=[Field('c', Region(1, 0, 0, 500, 100),
                                                    read=Printed("the cue", reads={'1': 'one'}))])
    ws = workspace(tmp_path, exam, subs=('s1', 's2', 's3'))
    holder = {}

    def reply(call):
        holder['run'].cancel()                      # stop pressed during the first read
        return '{"value": "1"}'
    runner = Runner(exam, ws, model=FakeModel(reply))
    plan = batch.plan(exam, ws.store, batch.Selection(('s1', 's2', 's3'), ('info',)), model_available=True,
                      regions=ws.fields)
    run = holder['run'] = batch.BatchRun(runner, plan, parallel=1)
    run.run()
    assert run.state == 'cancelled' and run.done == 1 < run.total
    assert [value(ws, s, 'info/c') is not None for s in ('s1', 's2', 's3')] == [True, False, False]
    runner.close()
    ws.close()


def test_a_batch_reads_what_it_plans_and_never_what_a_person_set_meanwhile(tmp_path):
    from examinator.core import batch
    exam = Exam('r', pages=1)
    exam.question('info', pages=[1], fields=[Field('c', read=Printed("the cue", reads={'1': 'one'}))])
    ws = workspace(tmp_path, exam, subs=('s1', 's2'))
    runner = Runner(exam, ws, model=FakeModel({('info', 'read:c'): '{"value": "1"}'}))
    sel = batch.Selection(('s1', 's2'), ('info',))
    plan = lambda: batch.plan(exam, ws.store, sel, model_available=True, regions=ws.fields)
    assert [(i.question, i.action) for i in plan().items] == [('info', 'blocked')] * 2
    overrides.set(exam, ws.store, 'info/fields/c/region', [1, 0, 0, 500, 100], author='ta')   # no value changed
    planned = plan()
    assert planned.count('run') == 2
    runner.set('s2', 'info', 'c', "two", author='ta')            # after the plan, before the run
    assert all(o.ok for o in batch.BatchRun(runner, planned).run())
    assert value(ws, 's1', 'info/c').text == 'one' and value(ws, 's2', 'info/c').text == 'two'
    runner.close()
    ws.close()
