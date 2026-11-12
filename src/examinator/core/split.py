"""Splitting a stack of scanned sheets into submissions. Pure: no PDFs, no model.

A source is `n` sheets, numbered from 0. A plan is a list of entries, each the
sheet where one submission starts, plus the sheets set aside (`dropped`). An
entry's pages are its start up to the next entry's start, less dropped sheets;
sheets before the first start belong to no submission.

    {'length': L, 'anchor': a, 'checked': bool,
     'entries': [{'start': i, 'confirmed': bool, 'id': str | None, 'orient': {...}?}],
     'dropped': [i, ...], 'readings': {'i': position | None}, 'orient': {...}}

A submission is expected to be `length` sheets. A *reading* says which position
(0-based, within its submission) a sheet is, from the exam's landmarks; it is
`None` when nothing could be read. `anchor` is the first position that carries
a landmark (after any cover sheets).

The first proposal is the simple one: every `length` sheets. With a reader,
each expected boundary is checked where it is expected; when the sheet there
disagrees, its neighbours forward and backward are tried until one reads as a
first page. Nothing is read that is not needed.

A start a person confirmed is never moved by a proposal: later guesses are
computed from the last confirmed start before them.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Iterable, Mapping, Sequence

Reader = Callable[[int], 'int | None']

REACH = 3        # how many sheets either side of an expected boundary a proposal looks at
DOUBTS = {'short': "too few pages", 'long': "too many pages",
          'start': "first page not recognised", 'number': "unexpected page number"}


# --- proposing ------------------------------------------------------------------

def every(n: int, length: int) -> list[int]:
    """Starts of the plain proposal: every `length` sheets."""
    if length < 1:
        raise ValueError("length must be at least 1")
    return list(range(0, n, length))


def propose(n: int, length: int, read: Reader | None = None, *, anchor: int = 0, reach: int = REACH,
            fixed_first: bool = False) -> list[int]:
    """Starts of the submissions in sheets `0..n-1`.

    `read(i)` is the position of sheet `i` within its submission, or None. With
    `fixed_first`, sheet 0 starts a submission; otherwise the first start is
    looked for too (stray sheets may come before it)."""
    if length < 1:
        raise ValueError("length must be at least 1")
    if n <= 0:
        return []
    if read is None:
        return every(n, length)
    seen: dict[int, int | None] = {}

    def at(i: int) -> int | None:
        if not 0 <= i < n:
            return None
        if i not in seen:
            seen[i] = read(i)
        return seen[i]

    def starts(b: int) -> bool:
        return 0 <= b + anchor < n and at(b + anchor) == anchor

    def find(e: int, lo: int) -> int | None:
        """The start nearest the expected sheet `e`, after sheet `lo`."""
        if starts(e):
            return e
        candidates = []
        r = at(e + anchor)
        if r is not None:          # a page number elsewhere: the submission that sheet belongs to
            b = e + anchor - r
            candidates.append(b if b > lo else b + length)
        for d in range(1, reach + 1):
            candidates += [e - d, e + d]
        for b in dict.fromkeys(candidates):
            if lo < b < n and b != e and starts(b):
                return b
        return None

    out = []
    s = 0 if fixed_first else (find(0, -1) or 0)
    while s < n:
        out.append(s)
        e = s + length
        if e >= n:
            break
        b = find(e, s)
        s = e if b is None else b
    return out


# --- reading a plan --------------------------------------------------------------

def kept(n: int, dropped: Iterable[int]) -> list[int]:
    gone = set(dropped)
    return [i for i in range(n) if i not in gone]


def pages(plan: Mapping, n: int) -> list[list[int]]:
    """Each entry's sheets, in order."""
    gone = set(plan.get('dropped') or ())
    starts = [e['start'] for e in plan['entries']] + [n]
    return [[i for i in range(a, b) if i not in gone] for a, b in zip(starts, starts[1:])]


def loose(plan: Mapping, n: int) -> list[int]:
    """Sheets in no submission: dropped, or before the first start."""
    first = plan['entries'][0]['start'] if plan['entries'] else n
    return sorted(set(plan.get('dropped') or ()) | set(range(first)))


def readings(plan: Mapping) -> dict[int, int | None]:
    return {int(k): v for k, v in (plan.get('readings') or {}).items()}


def assess(sheets: Sequence[int], length: int, read: Mapping[int, int | None], anchor: int = 0) -> list[str]:
    """Doubts about one submission (see `DOUBTS`), from its length and whatever
    readings exist for its sheets. A sheet that was never read is no doubt."""
    out = []
    if len(sheets) < length:
        out.append('short')
    elif len(sheets) > length:
        out.append('long')
    if anchor < len(sheets) and sheets[anchor] in read and read[sheets[anchor]] != anchor:
        out.append('start')
    if any(read.get(i) is not None and read[i] != j for j, i in enumerate(sheets) if j != anchor):
        out.append('number')
    return out


def doubts(plan: Mapping, n: int) -> list[list[str]]:
    read = readings(plan)
    return [assess(p, plan['length'], read, plan.get('anchor', 0)) for p in pages(plan, n)]


# --- building and editing plans (each returns a new plan) --------------------------

def new_plan(n: int, length: int, *, anchor: int = 0, read: Reader | None = None) -> dict:
    plan = {'length': length, 'anchor': anchor, 'checked': read is not None, 'entries': [],
            'dropped': [], 'readings': {}, 'orient': {'turn': 0, 'flip': False}}
    return refill(plan, n, read)


def _reader(plan: dict, read: Reader | None = None) -> Reader:
    """Stored readings first; `read` (if any) for the rest, recorded in the plan."""
    store = plan.setdefault('readings', {})

    def get(i: int) -> int | None:
        key = str(i)
        if key in store:
            return store[key]
        if read is None:
            return None
        store[key] = value = read(i)
        return value
    return get


def _fill(plan: dict, n: int, first: int, end: int, fixed: bool, read: Reader) -> list[int]:
    """Starts proposed for sheets `first..end-1`, counting only sheets kept."""
    sheets = [i for i in kept(n, plan.get('dropped') or ()) if first <= i < end]
    found = propose(len(sheets), plan['length'], lambda j: read(sheets[j]),
                    anchor=plan.get('anchor', 0), fixed_first=fixed)
    if end < n:
        # The segment ends at a confirmed start: a last guess that would leave too short a
        # tail, with no reading behind it, is no guess (the one before it runs long instead).
        a = plan.get('anchor', 0)
        while len(found) > 1 and len(sheets) - found[-1] < plan['length'] and (
                found[-1] + a >= len(sheets) or read(sheets[found[-1] + a]) != a):
            found.pop()
    out = [sheets[j] for j in found]
    if fixed and sheets and first not in out and out:
        out[0] = first          # the fixed start keeps its sheet even when that sheet is dropped
    return out


def _replace(plan: dict, lo: int, hi: int, starts: list[int]) -> dict:
    """Replace entries `lo..hi-1` by unconfirmed ones at `starts`, keeping their ids in order."""
    old = plan['entries'][lo:hi]
    ids = [e.get('id') for e in old if e.get('id')]
    keep = {e['start']: e for e in old}
    new = []
    for s in starts:
        e = keep.get(s)
        if e is not None and (e.get('confirmed') or e.get('id')):
            new.append(e)
            if e.get('id') in ids:
                ids.remove(e['id'])
        else:
            new.append({'start': s, 'confirmed': False, 'id': None, **({'orient': e['orient']} if e and 'orient' in e else {})})
    for e in new:
        if e.get('id') is None and ids:
            e['id'] = ids.pop(0)
    plan['entries'][lo:hi] = new
    return plan


def refill(plan: Mapping, n: int, read: Reader | None = None) -> dict:
    """Guess every unconfirmed start again, between the confirmed ones."""
    plan = copy.deepcopy(dict(plan))
    get = _reader(plan, read)
    entries = plan['entries']
    fixed = [k for k, e in enumerate(entries) if e.get('confirmed')]
    # segments: (first sheet, end sheet, starts fixed?, entries lo..hi), replaced last to first
    segs = [(0, entries[fixed[0]]['start'] if fixed else n, False, 0, fixed[0] if fixed else len(entries))]
    for j, k in enumerate(fixed):
        hi = fixed[j + 1] if j + 1 < len(fixed) else len(entries)
        segs.append((entries[k]['start'], entries[hi]['start'] if hi < len(entries) else n, True, k, hi))
    for first, end, is_fixed, lo, hi in reversed(segs):
        if first >= end:
            continue
        starts = _fill(plan, n, first, end, is_fixed, get)
        _replace(plan, lo, hi, starts or ([first] if is_fixed else []))
    return plan


def reguess_after(plan: dict, k: int, n: int) -> dict:
    """Guess again the unconfirmed starts after entry `k`, up to the next confirmed
    one, from the readings the plan holds (an edit reads nothing new)."""
    get = _reader(plan)
    entries = plan['entries']
    hi = next((j for j in range(k + 1, len(entries)) if entries[j].get('confirmed')), len(entries))
    end = entries[hi]['start'] if hi < len(entries) else n
    starts = _fill(plan, n, entries[k]['start'], end, True, get)
    return _replace(plan, k, hi, starts or [entries[k]['start']])


def _at(plan: Mapping, k: Any) -> int:
    if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < len(plan['entries']):
        raise ValueError("no such submission")
    return k


def confirm(plan: Mapping, k: int, n: int) -> dict:
    """A person says entry `k` starts where it does; later guesses follow from it."""
    plan = copy.deepcopy(dict(plan))
    plan['entries'][_at(plan, k)]['confirmed'] = True
    return reguess_after(plan, k, n)


def move(plan: Mapping, k: int, delta: int, n: int) -> dict:
    """Move entry `k`'s start `delta` kept sheets later (negative: earlier). The
    start is then a guess again; the guesses after it follow from it."""
    plan = copy.deepcopy(dict(plan))
    entries = plan['entries']
    _at(plan, k)
    sheets = kept(n, plan.get('dropped') or ())
    cur = entries[k]['start']
    pos = next((j for j, i in enumerate(sheets) if i >= cur), len(sheets))
    j = pos + delta
    if not 0 <= j < len(sheets):
        raise ValueError("no more pages that way")
    new = sheets[j]
    lo = entries[k - 1]['start'] if k else -1
    nxt = next((e['start'] for e in entries[k + 1:] if e.get('confirmed')), n)
    if not lo < new < nxt:
        raise ValueError("that would empty a neighbouring submission")
    entries[k]['start'] = new
    entries[k]['confirmed'] = False
    # unconfirmed guesses the move passed over are dropped; they are guessed again below
    plan['entries'] = entries[:k + 1] + [e for e in entries[k + 1:] if e['start'] > new or e.get('confirmed')]
    return reguess_after(plan, k, n)


def cut(plan: Mapping, sheet: int, n: int) -> dict:
    """A person says a submission starts at `sheet` (confirmed)."""
    plan = copy.deepcopy(dict(plan))
    if not 0 <= sheet < n:
        raise ValueError("no such page")
    entries = plan['entries']
    if any(e['start'] == sheet for e in entries):
        k = next(j for j, e in enumerate(entries) if e['start'] == sheet)
        return confirm(plan, k, n)
    k = sum(e['start'] < sheet for e in entries)
    entries.insert(k, {'start': sheet, 'confirmed': True, 'id': None})
    return reguess_after(plan, k, n)


def join(plan: Mapping, k: int) -> dict:
    """Entry `k` and the one after it become one submission."""
    plan = copy.deepcopy(dict(plan))
    if not 0 <= _at(plan, k) < len(plan['entries']) - 1:
        raise ValueError("nothing after it to join")
    gone = plan['entries'].pop(k + 1)
    if not plan['entries'][k].get('id'):
        plan['entries'][k]['id'] = gone.get('id')
    return plan


def drop(plan: Mapping, sheet: int, n: int) -> dict:
    """Set a sheet aside (a blank or stray page). Guesses after it follow."""
    plan = copy.deepcopy(dict(plan))
    if not 0 <= sheet < n:
        raise ValueError("no such page")
    plan['dropped'] = sorted(set(plan.get('dropped') or ()) | {sheet})
    return _after_sheet(plan, sheet, n)


def restore(plan: Mapping, sheet: int, n: int) -> dict:
    plan = copy.deepcopy(dict(plan))
    plan['dropped'] = [i for i in plan.get('dropped') or () if i != sheet]
    return _after_sheet(plan, sheet, n)


def _after_sheet(plan: dict, sheet: int, n: int) -> dict:
    k = sum(e['start'] <= sheet for e in plan['entries']) - 1
    return reguess_after(plan, k, n) if k >= 0 else plan


def cursor(plan: Mapping) -> int | None:
    """The first start still to be confirmed, or None when every one is."""
    return next((k for k, e in enumerate(plan['entries']) if not e.get('confirmed')), None)
