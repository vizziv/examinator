import threading

import pytest

from examinator.core.store import Conflict, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'exam.sqlite')
    yield s
    s.close()


def test_revs_increase_and_never_repeat(store):
    a = store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto')
    b = store.write('s', 'q/x', 'value', {'text': '2'}, origin='auto', expect_rev=a.rev)
    store.delete('s', 'q/x', 'value')
    c = store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto', expect_rev=None)
    assert a.rev < b.rev < c.rev


def test_keep_rev_for_a_change_that_is_not_content(store):
    a = store.write('s', 'q/x', 'value', {'text': '1', 'settled': None}, origin='auto')
    b = store.write('s', 'q/x', 'value', {'text': '1', 'settled': 'person'}, origin='auto', expect_rev=a.rev,
                    keep_rev=True)
    assert b.rev == a.rev and b.body['settled'] == 'person'
    assert [h.body['settled'] for h in store.history('s', 'q/x', 'value')] == [None]


def test_the_same_body_again_is_a_no_op(store):
    a = store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto')
    version = store.version()
    assert store.write('s', 'q/x', 'value', {'text': '1'}, origin='manual', author='ta').rev == a.rev
    assert store.version() == version and store.history('s', 'q/x', 'value') == []


def test_expect_rev_and_guard(store):
    a = store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto')
    with pytest.raises(Conflict):
        store.write('s', 'q/x', 'value', {'text': '2'}, origin='auto', expect_rev=None)
    with pytest.raises(Conflict, match="q/x changed"):
        store.write('s', 'q/c', 'result', {}, origin='auto', guard={('q/x', 'value'): a.rev + 1})
    with pytest.raises(Conflict):
        store.write('s', 'q/c', 'result', {}, origin='auto', guard={('q/y', 'value'): 3})
    store.write('s', 'q/c', 'result', {}, origin='auto', guard={('q/x', 'value'): a.rev, ('q/y', 'value'): None})
    assert store.get('s', 'q/c', 'result').body == {}


def test_history_and_restore(store):
    a = store.write('s', '', 'split', "one", origin='auto')
    store.write('s', '', 'split', "two", origin='manual', author='ta')
    assert [h.body for h in store.history('s', '', 'split')] == ["one"]
    r = store.restore('s', '', 'split', a.rev)
    assert r.body == "one" and r.origin == 'auto' and r.rev > a.rev
    assert [h.body for h in store.history('s', '', 'split')] == ["two", "one"]
    store.purge_history()
    assert store.history('s', '', 'split') == []


def test_rows_all_and_dropping_large_parts(store):
    store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto')
    store.write('s', 'q/c', 'result', {'grade': {'value': True}, 'blocks': [list(range(100))]}, origin='auto')
    store.write('t', 'q/x', 'value', {'text': '2'}, origin='auto')
    assert set(store.rows('s', 'value')) == {'q/x'}
    small = store.all(kinds=['result'], drop='$.blocks')
    assert [r.body for r in small] == [{'grade': {'value': True}}]
    assert {r.submission for r in store.all(kinds=['value'])} == {'s', 't'}
    assert [r.submission for r in store.all(submission='t')] == ['t']
    assert store.has_work('s') and not store.has_work('u')


def test_concurrent_edits_one_wins(store):
    a = store.write('s', 'q/x', 'value', {'text': 'base'}, origin='auto')
    results = []

    def edit(i):
        try:
            store.write('s', 'q/x', 'value', {'text': f"edit {i}"}, origin='manual', author=str(i), expect_rev=a.rev)
            results.append('ok')
        except Conflict:
            results.append('conflict')

    threads = [threading.Thread(target=edit, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ['conflict'] * 7 + ['ok']


def test_settings_and_memo(store):
    assert store.setting('cover_pages', 1) == 1
    store.set_setting('cover_pages', 2)
    assert store.setting('cover_pages') == 2
    assert store.memo('s', 'k') is None
    store.set_memo('s', 'k', "v")
    assert store.memo('s', 'k') == "v"
