"""Random sequences of every split operation, with the invariants of the plan checked
after each step. Pure: Accept is modelled by `split.accepted` and `split.link` over
a dict of what was written, as `intake.accept` uses them."""
import copy
import random

import pytest

from examinator.core import split

PAGES = 5
ANCHOR = 1


def pile(rng):
    """Sheets as (student, printed page 0-based) or None (a stray), with readings: a
    page number from the anchor on, nothing on page 1 or a stray."""
    sheets = [None] * rng.choice((0, 0, 1))
    for s in range(rng.randint(2, 4)):
        pages = list(range(PAGES))
        if rng.random() < 0.3:
            pages.remove(rng.randrange(1, PAGES))
        for p in pages:
            sheets.append((s, p))
            if rng.random() < 0.08:
                sheets.append(None)
    return sheets, (lambda i: None if sheets[i] is None or sheets[i][1] < ANCHOR else sheets[i][1])


def check(plan, n):
    L = plan['sheets']
    assert sorted(r['i'] for r in L) == list(range(n))                      # every sheet exactly once
    assert not any(r.get('start') and r.get('dropped') for r in L)          # starts only on kept sheets
    kept = [r for r in L if not r.get('dropped')]
    assert not kept or kept[0].get('start')                                 # the first kept sheet starts one
    runs = split.runs(plan)
    assert all(runs) and sum(map(len, runs)) == len(kept)                   # no empty submission
    assert split.guess(plan) == plan                                        # a guess changes nothing of an edit's


def whole(plan):
    """The verified submissions, each as its sheets."""
    return [tuple(run) for run, v in zip(split.runs(plan), split.verified(plan)) if v]


def looked(plan):
    """The verified submissions, each as a person looked at it."""
    return {look for look, v in zip(split.looks(plan), split.verified(plan)) if v}


def touched(plan, op, k, sheet):
    """The sheets an edit names: a verified submission holding none of them must not change."""
    at = split.starts(plan) + [len(plan['sheets'])]
    L = plan['sheets']

    def sub(j):
        return {r['i'] for r in L[at[j]:at[j + 1]]} if 0 <= j < len(at) - 1 else set()

    def owner(i):
        p = next(p for p, r in enumerate(L) if r['i'] == i)
        return sum(s <= p for s in at[:-1]) - 1
    if op in ('confirm', 'move', 'join'):
        return sub(k) | sub(k - 1 if op == 'move' else k + 1 if op == 'join' else k)
    if op in ('cut', 'drop', 'restore', 'shift', 'extra', 'missing', 'unmissing'):
        return {sheet} | sub(owner(sheet)) | (sub(owner(sheet) - 1) | sub(owner(sheet) + 1) if op == 'shift' else set())
    return set()


def person(plan):
    """What a guess must leave alone: the order, every sheet's own marks, and the start
    and dropped marks of the sheets a person spoke for."""
    return [{k: v for k, v in r.items() if r.get('said') or k not in ('start', 'dropped')} for r in plan['sheets']]


class Desk:
    """A plan with undo, and what Accept has written: id -> (pages, turns)."""

    def __init__(self, rng):
        self.rng = rng
        self.sheets, self.read = pile(rng)
        self.n = len(self.sheets)
        self.plan = split.guess(split.new_plan(self.n, PAGES, anchor=ANCHOR), self.read if rng.random() < 0.5 else None)
        self.history = [self.plan]
        self.made, self.counter, self.ever = {}, 1, set()

    def set(self, plan):
        self.plan = plan
        self.history.append(plan)

    def written(self, plan):
        turns = split.turns(plan)
        return [tuple((i, 0 if i is None else turns[i]) for i in split.written(lay))
                for lay in split.layouts(plan, PAGES)]

    def accept(self):
        plan = split.accepted(self.plan)
        ids, gone = split.link(split.runs(plan), {s: [i for i, _ in w] for s, w in self.made.items()})
        changes = len(gone)
        for sid, w in zip(ids, self.written(plan)):
            if sid is None:
                sid = f's{self.counter}'
                self.counter += 1
                assert sid not in self.ever                                 # ids are never reused
                self.ever.add(sid)
            if self.made.get(sid) != w:
                self.made[sid] = w
                changes += 1
        for sid in gone:
            del self.made[sid]
        if plan != self.plan:
            self.set(plan)
        return changes

    def step(self):
        rng, plan = self.rng, self.plan
        k = rng.randrange(max(1, len(split.runs(plan))))
        sheet = rng.randrange(self.n)
        d = rng.choice((-1, 1))
        op = rng.choice(['confirm', 'move', 'cut', 'join', 'drop', 'restore', 'extra', 'missing', 'unmissing',
                         'shift', 'turn', 'guess', 'guess', 'accept', 'accept', 'undo', 'undo'])
        if op == 'guess':
            got = split.guess(plan, self.read if rng.random() < 0.5 else None)
            assert person(got) == person(plan)                              # a guess changes only guesses
            assert looked(got) == looked(plan)
            self.set(got)
        elif op == 'accept':
            self.accept()
            assert self.accept() == 0                                       # Accept again: unchanged
        elif op == 'undo':
            if len(self.history) > 1:
                self.history.pop()
                self.plan = self.history[-1]
        else:
            fn = {'confirm': lambda: split.confirm(plan, k), 'move': lambda: split.move(plan, k, d),
                  'cut': lambda: split.cut(plan, sheet), 'join': lambda: split.join(plan, k),
                  'drop': lambda: split.drop(plan, sheet), 'restore': lambda: split.put_back(plan, sheet),
                  'extra': lambda: split.toggle_extra(plan, sheet), 'missing': lambda: split.add_missing(plan, sheet),
                  'unmissing': lambda: split.remove_missing(plan, sheet), 'shift': lambda: split.shift(plan, sheet, d),
                  'turn': lambda: split.turn(plan, [sheet])}[op]
            before = copy.deepcopy(plan)
            try:
                got = fn()
            except ValueError:
                assert plan == before                                       # refused: nothing changed
                return op
            # no edit changes a verified submission other than the ones it names
            named = touched(plan, op, k, sheet)
            assert {w for w in whole(plan) if not named & set(w)} <= set(whole(got)), op
            if op == 'confirm':                                             # verifying the guess changes no run
                assert split.runs(got) == split.runs(plan)
            # every submission still verified looks exactly as when it was verified
            assert looked(got) <= looked(plan) | ({split.looks(got)[k]} if op == 'confirm' else set()), op
            self.set(got)
        return op

    def check_ids(self):
        """A written submission whose sheets share a run with no other written one's keeps its id."""
        runs = split.runs(self.plan)
        ids, _ = split.link(runs, {s: [i for i, _ in w] for s, w in self.made.items()})
        whose = {}
        for sid, w in self.made.items():
            for i, _ in w:
                if i is not None:
                    whose.setdefault(i, set()).add(sid)
        for sid, w in self.made.items():
            mine = [run for run in runs if any(sid in whose.get(i, ()) for i in run)]
            alone = [run for run in mine if set().union(*(whose.get(i, set()) for i in run)) == {sid}]
            if alone:
                assert sid in ids, (sid, runs, self.made)


@pytest.mark.parametrize('seed', range(300))
def test_random_operation_sequences_keep_the_invariants(seed):
    desk = Desk(random.Random(seed))
    check(desk.plan, desk.n)
    for _ in range(30):
        desk.step()
        check(desk.plan, desk.n)
        desk.check_ids()
    # whatever happened, accepting settles everything: a guess then changes nothing at all
    desk.accept()
    assert split.guess(desk.plan, desk.read)['sheets'] == split.guess(desk.plan)['sheets'] == desk.plan['sheets']
    assert desk.accept() == 0 and not any(split.proposals(desk.plan))


@pytest.mark.parametrize('seed', range(2000))
def test_verifying_a_guess_changes_no_run_and_verified_runs_stay(seed):
    """Verify the guessed submissions in a random order: no step changes any run, so
    nothing verified moves, and nothing cascades past it (seeds 480, 1898: a student
    without page 2, a verified run with a stray in it)."""
    rng = random.Random(seed)
    sheets, read = pile(rng)
    plan = split.guess(split.new_plan(len(sheets), PAGES, anchor=ANCHOR), read)
    runs = split.runs(plan)
    for k in rng.sample(range(len(runs)), len(runs)):
        plan = split.confirm(plan, k)
        assert split.runs(plan) == runs
    assert all(split.verified(plan)) and split.guess(plan, read) == plan
