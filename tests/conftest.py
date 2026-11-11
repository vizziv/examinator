"""Shared fixtures. Everything here is synthetic."""
import pytest
from PIL import Image

from examinator import Bool, Cue, Evidence, Exam, Field, Region, Stage
from examinator.core.runner import Runner
from examinator.core.store import Submission
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.std import stages

KEY = {'one': {'x': '1', 'y': '2'}, 'two': {'x': '10', 'y': '20'}}


def check(ctx):
    """A plain in-process check: exact text match against a per-variant key."""
    fields = {}
    for label, want in KEY[ctx.variant].items():
        got = ctx.input('parse').get(label)
        outcome = ('blank' if got is None else None if got == '?' else
                   'correct' if got == want else 'incorrect')
        fields[label] = {'answer': got, 'outcome': outcome}
    return Evidence({'fields': fields})


def parse(ctx):
    import json
    return json.loads(ctx.model("parse", text=ctx.input('transcript'), expect='json'))


def make_exam(key_salt=""):
    exam = Exam('mini', pages=2)
    exam.question(
        'a', pages=[1], result={'x': Bool(), 'y': Bool(counts=False)}, variants=['one', 'two'],
        cues=[Cue('c1', region=Region(1, 0, 0, 1000, 200), ask="first cue", reads={'1': 'one', '2': 'two'}),
              Cue('c2', region=Region(1, 0, 200, 1000, 400), ask="second cue", reads={'i': 'one', 'ii': 'two'})],
        fields=[Field('x'), Field('y')],
        stages=[stages.transcribe(notes=key_salt),
                Stage('parse', parse, inputs=('transcript',), uses_model=True, editable=True),
                stages.check(check, uses_variant=True, isolate=False, accepts_markers=True),
                stages.grade(lambda ev: {'x': ev.outcome('x'), 'y': ev.outcome('y')})])
    exam.question(
        'b', pages=[2], result=Bool(), fields=[Field('x'), Field('y')],
        stages=[stages.transcribe(),
                Stage('parse', parse, inputs=('transcript',), uses_model=True, editable=True),
                stages.check(lambda ctx: Evidence({'fields': {'x': {'outcome': 'correct' if ctx.input('parse').get('x') == 'yes' else 'incorrect'}}}),
                             isolate=False),
                stages.grade(lambda ev: ev.outcome('x'))])
    return exam


SCRIPT = {
    ('a', 'variant'): lambda call: '{"value": "1"}' if "first cue" in call.prompt else '{"value": "i"}',
    'transcript': "x: 1\ny: 2",
    ('a', 'parse'): '{"x": "1", "y": "2"}',
    ('b', 'parse'): '{"x": "yes"}',
}


@pytest.fixture
def env(tmp_path):
    """(exam, workspace, runner) with two synthetic submissions and a scripted model."""
    exam = make_exam()
    root = tmp_path / 'ws'
    (root / 'pdfs').mkdir(parents=True)
    pages = [Image.new('RGB', (200, 260), 'white') for _ in range(2)]
    for sid in ('s1', 's2'):
        pages[0].save(root / 'pdfs' / f'{sid}.pdf', 'PDF', save_all=True, append_images=pages[1:])
    ws = Workspace(root, exam)
    ws.store.put_submissions([Submission(s, s, "", f'{s}.pdf') for s in ('s1', 's2')])
    runner = Runner(exam, ws, model=FakeModel(dict(SCRIPT)))
    yield exam, ws, runner
    runner.close()
    ws.close()
