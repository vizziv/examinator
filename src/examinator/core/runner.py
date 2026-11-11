"""Running stages, and the actions graders take.

`Runner.run_stage` runs one stage for one submission and question: it reads
the upstream artifacts, computes outside any transaction, then publishes only
if everything it read is still current. A stage that fails leaves the previous
artifact intact. A check that times out, crashes or lacks a resolved variant
is stored as undecided evidence: never "incorrect".

The grade stage is pure and fast; it runs in-process right after every check
and writes one row per result part, leaving manual decisions alone unless the
run was deliberate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from . import variants
from .context import BadOutput, Blocked, Context, Unavailable
from .defs import Exam, Grade, Question, Stage, grade_kind, has_marker, stage_hash
from .evidence import body_of, undecided
from .isolate import Pool, PoolClosed, StageCrash, StageTimeout
from .store import ANY, Artifact, Conflict
from .workflow import (_derived, evidence_current, grade_current, recheck_wanted, seen_revs,
                       variant_current)
from .workspace import Workspace

DEFAULT_TIMEOUT = 60.0


class Model(Protocol):
    """A model client (see `examinator.model`). Errors raised are `ModelError`s
    whose text is structural only: never prompt or response content."""

    def complete(self, prompt: str, *, images: Sequence[Any] = (), text: str | None = None,
                 expect: str = 'text', reasoning: bool = False,
                 tag: tuple[str, str, str] | None = None) -> str: ...


@dataclass(frozen=True)
class Outcome:
    submission: str
    question: str
    stage: str
    status: str          # 'done' | 'blocked' | 'failed' | 'discarded' | 'skipped'
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 'done'


class _Services:
    def __init__(self, workspace: Workspace, model: Model | None):
        self.ws, self._model = workspace, model

    def pages(self, submission, question):
        pages, _ = self.ws.selected_pages(submission, question)
        return [self.ws.page_image(submission, p) for p in pages]

    def crop(self, submission, region):
        return self.ws.crop(submission, region)

    def model(self, prompt, *, images, text, expect, reasoning, tag):
        if self._model is None:
            raise Unavailable("model unavailable")
        return self._model.complete(prompt, images=images, text=text, expect=expect,
                                    reasoning=reasoning, tag=tag)

    def memo(self, submission, key):
        return self.ws.store.memo(submission, key)

    def set_memo(self, submission, key, value):
        self.ws.store.set_memo(submission, key, value)


class Runner:
    def __init__(self, exam: Exam, workspace: Workspace, *, model: Model | None = None,
                 pool: Pool | None = None):
        self.exam = exam
        self.ws = workspace
        self.store = workspace.store
        self.model = model
        self.pool = pool if pool is not None else (Pool(exam.path) if exam.path else None)
        self._services = _Services(workspace, model)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()

    # --- running stages -------------------------------------------------------

    def run_stage(self, submission: str, qid: str, name: str, *, params: Mapping | None = None,
                  deliberate: bool = False, overwrite: bool = False, reasoning: bool = False) -> Outcome:
        """Run one stage.

        Content a grader wrote or verified is never replaced unless `overwrite`
        is set (the replaced row goes to history). `deliberate` marks a grader's
        explicit single run: it implies `overwrite`, and a deliberate check also
        replaces manual grades."""
        overwrite = overwrite or deliberate
        q = self.exam.questions[qid]
        stage = q.stage(name)

        def out(status, detail=""):
            return Outcome(submission, qid, name, status, detail)

        if stage is None:
            return out('blocked', f"no {name} stage")
        if name == 'grade':
            return self._grade(submission, q, deliberate=deliberate)
        if stage.uses_model and self.model is None:
            return out('blocked', "model unavailable")

        arts = self.store.for_question(submission, qid)
        target = arts.get(name)
        if target is not None and target.protected and not overwrite:
            return out('skipped', "protected")
        inputs, revs = {}, {}
        for kind in stage.inputs:
            a = arts.get(kind)
            if a is None:
                return out('blocked', f"no {kind}")
            if not stage.accepts_markers and has_marker(a.body):
                return out('blocked', f"{kind} has review markers")
            inputs[kind], revs[kind] = a.body, a.rev
        variant = None
        if q.variants and stage.uses_variant:      # only a stage that declares it sees the variant
            va = arts.get('variant')
            variant = variants.resolved(va.body) if va else None
            revs['variant'] = va.rev if va else None

        _, note = self.ws.selected_pages(submission, q)
        ctx = Context(submission=submission, question=q, stage=name, variant=variant,
                      inputs=inputs, params=dict(params or {}), note=note,
                      rerun=target is not None, reasoning=reasoning, services=self._services)
        is_check = name == 'evidence'
        try:
            if is_check and stage.uses_variant and q.variants and variant is None:
                body = undecided("variant unresolved")
            else:
                body = self._execute(q, stage, ctx)
            if is_check and not (isinstance(body, Mapping) and isinstance(body.get('data'), Mapping)):
                body = undecided("the check returned no evidence")
        except Blocked as e:
            return out('blocked', str(e))
        except PoolClosed:
            return out('failed', "shutting down")
        except StageTimeout:
            if not is_check:
                return out('failed', "timed out")
            body = undecided(f"timed out after {self._timeout(stage):g} s")
        except Exception as e:
            if not is_check:
                # Model errors are structural by contract; anything else might quote
                # student text, so only its type is reported.
                structural = isinstance(e, (Unavailable, BadOutput)) or type(e).__name__ == 'ModelError'
                return out('failed', str(e) if structural else type(e).__name__)
            detail = getattr(e, 'detail', "") or f"{type(e).__name__}: {e}"
            body = undecided("the check failed to run", [
                {'type': 'details', 'summary': "Error", 'audience': 'grader',
                 'blocks': [{'type': 'code', 'text': detail}]}])
        try:
            self.store.write(
                submission, qid, name, body, origin='auto',
                inputs={'revs': revs, 'defn': stage_hash(q, stage), 'code': self.exam.code_hash},
                expect_rev=target.rev if target else None, guard=revs, unless_protected=not overwrite)
        except Conflict as e:
            return out('discarded', str(e))
        if is_check and q.stage('grade') is not None:
            g = self._grade(submission, q, deliberate=deliberate)
            if not g.ok:
                return out(g.status, g.detail)
        return out('done')

    def run_from(self, submission: str, qid: str, name: str, **kw) -> list[Outcome]:
        """Run a stage and everything after it, stopping at the first that does not
        finish. `deliberate` and `overwrite` apply to the named stage only: later
        stages never replace protected work, so the chain stops at a verified parse."""
        q = self.exam.questions[qid]
        names = [s.name for s in q.stages]
        outs = []
        for n in names[names.index(name):]:
            if n == 'grade' and outs:       # already ran with the check
                continue
            outs.append(self.run_stage(submission, qid, n, **kw))
            if not outs[-1].ok:
                break
            kw = {**kw, 'deliberate': False, 'overwrite': False}
        return outs

    def _timeout(self, stage: Stage) -> float:
        return stage.timeout or DEFAULT_TIMEOUT

    def _execute(self, q: Question, stage: Stage, ctx: Context) -> Any:
        if stage.isolate and self.pool is not None:
            return self.pool.call(q.id, stage.name, {
                'submission': ctx.submission, 'variant': ctx.variant, 'inputs': ctx.inputs,
                'params': ctx.params, 'note': ctx.note}, self._timeout(stage))
        return body_of(stage.fn(ctx))

    def _grade(self, submission: str, q: Question, *, deliberate: bool) -> Outcome:
        stage = q.stage('grade')
        arts = self.store.for_question(submission, q.id)
        ev = arts.get('evidence')

        def out(status, detail=""):
            return Outcome(submission, q.id, 'grade', status, detail)

        if ev is None:
            return out('blocked', "no evidence")
        reason = (ev.body.get('data') or {}).get('undecided')
        if reason is not None:
            grades = {part: Grade(None, reason=str(reason)) for part in q.result}
        else:
            try:
                raw = stage.fn(Context(submission=submission, question=q, stage='grade',
                                       inputs={'evidence': ev.body}))
                if list(q.result) == [''] and not (isinstance(raw, Mapping) and '' in raw):
                    raw = {'': raw}
                if not isinstance(raw, Mapping):
                    raise ValueError("a multi-part result needs a dict of parts")
                grades = {part: spec.coerce(raw.get(part)) for part, spec in q.result.items()}
            except Exception as e:
                grades = {part: Grade(None, reason=f"grade rule failed: {type(e).__name__}")
                          for part in q.result}
        inputs = {'revs': {'evidence': ev.rev}, 'defn': stage_hash(q, stage)}
        try:
            for part, grade in grades.items():
                kind = grade_kind(part)
                cur = arts.get(kind)
                if cur is not None and cur.origin == 'manual' and not deliberate:
                    continue
                self.store.write(submission, q.id, kind, grade.to_json(), origin='auto', inputs=inputs,
                                 expect_rev=cur.rev if cur else None, guard={'evidence': ev.rev})
        except Conflict as e:
            return out('discarded', str(e))
        return out('done')

    def recheck(self, submission: str, qid: str) -> Outcome | None:
        """Automatic recheck after a parse or variant changes or is verified.

        Brings the variant in line with changed cue definitions (from the stored
        readings; no model), reruns derived stages and the check if the evidence
        is not current, and otherwise repairs automatic grades that fell behind.
        Never replaces a manual grade or anything protected."""
        q = self.exam.questions[qid]
        if q.stage('evidence') is None:
            return None
        arts = self.store.for_question(submission, qid)
        va = arts.get('variant')
        if va is not None and not variant_current(q, arts) and not va.protected:
            readings = {n: c.get('reading') for n, c in (va.body.get('cues') or {}).items()}
            try:
                self.store.write(submission, qid, 'variant', variants.narrow(q, readings), origin='auto',
                                 inputs={'revs': {}, 'defn': stage_hash(q, q.stage('variant'))},
                                 expect_rev=va.rev, unless_protected=True)
            except Conflict:
                pass
            arts = self.store.for_question(submission, qid)
        if not recheck_wanted(q, arts):
            return None
        if evidence_current(q, arts):
            if q.stage('grade') is not None and any(
                    (g := arts.get(grade_kind(p))) is None
                    or (g.origin != 'manual' and not grade_current(q, p, arts)) for p in q.result):
                return self._grade(submission, q, deliberate=False)
            return None
        out = None
        for name in self._derived_chain(q):
            out = self.run_stage(submission, qid, name)
            if not out.ok:
                break
        return out

    def _derived_chain(self, q: Question) -> list[str]:
        """The check and the derived (non-editable, model-free) stages it rests on, in order."""
        need: set[str] = set()

        def walk(kind):
            need.add(kind)
            for k in q.stage(kind).inputs:
                s = q.stage(k)
                if _derived(q, k) and not s.uses_model:
                    walk(k)

        walk('evidence')
        return [s.name for s in q.stages if s.name in need]

    # --- what graders do ------------------------------------------------------
    # Each raises `Conflict` if `expect_rev` is no longer current, and `ValueError`
    # for input a grader should correct. None of these runs a check: call
    # `recheck` afterwards (the app does so in the background).

    def save(self, submission: str, qid: str, kind: str, body: Any, *, author: str | None,
             expect_rev: Any = ANY, verify: bool = False) -> Artifact:
        """A grader's edit of an editable artifact. Editing clears verification
        unless `verify` approves the new content in the same step."""
        stage = self.exam.questions[qid].stage(kind)
        if stage is None or not stage.editable or kind == 'variant':
            raise ValueError(f"{kind} is not editable")
        if verify and has_marker(body):
            raise ValueError("resolve review markers before verifying")
        return self.store.write(submission, qid, kind, body, origin='manual', author=author,
                                expect_rev=expect_rev, verified=verify)

    def verify(self, submission: str, qid: str, kind: str, *, author: str | None,
               expect_rev: int, verified: bool = True) -> Artifact:
        cur = self.store.get(submission, qid, kind)
        if verified and cur is not None and has_marker(cur.body):
            raise ValueError("resolve review markers before verifying")
        if verified and kind == 'variant' and not variants.resolved(cur.body if cur else None):
            raise ValueError("choose a variant first")
        return self.store.verify(submission, qid, kind, expect_rev=expect_rev, author=author,
                                 verified=verified)

    def decide(self, submission: str, qid: str, part: str, value: Any, *, author: str | None,
               flags: Sequence[str] = (), reason: str | None = None, expect_rev: Any = ANY,
               seen: Mapping[str, int | None] | None = None) -> Artifact:
        """A manual grade for one part, recorded against the content the grader was
        looking at. `seen` maps each verifiable artifact (see `workflow.verifiable`;
        usually just 'parse') to the revision on the grader's screen; if any has
        changed the decision is refused. Without `seen`, the current revisions are used."""
        q = self.exam.questions[qid]
        grade = q.result[part].coerce(Grade(value, tuple(flags), reason))
        if grade.value is None:
            raise ValueError("a decision needs a value")
        current = seen_revs(q, self.store.for_question(submission, qid))
        if seen is not None:
            seen = {k: seen.get(k) for k in current}
        else:
            seen = current
        return self.store.write(submission, qid, grade_kind(part), grade.to_json(), origin='manual',
                                author=author, inputs={'revs': seen}, expect_rev=expect_rev, guard=seen)

    def undecide(self, submission: str, qid: str, part: str, *, expect_rev: Any = ANY) -> None:
        """Remove a manual grade; the automatic grade (if evidence exists) returns."""
        q = self.exam.questions[qid]
        cur = self.store.get(submission, qid, grade_kind(part))
        if cur is None or cur.origin != 'manual':
            return
        self.store.delete(submission, qid, grade_kind(part), expect_rev=expect_rev)
        if q.stage('grade') is not None:
            self._grade(submission, q, deliberate=False)

    def choose_variant(self, submission: str, qid: str, variant: str | None, *, author: str | None,
                       expect_rev: Any = ANY) -> Artifact | None:
        """A manual variant choice, authoritative until removed (`variant=None`
        returns to what the recorded cue readings say)."""
        q = self.exam.questions[qid]
        cur = self.store.get(submission, qid, 'variant')
        if variant is None:
            if cur is None:
                return None
            readings = {n: c.get('reading') for n, c in (cur.body.get('cues') or {}).items()}
            return self.store.write(submission, qid, 'variant', variants.narrow(q, readings),
                                    origin='auto', inputs=self._variant_inputs(q), expect_rev=expect_rev)
        return self.store.write(submission, qid, 'variant',
                                variants.choose(q, cur.body if cur else None, variant),
                                origin='manual', author=author, inputs=self._variant_inputs(q),
                                expect_rev=expect_rev)

    def _variant_inputs(self, q: Question) -> dict:
        return {'revs': {}, 'defn': stage_hash(q, q.stage('variant'))}

    def correct_cue(self, submission: str, qid: str, cue: str, reading: str | None, *,
                    author: str | None, expect_rev: Any = ANY) -> Artifact:
        """A grader corrects one cue's reading; candidates are narrowed again."""
        q = self.exam.questions[qid]
        if cue not in [c.name for c in q.cues]:
            raise ValueError(f"unknown cue {cue!r}")
        cur = self.store.get(submission, qid, 'variant')
        readings = {n: c.get('reading') for n, c in ((cur.body if cur else {}).get('cues') or {}).items()}
        readings[cue] = reading
        return self.store.write(submission, qid, 'variant', variants.narrow(q, readings),
                                origin='manual', author=author, inputs=self._variant_inputs(q),
                                expect_rev=expect_rev)
