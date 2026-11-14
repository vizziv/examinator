"""The small changes: read, set, verify, unverify; compute; decide, clear.
Everything here is synthetic."""
from pathlib import Path

import pytest

from examinator import Check, Exam, Field, Printed, Region
from examinator.core import overrides
from examinator.core.reading import Reading
from examinator.core.runner import Runner
from examinator.core.status import Value
from examinator.core.store import Conflict
from examinator.model.fake import FakeModel

from conftest import SCRIPT, Parse, make_exam, workspace

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'



def value(ws, sub, k):
    row = ws.store.get(sub, k, 'value')
    return Value.of(row) if row else None


def state(runner, sub, qid, check):
    q = runner.ws.exam.questions[qid]
    return runner.sheet(sub).status(runner.ws.exam, q, q.check(check))


def read_all(runner, sub):
    for qid in runner.ws.exam.questions:
        assert all(o.ok for o in runner.read(sub, qid)), qid


# --- read ---

def test_read_writes_values_unverified_unless_the_reader_vouches(env, tmp_path):
    exam, ws, runner = env
    read_all(runner, 's1')
    assert value(ws, 's1', 'a/x').text == '1' and not value(ws, 's1', 'a/x').is_verified
    assert value(ws, 's1', 'p/c1').text == 'one' and value(ws, 's1', 'p/c1').verified is None   # verify=True
    trusted = make_exam(trust=True)
    ws2 = workspace(tmp_path / 't', trusted)
    r2 = Runner(ws2, model=FakeModel(dict(SCRIPT)))
    read_all(r2, 's1')
    assert value(ws2, 's1', 'p/c1').verified == 'reader'           # printed, no marker: vouched for
    r2.close()
    ws2.close()


def test_a_printed_reading_is_vouched_for_unless_flagged_and_handwriting_never():
    from examinator import Ask
    f = Field('c', Region(1, 0, 0, 10, 10))
    printed = Printed("the printed number")
    assert printed.vouches(f, Reading("30"))
    unsure = Reading("3", "smudged")
    assert Value.read(unsure, printed.vouches(f, unsure)).state == 'review'      # unsure: a person looks
    assert not printed.vouches(f, Reading(""))                            # a blank is never vouched for
    assert not Printed("the printed number", verify=True).vouches(f, Reading("30"))
    assert not Ask("the student's name").vouches(f, Reading("Ann"))      # handwriting: a person verifies


def test_a_value_with_no_key_is_undecided_and_says_which(tmp_path):
    """A by-value key has no declared set of values: one with no key is caught when it is
    read, as an undecided grade naming the field and the value."""
    from examinator.std import math
    exam = Exam('k', pages=1)
    exam.question('q', pages=[1], fields=[Field('n', Region(1, 0, 0, 500, 100), read=Printed("n, as a number")),
                                          Field('x')], read=Parse(),
                  check=Check(math.keyed({'10': {'x': '1'}, '20': {'x': '2'}}, by='n'), isolate=False))
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(ws, model=FakeModel({('q', 'read:n'): '{"value": "30"}', ('q', 'parse'): '{"x": "1"}'}))
    try:
        assert all(o.ok for o in runner.read('s1', 'q'))
        assert value(ws, 's1', 'q/n').verified == 'reader'
        runner.refresh('s1')
        st = state(runner, 's1', 'q', 'check')
        assert st.value is None and st.reason == "no key for n = 30"
        runner.set('s1', 'q', 'n', '10', author='ta')
        runner.refresh('s1')
        assert state(runner, 's1', 'q', 'check').value is True
        # what to expect is the ask's to say; an edited ask is what the next read asks
        overrides.set(ws, 'q/fields/n/read/ask', "n, as a number: 10 or 20", author='ta')
        runner.read('s1', 'q', labels=['n'], include_protected=True)
        assert "Report: n, as a number: 10 or 20." in runner.model.calls[-1].prompt
    finally:
        runner.close()
        ws.close()


def test_reading_again_the_same_text_changes_nothing(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    before = value(ws, 's1', 'a/x')
    runner.verify('s1', 'a', 'x', author='ta', expect_rev=before.rev)
    version = ws.store.version()
    assert all(o.ok for o in runner.read('s1', 'a', include_protected=True))
    after = value(ws, 's1', 'a/x')
    assert (after.rev, after.verified) == (before.rev, 'person') and ws.store.version() == version


def test_a_read_never_undoes_what_a_person_said_about_a_value(env):
    """A person's flag survives a read of the same text (and a read never verifies it);
    a read of another text is a new value, its state the reader's."""
    exam, ws, runner = env
    read_all(runner, 's1')
    v = runner.review('s1', 'a', 'x', note="1 or 7?", expect_rev=value(ws, 's1', 'a/x').rev)
    runner.read('s1', 'a')
    after = value(ws, 's1', 'a/x')
    assert (after.rev, after.review, after.flagged, after.state) == (v.rev, "1 or 7?", 'person', 'review')
    ws.store.write('s1', 'a/x', 'value', Value('7').body(), origin='auto', expect_rev=v.rev)
    runner.review('s1', 'a', 'x', note="", expect_rev=value(ws, 's1', 'a/x').rev)
    runner.read('s1', 'a')
    assert (value(ws, 's1', 'a/x').text, value(ws, 's1', 'a/x').state) == ('1', 'unverified')


def test_the_small_changes_to_a_value():
    """Each change as `Value` states it; needing review and being verified exclude each other."""
    sure, unsure = Reading("1"), Reading("1", "1 or 7?")
    read = Value.read(sure, True)
    assert (read.verified, read.state) == ('reader', 'verified')
    flagged = Value.read(unsure, True)
    assert (flagged.verified, flagged.flagged, flagged.state) == (None, 'reader', 'review')
    assert Value.read(sure, True, flagged).state == 'verified'                 # the reader's own flag: its to drop
    mine = read.flag("why?")
    assert (mine.flagged, mine.verified) == ('person', None)
    assert Value.read(sure, True, mine) is mine                                # a person's flag: kept
    assert Value.read(Reading("7"), False, mine).state == 'unverified'         # another text: a new value
    v = mine.verify('ta')
    assert (v.verified, v.by, v.review, v.flagged) == ('person', 'ta', None, None) and v.protected
    assert Value.read(Reading("1", "hmm"), False, v) is v
    assert not v.unverify().protected and v.flag(None) == v
    assert Value.set("2", 'ta').protected and Value('2', verified='person', review='').state == 'review'


def test_a_read_never_replaces_what_a_person_verified_unless_told(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    runner.set('s1', 'a', 'x', "7", author='ta')
    runner.read('s1', 'a')
    assert value(ws, 's1', 'a/x').text == '7'
    runner.read('s1', 'a', include_protected=True)
    assert value(ws, 's1', 'a/x').text == '1' and value(ws, 's1', 'a/x').verified is None
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
    exam.question('info', pages=[1], fields=[Field('c', read=Printed("the cue"))])
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(ws, model=FakeModel({('info', 'read:c'): '{"value": "one"}'}))
    outs = runner.read('s1', 'info')
    assert [(o.status, o.detail) for o in outs] == [('blocked', "no region: c")] and value(ws, 's1', 'info/c') is None
    overrides.set(ws, 'info/fields/c/region', [1, 0, 0, 500, 100], scope='submission:s1',
                  author='ta')                                                  # this submission only
    assert [o.status for o in runner.read('s1', 'info')] == ['done'] and value(ws, 's1', 'info/c').text == 'one'
    runner.close()
    ws.close()


def test_a_read_asks_the_model_every_time_and_keeps_only_the_value(env):
    """Nothing remembers a field's reading but its value: reading again (always a
    person's request) asks the model again, for the same crop or another; reading
    only what is missing (resuming a read) asks nothing of what has a value."""
    exam, ws, _ = env
    replies = {'read:c1': '{"value": "one"}', 'read:c2': '{"value": "one"}'}
    runner = Runner(ws, model=FakeModel(lambda call: replies[call.tag[2]]))

    def calls():
        return len([c for c in runner.model.calls if c.tag[1] == 'p'])
    assert all(o.ok for o in runner.read('s1', 'p'))
    assert calls() == 2 and value(ws, 's1', 'p/c1').text == 'one'
    assert all(o.ok for o in runner.read('s1', 'p')) and calls() == 4        # the same crops: asked again
    replies.update({'read:c1': '{"value": "two"}', 'read:c2': '{"value": "two"}'})
    assert all(o.ok for o in runner.read('s1', 'p')) and calls() == 6
    assert value(ws, 's1', 'p/c1').text == 'two'
    assert runner.read('s1', 'p', missing_only=True) == [] and calls() == 6


def test_a_failed_read_writes_nothing_and_says_why_structurally(env):
    exam, ws, runner = env
    runner.model.script.pop(('a', 'parse'))
    outs = runner.read('s1', 'a')
    assert [(o.status, o.detail) for o in outs] == [('failed', "no scripted response")]
    assert value(ws, 's1', 'a/x') is None


def test_no_model_blocks_reads(env):
    exam, ws, runner = env
    runner.model = None
    assert [(o.status, o.detail) for o in runner.read('s1', 'a')] == [('blocked', "model unavailable: x, y")]


# --- set, verify, unverify ---

def test_a_person_who_sets_a_value_verifies_it_and_clears_its_review(env):
    exam, ws, runner = env
    v = runner.set('s1', 'a', 'x', "1", author='ta')
    assert (v.verified, v.by, v.author) == ('person', 'ta', 'ta')
    v = runner.review('s1', 'a', 'x', note="1 or 7?", expect_rev=v.rev)
    assert (v.review, v.verified, v.state) == ("1 or 7?", None, 'review')
    v = runner.set('s1', 'a', 'x', "7", author='ta', expect_rev=v.rev)
    assert (v.review, v.verified) == (None, 'person')
    with pytest.raises(Conflict):
        runner.set('s1', 'a', 'x', "2", author='tb', expect_rev=v.rev - 1)


def test_blank_is_a_value_and_differs_from_not_read(env):
    exam, ws, runner = env
    v = runner.set('s1', 'a', 'x', "", author='ta')
    assert v.text == '' and v.is_verified and value(ws, 's1', 'a/y') is None


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
    assert (w.rev, w.verified, w.by) == (v.rev, 'person', 'ta')
    u = runner.verify('s1', 'a', 'x', author='ta', expect_rev=v.rev, verified=False)
    assert (u.rev, u.verified, u.text) == (v.rev, None, '1')
    assert len(ws.store.history('s1', 'a/x', 'value')) == 2         # both are history


def test_setting_the_same_text_is_a_verify(env):
    exam, ws, runner = env
    read_all(runner, 's1')
    v = value(ws, 's1', 'a/x')
    w = runner.set('s1', 'a', 'x', "1", author='ta', expect_rev=v.rev)
    assert (w.rev, w.verified) == (v.rev, 'person')


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
    assert [o.step for o in runner.refresh('s1')] == ['x', 'y']           # x again; y never ran, a's fields are there
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


def test_refresh_runs_what_is_not_current_once_its_fields_are_there(env):
    exam, ws, runner = env
    assert runner.refresh('s1') == []                                         # nothing read: nothing to run
    runner.read('s1', 'a')
    assert sorted(o.step for o in runner.refresh('s1')) == ['x', 'y']         # a's fields are there; b's are not
    assert state(runner, 's1', 'b', 'check').state == 'not_run'
    read_all(runner, 's1')
    assert sorted(o.step for o in runner.refresh('s1')) == ['check', 'x', 'y']   # a's read p: outdated
    assert runner.refresh('s1') == []
    runner.set('s1', 'a', 'y', "3", author='ta')
    assert [o.step for o in runner.refresh('s1')] == ['y']                    # only the check that read it


def test_a_check_with_no_function_is_decided_by_a_person(tmp_path):
    exam = Exam('m', pages=1)
    exam.question('w', pages=[1], fields=[Field('x')], check=Check(None))
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(ws)
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

def test_a_cancelled_batch_stops_queued_reads_and_what_was_read_is_checked(tmp_path):
    """The batch is its reads: Stop stops reads, progress counts reads (as the plan
    does), and every submission something was read for is refreshed regardless."""
    from examinator.core import batch
    exam = Exam('r', pages=1)
    exam.question('info', pages=[1], fields=[Field('c', Region(1, 0, 0, 500, 100), read=Printed("the cue"))],
                  check=Check(lambda ctx: 'correct' if ctx.read('c') == 'one' else 'incorrect', isolate=False))
    subs = ('s1', 's2', 's3')
    ws = workspace(tmp_path, exam, subs=subs)
    holder, calls = {}, []

    def reply(call):
        calls.append(call)
        if len(calls) == 2:
            holder['run'].cancel()                  # stop pressed during the second read
        return '{"value": "one"}'
    runner = Runner(ws, model=FakeModel(reply))
    plan = batch.plan(ws, batch.Selection(subs, ('info',)), model_available=True)
    run = holder['run'] = batch.BatchRun(runner, plan, parallel=1)
    run.run()
    assert run.state == 'cancelled' and (run.done, run.total) == (2, plan.count('read')) == (2, 3)
    assert [value(ws, s, 'info/c') is not None for s in subs] == [True, True, False]
    assert [state(runner, s, 'info', 'check').state for s in subs] == ['graded', 'graded', 'not_run']
    resume = batch.plan(ws, batch.Selection(subs, ('info',), missing_only=True), model_available=True)
    assert [i.submission for i in resume.items if i.action == 'read'] == ['s3']
    runner.close()
    ws.close()


def test_a_batch_reads_what_it_plans_and_never_what_a_person_set_meanwhile(tmp_path):
    from examinator.core import batch
    exam = Exam('r', pages=1)
    exam.question('info', pages=[1], fields=[Field('c', read=Printed("the cue"))])
    ws = workspace(tmp_path, exam, subs=('s1', 's2'))
    runner = Runner(ws, model=FakeModel({('info', 'read:c'): '{"value": "one"}'}))
    sel = batch.Selection(('s1', 's2'), ('info',))
    plan = lambda: batch.plan(ws, sel, model_available=True)
    assert [(i.question, i.action) for i in plan().items] == [('info', 'blocked')] * 2
    overrides.set(ws, 'info/fields/c/region', [1, 0, 0, 500, 100], author='ta')   # no value changed
    planned = plan()
    assert planned.count('read') == 2
    runner.set('s2', 'info', 'c', "two", author='ta')            # after the plan, before the run
    assert all(o.ok for o in batch.BatchRun(runner, planned).run())
    assert value(ws, 's1', 'info/c').text == 'one' and value(ws, 's2', 'info/c').text == 'two'
    runner.close()
    ws.close()


# --- a read needs its pages ---

def short_pdf(ws, sub, pages):
    """Rewrite one submission's PDF with only its first `pages` pages: the rest are missing."""
    from PIL import Image
    imgs = [Image.new('RGB', (200, 260), 'white') for _ in range(pages)]
    imgs[0].save(ws.pdfs / f'{sub}.pdf', 'PDF', save_all=True, append_images=imgs[1:])


def test_a_whole_page_reader_on_a_missing_page_is_blocked_and_not_planned(tmp_path):
    """Question 'b' (a field without a region, read from the question's page) on a
    missing page: the runner blocks it, the batch plan does not count it, and the
    model is never asked."""
    from examinator.core import batch
    exam = make_exam()
    ws = workspace(tmp_path, exam, subs=('s1', 's2'))
    short_pdf(ws, 's1', 1)                                  # s1 has no page 2
    model = FakeModel(dict(SCRIPT))
    runner = Runner(ws, model=model)
    try:
        assert ws.missing('s1') == {2}
        assert runner.to_read('s1', 'b').blocked == {'x': 'page missing'}
        assert runner.to_read('s2', 'b').blocked == {}
        assert [(o.status, o.detail) for o in runner.read('s1', 'b')] == [('blocked', "page missing: x")]
        assert value(ws, 's1', 'b/x') is None and not model.calls
        plan = batch.plan(ws, batch.Selection(('s1', 's2'), ('b',)), model_available=True)
        assert [(i.submission, i.action, i.reason) for i in plan.items] == [
            ('s1', 'blocked', "page missing"), ('s2', 'read', "1 field")]
        assert all(o.ok for o in runner.read('s2', 'b')) and value(ws, 's2', 'b/x').text == 'yes'
    finally:
        runner.close()
        ws.close()


def test_a_whole_page_reader_needs_every_page_of_its_question(tmp_path):
    """Some of a whole-page reader's pages missing: none of its fields is read (a
    field with a region on a page that is there included). A region reader reads
    a field whose page is there."""
    from examinator.core.runner import to_read
    exam = Exam('two', pages=2)
    exam.question('w', pages=[1, 2], fields=[Field('x', Region(1, 0, 0, 500, 100)), Field('y')], read=Parse())
    cue = Printed("cue")
    exam.question('r', pages=[1, 2], fields=[Field('c', Region(1, 0, 0, 500, 100), read=cue),
                                            Field('d', Region(2, 0, 0, 500, 100), read=cue), Field('e', read=cue)])
    ws = workspace(tmp_path, exam, subs=('s1',))
    short_pdf(ws, 's1', 1)
    try:
        why = lambda qid: to_read(ws.layers('s1').question(qid), {}, ws.missing('s1')).blocked
        assert why('w') == {'x': 'page missing', 'y': 'page missing'}
        assert why('r') == {'d': 'page missing', 'e': 'no region'}
    finally:
        ws.close()


def test_a_crops_reader_reads_regions():
    from examinator.std import readers
    assert readers.answers(crops=True).needs_region and not readers.answers().needs_region


# --- what a read reads: one computation ----------------------------------------------

def test_the_plan_counts_exactly_what_the_reads_then_do(tmp_path, monkeypatch):
    """For random selections and options on a synthetic workspace (some values verified,
    a box taken away, a submission missing a page, the model on or off), each question's
    plan is what reading it then does: the fields its readers are given, field for field,
    and nothing at all where it reads none."""
    import random

    from examinator import load_exam, synth
    from examinator.core import batch, runner as runner_mod
    from examinator.core.workspace import Workspace
    toy = load_exam(TOY)
    root = synth.generate(toy, tmp_path / 'ws', synth.load_cases(toy))
    ws = Workspace(root, toy)
    ws.sync()
    model = synth.fake_model(root)
    runner = Runner(ws, model=model)
    given: list[tuple[str, str, tuple[str, ...]]] = []
    real = runner_mod.read_group
    monkeypatch.setattr(runner_mod, 'read_group', lambda ws_, sub, q, reader, fields, services: (
        given.append((sub, q.id, tuple(f.label for f in fields))), real(ws_, sub, q, reader, fields, services))[1])
    rng = random.Random(4580)
    subs = [s.id for s in ws.store.submissions()]
    seen = set()
    try:
        short_pdf(ws, subs[0], 2)                                       # pages 3 and 4 missing
        overrides.set(ws, 'areas/fields/A.1/region', None, author='ta')   # this box taken away
        for sid in subs[1:3]:
            runner.read(sid, 'areas')
            runner.read(sid, 'parity')
            for k, v in list(runner.values(sid).items()):
                if k.startswith('parity/') or k.endswith('/side'):
                    runner.verify(sid, *k.split('/'), author='ta', expect_rev=v.rev)
        for _ in range(25):
            sel = batch.Selection(tuple(rng.sample(subs, rng.randint(1, len(subs)))),
                                  tuple(rng.sample(list(ws.exam.questions), rng.randint(1, len(ws.exam.questions)))),
                                  missing_only=rng.random() < 0.4, include_protected=rng.random() < 0.3)
            on = rng.random() < 0.8
            plan = batch.plan(ws, sel, model_available=on)
            seen |= {(i.action, i.reason) for i in plan.items if i.action != 'read'} | {i.action for i in plan.items}
            runner.model = model if on else None
            planned = {(i.submission, i.question): i for i in plan.items}
            for sid in sel.submissions:
                for qid in sel.questions:
                    want = runner.to_read(sid, qid, missing_only=sel.missing_only,
                                          include_protected=sel.include_protected)
                    given.clear()
                    outs = runner.read(sid, qid, missing_only=sel.missing_only, include_protected=sel.include_protected)
                    item = planned.get((sid, qid))
                    assert sum(len(g[2]) for g in given) == (item.fields if item else 0) == want.count
                    assert [g[2] for g in given] == [tuple(f.label for f in fs) for _, fs in want.groups]
                    assert (item is not None and item.action == 'blocked') == (
                        not given and any(o.status == 'blocked' for o in outs))
        assert {'read', 'skip_present', 'skip_protected', ('blocked', "model unavailable"),
                ('blocked', "page missing")} <= seen
    finally:
        runner.close()
        ws.close()
