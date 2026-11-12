"""Workflow rules: one test per row of the table in DESIGN §7, and per further rule."""
import pytest

from examinator.core import batch
from examinator.core.store import Conflict
from examinator.core.workflow import NOT_WORK, part_status, question_status, total
from examinator import Bool, Evidence, Field
from examinator.std import stages
from conftest import make_exam

S, Q, P = 's1', 'a', 'p'      # question 'a' reads the printing, question 'p' (no grade)


def status(env, part='x', sub=S, q=Q, exam=None):
    exam0, ws, _ = env
    exam = exam or exam0
    return part_status(exam.questions[q], part, ws.store.for_question(sub, q),
                       parses=ws.store.of_kind(sub, 'parse'))


def run_all(env, sub=S, q=Q, printing=True):
    """Run a question's stages; for 'a', first read the printing ('p') and, with
    `printing`, verify it, so that only 'a' itself waits for a person."""
    exam, ws, runner = env
    if q == Q and ws.store.get(sub, P, 'parse') is None:
        assert runner.run_stage(sub, P, 'parse').ok
        if printing:
            verify(env, sub=sub, q=P)
    outs = runner.run_from(sub, q, exam.questions[q].stages[0].name)
    assert all(o.ok for o in outs), outs


def verify(env, kind='parse', sub=S, q=Q):
    _, ws, runner = env
    runner.verify(sub, q, kind, author='t', expect_rev=ws.store.get(sub, q, kind).rev)


# --- the table ---

def test_nothing_exists(env):
    st = status(env)
    assert st.state == 'not_started' and not st.counts


def test_a_page_selection_alone_is_not_work(env):
    """Row 'nothing exists': per-submission settings are not artifacts of the answer,
    and the same list keeps intake free to rewrite the submission."""
    exam, ws, _ = env
    ws.set_pages(S, exam.questions[Q], [1, 2], "spills over", author='t')
    assert set(ws.store.for_question(S, Q)) == {'pages'}
    assert status(env).state == 'not_started'
    assert not ws.store.has_work(S, ignore=NOT_WORK)
    ws.store.write(S, Q, 'parse', {'x': '1'}, origin='manual', author='t')
    assert ws.store.has_work(S, ignore=NOT_WORK)


def test_unverified_parse_shows_proposed_grade_but_does_not_count(env):
    run_all(env)
    st = status(env)
    assert (st.state, st.value, st.counts, st.waiting) == ('unverified', True, False, ('parse',))


def test_verified_current_decided_counts(env):
    run_all(env)
    verify(env)
    st = status(env)
    assert (st.state, st.value, st.counts) == ('graded', True, True)


def test_verified_current_undecided_needs_review(env):
    exam, ws, runner = env
    run_all(env)
    runner.save(S, Q, 'parse', {'x': '?', 'y': '2'}, author='t', verify=True)
    runner.recheck(S, Q)
    st = status(env)
    assert (st.state, st.value, st.counts) == ('needs_review', None, False)
    assert status(env, 'y').state == 'graded'


def test_outdated_by_older_parse(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    runner.save(S, Q, 'parse', {'x': '5', 'y': '2'}, author='t', verify=True)   # no recheck yet
    st = status(env)
    assert (st.state, st.value, st.counts) == ('outdated', True, False)   # old evidence still visible


def test_outdated_when_a_parse_it_read_changes(env):
    """Row 'evidence computed from an older parse': another question's parse that
    the check read counts as one of its inputs."""
    exam, ws, runner = env
    run_all(env)
    verify(env)
    assert ws.store.get(S, Q, 'evidence').reads == {P: ws.store.get(S, P, 'parse').rev}
    runner.save(S, P, 'parse', {'c1': 'two', 'c2': 'two'}, author='t', verify=True)
    assert status(env).state == 'outdated'
    runner.recheck(S, P)                                   # rechecks the questions that read 'p'
    st = status(env)
    assert (st.state, st.value) == ('graded', False)      # the other printing's key now applies


def trusting(env, script=None):
    """The same workspace under the exam whose printed fields vouch for an expected reading."""
    from conftest import SCRIPT, make_exam
    from examinator.core.runner import Runner
    from examinator.model.fake import FakeModel
    exam, ws, runner = env
    runner.close()
    exam = make_exam(trust=True)
    return exam, ws, Runner(exam, ws, model=FakeModel({**SCRIPT, **(script or {})}))


def test_a_printed_reading_that_was_expected_needs_no_person(env):
    """Row 'parse verified, evidence current': a field another question's check read
    counts as verified when its reader vouched for the value it read."""
    env = exam, ws, runner = trusting(env)
    run_all(env, printing=False)
    verify(env)
    assert ws.store.get(S, P, 'parse').inputs['trusted'] == ['c1', 'c2']
    assert not ws.store.get(S, P, 'parse').protected              # a later read replaces it freely
    st = status(env)
    assert (st.state, st.value, st.waiting_on) == ('graded', True, ())
    # A person's edit is a person's to verify.
    runner.save(S, P, 'parse', {'c1': 'two', 'c2': 'two'}, author='t')
    runner.recheck(S, P)
    assert (status(env).value, status(env).waiting_on) == (False, (P,))
    runner.close()


def test_an_unreadable_field_holds_back_only_the_checks_that_read_it(env):
    env = exam, ws, runner = trusting(env, {
        ('p', 'read'): lambda call: '{"value": "1"}' if "first cue" in call.prompt else '{"value": ""}',
        ('c', 'parse'): '{"x": "yes"}'})
    exam.question('c', pages=[2], result=Bool(), fields=[Field('x')],
                  stages=[*exam.questions['b'].stages[:2],
                          stages.check(lambda ctx: Evidence({'fields': {'x': {'outcome': 'correct' if ctx.read(P, 'c1') == 'one' else 'incorrect'}}}), isolate=False),
                          exam.questions['b'].stages[3]])
    run_all(env, printing=False)
    verify(env)
    assert ws.store.get(S, P, 'parse').inputs['trusted'] == ['c1']
    assert (status(env).state, status(env).waiting_on) == ('unverified', (P,))     # 'a' read c2 as well
    assert all(o.ok for o in runner.run_from(S, 'c', 'transcript'))
    verify(env, q='c')
    st = status(env, part='', q='c')
    assert (st.state, st.value, st.waiting_on) == ('graded', True, ())             # 'c' read only c1
    assert ws.store.get(S, 'c', 'evidence').inputs['labels'] == {P: ['c1']}
    runner.close()


def test_waits_until_a_parse_it_read_is_verified(env):
    """Row 'parse verified, evidence current': only when every parse the check read
    is verified too; until then the grade waits, naming the question."""
    exam, ws, runner = env
    run_all(env, printing=False)
    verify(env)
    st = status(env)
    assert (st.state, st.value, st.counts, st.waiting, st.waiting_on) == ('unverified', True, False, (), (P,))
    verify(env, q=P)
    assert (status(env).state, status(env).value) == ('graded', True)
    runner.verify(S, P, 'parse', author='t', expect_rev=ws.store.get(S, P, 'parse').rev, verified=False)
    assert status(env).waiting_on == (P,)


def test_a_missing_parse_it_reads_leaves_the_check_undecided(env):
    """A check that asks for a parse that does not exist is undecided and waits;
    it is outdated once the parse appears, and a recheck then decides it."""
    exam, ws, runner = env
    for kind in ('transcript', 'parse', 'evidence'):
        assert runner.run_stage(S, Q, kind).ok
    verify(env)
    ev = ws.store.get(S, Q, 'evidence')
    assert ev.reads == {P: None} and ev.body['data'] == {'undecided': "no parse of p yet"}
    st = status(env)
    assert (st.state, st.value, st.counts, st.waiting_on) == ('unverified', None, False, (P,))
    assert runner.run_stage(S, P, 'parse').ok
    assert status(env).state == 'outdated'
    verify(env, q=P)
    runner.recheck(S, P)
    assert (status(env).state, status(env).value) == ('graded', True)


def test_a_check_that_reads_nothing_does_not_depend(env):
    """What a check did not read does not hold it back or outdate it: question 'b'
    reads no other question, whatever happens to 'p'."""
    exam, ws, runner = env
    run_all(env, q='b')
    verify(env, q='b')
    assert ws.store.get(S, 'b', 'evidence').reads == {}
    assert runner.run_stage(S, P, 'parse').ok                    # exists, unverified
    assert status(env, '', q='b').state == 'graded'
    runner.save(S, P, 'parse', {'c1': 'two', 'c2': 'two'}, author='t')
    assert status(env, '', q='b').state == 'graded'
    assert runner.readers(S, P) == []


def test_status_needs_the_parses_when_the_check_read_some(env):
    exam, ws, runner = env
    run_all(env)
    with pytest.raises(TypeError):
        part_status(exam.questions[Q], 'x', ws.store.for_question(S, Q))


def test_a_question_with_no_result(env):
    """The printing has a parse to verify and no grade: no status parts, nothing to total."""
    exam, ws, runner = env
    q = exam.questions[P]
    assert q.result == {} and q.labels == ('c1', 'c2') and q.written == ()
    assert runner.run_stage(S, P, 'parse').ok
    assert ws.store.get(S, P, 'parse').body == {'c1': 'one', 'c2': 'one'}
    assert question_status(q, ws.store.for_question(S, P)) == {}
    assert runner.recheck(S, P) is None


def test_outdated_by_older_definition(env):
    run_all(env)
    verify(env)
    assert status(env).state == 'graded'
    changed = make_exam()
    q = changed.questions[Q]
    import dataclasses
    stage = dataclasses.replace(q.stage('evidence'), timeout=7)      # the check's definition changed
    changed.questions[Q] = dataclasses.replace(q, stages=tuple(stage if s.name == 'evidence' else s for s in q.stages))
    assert status(env, exam=changed).state == 'outdated'
    # ...but a change to an unrelated stage (a transcribe prompt) outdates nothing
    assert status(env, exam=make_exam(key_salt="extra prompt note")).state == 'graded'


def test_manual_grade_counts_while_parse_unchanged(env):
    exam, ws, runner = env
    run_all(env)
    runner.decide(S, Q, 'x', False, author='ta')
    st = status(env)
    assert (st.state, st.value, st.counts, st.author) == ('manual', False, True, 'ta')


def test_manual_grade_outdated_when_parse_changes(env):
    exam, ws, runner = env
    run_all(env)
    runner.decide(S, Q, 'x', False, author='ta')
    runner.save(S, Q, 'parse', {'x': '9', 'y': '2'}, author='t')
    st = status(env)
    assert (st.state, st.value, st.counts) == ('manual_outdated', False, False)


# --- further rules ---

def test_editing_clears_verification_rerunning_check_does_not(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    assert runner.run_stage(S, Q, 'evidence').ok
    assert ws.store.get(S, Q, 'parse').verified
    assert status(env).state == 'graded'
    runner.save(S, Q, 'parse', {'x': '3', 'y': '2'}, author='t')
    assert not ws.store.get(S, Q, 'parse').verified


def test_verification_does_not_change_author(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    a = ws.store.get(S, Q, 'parse')
    assert a.origin == 'auto' and a.verified and a.verified_by == 't'


def test_saving_unchanged_content_is_a_no_op(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    before = ws.store.get(S, Q, 'parse')
    runner.save(S, Q, 'parse', before.body, author='t', expect_rev=before.rev)
    assert ws.store.get(S, Q, 'parse') == before
    assert status(env).state == 'graded'


def test_manual_grade_with_markers_and_without_evidence(env):
    exam, ws, runner = env
    runner.decide(S, Q, 'x', True, author='ta')                 # nothing else exists
    assert status(env).counts
    assert status(env, 'y').state == 'in_progress'
    ws.store.write(S, Q, 'transcript', "x: [REVIEW: smudge]", origin='auto')
    assert status(env).state == 'manual'                        # transcript is not the parse


def test_verify_refuses_review_markers(env):
    exam, ws, runner = env
    run_all(env)
    a = runner.save(S, Q, 'parse', {'x': '1 [REVIEW: or 7?]', 'y': '2'}, author='t')
    with pytest.raises(ValueError):
        runner.verify(S, Q, 'parse', author='t', expect_rev=a.rev)


def test_stage_refuses_marked_input(env):
    exam, ws, runner = env
    ws.store.write(S, Q, 'transcript', "x: [REVIEW: smudge]", origin='manual', author='t')
    out = runner.run_stage(S, Q, 'parse')
    assert out.status == 'blocked' and 'review' in out.detail


def test_parts_are_decided_independently(env):
    exam, ws, runner = env
    run_all(env)
    runner.decide(S, Q, 'x', False, author='ta')
    assert status(env, 'x').state == 'manual'
    assert status(env, 'y').state == 'unverified'               # deciding x did not approve y


def test_automatic_recheck_never_replaces_manual_grade(env):
    exam, ws, runner = env
    run_all(env)
    runner.decide(S, Q, 'x', False, author='ta')
    runner.save(S, Q, 'parse', {'x': '1', 'y': '9'}, author='t', verify=True)
    assert runner.recheck(S, Q).ok
    assert ws.store.get(S, Q, 'grade:x').origin == 'manual'
    assert status(env, 'x').state == 'manual_outdated'          # the decision predates the edit
    assert (status(env, 'y').state, status(env, 'y').value) == ('graded', False)


def test_recheck_skipped_when_every_part_is_manual(env):
    exam, ws, runner = env
    run_all(env)
    runner.decide(S, Q, 'x', False, author='ta')
    runner.decide(S, Q, 'y', False, author='ta')
    runner.save(S, Q, 'parse', {'x': '4', 'y': '4'}, author='t')
    assert runner.recheck(S, Q) is None


def test_deliberate_check_replaces_manual_grade_and_keeps_history(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    runner.decide(S, Q, 'x', False, author='ta')
    assert runner.run_stage(S, Q, 'evidence', deliberate=True).ok
    assert (status(env).state, status(env).value) == ('graded', True)
    old = ws.store.history(S, Q, 'grade:x')
    assert old[0].origin == 'manual' and old[0].author == 'ta' and old[0].body['value'] is False


def test_undecide_restores_automatic_grade(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    runner.decide(S, Q, 'x', False, author='ta')
    runner.undecide(S, Q, 'x')
    assert (status(env).state, status(env).value) == ('graded', True)


def test_disagreeing_printed_fields_leave_the_check_undecided(env):
    exam, ws, runner = env
    runner.model.script[(P, 'read')] = lambda call: '{"value": "1"}' if "first cue" in call.prompt else '{"value": "ii"}'
    run_all(env)
    verify(env)
    st = status(env)
    assert (st.state, st.value, st.reason) == ('needs_review', None, "p c1, p c2 disagree")
    runner.save(S, P, 'parse', {'c1': 'one', 'c2': 'one'}, author='t', verify=True)
    runner.recheck(S, P)
    assert (status(env).state, status(env).value) == ('graded', True)


def test_an_unread_printed_field_holds_the_parse_back(env):
    """A printed field the model could not read carries a review marker: the parse
    of the printing cannot be verified until a person fills it in."""
    exam, ws, runner = env
    runner.model.script[(P, 'read')] = lambda call: '{"value": "1"}' if "first cue" in call.prompt else '{"value": null}'
    assert runner.run_stage(S, P, 'parse').ok
    a = ws.store.get(S, P, 'parse')
    assert a.body == {'c1': 'one', 'c2': '[REVIEW: not read]'}
    with pytest.raises(ValueError):
        runner.verify(S, P, 'parse', author='t', expect_rev=a.rev)


def test_question_reading_nothing(env):
    run_all(env, q='b')
    verify(env, q='b')
    st = status(env, '', q='b')
    assert (st.state, st.value) == ('graded', True)


def test_totals_count_only_counting_parts(env):
    exam, ws, runner = env
    run_all(env)
    run_all(env, q='b')
    parses = ws.store.of_kind(S, 'parse')
    by_q = {q: question_status(exam.questions[q], ws.store.for_question(S, q), parses=parses)
            for q in exam.questions}
    assert total(exam, by_q) == (0.0, False)                     # nothing verified
    verify(env)
    verify(env, q='b')
    by_q = {q: question_status(exam.questions[q], ws.store.for_question(S, q), parses=parses)
            for q in exam.questions}
    assert total(exam, by_q) == (2.0, True)                      # a.x and b; a.y does not count; p has no result


def test_stale_computation_is_discarded(env):
    exam, ws, runner = env
    run_all(env)
    real = exam.questions[Q].stage('evidence').fn

    def edit_midway(ctx):
        runner.save(S, Q, 'parse', {'x': '8', 'y': '8'}, author='t')
        return real(ctx)

    import dataclasses
    q = exam.questions[Q]
    exam.questions[Q] = dataclasses.replace(q, stages=tuple(
        dataclasses.replace(s, fn=edit_midway) if s.name == 'evidence' else s for s in q.stages))
    before = ws.store.get(S, Q, 'evidence')
    assert runner.run_stage(S, Q, 'evidence').status == 'discarded'
    assert ws.store.get(S, Q, 'evidence') == before


def test_failed_stage_leaves_previous_artifact(env):
    exam, ws, runner = env
    run_all(env)
    before = ws.store.get(S, Q, 'transcript')
    del runner.model.script['transcript']
    assert runner.run_stage(S, Q, 'transcript').status == 'failed'
    assert ws.store.get(S, Q, 'transcript') == before


def test_stale_write_rejected(env):
    exam, ws, runner = env
    run_all(env)
    rev = ws.store.get(S, Q, 'parse').rev
    runner.save(S, Q, 'parse', {'x': '2', 'y': '2'}, author='a', expect_rev=rev)
    with pytest.raises(Conflict):
        runner.save(S, Q, 'parse', {'x': '3', 'y': '2'}, author='b', expect_rev=rev)


# --- batches ---

def select(exam, **kw):
    return batch.Selection(('s1', 's2'), tuple(exam.questions), tuple(batch.stage_order(exam)), **kw)


def test_batch_runs_everything_then_skips_protected(env):
    exam, ws, runner = env
    plan = batch.plan(exam, ws.store, select(exam), model_available=True)
    assert plan.count('run') == len(plan.items)
    outs = batch.BatchRun(runner, plan).run()
    assert all(o.ok for o in outs)
    verify(env)
    runner.decide(S, Q, 'x', False, author='ta')
    plan = batch.plan(exam, ws.store, select(exam), model_available=True)
    skipped = {(i.submission, i.question, i.stage) for i in plan.items if i.action == 'skip_protected'}
    assert ('s1', 'a', 'parse') in skipped and ('s1', 'p', 'parse') not in skipped
    batch.BatchRun(runner, plan).run()
    assert ws.store.get(S, Q, 'parse').verified
    assert status(env).state == 'manual'                          # a manual grade survives a batch


def test_batch_missing_only_and_stale_preview(env):
    exam, ws, runner = env
    run_all(env)
    plan = batch.plan(exam, ws.store, select(exam, missing_only=True), model_available=True)
    assert {i.action for i in plan.items if (i.submission, i.question) == (S, Q)} == {'skip_present'}
    runner.save(S, Q, 'parse', {'x': '2', 'y': '2'}, author='t')
    with pytest.raises(batch.StalePlan):
        batch.BatchRun(runner, plan).run()


def test_batch_blocked_without_model(env):
    exam, ws, runner = env
    plan = batch.plan(exam, ws.store, select(exam), model_available=False)
    assert plan.count('run') == 0 and plan.count('blocked') == len(plan.items)
