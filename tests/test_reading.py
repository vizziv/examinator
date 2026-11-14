"""A value is what was read; whether a person should look is a status. The model
transcribes; the program splits. Every reply here is scripted (the fake model);
everything is synthetic."""
import json
from pathlib import Path

import pytest
from PIL import Image

from examinator import Ask, Check, Exam, Field, Printed, Region
from examinator.core.context import ReadContext
from examinator.core.reading import Reading, reading
from examinator.core.runner import Runner
from examinator.model.fake import FakeModel
from examinator.std import math, readers
from examinator.std.readers import calls, functions, is_empty

from conftest import workspace

ROOT = Path(__file__).resolve().parents[1]
LABELS = ['1', '2', '3', '4']


class Services:
    """Blank pages and crops, and a scripted model."""

    def __init__(self, script):
        self.fake = FakeModel(script)

    def pages(self, submission, question):
        return [Image.new('RGB', (20, 20), 'white')]

    def crop(self, submission, region):
        return Image.new('RGB', (10, 10), 'white')

    def model(self, prompt, *, images, text, expect, tag):
        return self.fake.complete(prompt, images=images, text=text, expect=expect, tag=tag)


def read(reader, labels, script, qid='q'):
    exam = Exam('t', pages=1)
    exam.question(qid, pages=[1], fields=[Field(label, Region(1, 0, 0, 100, 100)) for label in labels], read=reader)
    q = exam.questions[qid]
    services = Services(script)
    return reader.read(ReadContext('s', q, services), tuple(q.fields)), services.fake.calls


def obj(value, unsure=""):
    return {'value': value, 'unsure': unsure}


# --- one reply shape: a field object {"value": ..., "unsure": ...} --------------------------

@pytest.mark.parametrize('said, want', [
    (obj("x + 1"), Reading("x + 1")), (obj(" 7 "), Reading("7")), (obj(""), Reading("")),
    (obj(7), Reading("7")), (obj([14, 12, 15]), Reading("[14, 12, 15]")), (obj("None"), Reading("None")),
    (obj("3", "smudged"), Reading("3", "smudged")), ({'value': "3", 'unsure': True}, Reading("3", "")),
    ({'value': "3"}, Reading("3")),
    (obj(None), None), (obj("null"), None), (obj(True), None), (obj({'a': 1}), None),
    ({'unsure': "x"}, None), ("3", None), (None, None), ([obj("3")], None),
])
def test_one_field_object(said, want):
    assert reading(said) == want


@pytest.mark.parametrize('reply', [
    json.dumps({'1': obj("a")}),                                     # the other labels are missing
    json.dumps({'1': obj("a"), '2': obj(None), '3': obj("null"), '4': "d"}),
    json.dumps({'A': obj("a"), 'B': obj("b"), 'C': obj("c"), 'D': obj("d")}),     # about other labels
    json.dumps({'answers': {'1': "a", '2': "b", '3': "c", '4': "d"}}),            # another shape
    'the answers are a, b, c and d', '[]', '{}',
])
def test_silence_is_left_out(reply):
    got, _ = read(readers.answers(), LABELS, {'transcribe': "...", 'parse': reply})
    stated = json.loads(reply) if reply.startswith('{') else {}
    assert got == {k: reading(v) for k, v in stated.items() if k in LABELS and reading(v) is not None}
    assert set(got) <= {'1'}


def test_a_blank_is_an_ordinary_value_and_unsure_is_a_note():
    reply = json.dumps({'1': obj(""), '2': obj("x"), '3': obj("C", "B and C both marked")})
    got, _ = read(readers.answers(), LABELS, {'transcribe': "...", 'parse': reply})
    assert got == {'1': Reading(""), '2': Reading("x"), '3': Reading("C", "B and C both marked")}


@pytest.mark.parametrize('reply, want', [
    ('{"value": "Ann Example", "unsure": ""}', Reading("Ann Example")), ('{"value": ""}', Reading("")),
    ('{"quote": "n = 7", "value": "7", "unsure": "the 7 is faint"}', Reading("7", "the 7 is faint")),
    ('{"value": null}', None), ('{"value": "null"}', None), ('{"name": "Ann"}', None), ('not json', None),
])
def test_asking_one_region(reply, want):
    for reader in (Ask("the name"), Printed("the printed n")):
        got, _ = read(reader, ['n'], {'read:n': reply})
        assert got == ({} if want is None else {'n': want})


def test_nothing_verifies_a_blank_but_a_person():
    f = Field('n', Region(1, 0, 0, 10, 10))
    assert not Printed("n").vouches(f, Reading("")) and Printed("n").vouches(f, Reading("3"))
    assert not Ask("n").vouches(f, Reading("")) and not Ask("n", verify=False).vouches(f, Reading(""))


def exam_with_reader():
    exam = Exam('t', pages=1)
    exam.question('q', pages=[1], fields=[Field('n', Region(1, 0, 0, 500, 100), read=Printed("n")),
                                          Field('x', Region(1, 0, 200, 500, 300)),
                                          Field('y', Region(1, 0, 300, 500, 400))],
                  read=readers.answers(), check=Check(math.keyed({'x': '1', 'y': '2'}), isolate=False))
    return exam


def test_silence_stores_nothing_and_the_outcome_counts_it(tmp_path):
    exam = exam_with_reader()
    ws = workspace(tmp_path, exam, subs=('s1',))
    script = {'read:n': '{"value": null}', 'transcribe': "x: 1", 'parse': json.dumps({'x': obj("1")})}
    runner = Runner(ws, model=FakeModel(script))
    try:
        outs = runner.read('s1', 'q')
        assert [(o.status, o.asked, o.read) for o in outs] == [('done', 1, 0), ('done', 2, 1)]
        assert outs[1].detail == "not read: y"
        values = runner.values('s1')
        assert set(values) == {'q/x'}                      # n and y: not read, nothing written
        # "missing only" reads them next time
        script.update({'read:n': '{"value": "5"}', 'parse': json.dumps({'x': obj("9"), 'y': obj("")})})
        outs = runner.read('s1', 'q', missing_only=True)
        values = runner.values('s1')
        assert (values['q/x'].text, values['q/y'].text, values['q/n'].text) == ("1", "", "5")
        assert values['q/n'].verified == 'reader' and values['q/y'].verified is None    # a blank: a person
    finally:
        runner.close()
        ws.close()


def test_unsure_flags_a_value_until_a_person_verifies_it(tmp_path):
    exam = exam_with_reader()
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(ws, model=FakeModel({'read:n': '{"value": "5", "unsure": "faint"}', 'transcribe': "...",
                                         'parse': json.dumps({'x': obj("1", "1 or 7"), 'y': obj("")})}))
    q = exam.questions['q']
    try:
        assert all(o.ok for o in runner.read('s1', 'q'))
        values = runner.values('s1')
        assert (values['q/x'].review, values['q/x'].state) == ("1 or 7", 'review')
        assert (values['q/n'].review, values['q/n'].verified) == ("faint", None)     # never vouched
        runner.verify('s1', 'q', 'y', author='ta', expect_rev=runner.values('s1')['q/y'].rev)
        runner.refresh('s1')
        st = runner.ledger('s1').status(ws.exam, q, q.check('check'))
        assert (st.state, st.counts, st.reason) == ('review', False, "x needs review")
        runner.verify('s1', 'q', 'x', author='ta', expect_rev=runner.values('s1')['q/x'].rev)
        st = runner.ledger('s1').status(ws.exam, q, q.check('check'))
        assert st.counts and runner.values('s1')['q/x'].review is None
        # a person flags it again (the ⋯ menu): it waits for a person again, same revision
        v = runner.values('s1')['q/x']
        flagged = runner.review('s1', 'q', 'x', note="", expect_rev=v.rev)
        assert (flagged.rev, flagged.state) == (v.rev, 'review')
        assert runner.ledger('s1').status(ws.exam, q, q.check('check')).state == 'review'
    finally:
        runner.close()
        ws.close()


def test_no_review_markers_left_in_text():
    """A review is a status: no `[REVIEW` marker text is written or parsed anywhere."""
    found = [str(p.relative_to(ROOT)) for base in ('src', 'exams') for p in (ROOT / base).rglob('*')
             if p.is_file() and p.suffix in ('.py', '.js', '.html', '.css') and '[REVIEW' in p.read_text()]
    assert found == []


# --- the model transcribes; the program splits ------------------------------------------

FUNCTIONS = {
    'helper': obj("def helper(x):\n    return x + 1\n"),
    'first': obj("def first(a):\n    return helper(a) * 2\n", "line 2: 2 or 7?"),
    'second': obj("def second():\n    return first(3) + 1\n"),
    'third': obj("def third():\n    # TODO\n"),
    'stray': obj("not a definition of stray"),
}


def code():
    return readers.code({'p': ['first'], 'q': ['second'], 'r': ['third'], 's': ['absent']})


def test_each_field_takes_its_own_functions():
    got = code().split(json.dumps(FUNCTIONS), ['p', 'q', 'r', 's'])
    assert got['p'] == Reading("def helper(x):\n    return x + 1\n\ndef first(a):\n    return helper(a) * 2\n",
                               "first: line 2: 2 or 7?")
    # another field's function is that field's: its review does not reach this field
    assert got['q'] == Reading("def second():\n    return first(3) + 1\n")
    assert got['r'] == Reading("")         # only the printed stub: the student wrote nothing
    assert 's' not in got                  # the reply says nothing about it: not read


def test_one_transcription_and_one_conversion_per_question():
    reader = code()
    got, calls_ = read(reader, ['p', 'q', 'r', 's'], {'transcribe': "...", 'parse': json.dumps(FUNCTIONS)})
    assert [c.tag[2] for c in calls_] == ['transcribe', 'parse']
    assert got == reader.split(json.dumps(FUNCTIONS), ['p', 'q', 'r', 's'])
    assert "Requested functions: first (p); second (q); third (r); absent (s)" in calls_[0].prompt


def test_split_edge_cases():
    reader = readers.code({'p': ['f', 'g'], 'q': ['h']})
    assert reader.split("not json", ['p', 'q']) == {}
    got = reader.split(json.dumps({'f': obj("def f():\n    return 1\n"), 'h': obj("def h():\n    pass\n")}), ['p', 'q'])
    assert got == {'q': Reading("")}       # g is missing: p is not read
    got = reader.split(json.dumps({'f': obj("def f(): pass"), 'g': obj("def g():\n    ...\n"),
                                   'h': obj("def h():\n    return 0\n")}), ['p', 'q'])
    assert got == {'p': Reading(""), 'q': Reading("def h():\n    return 0\n")}


def test_functions_from_lines_alone():
    defs, rest = functions("x = 1\ndef a(:\n    oops(\n# note\ndef b():\n    return a()\ndef a():\n    pass\n")
    assert list(defs) == ['a', 'b'] and rest == "x = 1"
    assert defs['a'].count("def a") == 2                    # defined twice: both kept, for a person to see
    assert calls(defs['b'], defs) == ['a'] and calls("def a():\n    return a  # b()\n", ['a', 'b']) == ['a']
    assert is_empty("def f(a, b):\n    # TODO\n") and is_empty('def f():\n    """Doc."""\n    pass\n')
    assert not is_empty("def f():\n    return 1\n") and not is_empty("def f(:\n    x\n")


def test_script_reads_back_what_it_was_given():
    reader = readers.code({'p': ['first'], 'q': ['second'], 'r': ['third']})
    exam = Exam('t', pages=1)
    exam.question('c', pages=[1], fields=[Field(x) for x in 'pqr'], read=reader)
    fields = exam.questions['c'].fields
    given = {'p': "def first(a):\n    return a\n", 'q': "def first(a):\n    return a\n\ndef second():\n    return 1\n",
             'r': None}
    got = reader.split(reader.script(fields, given)['parse'], ['p', 'q', 'r'])
    assert got == {'p': Reading(given['p']), 'q': Reading("def second():\n    return 1\n"), 'r': Reading("")}
