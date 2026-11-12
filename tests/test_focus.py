"""The focused set: facets, the values table, filter semantics. All synthetic."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from examinator import Exam, Field
from examinator.core import focus, grid
from examinator.core.focus import BLANK, Facet
from examinator.core.store import Store, Submission
from examinator.std import math as m
from examinator.std import readers
from examinator.std.pseudocode import comment_facet, comments

CASES = json.loads((Path(__file__).parent / 'focus_cases.json').read_text())
FOCUS_JS = Path(__file__).parents[1] / 'src' / 'examinator' / 'web' / 'static' / 'focus.js'


# --- built-in facets and the table ---------------------------------------------------

def test_builtin_facets_agree_with_the_status_rules(env):
    exam, ws, runner = env
    for qid in ('p', 'a'):
        runner.read('s1', qid)
    runner.refresh('s1', questions=['a'])
    table = focus.values_table(exam, ws.store)
    rows = {r.submission.id: r for r in grid.load(exam, ws.store)}
    for sid in ('s1', 's2'):
        for qid, q in exam.questions.items():
            several = len(q.checks) > 1
            for c in q.checks:
                st = rows[sid].status[(qid, c.name)]
                suffix = f':{c.name}' if several else ''
                assert table[f'{qid}/state{suffix}'][sid] == [st.state]
                assert table[f'{qid}/grade{suffix}'][sid] == [focus.grade_value(st)]
    assert table['a/state:x']['s1'] == ['unverified'] and table['a/grade:x']['s1'] == ['✓']
    # a printed field's value is a facet of its question (which printing a submission has)
    assert table['p/field/c1'] == {'s1': ['one']} and table['p/field/c2'] == {'s1': ['one']}
    assert table['p/state'] == {'s1': ['unverified'], 's2': ['not_read']}       # no checks: how its fields stand
    assert table['b/state']['s2'] == ['not_run'] and table['b/grade']['s2'] == ['undecided']
    ids = {q['id']: [f['id'] for f in q['facets']] for q in focus.describe_facets(exam)}
    assert ids['p'] == ['p/state', 'p/field/c1', 'p/field/c2']
    assert not any('/field/' in f for f in ids['a'] + ids['b'])


def test_table_shape_and_one_submission(env):
    exam, ws, runner = env
    table = focus.values_table(exam, ws.store)
    ids = {focus.facet_id(qid, f) for qid, q in exam.questions.items() for f in focus.facets(q)}
    assert set(table) == ids
    for col in table.values():
        assert all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in col.values())
    one = focus.values_table(exam, ws.store, submission='s2')
    assert set(one) == ids and all(set(col) <= {'s2'} for col in one.values())
    assert one['a/state:x'] == {'s2': table['a/state:x']['s2']}


def _facets_exam():
    exam = Exam('f', pages=1)
    exam.question('q', pages=[1], fields=[Field('x'), Field('y')], check=lambda ctx: 'correct',
                  facets=[Facet('size', "size", lambda v: [len(v.value('x') or '')], group='mine'),
                          Facet('words', "words", lambda v: (v.value('y') or '').split())])
    return exam


def test_question_facets(tmp_path):
    exam = _facets_exam()
    assert [f.name for f in focus.facets(exam.questions['q'])] == ['grade', 'state', 'size', 'words']
    store = Store(tmp_path / 's.sqlite')
    store.put_submissions([Submission(s, s, '', '') for s in ('s1', 's2')])
    store.write('s1', 'q/x', 'value', {'text': '12'}, origin='auto')
    store.write('s1', 'q/y', 'value', {'text': 'b a b'}, origin='auto')
    table = focus.values_table(exam, store)
    assert table['q/words'] == {'s1': ['b', 'a']}           # deduplicated, in order
    assert table['q/size'] == {'s1': ['2'], 's2': ['0']}    # values become strings
    assert table['q/state']['s2'] == ['not_run']


def test_facet_names_are_checked():
    exam = Exam('d', pages=1)
    with pytest.raises(ValueError, match='duplicate facet'):
        exam.question('q', pages=[1], check=lambda ctx: None, facets=[Facet('grade', "mine", lambda v: [])])
    with pytest.raises(ValueError):
        Facet('x', "x", lambda v: [], kind='list')


def test_the_table_is_one_query_and_never_loads_plot_blocks(tmp_path):
    exam = _facets_exam()
    store = Store(tmp_path / 's.sqlite')
    store.put_submissions([Submission(s, s, '', '') for s in ('s1', 's2', 's3')])
    for s in ('s1', 's2', 's3'):
        store.write(s, 'q/check', 'result', {'grade': {'value': True}, 'blocks': [{'type': 'plot'}]}, origin='auto')
    seen = []
    store._db.set_trace_callback(seen.append)
    focus.values_table(exam, store)
    store._db.set_trace_callback(None)
    selects = [s for s in seen if s.lstrip().lower().startswith('select')]
    assert len(selects) == 2                    # every row (blocks left out), the submissions
    assert any("json_remove(body, '$.blocks')" in s for s in selects)


# --- filter semantics, shared with focus.js -------------------------------------------

@pytest.mark.parametrize('case', CASES['cases'], ids=lambda c: json.dumps(c['sel'], ensure_ascii=False))
def test_filter_semantics(case):
    table, subs = CASES['table'], CASES['subs']
    assert focus.focused(table, case['sel'], subs) == case['focused']
    for fid, want in case['counts'].items():
        assert focus.counts(table, case['sel'], fid, subs) == want


@pytest.mark.skipif(shutil.which('node') is None, reason="node not installed")
def test_focus_js_mirrors_python():
    script = f"""
const Focus = require({json.dumps(str(FOCUS_JS))});
const data = {json.dumps(CASES, ensure_ascii=False)};
const out = data.cases.map(c => ({{focused: Focus.focused(data.table, c.sel, data.subs),
  counts: Object.fromEntries(Object.keys(c.counts).map(f => [f, Focus.counts(data.table, c.sel, f, data.subs)]))}}));
console.log(JSON.stringify(out));
"""
    res = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    for case, got in zip(CASES['cases'], json.loads(res.stdout)):
        assert got['focused'] == case['focused']
        assert got['counts'] == case['counts']


# --- the standard answer facet ---------------------------------------------------------

def test_normal_form_groups_equivalent_answers():
    sym = {'x': 'positive'}
    assert m.normal_form('2x^2', symbols=sym) == m.normal_form('x*2*x', symbols=sym) == '2*x**2'
    assert m.normal_form('(x+1)^2', symbols=sym) == m.normal_form('x^2 + 2x + 1', symbols=sym)
    assert m.normal_form('0.50') == m.normal_form('1/2') == '0.5'
    assert m.normal_form('-3/4') == '-0.75' and m.normal_form('1/3') == '1/3'
    assert m.normal_form('  ') is None and m.normal_form(None) is None
    assert m.normal_form('Even.', text=True) == 'even'
    assert m.normal_form('x <= 3 !!', symbols=sym) == 'x <= 3 !!'         # unparsable: the text
    assert m.normal_form('(x+1)^40', symbols=sym) == '(x + 1)**40'         # not expanded
    assert m.normal_form('10^2000') == '10^2000'                           # too long: the text


def test_answer_facet_over_the_keyed_check(tmp_path):
    exam = Exam('m', pages=1)
    exam.question('q', pages=[1], fields=[Field('a'), Field('b')], read=readers.answers(),
                  check=m.keyed({'a': '2*x', 'b': m.Choice('yes')}, symbols=['x']))
    assert [f.name for f in focus.facets(exam.questions['q'])][-2:] == ['answer/a', 'answer/b']
    store = Store(tmp_path / 's.sqlite')
    answers = {'s1': {'a': '2x', 'b': 'Yes.'}, 's2': {'a': 'x+x', 'b': ''}, 's3': {'a': 'x ??', 'b': 'no'}}
    store.put_submissions([Submission(s, s, '', '') for s in [*answers, 's4']])
    for s, a in answers.items():
        for label, text in a.items():
            store.write(s, f'q/{label}', 'value', {'text': text}, origin='auto')
    memo = {}
    table = focus.values_table(exam, store, memo=memo)
    assert table['q/answer/a'] == {'s1': ['2*x'], 's2': ['2*x'], 's3': ['x ??']}     # s4: not read
    assert table['q/answer/b'] == {'s1': ['yes'], 's2': [BLANK], 's3': ['no']}
    assert focus.focused(table, {'q/answer/a': ['2*x']}, ['s1', 's2', 's3', 's4']) == ['s1', 's2']
    assert memo and focus.values_table(exam, store, memo=memo) == table


def test_keys_per_value_offer_every_label_once():
    exam = Exam('m', pages=1)
    exam.question('q', pages=[1], fields=[Field(x) for x in 'abcdw'], checks={
        'one': m.keyed({'v1': {'a': '1', ('b', 'c'): lambda g: 'correct'}, 'v2': {'a': '2', 'd': '3'}}, by='w'),
        'two': m.keyed({'a': '1'})})
    names = [f.name for f in focus.facets(exam.questions['q'])]
    assert [n for n in names if n.startswith('answer/')] == ['answer/a', 'answer/b', 'answer/c', 'answer/d']


# --- comments in pseudocode ----------------------------------------------------------

SOURCE = '''def f(n):  # entry point
    s = "# not a comment"
    # add them up
    for i in range(n):
        s += i
    return s
'''


def test_comments_with_context():
    found = comments(SOURCE, context=1)
    assert [(c.line, c.text) for c in found] == [(1, 'entry point'), (3, 'add them up')]
    assert found[1].context == ('    s = "# not a comment"', '    # add them up', '    for i in range(n):')
    assert comments(None) == [] and comments('') == []


def test_comments_in_incomplete_source():
    src = 'def g(x:\n    # first\n    y = "open\n    # second\nz = """never closed # third\n'
    assert [c.text for c in comments(src)] == ['first', 'second']
    assert [c.text for c in comments('if x:\n  # a\n bad\n# b\n')] == ['a', 'b']


def test_comment_facet_and_the_code_reader():
    f = comment_facet('code')

    class View:
        def value(self, label):
            return SOURCE if label == 'code' else None
    vals = f.values(View())
    assert f.kind == 'text' and vals[1].split('\n')[0] == 'add them up' and '    # add them up' in vals[1].split('\n')
    exam = Exam('c', pages=1)
    exam.question('one', pages=[1], fields=[Field('code')], read=readers.code(['f']))
    exam.question('two', pages=[1], fields=[Field('1'), Field('2')], read=readers.code({'1': ['f'], '2': ['g']}))
    assert [x.name for x in focus.facets(exam.questions['one'])][-1:] == ['comments']
    assert [x.name for x in focus.facets(exam.questions['two'])][-2:] == ['comments/1', 'comments/2']


def test_store_version_follows_every_change(tmp_path):
    store = Store(tmp_path / 's.sqlite')
    seen = [store.version()]

    def changed():
        seen.append(store.version())
        assert seen[-1] != seen[-2]

    a = store.write('s1', 'q/x', 'value', {'text': '1', 'settled': None}, origin='auto')
    changed()
    store.write('s1', 'q/x', 'value', {'text': '1', 'settled': 'person'}, origin='auto', keep_rev=True,
                expect_rev=a.rev)
    changed()
    store.delete('s1', 'q/x', 'value')
    changed()
    store.set_override('q/x', 1, author='t')
    changed()
    store.clear_override('q/x')
    changed()
