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
    a = store.write('s', 'q/x', 'value', {'text': '1', 'verified': None}, origin='auto')
    b = store.write('s', 'q/x', 'value', {'text': '1', 'verified': 'person'}, origin='auto', expect_rev=a.rev,
                    keep_rev=True)
    assert b.rev == a.rev and b.body['verified'] == 'person'
    assert [h.body['verified'] for h in store.history('s', 'q/x', 'value')] == [None]


def test_the_same_body_again_is_a_no_op(store):
    a = store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto')
    version = store.version()
    assert store.write('s', 'q/x', 'value', {'text': '1'}, origin='manual', author='ta').rev == a.rev
    assert store.version() == version and store.history('s', 'q/x', 'value') == []


def test_expect_rev_and_guard(store):
    a = store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto')
    with pytest.raises(Conflict):
        store.write('s', 'q/x', 'value', {'text': '2'}, origin='auto', expect_rev=None)
    with pytest.raises(Conflict) as e:                  # which row; the words are the caller's
        store.write('s', 'q/c', 'result', {}, origin='auto', guard={('q/x', 'value'): a.rev + 1})
    assert (e.value.name, e.value.kind, str(e.value)) == ('q/x', 'value', "changed elsewhere")
    with pytest.raises(Conflict):
        store.write('s', 'q/c', 'result', {}, origin='auto', guard={('q/y', 'value'): 3})
    store.write('s', 'q/c', 'result', {}, origin='auto', guard={('q/x', 'value'): a.rev, ('q/y', 'value'): None})
    assert store.get('s', 'q/c', 'result').body == {}


def test_history(store):
    store.write('s', '', 'split', "one", origin='auto')
    store.write('s', '', 'split', "two", origin='manual', author='ta')
    store.write('s', '', 'split', "three", origin='manual', author='ta')
    assert [h.body for h in store.history('s', '', 'split')] == ["two", "one"]


def test_rows_all_and_dropping_large_parts(store):
    store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto')
    store.write('s', 'q/c', 'result', {'grade': {'value': True}, 'blocks': [list(range(100))]}, origin='auto')
    store.write('t', 'q/x', 'value', {'text': '2'}, origin='auto')
    assert [r.name for r in store.all(submission='s', kinds=['value'])] == ['q/x']
    small = store.all(kinds=['result'], drop='$.blocks')
    assert [r.body for r in small] == [{'grade': {'value': True}}]
    assert {r.submission for r in store.all(kinds=['value'])} == {'s', 't'}
    assert [r.submission for r in store.all(submission='t')] == ['t']


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


def test_the_version_changes_with_every_write(store):
    """`version` keys caches of what is computed from the store: every kind of write moves it."""
    from examinator.core.store import Made, Source, Submission
    writes = [lambda: store.write('s', 'q/x', 'value', {'text': '1'}, origin='auto'),
              lambda: store.put_submissions([Submission('s', 's.pdf')]),
              lambda: store.add_source(Source('src', 'src.pdf', 'scan', 'abc', 2, 'now')),
              lambda: store.set_made(Made('s', 'src', (0, 1), (0, 0), 'now', 'f.pdf')),
              lambda: store.delete_made('s'),
              lambda: store.set_setting('people', []),
              lambda: store.set_memo('src', 'k', "v"),
              lambda: store.set_override('q/x', 1, author=None),
              lambda: store.delete_submission('s')]
    for write in writes:
        before = store.version()
        write()
        assert store.version() != before
