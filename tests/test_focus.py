"""The focused set: facets, the values table, filter semantics. All synthetic."""
import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from examinator import Bool, Evidence, Exam, Field, Stage
from examinator.core import focus, grid
from examinator.core.defs import stage_hash
from examinator.core.focus import BLANK, Facet
from examinator.core.store import Store, Submission
from examinator.core.workflow import question_status
from examinator.std import math as m
from examinator.std import stages
from examinator.std.pseudocode import comment_facet, comments

CASES = json.loads((Path(__file__).parent / 'focus_cases.json').read_text())
FOCUS_JS = Path(__file__).parents[1] / 'src' / 'examinator' / 'web' / 'static' / 'focus.js'


# --- built-in facets and the table ---------------------------------------------------

def test_builtin_facets_agree_with_the_workflow(env):
    exam, ws, runner = env
    for stage in ('variant', 'transcript', 'parse', 'evidence', 'grade'):
        assert runner.run_stage('s1', 'a', stage).ok
    table = focus.values_table(exam, ws.store)
    rows = {r.submission.id: r for r in grid.load(exam, ws.store)}
    for sid in ('s1', 's2'):
        for qid, q in exam.questions.items():
            status = question_status(q, ws.store.for_question(sid, qid))
            for part in q.result:
                suffix = f':{part}' if part else ''
                assert table[f'{qid}/state{suffix}'][sid] == [status[part].state]
                assert table[f'{qid}/grade{suffix}'][sid] == [focus.grade_value(status[part])]
            if q.variants:
                assert table[f'{qid}/variant'][sid] == [rows[sid].cells[qid].variant or 'undecided']
    assert table['a/state:x']['s1'] == ['unverified'] and table['a/grade:x']['s1'] == ['✓']
    assert table['a/variant'] == {'s1': ['one'], 's2': ['undecided']}
    assert table['b/state']['s2'] == ['not_started'] and table['b/grade']['s2'] == ['undecided']
    assert 'a/variant' not in [f['id'] for q in focus.describe_facets(exam) if q['id'] == 'b' for f in q['facets']]


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
    def check(ctx):
        return Evidence({'fields': {'x': {'outcome': 'correct'}}, 'words': ['b', 'a', 'b']},
                        [{'type': 'plot', 'data': list(range(1000))}])
    exam = Exam('f', pages=1)
    exam.question('q', pages=[1], result=Bool(), fields=[Field('x')],
                  stages=[Stage('parse', lambda ctx: {}, editable=True),
                          Stage('evidence', check, inputs=('parse',),
                                facets=(Facet('words', "words", lambda b: b['evidence'],
                                              reads={'evidence': '$.data.words'}),)),
                          stages.grade(lambda ev: ev.outcome('x'))],
                  facets=[Facet('size', "size", lambda b: [len(b['parse'])], reads=('parse',), group='mine')])
    return exam


def test_stage_and_question_facets(tmp_path):
    exam = _facets_exam()
    names = [f.name for f in focus.facets(exam.questions['q'])]
    assert names == ['grade', 'state', 'words', 'size']
    store = Store(tmp_path / 's.sqlite')
    store.put_submissions([Submission(s, s, '', '') for s in ('s1', 's2')])
    store.write('s1', 'q', 'parse', {'x': '1', 'y': '2'}, origin='auto')
    store.write('s1', 'q', 'evidence', exam.questions['q'].stage('evidence').fn(None).to_json(), origin='auto')
    table = focus.values_table(exam, store)
    assert table['q/words'] == {'s1': ['b', 'a']}           # deduplicated, in order
    assert table['q/size'] == {'s1': ['2']}                 # values become strings
    assert table['q/state']['s2'] == ['not_started']


def test_facets_do_not_change_stage_hash():
    exam = _facets_exam()
    q = exam.questions['q']
    bare = dataclasses.replace(q, stages=tuple(dataclasses.replace(s, facets=()) for s in q.stages), facets=())
    for s, b in zip(q.stages, bare.stages):
        assert stage_hash(q, s) == stage_hash(bare, b)
    km = stages.check_math({'x': '1'})
    assert km.facets and stage_hash(q, km) == stage_hash(q, dataclasses.replace(km, facets=()))


def test_facet_names_are_checked():
    exam = Exam('d', pages=1)
    with pytest.raises(ValueError, match='duplicate facet'):
        exam.question('q', pages=[1], result=Bool(), stages=[stages.transcribe()],
                      facets=[Facet('grade', "mine", lambda b: [])])
    with pytest.raises(ValueError):
        Facet('x', "x", lambda b: [], kind='list')
    with pytest.raises(ValueError):
        Facet('x', "x", lambda b: [], reads={'evidence': 'data.fields'})


def test_path_read_skips_plot_blocks_and_queries_are_few(tmp_path):
    exam = _facets_exam()
    store = Store(tmp_path / 's.sqlite')
    store.put_submissions([Submission(s, s, '', '') for s in ('s1', 's2', 's3')])
    body = exam.questions['q'].stage('evidence').fn(None).to_json()
    for s in ('s1', 's2', 's3'):
        store.write(s, 'q', 'evidence', body, origin='auto')
    got = store.bodies('q', 'evidence', '$.data.words')
    assert got == {s: ['b', 'a', 'b'] for s in ('s1', 's2', 's3')}
    assert store.bodies('q', 'evidence', '$.data.missing') == {}
    assert store.bodies('q', 'evidence', '$.data', submission='s2') == {'s2': body['data']}
    seen = []
    store._db.set_trace_callback(seen.append)
    focus.values_table(exam, store)
    store._db.set_trace_callback(None)
    selects = [s for s in seen if s.lstrip().lower().startswith('select')]
    assert len(selects) == 3 + 2      # the grid's (artifacts twice, submissions), then one per (kind, path)
    assert not any('blocks' in s for s in selects)
    assert any("body -> '$.data.words'" in s for s in selects)


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


def test_answer_facet_over_check_math(tmp_path):
    exam = Exam('m', pages=1)
    exam.question('q', pages=[1], result=Bool(), fields=[Field('a'), Field('b')],
                  stages=[stages.transcribe(), stages.parse_math(), stages.check_math({'a': '2*x', 'b': m.Choice('yes')}, symbols=['x']),
                          stages.grade(m.all_correct)])
    assert [f.name for f in focus.facets(exam.questions['q'])][-2:] == ['answer/a', 'answer/b']
    store = Store(tmp_path / 's.sqlite')
    answers = {'s1': {'a': '2x', 'b': 'Yes.'}, 's2': {'a': 'x+x', 'b': None}, 's3': {'a': 'x ??', 'b': 'no'}}
    store.put_submissions([Submission(s, s, '', '') for s in [*answers, 's4']])
    for s, a in answers.items():
        data = m.check_answers(a, {'a': '2*x', 'b': m.Choice('yes')}, symbols=['x'])
        store.write(s, 'q', 'evidence', Evidence(data, [{'type': 'plot'}]).to_json(), origin='auto')
    store.write('s4', 'q', 'evidence', {'data': {'fields': {'a': {'answer': '2x', 'outcome': 'correct'}}}},
                origin='auto')                                    # from before normal forms: no value
    table = focus.values_table(exam, store)
    assert table['q/answer/a'] == {'s1': ['2*x'], 's2': ['2*x'], 's3': ['x ??']}
    assert table['q/answer/b'] == {'s1': ['yes'], 's2': [BLANK], 's3': ['no']}
    assert focus.focused(table, {'q/answer/a': ['2*x']}, ['s1', 's2', 's3', 's4']) == ['s1', 's2']


def test_variant_keys_declare_every_label():
    st = stages.check_math({'v1': {'a': '1', ('b', 'c'): lambda g: 'correct'}, 'v2': {'a': '2', 'd': '3'}})
    assert [f.label for f in st.facets] == ['a', 'b', 'c', 'd']


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


def test_comment_facet_and_parse_pseudocode_default():
    f = comment_facet()
    assert f.kind == 'text' and f.reads == (('parse', None),)
    vals = f.values({'parse': SOURCE})
    assert vals[1].split('\n')[0] == 'add them up' and '    # add them up' in vals[1].split('\n')
    assert [x.name for x in stages.parse_pseudocode(['f']).facets] == ['comments']
    assert stages.parse_pseudocode(['f'], facets=()).facets == ()


def test_store_version_follows_every_change(tmp_path):
    store = Store(tmp_path / 's.sqlite')
    seen = [store.version()]

    def changed():
        seen.append(store.version())
        assert seen[-1] != seen[-2]

    a = store.write('s1', 'q', 'parse', {'x': '1'}, origin='auto')
    changed()
    store.verify('s1', 'q', 'parse', expect_rev=a.rev, author='t')
    changed()
    store.verify('s1', 'q', 'parse', expect_rev=a.rev, author='t', verified=False)
    changed()
    store.delete('s1', 'q', 'parse')
    changed()
    store.set_override('q/x', 1, author='t')
    changed()
    store.clear_override('q/x')
    changed()
