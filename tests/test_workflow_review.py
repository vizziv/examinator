"""Independent review of the workflow rules (DESIGN §7-§9).

Two groups:

* `BUG:` tests currently FAIL. Each demonstrates a way a grade can count when
  it should not, or a grader's work can be replaced. They assert the behaviour
  the spec (or the lead's stated design decisions) calls for.
* `OK:` tests PASS. They record scenarios that were tried and found sound, and
  cover §7 rows that `test_workflow.py` does not.

Everything here is synthetic (fixtures from `conftest.py`).
"""
import dataclasses

import pytest

from examinator import Evidence, Stage
from examinator.core import batch, grid
from examinator.core.defs import has_marker
from examinator.core.isolate import StageTimeout
from examinator.core.runner import Runner
from examinator.core.store import Conflict
from examinator.core.workflow import part_status, question_status, total
from examinator.std import stages
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


def replace_stage(exam, qid, name, **changes):
    q = exam.questions[qid]
    exam.questions[qid] = dataclasses.replace(q, stages=tuple(
        dataclasses.replace(s, **changes) if s.name == name else s for s in q.stages))


def one_cue(env):
    """Question 'a' resolved from a single cue, on an exam that has a person verify every variant."""
    exam, ws, runner = env
    runner.model.script[('a', 'variant')] = (
        lambda call: '{"value": "1"}' if "first cue" in call.prompt else '{"value": null}')
    exam.questions[Q] = dataclasses.replace(exam.questions[Q], require=1, verify_variant=True)


# =============================================================================
# BUG: tests that currently fail
# =============================================================================

# BUG (work lost; protection). `Runner.run_stage` never looks at `target.protected`;
# protection lives only in `batch.plan`, which is evaluated once, before the run.
# A parse a grader writes and verifies after the batch has started (but before the
# batch reaches the parse stage) is overwritten by model output.
def test_fixed_batch_overwrites_parse_verified_after_the_batch_started(env):
    exam, ws, runner = env

    def transcript(call):
        if call.tag == (S, Q, 'transcript'):      # a grader works while the batch is running
            runner.save(S, Q, 'parse', {'x': '7', 'y': '7'}, author='ta', verify=True)
        return "x: 1\ny: 2"

    runner.model.script['transcript'] = transcript
    sel = batch.Selection((S,), (Q,), tuple(batch.stage_order(exam)))
    batch.BatchRun(runner, batch.plan(exam, ws.store, sel, model_available=True)).run()
    parse = ws.store.get(S, Q, 'parse')
    assert (parse.origin, parse.verified, parse.body) == ('manual', True, {'x': '7', 'y': '7'})


# BUG (work lost; protection). `include_protected` makes every evidence run
# "deliberate", so manual grades are replaced even when the grade stage is not
# selected, and the preview has no item saying so.
def test_fixed_include_protected_replaces_manual_grades_not_in_the_preview(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    runner.decide(S, Q, 'x', False, author='ta')
    sel = batch.Selection((S,), (Q,), ('evidence',), include_protected=True)
    plan = batch.plan(exam, ws.store, sel, model_available=True)
    assert [(i.stage, i.action) for i in plan.items] == [('evidence', 'run')]   # nothing about grades
    batch.BatchRun(runner, plan).run()
    assert ws.store.get(S, Q, 'grade:x').origin == 'manual'


# BUG (wrong grade can count). `ctx.variant` is handed to every stage, but the
# variant revision is recorded, guarded and required to be trusted only when the
# stage declares `uses_variant` (default False in `stages.check`). A check that
# reads `ctx.variant` without the flag yields a grade that still counts after
# the variant changes, and that never waited for the variant to be trusted.
def test_fixed_check_reading_variant_without_uses_variant_still_counts(env):
    # Fixed: a stage that does not declare `uses_variant` is not shown the variant,
    # so a check that forgot the flag fails as undecided instead of counting.
    exam, ws, runner = env
    replace_stage(exam, Q, 'evidence', uses_variant=False)
    run_all(env)
    verify(env)
    assert (status(env).state, status(env).value) == ('needs_review', None)


# BUG (wrong grade can count). The lead's rule: an automatically resolved variant
# under `verify_variant` is trusted only if a person chose or verified it.
# `correct_cue` writes origin='manual', which `variant_trusted` reads as "a person
# chose it", even when the correction adds no information.
def test_fixed_correct_cue_makes_a_one_cue_variant_trusted(env):
    exam, ws, runner = env
    one_cue(env)
    run_all(env)
    verify(env)
    assert status(env).waiting == ('variant',)
    runner.correct_cue(S, Q, 'c2', None, author='t')      # "c2 is unreadable": says nothing about which variant
    runner.recheck(S, Q)
    v = ws.store.get(S, Q, 'variant')
    assert (v.body['resolved'], v.body['agree']) == ('one', 1)
    assert status(env).waiting == ('variant',)


# BUG (wrong grade can count). `Store.restore` rewrites any old version as
# origin='manual'. Undoing a manual choice back to the one-cue automatic reading
# therefore yields a "manual" variant, which is trusted.
def test_fixed_restoring_an_automatic_variant_makes_it_trusted(env):
    exam, ws, runner = env
    one_cue(env)
    run_all(env)
    verify(env)
    auto = ws.store.get(S, Q, 'variant')
    runner.choose_variant(S, Q, 'two', author='t')
    ws.store.restore(S, Q, 'variant', auto.rev)   # undo
    runner.recheck(S, Q)
    assert ws.store.get(S, Q, 'variant').body['agree'] == 1
    assert 'variant' in status(env).waiting


# BUG (wrong grade can count). Nothing checks the variant artifact against the
# current cue definitions: `stage_hash` leaves out `cues`/`require`, and
# `evidence_current` compares only the variant's revision. Correcting a cue map
# leaves grades counting under the variant the old map picked.
def test_fixed_changed_cue_map_does_not_outdate(env):
    run_all(env)
    verify(env)
    assert status(env).state == 'graded'
    changed = make_exam()
    q = changed.questions[Q]
    swapped = tuple(dataclasses.replace(c, reads={k: ('two' if v == 'one' else 'one') for k, v in c.reads.items()})
                    for c in q.cues)
    changed.questions[Q] = dataclasses.replace(q, cues=swapped)
    assert not status(env, exam=changed).counts


# BUG (wrong grade can count). `waiting` and `evidence_current` look one level up
# only. With a derived, non-editable stage between the parse and the check, the
# grade counts although no person verified anything, and stays "current" after
# the parse is edited.
def test_fixed_intermediate_stage_lets_a_grade_count_without_a_verified_parse(env):
    exam, ws, runner = env
    q = exam.questions['b']
    t, p, _, g = q.stages
    norm = Stage('norm', lambda ctx: ctx.input('parse'), inputs=('parse',))
    chk = stages.check(lambda ctx: Evidence({'fields': {'x': {
        'outcome': 'correct' if ctx.input('norm').get('x') == 'yes' else 'incorrect'}}}),
        inputs=('norm',), isolate=False)
    exam.questions['b'] = dataclasses.replace(q, stages=(t, p, norm, chk, g))
    run_all(env, q='b')
    assert not ws.store.get(S, 'b', 'parse').verified
    assert not status(env, '', q='b').counts                       # fails: 'graded'
    runner.save(S, 'b', 'parse', {'x': 'no'}, author='t')          # and an edit changes nothing
    assert runner.recheck(S, 'b') is not None


# BUG (wrong grade can count). §7: a manual grade records "which parse content
# they were looking at". `decide` cannot be told; it reads whatever parse is
# current when the request arrives. If another grader edits the parse while this
# grader's page shows the old one, the decision counts against text never seen.
def test_fixed_decide_cannot_record_the_parse_the_grader_saw(env):
    exam, ws, runner = env
    run_all(env)
    seen = ws.store.get(S, Q, 'parse').rev                         # grader A's page shows this
    runner.save(S, Q, 'parse', {'x': '9', 'y': '9'}, author='b')   # grader B edits
    with pytest.raises(Conflict):                                  # fixed: refused, not recorded
        runner.decide(S, Q, 'x', True, author='a', seen={'parse': seen})
    assert status(env).state != 'manual'


# BUG (wrong grade can count, for custom checks). `describe` reduces a function
# to its qualified name, so a key held in a closure, a lambda body or a
# module-level constant is not part of the definition hash. Correcting such a
# key outdates nothing; only the informational `stale_code` flag can notice,
# and that is empty unless the exam was loaded from a package.
def test_fixed_key_in_a_closure_is_not_part_of_the_definition(env):
    exam, ws, runner = env

    def check_for(want):
        return lambda ctx: Evidence({'fields': {'x': {
            'outcome': 'correct' if ctx.input('parse').get('x') == want else 'incorrect'}}})

    replace_stage(exam, 'b', 'evidence', fn=check_for('yes'))
    run_all(env, q='b')
    verify(env, q='b')
    assert status(env, '', q='b').state == 'graded'
    replace_stage(exam, 'b', 'evidence', fn=check_for('no'))       # the key was wrong
    assert status(env, '', q='b').state == 'outdated'


# BUG (review markers). Only a closed, upper-case marker is recognised. A marker
# cut off by a length limit, or written in lower case, can be verified.
@pytest.mark.parametrize('text', ['1 [REVIEW: or 7', '1 [review: or 7?]'])
def test_fixed_unclosed_or_lowercase_marker_can_be_verified(env, text):
    exam, ws, runner = env
    run_all(env)
    with pytest.raises(ValueError):
        runner.save(S, Q, 'parse', {'x': text, 'y': '2'}, author='t', verify=True)


# BUG (robustness; aborts a whole batch). A check that returns something other
# than an evidence dict is published, then `_grade` raises AttributeError out of
# `run_stage` instead of leaving the result undecided.
def test_fixed_check_returning_none_raises_instead_of_undecided(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    replace_stage(exam, Q, 'evidence', fn=lambda ctx: None)
    runner.run_stage(S, Q, 'evidence')
    st = status(env)
    assert st.state == 'needs_review' and st.value is None


# BUG (liveness, not a wrong grade). `recheck` returns early whenever the
# evidence is current, so an automatic grade that is outdated or missing for any
# other reason (grade rule changed, grade publish discarded, `undecide` lost a
# race) is never recomputed by saving or verifying.
def test_fixed_recheck_does_not_repair_an_outdated_grade(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    changed = make_exam()
    replace_stage(changed, Q, 'grade', timeout=5)                  # the grade rule's definition changed
    assert status(env, exam=changed).state == 'outdated'
    Runner(changed, ws, model=runner.model).recheck(S, Q)
    assert status(env, exam=changed).state == 'graded'


# =============================================================================
# OK: scenarios tried and found sound (these pass)
# =============================================================================

def test_OK_parse_edited_back_to_identical_content_is_outdated_until_recheck(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    original = ws.store.get(S, Q, 'parse').body
    runner.save(S, Q, 'parse', {'x': '5', 'y': '2'}, author='t')
    runner.save(S, Q, 'parse', original, author='t', verify=True)
    assert status(env).state == 'outdated'          # revisions never repeat
    runner.recheck(S, Q)
    assert status(env).state == 'graded'


def test_OK_unverifying_stops_the_grade_counting(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    rev = ws.store.get(S, Q, 'parse').rev
    runner.verify(S, Q, 'parse', author='t', expect_rev=rev, verified=False)
    assert (status(env).state, status(env).waiting) == ('unverified', ('parse',))


def test_OK_verify_with_a_stale_revision_is_rejected(env):
    exam, ws, runner = env
    run_all(env)
    old = ws.store.get(S, Q, 'parse').rev
    runner.save(S, Q, 'parse', {'x': '3', 'y': '2'}, author='b')
    with pytest.raises(Conflict):
        runner.verify(S, Q, 'parse', author='a', expect_rev=old)
    assert not ws.store.get(S, Q, 'parse').verified


@pytest.mark.parametrize('kind', ['parse', 'evidence', 'variant'])
def test_OK_deleting_an_artifact_stops_the_grade_counting(env, kind):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    ws.store.delete(S, Q, kind)
    st = status(env)
    assert st.state == 'outdated' and st.value is True


def test_OK_deleting_the_parse_outdates_a_manual_grade(env):
    exam, ws, runner = env
    run_all(env)
    runner.decide(S, Q, 'x', True, author='ta')
    ws.store.delete(S, Q, 'parse')
    assert status(env).state == 'manual_outdated'


def test_OK_removing_a_manual_variant_choice_outdates_and_then_waits(env):
    exam, ws, runner = env
    one_cue(env)
    run_all(env)
    verify(env)
    runner.choose_variant(S, Q, 'one', author='t')
    runner.recheck(S, Q)
    assert status(env).state == 'graded'
    runner.choose_variant(S, Q, None, author='t')    # back to what one cue says
    assert status(env).state == 'outdated'
    runner.recheck(S, Q)
    assert status(env).waiting == ('variant',)


def test_OK_unverifying_the_variant_makes_the_grade_wait(env):
    exam, ws, runner = env
    one_cue(env)
    run_all(env)
    verify(env)
    verify(env, 'variant')
    assert status(env).state == 'graded'
    runner.verify(S, Q, 'variant', author='t', expect_rev=ws.store.get(S, Q, 'variant').rev, verified=False)
    assert status(env).waiting == ('variant',)


def test_OK_markers_cannot_be_verified_by_any_runner_path(env):
    exam, ws, runner = env
    run_all(env)
    marked = {'x': '1 [REVIEW: or 7?]', 'y': '2'}
    with pytest.raises(ValueError):
        runner.save(S, Q, 'parse', marked, author='t', verify=True)
    a = runner.save(S, Q, 'parse', marked, author='t')
    with pytest.raises(ValueError):                                # the no-op path
        runner.save(S, Q, 'parse', marked, author='t', expect_rev=a.rev, verify=True)
    with pytest.raises(ValueError):
        runner.verify(S, Q, 'parse', author='t', expect_rev=a.rev)
    runner.recheck(S, Q)
    assert not status(env).counts and not ws.store.get(S, Q, 'parse').verified
    assert has_marker({'k [REVIEW: x]': 1}) and has_marker(['a', {'b': '[REVIEW]'}])


def test_OK_unchanged_save_with_verify_approves_without_a_new_revision(env):
    exam, ws, runner = env
    run_all(env)
    before = ws.store.get(S, Q, 'parse')
    after = runner.save(S, Q, 'parse', before.body, author='t', expect_rev=before.rev, verify=True)
    assert (after.rev, after.origin, after.verified, after.verified_by) == (before.rev, 'auto', True, 't')
    assert status(env).state == 'graded'            # evidence is still from this revision


def test_OK_unchanged_save_with_a_stale_revision_is_still_rejected(env):
    exam, ws, runner = env
    run_all(env)
    old = ws.store.get(S, Q, 'parse')
    runner.save(S, Q, 'parse', {'x': '3', 'y': '2'}, author='b')
    with pytest.raises(Conflict):
        runner.save(S, Q, 'parse', {'x': '3', 'y': '2'}, author='a', expect_rev=old.rev, verify=True)


def test_OK_restored_parse_is_unverified_and_outdates(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    first = ws.store.get(S, Q, 'parse')
    runner.save(S, Q, 'parse', {'x': '5', 'y': '2'}, author='t', verify=True)
    runner.recheck(S, Q)
    back = ws.store.restore(S, Q, 'parse', first.rev)
    assert back.rev > first.rev and not back.verified
    assert status(env).state == 'outdated'


def test_OK_restored_manual_grade_is_outdated_if_the_parse_moved_on(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    manual = runner.decide(S, Q, 'x', False, author='ta')
    runner.save(S, Q, 'parse', {'x': '5', 'y': '2'}, author='t', verify=True)
    runner.undecide(S, Q, 'x')
    ws.store.restore(S, Q, 'grade:x', manual.rev)
    assert status(env).state == 'manual_outdated'


def test_OK_restored_automatic_grade_does_not_count(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    auto = ws.store.get(S, Q, 'grade:x')
    runner.decide(S, Q, 'x', False, author='ta')
    ws.store.restore(S, Q, 'grade:x', auto.rev)
    assert status(env).state == 'graded'            # fixed: restored as the automatic grade it was


def test_OK_check_timeout_crash_and_failed_rule_are_undecided_not_incorrect(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)

    def timeout(ctx):
        raise StageTimeout()

    def crash(ctx):
        raise RuntimeError("boom")

    for fn in (timeout, crash):
        replace_stage(exam, Q, 'evidence', fn=fn)
        assert runner.run_stage(S, Q, 'evidence').ok
        for part in ('x', 'y'):
            st = status(env, part)
            assert (st.state, st.value) == ('needs_review', None) and st.reason
    fresh = make_exam()
    exam.questions[Q] = fresh.questions[Q]
    replace_stage(exam, Q, 'grade', fn=lambda ctx: 1 / 0)
    assert runner.run_stage(S, Q, 'evidence').ok
    assert (status(env).state, status(env).value) == ('needs_review', None)
    replace_stage(exam, Q, 'grade', fn=lambda ctx: {'x': 1, 'y': 'correct'})   # 1 is not a bool
    assert runner.run_stage(S, Q, 'evidence').ok
    assert (status(env).state, status(env).value) == ('needs_review', None)
    replace_stage(exam, Q, 'grade', fn=lambda ctx: {'y': 'correct'})           # a part left out
    assert runner.run_stage(S, Q, 'evidence').ok
    assert (status(env).state, status(env).value) == ('needs_review', None)
    assert (status(env, 'y').state, status(env, 'y').value) == ('graded', True)


def test_OK_blocked_check_keeps_old_evidence_which_is_outdated(env):
    exam, ws, runner = env
    replace_stage(exam, Q, 'evidence', accepts_markers=False)
    run_all(env)
    verify(env)
    runner.save(S, Q, 'parse', {'x': '1 [REVIEW: ?]', 'y': '2'}, author='t')
    assert runner.recheck(S, Q).status == 'blocked'
    st = status(env)
    assert (st.state, st.value) == ('outdated', True)


def test_OK_decision_made_while_a_check_runs_survives(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    real = exam.questions[Q].stage('evidence').fn

    def decide_midway(ctx):
        runner.decide(S, Q, 'x', False, author='ta')
        return real(ctx)

    replace_stage(exam, Q, 'evidence', fn=decide_midway)
    assert runner.run_stage(S, Q, 'evidence').ok
    assert (status(env).state, status(env).value) == ('manual', False)


def test_OK_decision_made_while_the_grade_rule_runs_survives(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    real = exam.questions[Q].stage('grade').fn

    def decide_midway(ctx):                       # after `_grade` has read the rows
        runner.decide(S, Q, 'x', False, author='ta')
        return real(ctx)

    replace_stage(exam, Q, 'grade', fn=decide_midway)
    assert runner.run_stage(S, Q, 'evidence').status == 'discarded'
    assert (status(env).state, status(env).value) == ('manual', False)


def test_OK_variant_changed_while_a_check_runs_discards_the_result(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    real = exam.questions[Q].stage('evidence').fn

    def choose_midway(ctx):
        runner.choose_variant(S, Q, 'two', author='t')
        return real(ctx)

    replace_stage(exam, Q, 'evidence', fn=choose_midway)
    assert runner.run_stage(S, Q, 'evidence').status == 'discarded'
    assert status(env).state == 'outdated'


def test_OK_batch_without_include_protected_keeps_manual_grades_and_verified_parse(env):
    exam, ws, runner = env
    run_all(env)
    verify(env)
    runner.decide(S, Q, 'x', False, author='ta')
    chosen = runner.choose_variant(S, Q, 'one', author='t')
    sel = batch.Selection((S,), (Q,), tuple(batch.stage_order(exam)))
    plan = batch.plan(exam, ws.store, sel, model_available=True)
    actions = {i.stage: i.action for i in plan.items}
    assert actions['parse'] == actions['variant'] == 'skip_protected'
    batch.BatchRun(runner, plan).run()
    assert ws.store.get(S, Q, 'parse').verified and ws.store.get(S, Q, 'variant').rev == chosen.rev
    assert (status(env).state, status(env).value) == ('manual', False)
    assert status(env, 'y').state == 'graded'


def test_OK_multi_part_only_the_manual_part_counts(env):
    exam, ws, runner = env
    exam.questions[Q] = dataclasses.replace(
        exam.questions[Q], result={'x': exam.questions[Q].result['x'], 'y': exam.questions[Q].result['x']})
    run_all(env)
    runner.decide(S, Q, 'x', True, author='ta')
    by_q = {Q: question_status(exam.questions[Q], ws.store.for_question(S, Q))}
    assert (by_q[Q]['x'].state, by_q[Q]['y'].state) == ('manual', 'unverified')
    assert total(exam, by_q) == (1.0, False)


def test_OK_grid_agrees_with_part_status(env):
    exam, ws, runner = env
    run_all(env)                                   # s1.a: verified, x manual
    verify(env)
    runner.decide(S, Q, 'x', False, author='ta')
    run_all(env, q='b')                            # s1.b: unverified
    run_all(env, sub='s2')                         # s2.a: verified, then parse edited
    verify(env, sub='s2')
    runner.save('s2', Q, 'parse', {'x': '5', 'y': '2'}, author='t')
    run_all(env, sub='s2', q='b')                  # s2.b: graded
    verify(env, sub='s2', q='b')
    rows = {r.submission.id: r for r in grid.load(exam, ws.store)}
    seen = set()
    for sub, row in rows.items():
        by_q = {}
        for qid, q in exam.questions.items():
            by_q[qid] = question_status(q, ws.store.for_question(sub, qid), code_hash=exam.code_hash)
            assert row.cells[qid].status == by_q[qid]
            seen |= {st.state for st in by_q[qid].values()}
        assert (row.points, row.complete) == total(exam, by_q)
    assert {'manual', 'graded', 'unverified', 'outdated'} <= seen
    assert (rows['s1'].points, rows['s2'].points) == (0.0, 1.0)
