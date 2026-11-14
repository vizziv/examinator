"""The definition and its overrides, as three principles. Everything here is synthetic.

1. An override is a change to the definition: valid exactly when the definition with it
   applied is, by the one validation the package goes through (`Exam.validate`).
2. An open workspace always has its overrides applied: the CLI, the app and the runner
   see the same definition.
3. A group that stands apart ('@identity') is reachable only through its own step.
"""
from pathlib import Path

import pytest

from examinator.core.reading import Reader
from PIL import Image

from examinator import Ask, Exam, Field, Printed, Region, cli, load_exam, synth
from examinator.core import identity, overrides
from examinator.core.ask import HANDWRITTEN_PROMPT
from examinator.core.defs import ASKS, IDENTITY
from examinator.core.runner import Runner
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.std import fields, math, readers

from conftest import SCRIPT, Parse, workspace
from test_web import Site

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'
S1 = overrides.scope(overrides.SUBMISSION, 's1')


def tiny(*, key='2', ask=ASKS['name'], prompt=HANDWRITTEN_PROMPT, print_area=(0, 0, 1000, 1000),
         region=Region(1, 0, 500, 500, 600)):
    """A small exam, each of whose values an override may change."""
    exam = Exam('tiny', pages=1, print_area=print_area,
                identity={'name': Field('name', Region(1, 100, 100, 800, 200), read=Ask(ask, prompt=prompt))})
    exam.question('p', pages=[1], fields=[
        Field('c', Region(1, 0, 0, 1000, 100), read=Printed("cue"))])
    exam.question('k', pages=[1], fields=[Field('x', region)], read=Parse(), check=math.keyed({'x': key}))
    exam.question('m', pages=[1], fields=[Field('y')], read=Parse(),
                  check=math.keyed({'one': {'y': '1'}, 'two': {'y': '2'}}, by=fields.value('c', question='p')))
    return exam


# --- 1. one validation ----------------------------------------------------------------------

BAD = [   # path, value, the same definition as the package would write it
    ('@identity/fields/name/read/ask', "", dict(ask="")),
    ('@identity/fields/name/read/prompt', "x {y} z", dict(prompt="x {y} z")),     # every read would fail
    ('k/checks/check/key/x', "(((", dict(key="(((")),                             # every check would fail
    ('@print_area', [0, 0, 1000, 1001], dict(print_area=(0, 0, 1000, 1001))),
    ('k/fields/x/region', [2, 0, 0, 10, 10], dict(region=Region(2, 0, 0, 10, 10))),
]


@pytest.mark.parametrize('path, value, built', BAD, ids=[b[0] for b in BAD])
def test_an_override_is_refused_as_the_package_would_be(tmp_path, path, value, built):
    ws = workspace(tmp_path, tiny())
    with pytest.raises(ValueError) as by_package:
        tiny(**built)
    for scope in ('', S1):
        if scope and overrides.kind(ws.exam, path) not in overrides.SCOPES:
            continue
        with pytest.raises(ValueError) as by_override:
            overrides.set(ws, path, value, scope=scope, author='ta')
        assert str(by_override.value) == str(by_package.value)
    assert ws.store.all_overrides() == []
    # stored anyway (say, before the package changed): skipped on opening, with the same reason
    ws.store.set_override(path, value, author='ta')
    assert [(s.path, s.reason) for s in ws.apply_overrides()] == [(path, str(by_package.value))]
    ws.close()


def test_an_override_the_definition_accepts_is_applied(tmp_path):
    ws = workspace(tmp_path, tiny())
    overrides.set(ws, 'p/fields/c/read/ask', "the cue, as a word (one or two)", author='ta')
    overrides.set(ws, 'k/checks/check/key/x', '1 + 1', author='ta')
    overrides.set(ws, '@identity/fields/name/read/prompt', "Read {ask}; {{\"value\": ...}}", author='ta')
    assert ws.exam.identity.field('name').read.text().startswith("Read " + ASKS['name'])
    assert ws.skipped == []
    ws.close()


def test_the_package_goes_through_the_same_validation():
    with pytest.raises(ValueError, match="print area"):
        Exam('t', pages=1, print_area=(500, 0, 400, 1000))
    with pytest.raises(ValueError, match="does not parse"):
        tiny(key="(((")
    # the identity group is a question like any: a reader without `validate` is fine ...
    class Plain(Reader):
        def read(self, ctx, fields):
            return {f.label: "x" for f in fields}
    exam = Exam('t', pages=1, identity={'name': Field('name', Region(1, 0, 0, 10, 10), read=Plain())})
    assert isinstance(exam.identity.field('name').read, Plain)
    with pytest.raises(ValueError, match="a reader is a `Reader`"):          # what a reader is, stated once
        Exam('t', pages=1).question('q', pages=[1], fields=[Field('x')], read=lambda ctx, fields: {})
    # ... and the rules for every field hold for it
    with pytest.raises(ValueError, match="@identity: field 'id': a default is text"):
        Exam('t', pages=1, identity={'id': Field('id', default="1\n2")})


def test_a_submission_may_name_its_extra_sheets(tmp_path):
    """One submission's extra sheets widen the pages a region may sit on, as context to the
    one validation (a question's pages stay the package's): a region there is read from that sheet."""
    exam = tiny()
    ws = workspace(tmp_path, exam)
    sheets = [Image.new('RGB', (200, 260), c) for c in ('white', 'black')]
    sheets[0].save(ws.pdfs / 's1.pdf', 'PDF', save_all=True, append_images=sheets[1:])      # one extra sheet
    for path, value in (('k/fields/x/region', [2, 0, 0, 500, 100]),):
        with pytest.raises(ValueError, match=r"page 2 is not a printed page \(1..1\)$"):
            overrides.set(ws, path, value, author='ta')                                   # every submission: no
        with pytest.raises(ValueError, match=r"page 2 is not a printed page \(1..1\)$"):
            overrides.set(ws, path, value, scope='submission:s2', author='ta')            # s2 has none
        overrides.set(ws, path, value, scope=S1, author='ta')
    with pytest.raises(ValueError, match=r"page 3 is not a printed page \(1..1\) or an extra sheet \(2..2\)"):
        overrides.set(ws, 'k/fields/x/region', [3, 0, 0, 500, 100], scope=S1, author='ta')
    q = exam.questions['k']
    assert ws.layers('s1').question(q.id).field('x').region == Region(2, 0, 0, 500, 100) and ws.layers('s1').question(q.id).pages == (1,)
    assert ws.layers('s2').question(q.id).field('x').region == q.field('x').region and ws.layers('s2').question(q.id).pages == (1,)
    assert ws.crop('s1', ws.layers('s1').question(q.id).field('x').region).getpixel((5, 5))[0] < 50          # from the black sheet
    # a printed field moved there is read from there
    overrides.set(ws, 'p/fields/c/region', [2, 0, 0, 500, 100], scope=S1, author='ta')
    runner = Runner(ws, model=FakeModel({('p', 'read:c'): '{"value": "one"}'}))
    assert all(o.ok for o in runner.read('s1', 'p'))
    assert runner.model.calls[-1].images[0].getpixel((5, 5))[0] < 50
    runner.close()
    ws.close()


# --- 2. an open workspace has its overrides applied ------------------------------------------

def test_cli_intake_honours_an_exam_wide_box(tmp_path, capsys):
    root = tmp_path / 'ws'
    paths = synth.stacks(load_exam(TOY), root)
    assert cli.main(['intake', str(TOY), '-w', str(root), '--fake', '--accept', str(paths[0])]) == 0
    ws = Workspace(root, load_exam(TOY))
    overrides.set(ws, '@identity/fields/id/region', None, author='ta')        # drawn away in the app
    ws.close()
    assert cli.main(['intake', str(TOY), '-w', str(root), '--fake', '--names']) == 0
    ws = Workspace(root, load_exam(TOY))
    got = identity.values(ws)
    assert got and all(set(v) == {'name'} for v in got.values())                # the ID box is not read
    ws.close()


def test_opening_a_workspace_applies_its_overrides(tmp_path):
    ws = workspace(tmp_path, tiny())
    overrides.set(ws, 'k/fields/x/region', [1, 10, 10, 20, 20], author='ta')
    ws.close()
    exam = tiny()
    base = exam.questions['k'].field('x').region
    ws = Workspace(ws.root, exam)
    assert ws.exam.questions['k'].field('x').region == Region(1, 10, 10, 20, 20)
    assert ws.layers('s1').question('k').field('x').region == Region(1, 10, 10, 20, 20)
    other = Workspace(workspace(tmp_path / 'other', exam).root, exam)       # the same package, another workspace
    assert exam.questions['k'].field('x').region == base and exam.overrides == {}   # the package is untouched
    assert other.exam.questions['k'].field('x').region == base and ws.exam is not other.exam
    ws.close()
    other.close()


# --- 3. a group apart is reached only through its own step -----------------------------------

@pytest.fixture
def site(tmp_path):
    exam = tiny()
    ws = workspace(tmp_path, exam)
    runner = Runner(ws, model=FakeModel(dict(SCRIPT)))
    yield Site(exam, ws, runner)
    runner.close()
    ws.close()


def test_the_grading_routes_cannot_name_the_identity_group(site):
    assert site.get(f'/s/s1/{IDENTITY}').status_code == 404
    for action in ('read', 'check', 'decide', 'clear', 'unverify', 'default', 'restore'):
        assert site.post(f'/s/s1/{IDENTITY}/{action}', {'check': 'check', 'key': f'{IDENTITY}/name'}).status_code == 404
    # nor a field of it from a grading page
    r = site.post('/s/s1/k/save', {'values': {f'{IDENTITY}/name': {'text': "Ann", 'rev': None}}})
    assert r.status_code == 400
    # the Names step saves its values through its own route
    r = site.post(f'/s/s1/{IDENTITY}/save', {'values': {f'{IDENTITY}/name': {'text': "Ann", 'rev': None}}})
    assert r.status_code == 200 and site.runner.values('s1')[f'{IDENTITY}/name'].text == "Ann"
    r = site.post(f'/s/s1/{IDENTITY}/save', {'values': {'k/x': {'text': "2", 'rev': None}}})
    assert r.status_code == 400                                                  # and only those


def test_the_grid_hint_stays_after_a_names_only_read(site):
    assert "Nothing read yet" in site.get('/').text
    site.runner.set('s1', IDENTITY, 'name', "Ann", author='t')
    assert "Nothing read yet" in site.get('/').text
    site.runner.set('s1', 'k', 'x', "2", author='t')
    assert "Nothing read yet" not in site.get('/').text


def test_cli_run_refuses_the_identity_group(tmp_path, capsys):
    root = synth.generate(load_exam(TOY), tmp_path / 'ws', synth.load_cases(load_exam(TOY)))
    assert cli.main(['run', str(TOY), '-w', str(root), '--fake', '-q', IDENTITY]) == 2
    assert f"no question {IDENTITY}" in capsys.readouterr().err


@pytest.mark.parametrize('reader', [
    lambda: Ask("the side", prompt="  "),
    lambda: readers.answers(prompt=""),
    lambda: readers.answers(parse_prompt=" "),
    lambda: readers.code(['f'], prompt=""),
])
def test_a_reader_with_an_empty_prompt_is_refused(reader):
    with pytest.raises(ValueError, match="empty"):
        reader().validate("q")
