import threading

import pytest

from examinator.core.store import Conflict, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'exam.sqlite')
    yield s
    s.close()


def test_revs_increase_and_never_repeat(store):
    a = store.write('s', 'q', 'parse', {'x': 1}, origin='auto')
    b = store.write('s', 'q', 'parse', {'x': 2}, origin='auto', expect_rev=a.rev)
    store.delete('s', 'q', 'parse')
    c = store.write('s', 'q', 'parse', {'x': 1}, origin='auto', expect_rev=None)
    assert a.rev < b.rev < c.rev


def test_expect_rev_and_guard(store):
    a = store.write('s', 'q', 'parse', "p", origin='auto')
    with pytest.raises(Conflict):
        store.write('s', 'q', 'parse', "p2", origin='auto', expect_rev=None)
    with pytest.raises(Conflict):
        store.write('s', 'q', 'evidence', {}, origin='auto', guard={'parse': a.rev + 1})
    with pytest.raises(Conflict):
        store.write('s', 'q', 'evidence', {}, origin='auto', guard={'variant': 3})
    store.write('s', 'q', 'evidence', {}, origin='auto', guard={'parse': a.rev, 'variant': None})
    assert store.get('s', 'q', 'parse').body == "p"


def test_history_and_restore(store):
    a = store.write('s', 'q', 'parse', "one", origin='auto')
    store.write('s', 'q', 'parse', "two", origin='manual', author='ta')
    assert [h.body for h in store.history('s', 'q', 'parse')] == ["one"]
    r = store.restore('s', 'q', 'parse', a.rev)
    assert r.body == "one" and r.origin == 'auto' and not r.verified and r.rev > a.rev
    assert [h.body for h in store.history('s', 'q', 'parse')] == ["two", "one"]
    store.purge_history()
    assert store.history('s', 'q', 'parse') == []


def test_verify_needs_current_rev(store):
    a = store.write('s', 'q', 'parse', "one", origin='auto')
    b = store.write('s', 'q', 'parse', "two", origin='auto')
    with pytest.raises(Conflict):
        store.verify('s', 'q', 'parse', expect_rev=a.rev, author='ta')
    assert store.verify('s', 'q', 'parse', expect_rev=b.rev, author='ta').verified
    assert not store.verify('s', 'q', 'parse', expect_rev=b.rev, author='ta', verified=False).verified


def test_concurrent_edits_one_wins(store):
    a = store.write('s', 'q', 'parse', "base", origin='auto')
    results = []

    def edit(i):
        try:
            store.write('s', 'q', 'parse', f"edit {i}", origin='manual', author=str(i), expect_rev=a.rev)
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
