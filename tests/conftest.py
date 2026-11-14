"""Shared fixtures. Everything here is synthetic."""
import json
from dataclasses import dataclass

import pytest
from PIL import Image

from examinator import Check, Exam, Field, Printed, Region, Result
from examinator.core.runner import Runner
from examinator.core.store import Submission
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.std import fields as fields_

KEY = {'one': {'x': '1', 'y': '2'}, 'two': {'x': '10', 'y': '20'}}
PRINTING = fields_.agree(fields_.value('c1', question='p'), fields_.value('c2', question='p'))


@dataclass(frozen=True)
class Parse:
    """A plain reader: one model call whose JSON reply is the fields' values."""
    notes: str = ""

    def read(self, ctx, fields):
        got = json.loads(ctx.model("parse " + self.notes, expect='json', step='parse'))
        return {f.label: str(got.get(f.label) or '') for f in fields}


def exact(label):
    """A check: exact text match of one field against the key of the printing
    that question 'p' reads (its two printed fields must agree)."""
    def check(ctx):
        want = KEY[PRINTING(ctx)][label]
        got = ctx.read(label)
        outcome = 'blank' if not got else None if got == '?' else 'correct' if got == want else 'incorrect'
        return Result(outcome, {label: outcome})
    check.__qualname__ = f'exact_{label}'
    return check


def yes(ctx):
    return 'correct' if ctx.read('x') == 'yes' else 'incorrect'


def make_exam(notes="", trust=False):
    exam = Exam('mini', pages=2)
    exam.question(          # the printing: no checks, two printed fields that 'a' reads; with `trust`,
                            # what the reader reads needs no person
        'p', pages=[1],
        fields=[Field('c1', Region(1, 0, 0, 1000, 200),
                      read=Printed("first cue", verify=not trust)),
                Field('c2', Region(1, 0, 200, 1000, 400),
                      read=Printed("second cue", verify=not trust))])
    exam.question(
        'a', pages=[1], fields=[Field('x'), Field('y')], read=Parse(notes),
        checks={'x': Check(exact('x'), isolate=False), 'y': Check(exact('y'), isolate=False)})
    exam.question('b', pages=[2], fields=[Field('x')], read=Parse(), check=Check(yes, isolate=False))
    return exam


SCRIPT = {
    ('p', 'read:c1'): '{"value": "one"}',
    ('p', 'read:c2'): '{"value": "one"}',
    ('a', 'parse'): '{"x": "1", "y": "2"}',
    ('b', 'parse'): '{"x": "yes"}',
}


def first_of(ws, src, k):
    """Submission `k`'s first sheet in a scan's plan: how an edit names a submission."""
    from examinator.core import intake, split
    return split.runs(intake.plan(ws, src).body)[k][0]


def workspace(tmp_path, exam, subs=('s1', 's2')):
    root = tmp_path / 'ws'
    (root / 'pdfs').mkdir(parents=True)
    pages = [Image.new('RGB', (200, 260), 'white') for _ in range(exam.pages)]
    for sid in subs:
        pages[0].save(root / 'pdfs' / f'{sid}.pdf', 'PDF', save_all=True, append_images=pages[1:])
    ws = Workspace(root, exam)
    ws.store.put_submissions([Submission(s, f'{s}.pdf') for s in subs])
    return ws


@pytest.fixture
def env(tmp_path):
    """(exam, workspace, runner) with two synthetic submissions and a scripted model;
    `exam` is the workspace's definition."""
    ws = workspace(tmp_path, make_exam())
    runner = Runner(ws, model=FakeModel(dict(SCRIPT)))
    yield ws.exam, ws, runner
    runner.close()
    ws.close()
