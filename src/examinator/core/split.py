"""Splitting a stack of scanned sheets into submissions. Pure: no PDFs, no model.

A source is `n` sheets, numbered from 0 as scanned. A plan is the pile sorted:
one ordered list of the source's sheets, each a record with its marks.

    {'length': L, 'pages': P, 'anchor': a, 'checked': bool, 'readings': {'i': position | None},
     'sheets': [{'i': sheet, 'start': {'confirmed': bool?, 'id': str?, 'proposed': {...}?}?,
                 'extra': True?, 'dropped': True?, 'missing': count?, 'turn': 1-3?}]}

A sheet with a `start` mark begins a submission: the run of sheets from it up
to the next start, less dropped sheets. The first record always carries one.
What a submission carries before it is accepted (a person confirmed it, its
id, `proposed` marks awaiting confirmation) hangs on its start. Every other
mark is the sheet's own: `extra` (kept, not a printed page), `dropped` (in no
submission), `missing` (that many printed pages not scanned come before it),
`turn` (quarter turns clockwise).

A submission maps the exam's `P` printed pages to its sheets (`lay_out`): its
sheets in order, less extra ones, with the missing pages, are printed pages 1,
2, ... P. Sheets beyond page P are extra too; pages beyond the last sheet are
missing. A start's `proposed` marks are a guess from the landmarks, kept apart
until it is confirmed.

A submission is expected to be `length` sheets (the exam's `pages`, unless a
person says each has more). A *reading* says which printed page (0-based) a
sheet is, from the exam's landmarks; it is `None` when nothing could be read.
`anchor` is the first page that carries a landmark.

The first proposal is the simple one: every `length` sheets. With a reader,
each expected boundary is checked where it is expected; when the sheet there
disagrees, its neighbours forward and backward are tried until one reads as a
first page. Nothing is read that is not needed. A submission one sheet short
(or long) is then searched by halves for the page that is missing (or the
sheet that is extra), and the mark is proposed when the readings around it agree.

A start a person confirmed is never moved by a proposal: later guesses are
computed from the last confirmed start before them. Guessing moves only start
marks (and drops stray sheets before the first submission); the order and
every sheet's own marks stay.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any, Callable, Mapping, Sequence

Reader = Callable[[int], 'int | None']

REACH = 3        # how many sheets either side of an expected boundary a proposal looks at
DOUBTS = {'short': "too few pages", 'long': "too many pages",
          'start': "first page not recognised", 'number': "unexpected page number",
          'missing': "missing page?", 'extra': "extra page?"}


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

def starts(plan: Mapping) -> list[int]:
    """The positions in the list that start a submission."""
    return [p for p, r in enumerate(plan['sheets']) if 'start' in r]


def entries(plan: Mapping) -> list[dict]:
    """Each submission's start mark (what it carries), in order."""
    return [r['start'] for r in plan['sheets'] if 'start' in r]


def span(plan: Mapping, k: int) -> list[int]:
    """Every sheet from submission `k`'s start up to the next, dropped ones too."""
    at = starts(plan) + [len(plan['sheets'])]
    return [r['i'] for r in plan['sheets'][at[entry_index(plan, k)]:at[k + 1]]]


def runs(plan: Mapping) -> list[list[int]]:
    """Each submission's sheets, in order."""
    out: list[list[int]] = []
    for r in plan['sheets']:
        if 'start' in r:
            out.append([])
        if out and not r.get('dropped'):
            out[-1].append(r['i'])
    return out


def dropped(plan: Mapping) -> list[int]:
    return [r['i'] for r in plan['sheets'] if r.get('dropped')]


def turns(plan: Mapping) -> dict[int, int]:
    return {r['i']: r.get('turn', 0) for r in plan['sheets']}


def readings(plan: Mapping) -> dict[int, int | None]:
    return {int(k): v for k, v in (plan.get('readings') or {}).items()}


def marks(plan: Mapping) -> tuple[set[int], list[int]]:
    """The extra sheets and the missing marks: a person's, and the starts' proposals."""
    extra, missing = set(), []
    for r in plan['sheets']:
        if r.get('extra'):
            extra.add(r['i'])
        missing += [r['i']] * r.get('missing', 0)
        got = (r.get('start') or {}).get('proposed') or {}
        extra.update(got.get('extra', ()))
        missing += got.get('missing', ())
    return extra, missing


def lay_out(sheets: Sequence[int], pages: int, extra=(), missing=()) -> list[tuple[int | None, int | None]]:
    """One submission in order, as (sheet, printed page): page None, an extra
    sheet; sheet None, a page not scanned (a mark, before the next sheet; or
    a page after the last sheet)."""
    out, p, before = [], 0, Counter(missing)
    for i in sheets:
        for _ in range(before[i]):
            if p < pages:
                p += 1
                out.append((None, p))
        if i in extra or p >= pages:
            out.append((i, None))
        else:
            p += 1
            out.append((i, p))
    return out + [(None, q) for q in range(p + 1, pages + 1)]


def layouts(plan: Mapping) -> list[list[tuple[int | None, int | None]]]:
    extra, missing = marks(plan)
    return [lay_out(p, plan.get('pages', plan['length']), extra, missing) for p in runs(plan)]


def written(lay: Sequence[tuple[int | None, int | None]]) -> list[int | None]:
    """A submission's PDF: the printed pages in order (None: missing), then the extra sheets."""
    return [i for i, p in lay if p is not None] + [i for i, p in lay if p is None]


def assess(lay: Sequence[tuple[int | None, int | None]], read: Mapping[int, int | None], *,
           anchor: int = 0, spare: int = 0, extra=(), proposed: Mapping | None = None) -> list[str]:
    """Doubts about one submission (see `DOUBTS`), from how its sheets lay out
    and whatever readings exist for them. A sheet that was never read is no
    doubt; sheets a person marked extra are none either, nor `spare` more
    (sheets per submission beyond the exam's pages)."""
    out = []
    if lay and lay[-1][0] is None:
        out.append('short')
    if sum(p is None and i not in extra for i, p in lay) > spare:
        out.append('long')
    first = next((i for i, p in lay if p == anchor + 1), None)
    if first is not None and first in read and read[first] != anchor:
        out.append('start')
    if any(i is not None and p is not None and p - 1 != anchor and read.get(i) is not None and read[i] != p - 1
           for i, p in lay):
        out.append('number')
    return out + sorted(proposed or ())


def doubts(plan: Mapping) -> list[list[str]]:
    read, spare = readings(plan), max(0, plan['length'] - plan.get('pages', plan['length']))
    extra = marks(plan)[0]
    return [assess(lay, read, anchor=plan.get('anchor', 0), spare=spare, extra=extra, proposed=e.get('proposed'))
            for e, lay in zip(entries(plan), layouts(plan))]


def entry_index(plan: Mapping, k: Any) -> int:
    """`k`, if the plan has a submission `k`; else ValueError."""
    if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < len(entries(plan)):
        raise ValueError("no such submission")
    return k


def cursor(plan: Mapping) -> int | None:
    """The first start still to be confirmed, or None when every one is."""
    return next((k for k, e in enumerate(entries(plan)) if not e.get('confirmed')), None)


# --- guessing (in place, on a plan already copied) ----------------------------------

def new_plan(n: int, length: int, *, pages: int | None = None, anchor: int = 0, read: Reader | None = None) -> dict:
    plan = {'length': length, 'pages': pages or length, 'anchor': anchor, 'checked': read is not None,
            'readings': {}, 'sheets': [{'i': i} for i in range(n)]}
    return refill(plan, read)


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


def _guess(plan: dict, a: int, b: int, fixed: bool, read: Reader) -> None:
    """Start marks guessed again for positions `a..b-1`, counting only sheets kept;
    `a` always starts one (`fixed`: a person confirmed it). Stray sheets the
    guess finds before the first submission are dropped. Starts a person
    confirmed, or that carry an id, stay where the guess puts a start; ids of
    the others go to the new guesses in order."""
    L, length, anchor = plan['sheets'], plan['length'], plan.get('anchor', 0)
    kept = [p for p in range(a, b) if not L[p].get('dropped')]
    found = propose(len(kept), length, lambda j: read(L[kept[j]]['i']), anchor=anchor, fixed_first=fixed)
    if b < len(L):
        # The segment ends at a confirmed start: a last guess that would leave too short a
        # tail, with no reading behind it, is no guess (the one before it runs long instead).
        while len(found) > 1 and len(kept) - found[-1] < length and (
                found[-1] + anchor >= len(kept) or read(L[kept[found[-1] + anchor]]['i']) != anchor):
            found.pop()
    if found and found[0]:
        for p in kept[:found[0]]:
            L[p]['dropped'] = True
    old = {p: L[p].pop('start') for p in range(a, b) if 'start' in L[p]}
    ids = [e['id'] for e in old.values() if e.get('id')]
    new = []
    for p in [a] + [kept[j] for j in found[1:]]:
        e = old.get(p)
        if e is None or not (e.get('confirmed') or e.get('id')):
            e = {}
        elif e.get('id') in ids:
            ids.remove(e['id'])
        L[p]['start'] = e
        new.append(e)
    for e in new:
        if not e.get('id') and ids:
            e['id'] = ids.pop(0)


def _segments(plan: Mapping) -> list[tuple[int, int, bool]]:
    """The stretches between confirmed starts: (first position, end, its start confirmed)."""
    L = plan['sheets']
    fixed = [p for p in starts(plan) if L[p]['start'].get('confirmed')]
    cuts = sorted({0, *fixed})
    return [(a, b, a in fixed) for a, b in zip(cuts, cuts[1:] + [len(L)]) if a < b]


def refill(plan: Mapping, read: Reader | None = None) -> dict:
    """Guess every unconfirmed start again, between the confirmed ones."""
    plan = copy.deepcopy(dict(plan))
    get = _reader(plan, read)
    for a, b, fixed in _segments(plan):
        _guess(plan, a, b, fixed, get)
    return _propose(plan, get)


def _after(plan: dict, k: int) -> dict:
    """Guess again the unconfirmed starts after submission `k`, up to the next
    confirmed one, from the readings the plan holds (an edit reads nothing new)."""
    get, at, L = _reader(plan), starts(plan), plan['sheets']
    end = next((p for p in at[k + 1:] if L[p]['start'].get('confirmed')), len(L))
    _guess(plan, at[k], end, True, get)
    return _propose(plan, get)


def _gap(sheets: Sequence[int], pages: int, read: Reader) -> dict | None:
    """In a submission one sheet short (or long), the page missing (or the sheet
    extra), found by halves: the first sheet that does not read as its position;
    proposed only if its neighbours' readings agree, and not where the default
    already has it (at the end)."""
    d = len(sheets) - pages
    if d not in (-1, 1):
        return None
    lo, hi = 0, len(sheets)
    while lo < hi:
        mid = (lo + hi) // 2
        if read(sheets[mid]) == mid:
            lo = mid + 1
        else:
            hi = mid
    x = lo
    if x >= len(sheets) - max(d, 0) or (x and read(sheets[x - 1]) != x - 1):
        return None
    if d < 0:
        return {'missing': [sheets[x]]} if read(sheets[x]) == x + 1 else None
    return {'extra': [sheets[x]]} if read(sheets[x + 1]) == x else None


def _propose(plan: dict, read: Reader) -> dict:
    """Propose marks for every unconfirmed start whose sheets carry none of a person's."""
    mine = {r['i'] for r in plan['sheets'] if r.get('extra') or r.get('missing')}
    for e, sheets in zip(entries(plan), runs(plan)):
        e.pop('proposed', None)
        if not e.get('confirmed') and not mine.intersection(sheets):
            got = _gap(sheets, plan.get('pages', plan['length']), read)
            if got:
                e['proposed'] = got
    return plan


def settle(plan: dict, k: int) -> None:
    """Submission `k`'s proposed marks become a person's."""
    got = entries(plan)[k].pop('proposed', None) or {}
    at = {r['i']: r for r in plan['sheets']}
    for i in got.get('extra', ()):
        at[i]['extra'] = True
    for i in got.get('missing', ()):
        at[i]['missing'] = at[i].get('missing', 0) + 1


# --- a person's edits (each returns a new plan) --------------------------------------

def _pos(plan: Mapping, sheet: Any) -> int:
    p = next((p for p, r in enumerate(plan['sheets']) if r['i'] == sheet), None)
    if p is None or isinstance(sheet, bool):
        raise ValueError("no such page")
    return p


def _owner(plan: Mapping, p: int) -> int:
    """The submission whose run holds position `p`."""
    return sum(s <= p for s in starts(plan)) - 1


def confirm(plan: Mapping, k: int) -> dict:
    """A person says submission `k` starts where it does; later guesses follow from it."""
    plan = copy.deepcopy(dict(plan))
    entries(plan)[entry_index(plan, k)]['confirmed'] = True
    settle(plan, k)
    return _after(plan, k)


def move(plan: Mapping, k: int, delta: int) -> dict:
    """Submission `k`'s first page `delta` kept sheets later (negative: earlier).
    Its start is then a guess again; the guesses after it follow from it. The
    first submission always starts the list: its first page moves later by
    dropping that page, and earlier by putting back the dropped one before it."""
    plan = copy.deepcopy(dict(plan))
    L, at = plan['sheets'], starts(plan) + [len(plan['sheets'])]
    entry_index(plan, k)
    kept = [p for p, r in enumerate(L) if not r.get('dropped')]
    if k == 0:
        mine = [p for p in kept if p < at[1]]
        if delta > 0 and len(mine) < 2:
            raise ValueError("that would empty the submission")
        p = mine[0] if delta > 0 else (mine[0] if mine else at[1]) - 1
        if p < 0:
            raise ValueError("no more pages that way")
        if delta > 0:
            L[p]['dropped'] = True
        else:
            L[p].pop('dropped', None)
    else:
        j = next((j for j, p in enumerate(kept) if p >= at[k]), len(kept)) + delta
        end = next((p for p in at[k + 1:-1] if L[p]['start'].get('confirmed')), len(L))
        if not 0 <= j < len(kept):
            raise ValueError("no more pages that way")
        new = kept[j]
        if not at[k - 1] < new < end:
            raise ValueError("that would empty a neighbouring submission")
        e = L[at[k]].pop('start')
        for p in range(at[k] + 1, new + 1):     # unconfirmed guesses passed over are guessed again
            L[p].pop('start', None)
        L[new]['start'] = e
    e = entries(plan)[k]
    e.pop('proposed', None)
    e['confirmed'] = False
    return _after(plan, k)


def cut(plan: Mapping, sheet: int) -> dict:
    """A person says a submission starts at `sheet` (confirmed)."""
    plan = copy.deepcopy(dict(plan))
    p = _pos(plan, sheet)
    if 'start' not in plan['sheets'][p]:
        plan['sheets'][p]['start'] = {'confirmed': True}
    return confirm(plan, _owner(plan, p))


def join(plan: Mapping, k: int) -> dict:
    """Submission `k` and the one after it become one."""
    plan = copy.deepcopy(dict(plan))
    if not 0 <= entry_index(plan, k) < len(entries(plan)) - 1:
        raise ValueError("nothing after it to join")
    gone = plan['sheets'][starts(plan)[k + 1]].pop('start')
    e = entries(plan)[k]
    e.pop('proposed', None)       # guessed for its old sheets
    if not e.get('id') and gone.get('id'):
        e['id'] = gone['id']
    return plan


def drop(plan: Mapping, sheet: int) -> dict:
    """Set a sheet aside (a blank or stray page). Guesses after it follow."""
    return _set_dropped(plan, sheet, True)


def restore(plan: Mapping, sheet: int) -> dict:
    return _set_dropped(plan, sheet, False)


def _set_dropped(plan: Mapping, sheet: int, on: bool) -> dict:
    plan = copy.deepcopy(dict(plan))
    p = _pos(plan, sheet)
    if on:
        plan['sheets'][p]['dropped'] = True
    else:
        plan['sheets'][p].pop('dropped', None)
    return _after(plan, _owner(plan, p))


def _holder(plan: dict, sheet: Any) -> dict:
    """The record of a sheet in a submission (its proposals settled: a person is
    marking it), else ValueError."""
    p = _pos(plan, sheet)
    if plan['sheets'][p].get('dropped'):
        raise ValueError("the page is in no submission")
    settle(plan, _owner(plan, p))
    return plan['sheets'][p]


def toggle_extra(plan: Mapping, sheet: int) -> dict:
    """Mark a sheet extra (kept, not a printed page), or count it again."""
    plan = copy.deepcopy(dict(plan))
    r = _holder(plan, sheet)
    if r.pop('extra', None) is None:
        r['extra'] = True
    return plan


def add_missing(plan: Mapping, sheet: int) -> dict:
    """A printed page that was not scanned comes before `sheet`."""
    plan = copy.deepcopy(dict(plan))
    r = _holder(plan, sheet)
    r['missing'] = r.get('missing', 0) + 1
    return plan


def remove_missing(plan: Mapping, sheet: int) -> dict:
    plan = copy.deepcopy(dict(plan))
    r = _holder(plan, sheet)
    if not r.get('missing'):
        raise ValueError("no missing page marked there")
    r['missing'] -= 1
    if not r['missing']:
        del r['missing']
    return plan


def shift(plan: Mapping, sheet: int, delta: int) -> dict:
    """Move a sheet one place (`delta` -1 or 1) in the list. Start marks stay
    where they are, so a sheet swapped with its neighbour stays in its
    submission; at a boundary it crosses into the neighbouring submission
    instead (the boundary moves past it: its first or last page). A move that
    would empty a submission is refused."""
    plan = copy.deepcopy(dict(plan))
    L = plan['sheets']
    p = _pos(plan, sheet)
    q = p + delta
    if not 0 <= q < len(L):
        raise ValueError("no more pages that way")
    a = min(p, q)
    for x in (p, q):
        settle(plan, _owner(plan, x))
    if 'start' in L[a + 1]:
        to = p if delta > 0 else p + 1
        if to >= len(L) or 'start' in L[to]:
            raise ValueError("that would empty its submission")
        L[to]['start'] = L[a + 1].pop('start')
    else:
        x, y = L[a], L[a + 1]
        if 'start' in x:
            y['start'] = x.pop('start')
        L[a], L[a + 1] = y, x
    return plan


def turn(plan: Mapping, sheets) -> dict:
    """These sheets a quarter turn more, each from its own turn; their readings are stale."""
    plan = copy.deepcopy(dict(plan))
    want = set(sheets)
    for r in plan['sheets']:
        if r['i'] in want:
            want.discard(r['i'])
            r['turn'] = (r.get('turn', 0) + 1) % 4
            if not r['turn']:
                del r['turn']
            (plan.get('readings') or {}).pop(str(r['i']), None)
    if want:
        raise ValueError("no such page")
    return plan
