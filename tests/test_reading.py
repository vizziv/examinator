"""Silence is not a value; the model transcribes and the program splits.
Every reply here is scripted (the fake model); everything is synthetic."""
import json

import pytest
from PIL import Image

from examinator import Ask, Check, Exam, Field, Printed, Region
from examinator.core.context import ReadContext
from examinator.core.defs import has_marker
from examinator.core.reading import BLANK, NOT_READ, value
from examinator.core.runner import Runner
from examinator.model.fake import FakeModel
from examinator.std import math, readers
from examinator.std.pseudocode import calls, functions, is_empty

from conftest import workspace

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


# --- Principle 1: a field gets a value only from an explicit statement about it ------------

@pytest.mark.parametrize('said, want', [
    ("x + 1", "x + 1"), ("  7 ", "7"), (BLANK, ''), (7, "7"), ([14, 12, 15], "[14, 12, 15]"), ("None", "None"),
    (None, NOT_READ), ("", NOT_READ), ("   ", NOT_READ), ("null", NOT_READ), ("NULL", NOT_READ),
    ("[REVIEW: smudged] 3", "[REVIEW: smudged] 3"),
])
def test_the_rule(said, want):
    assert value(said) == want


def test_what_else_the_model_said_follows_the_marker():
    assert value(None, also="7") == f"{NOT_READ} 7"
    assert value("null", also="") == NOT_READ
    assert value("3", also="7") == "3"


@pytest.mark.parametrize('reply', [
    '{"answers": {"1": "a"}}',                                # the other labels are missing
    '{"answers": {"1": "a", "2": null, "3": "null", "4": ""}}',
    '{"answers": {"A": "a", "B": "b", "C": "c", "D": "d"}}',  # a reply about other labels
    'the answers are a, b, c and d',                          # not JSON
    '{"answers": ["a", "b", "c", "d"]}', '[]', '{}',
])
def test_answers_never_store_silence_as_blank(reply):
    got, _ = read(readers.answers(), LABELS, {'transcribe': "...", 'parse': reply})
    for label in LABELS:
        stated = json.loads(reply).get('answers', {}).get(label) if reply.startswith('{"answers": {') else None
        if stated in (None, '', 'null'):
            assert got[label] == NOT_READ, (label, got)
        else:
            assert got[label] == stated


def test_answers_blank_only_when_stated():
    reply = json.dumps({'answers': {'1': BLANK, '2': "x", '3': "[REVIEW: two marked]"}})
    got, _ = read(readers.answers(), LABELS, {'transcribe': "...", 'parse': reply})
    assert got == {'1': '', '2': "x", '3': "[REVIEW: two marked]", '4': NOT_READ}


@pytest.mark.parametrize('reply, want', [
    ('{"value": "Ann Example"}', "Ann Example"), ('{"value": "BLANK"}', ''),
    ('{"value": ""}', NOT_READ), ('{"value": null}', NOT_READ), ('{"value": "null"}', NOT_READ),
    ('{"name": "Ann"}', NOT_READ), ('not json', NOT_READ),
    ('{"quote": "n = 7", "value": null}', f"{NOT_READ} n = 7"),     # the quote is kept
])
def test_asking_one_region(reply, want):
    for reader in (Ask("the name"), Printed("the printed n")):
        got, _ = read(reader, ['n'], {'read:n': reply})
        assert got == {'n': want}


def test_a_field_not_read_waits_for_a_person(tmp_path):
    """Not read is a review marker: never vouched for, refused by "verify", and
    a check that reads it is undecided."""
    exam = Exam('t', pages=1)
    exam.question('q', pages=[1], fields=[Field('n', Region(1, 0, 0, 500, 100), read=Printed("n")),
                                          Field('x', Region(1, 0, 200, 500, 300))],
                  read=readers.answers(), check=Check(math.keyed({'x': '1'}), isolate=False))
    ws = workspace(tmp_path, exam, subs=('s1',))
    runner = Runner(ws, model=FakeModel({'read:n': '{"value": null}', 'transcribe': "x: ?",
                                         'parse': '{"answers": {}}'}))
    try:
        assert all(o.ok for o in runner.read('s1', 'q'))
        values = runner.values('s1')
        for k in ('q/n', 'q/x'):
            assert values[k].text == NOT_READ and values[k].marker and values[k].verified is None
            with pytest.raises(ValueError):
                runner.verify('s1', 'q', k.split('/')[1], author='t', expect_rev=values[k].rev)
        runner.refresh('s1', questions=['q'])
        q = exam.questions['q']
        st = runner.sheet('s1').status(ws.exam, q, q.check('check'))
        assert not st.counts and st.value is None
    finally:
        runner.close()
        ws.close()


# --- Principle 2: the model transcribes; the program splits ------------------------------

SOURCE = """\
def helper(x):
    return x + 1

def first(a):
    return helper(a) * 2   # [REVIEW: 2 or 7?]

def second():
    return first(3) + 1

def third():
    # TODO
"""


def code():
    return readers.code({'p': ['first'], 'q': ['second'], 'r': ['third'], 's': ['absent']})


def test_each_field_takes_its_own_functions():
    got = code().split(SOURCE, ['p', 'q', 'r', 's'])
    assert got['p'].startswith("def helper(x):") and "def first" in got['p'] and "second" not in got['p']
    # another field's function is that field's: a marker in it does not reach this field
    assert "def first" not in got['q'] and not has_marker(got['q']) and got['q'].startswith("def second")
    assert got['r'] == ''                   # only the printed stub: the student wrote nothing
    assert got['s'] == NOT_READ             # the reply says nothing about it


def test_one_transcription_and_one_conversion_per_question():
    reader = code()
    got, calls_ = read(reader, ['p', 'q', 'r', 's'], {'transcribe': "...", 'parse': SOURCE})
    assert [c.tag[2] for c in calls_] == ['transcribe', 'parse']
    assert got == reader.split(SOURCE, ['p', 'q', 'r', 's'])
    assert "Requested functions: first (p); second (q); third (r); absent (s)" in calls_[0].prompt


def test_split_edge_cases():
    reader = readers.code({'p': ['f', 'g'], 'q': ['h']})
    assert reader.split(BLANK, ['p', 'q']) == {'p': '', 'q': ''}
    got = reader.split("def f():\n    return 1\n", ['p', 'q'])
    assert got['q'] == NOT_READ and got['p'].startswith("def f():") and f"# {NOT_READ} g" in got['p']
    # a line outside every function goes to every field, so a marker there blocks them all
    got = reader.split("# [REVIEW: a page is torn]\ndef f():\n    pass\ndef g():\n    pass\ndef h():\n    pass\n",
                       ['p', 'q'])
    assert all(has_marker(v) for v in got.values())
    # a stub that is only `pass` is the student writing nothing; an invented body is not
    got = reader.split("```python\ndef f(): pass\ndef g():\n    ...\ndef h():\n    return 0\n```\n", ['p', 'q'])
    assert got == {'p': '', 'q': "def h():\n    return 0\n"}


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
    script = reader.script(fields, given)
    got = reader.split(script['parse'], ['p', 'q', 'r'])
    assert got == {'p': given['p'], 'q': "def second():\n    return 1\n", 'r': ''}
