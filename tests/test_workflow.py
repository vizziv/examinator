"""Workflow rules: one test per row of the table in DESIGN §7, and per further rule."""
import pytest

from examinator.core import batch
from examinator.core.store import Conflict
from examinator.core.workflow import NOT_WORK, part_status, question_status, total
from conftest import make_exam

S, Q = 's1', 'a'


def status(env, part='x', sub=S, q=Q, exam=None):
    exam0, ws, _ = env
    exam = exam or exam0
    return part_status(exam.questions[q], part, ws.store.for_question(sub, q))


def run_all(env, sub=S, q=Q):
    exam, ws, runner = env
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


def test_outdated_by_older_variant(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    runner.choose_variant(S, Q, 'two', author='t')
    assert status(env).state == 'outdated'
    runner.recheck(S, Q)
    st = status(env)
    assert (st.state, st.value) == ('graded', False)      # the other variant's key now applies


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


def test_unresolved_variant_makes_the_check_undecided(env):
    exam, ws, runner = env
    runner.model.script[('a', 'variant')] = lambda call: '{"value": "1"}' if "first cue" in call.prompt else '{"value": "ii"}'
    run_all(env)
    assert ws.store.get(S, Q, 'variant').body['resolved'] is None
    verify(env)
    st = status(env)
    assert st.state == 'unverified' and st.value is None and 'variant' in st.waiting
    runner.choose_variant(S, Q, 'one', author='t')
    runner.recheck(S, Q)
    assert (status(env).state, status(env).value) == ('graded', True)


def test_single_cue_variant_needs_confirmation(env):
    exam, ws, runner = env
    runner.model.script[('a', 'variant')] = lambda call: '{"value": "1"}' if "first cue" in call.prompt else '{"value": null}'
    import dataclasses
    exam.questions[Q] = dataclasses.replace(exam.questions[Q], require=1)
    run_all(env)
    verify(env)
    assert ws.store.get(S, Q, 'variant').body['resolved'] == 'one'
    assert status(env).waiting == ('variant',)                   # one cue is below `trust`
    verify(env, 'variant')
    assert status(env).state == 'graded'


def test_question_without_variants(env):
    run_all(env, q='b')
    verify(env, q='b')
    st = status(env, '', q='b')
    assert (st.state, st.value) == ('graded', True)


def test_totals_count_only_counting_parts(env):
    exam, ws, runner = env
    run_all(env)
    run_all(env, q='b')
    by_q = {q: question_status(exam.questions[q], ws.store.for_question(S, q)) for q in exam.questions}
    assert total(exam, by_q) == (0.0, False)                     # nothing verified
    verify(env)
    verify(env, q='b')
    by_q = {q: question_status(exam.questions[q], ws.store.for_question(S, q)) for q in exam.questions}
    assert total(exam, by_q) == (2.0, True)                      # a.x and b; a.y does not count


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
    assert ('s1', 'a', 'parse') in skipped
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
