"""Splitting a stack of scanned sheets into submissions. Pure: no PDFs, no model.

A source is `n` sheets, numbered from 0 as scanned. A plan is the pile sorted:
one ordered list of the source's sheets, each a record with its marks.

    {'length': L, 'anchor': a, 'readings': {'i': position | None},
     'sheets': [{'i': sheet, 'start': True?, 'said': True?, 'verified': True?, 'dropped': True?,
                 'extra': bool?, 'missing': count?, 'turn': 1-3?}]}

(`intake` adds two notes of its own: `failed`, how many sheets the last reading
left unread because the model failed; `undone`, in a plan brought back by undo,
the revision it came from.) The plan says nothing about what has been written
(that is the store's `made`) nor about the exam's page count (an argument where
it matters).

**A submission is its kept sheets.** A sheet with a `start` mark begins a
submission: the run of kept sheets from it up to the next start. Dropped sheets
belong to no submission. Every operation ends by normalising (`_normal`): a
start sits only on a kept sheet (a dropped start, with what a person said of
it, is handed to the next kept sheet of its run, or the run ceases), and the
first kept sheet starts one; so there is no empty submission. The other marks
are the sheet's own: `extra` (kept, not a printed page), `missing` (that many
printed pages not scanned come before it), `turn` (quarter turns clockwise).

**A mark is a guess or a person's statement.** `said` on a sheet means a person
has said what it is: whether it starts a submission and whether it is dropped.
A guess (`guess`: the first one, "guess again", reading pages, and the re-guess
that ends every edit) clears the start and dropped marks of every sheet nobody
has spoken for and guesses them again; it never changes a sheet that is `said`.
Each person's edit says exactly what the person did, on the sheets it names:

    confirm  submission k is its sheets (below)
    move     the boundary before k is at the sheet it moves to, not at the one it leaves
             (the first submission: every sheet before it is in none)
    cut      a submission starts at the sheet
    join     none of the two submissions' sheets but the first starts one
    drop, restore    the sheet is in no submission, or is in the one whose run it is in
    shift    within a submission, nothing (the sheets swap places, marks staying in
             place); across a boundary, the sheet and the one it passes are where they
             now are
    extra, missing   the submission's proposals become the person's, then the mark

**A verified submission is its sheets.** `verified` on a first sheet means a
person looked at that submission as it is (`look`: its kept sheets in order,
and which are extra and before which printed pages are missing) and said it
is these sheets: its first sheet starts one, none of its other sheets does,
the dropped ones are dropped, and the next kept sheet starts one. Verifying
(`confirm`, V) is the only edit that sets it; accepting sets it on every
submission. A verified submission is kept whole after every edit (`_whole`),
so a guess never changes it and a statement that agrees with the guess
changes nothing. An edit that changes what a person looked at (its sheets,
their order, its extra or missing marks) takes `verified` from it
(`_unverify`): the person verifies it again. Its sheets' turns are not part of
it (a turn changes how a sheet is shown, not which page it is). A boundary a
person moves, a cut or a join is theirs but makes no submission verified.

Guessing works in stretches, each from the first kept sheet or a start a person
said up to the next one: the starts in a stretch are those `propose` finds from
its beginning (over the rest of the pile, so what it finds before the stretch
ends does not depend on where it ends). So a statement re-guesses only the
sheets after it nobody spoke for, up to the next start a person said: a
correction cascades forward and never past a verified submission.

A submission maps `pages` printed pages to its sheets (`lay_out`): its sheets in
order, less extra ones, with the missing pages, are printed pages 1, 2, ...
Sheets beyond the last page are extra too; pages beyond the last sheet are
missing. *Proposals* are guessed extra and missing marks, computed (never
stored) from the stored readings, for a submission with no extra or missing
mark of a person's and a sheet nobody has spoken for. They count in the
layout; when a person marks the submission, verifies it, or accepts, they
become that person's (`settle`).

A submission is expected to be `length` sheets. A *reading* says which printed
page (0-based) a sheet is, from the exam's landmarks; `None` when nothing could
be read. `anchor` is the first page that carries a landmark. The guess is
every `length` kept sheets; with readings, each expected boundary is checked
where it is expected, and when the sheet there disagrees its neighbours are
tried. A submission one sheet short (or long) is searched by halves for the
page that is missing (or the sheet that is extra).
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

def propose(n: int, length: int, read: Reader | None = None, *, anchor: int = 0,
            fixed_first: bool = False, until: int | None = None) -> list[int]:
    """Starts of the submissions in sheets `0..n-1` (those before `until`).

    `read(i)` is the position of sheet `i` within its submission, or None. With
    `fixed_first`, sheet 0 starts a submission; otherwise the first start is
    looked for too (stray sheets may come before it). Each start is found from
    the one before, so the starts before `until` are the same whatever `until` is."""
    if length < 1:
        raise ValueError("length must be at least 1")
    if n <= 0:
        return []
    end = n if until is None else min(n, until)
    if read is None:
        return list(range(0, end, length))
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
        for d in range(1, REACH + 1):
            candidates += [e - d, e + d]
        for b in dict.fromkeys(candidates):
            if lo < b < n and b != e and starts(b):
                return b
        return None

    out = []
    s = 0 if fixed_first else (find(0, -1) or 0)
    while s < end:
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
    return [p for p, r in enumerate(plan['sheets']) if r.get('start')]


def runs(plan: Mapping) -> list[list[int]]:
    """Each submission's sheets (its kept sheets), in order."""
    out: list[list[int]] = []
    for r in plan['sheets']:
        if r.get('start'):
            out.append([])
        if out and not r.get('dropped'):
            out[-1].append(r['i'])
    return out


def verified(plan: Mapping) -> list[bool]:
    """Per submission: a person verified it (it is its sheets)."""
    return [bool(plan['sheets'][p].get('verified')) for p in starts(plan)]


def looks(plan: Mapping) -> list[tuple]:
    """Per submission, what a person who verifies it looks at: its kept sheets in
    order, the extra ones, and the missing marks (proposals included)."""
    extra, missing = marks(plan)
    return [(tuple(run), tuple(i for i in run if i in extra), tuple(i for i in missing if i in run))
            for run in runs(plan)]


def cursor(plan: Mapping) -> int | None:
    """The first submission not verified, or None when every one is."""
    return next((k for k, v in enumerate(verified(plan)) if not v), None)


def span(plan: Mapping, k: int) -> list[int]:
    """Every sheet from submission `k`'s start up to the next, dropped ones too."""
    at = starts(plan) + [len(plan['sheets'])]
    _start(plan, k)
    return [r['i'] for r in plan['sheets'][at[k]:at[k + 1]]]


def dropped(plan: Mapping) -> list[int]:
    return [r['i'] for r in plan['sheets'] if r.get('dropped')]


def turns(plan: Mapping) -> dict[int, int]:
    return {r['i']: r.get('turn', 0) for r in plan['sheets']}


def readings(plan: Mapping) -> dict[int, int | None]:
    return {int(k): v for k, v in (plan.get('readings') or {}).items()}


def _stored(plan: Mapping) -> Reader:
    got = plan.get('readings') or {}
    return lambda i: got.get(str(i))


def proposals(plan: Mapping) -> list[dict]:
    """Per submission, the guessed marks (`{'extra': [sheet]}` or `{'missing': [sheet]}`),
    from the stored readings: for one with no extra or missing mark of a person's
    and a sheet nobody has spoken for."""
    settled = {r['i'] for r in plan['sheets'] if r.get('said') or 'extra' in r or 'missing' in r}
    marked = {r['i'] for r in plan['sheets'] if 'extra' in r or 'missing' in r}
    read = _stored(plan)
    return [{} if marked.intersection(sheets) or settled.issuperset(sheets)
            else _gap(sheets, plan['length'], read) or {} for sheets in runs(plan)]


def marks(plan: Mapping) -> tuple[set[int], list[int]]:
    """The extra sheets and the missing marks: a person's, and the proposals."""
    extra, missing = set(), []
    for r in plan['sheets']:
        if r.get('extra'):
            extra.add(r['i'])
        missing += [r['i']] * r.get('missing', 0)
    for got in proposals(plan):
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


def layouts(plan: Mapping, pages: int) -> list[list[tuple[int | None, int | None]]]:
    extra, missing = marks(plan)
    return [lay_out(p, pages, extra, missing) for p in runs(plan)]


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


def doubts(plan: Mapping, pages: int) -> list[list[str]]:
    read, spare = readings(plan), max(0, plan['length'] - pages)
    extra = marks(plan)[0]
    return [assess(lay, read, anchor=plan.get('anchor', 0), spare=spare, extra=extra, proposed=got)
            for got, lay in zip(proposals(plan), layouts(plan, pages))]


# --- what was written, and which submission is which -----------------------------------

def link(runs: Sequence[Sequence[int]], made: Mapping[str, Sequence[int | None]]) -> tuple[list[str | None], list[str]]:
    """Which written submission each run is, and the written ones no run is.

    `made` maps a written submission's id to the sheets it was written with.
    A written submission and a run that share a sheet are a candidate pair,
    ranked by where the first shared sheet comes in the run, then where it came
    in what was written, then the id; pairs are linked best first, each
    submission and each run at most once. So a run is the written submission
    its first sheet belonged to, unless that one is already a run's that starts
    with it earlier; a join keeps the first. A run linked to none is new; a
    written submission linked to no run is to be removed."""
    where = {i: (k, x) for k, run in enumerate(runs) for x, i in enumerate(run)}
    best: dict[tuple[str, int], tuple[int, int]] = {}
    for sid, sheets in made.items():
        for n, i in enumerate(sheets):
            if i in where:
                k, x = where[i]
                best[sid, k] = min(best.get((sid, k), (x, n)), (x, n))
    ids: list[str | None] = [None] * len(runs)
    taken = set()
    for _, _, sid, k in sorted((x, n, sid, k) for (sid, k), (x, n) in best.items()):
        if sid not in taken and ids[k] is None:
            ids[k] = sid
            taken.add(sid)
    return ids, sorted(set(made) - taken)


# --- guessing (in place, on a plan already copied) ----------------------------------

def new_plan(n: int, length: int, *, anchor: int = 0) -> dict:
    """The pile as scanned, guessed every `length` sheets."""
    return guess({'length': length, 'anchor': anchor, 'readings': {}, 'sheets': [{'i': i} for i in range(n)]})


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


def guess(plan: Mapping, read: Reader | None = None) -> dict:
    """Guess again every start and drop nobody has spoken for, from the stored
    readings and `read` (recorded). Sheets that are `said` are never changed.

    Before the first submission, sheets nobody spoke for that the guess finds to
    be strays are dropped. After that the kept sheets are guessed in stretches,
    each from the first kept sheet or a start a person said up to the next: the
    starts `propose` finds from the stretch's beginning, never on a sheet a
    person said does not start one."""
    plan = copy.deepcopy(dict(plan))
    L, length, anchor = plan['sheets'], plan['length'], plan.get('anchor', 0)
    get = _reader(plan, read)
    for r in L:
        if not r.get('said'):
            r.pop('start', None)
            r.pop('dropped', None)
    kept = [p for p, r in enumerate(L) if not r.get('dropped')]
    # strays before the first submission are dropped, up to the first sheet a person spoke
    # for, and only when the sheet then first may start one
    first = propose(len(kept), length, lambda j: get(L[kept[j]]['i']), anchor=anchor)[:1]
    m = first[0] if first else 0
    m = next((x for x in range(m) if L[kept[x]].get('said')), m)
    if m < len(kept) and L[kept[m]].get('said') and not L[kept[m]].get('start'):
        m = 0
    for p in kept[:m]:
        L[p]['dropped'] = True
    kept = kept[m:]
    begins = [x for x, p in enumerate(kept) if x == 0 or L[p].get('said') and L[p].get('start')]
    for a, b in zip(begins, begins[1:] + [len(kept)]):
        mine = kept[a:]
        for j in propose(len(mine), length, lambda j: get(L[mine[j]]['i']), anchor=anchor,
                         fixed_first=True, until=b - a)[1:]:
            if not L[mine[j]].get('said'):
                L[mine[j]]['start'] = True
    _normal(plan)
    if read is not None:            # read what the proposals need, so they hold without the model
        for sheets in runs(plan):
            _gap(sheets, length, get)
    return plan


def _normal(plan: dict) -> dict:
    """A start sits only on a kept sheet: a dropped one hands its start to the next
    kept sheet of its run, if any (else the run is gone); the first kept sheet
    starts a submission; only a first sheet is verified."""
    carry = False
    for r in plan['sheets']:
        if r.get('start') and r.get('dropped'):
            carry = True
            del r['start']
        elif r.get('start'):
            carry = False
        elif carry and not r.get('dropped'):
            r['start'], carry = True, False
        if not r.get('start'):
            r.pop('verified', None)
    first = next((r for r in plan['sheets'] if not r.get('dropped')), None)
    if first is not None:
        first['start'] = True
    return plan


def _whole(plan: dict) -> dict:
    """Every verified submission is its sheets: a person said its first sheet starts
    one, none of the others does (the dropped ones dropped), and the next kept sheet
    starts one."""
    at = starts(plan) + [len(plan['sheets'])]
    for k, v in enumerate(verified(plan)):
        if v:
            _say(plan, at[k], start=True)
            _say(plan, *range(at[k] + 1, at[k + 1]), start=False)
            if at[k + 1] < len(plan['sheets']):
                _say(plan, at[k + 1], start=True)
    return plan


def _seen(plan: Mapping) -> dict[int, tuple]:
    """Each verified submission's first sheet, and how it looked."""
    return {plan['sheets'][p]['i']: look for p, look, v in zip(starts(plan), looks(plan), verified(plan)) if v}


def _unverify(plan: dict, before: Mapping[int, tuple]) -> dict:
    """`verified` stays only where the submission looks as it did (`before`: `_seen`
    before the edit); verifying is the one edit that sets it anew."""
    for p, look in zip(starts(plan), looks(plan)):
        r = plan['sheets'][p]
        if r['i'] in before and before[r['i']] != look:
            r.pop('verified', None)
    return plan


def _gap(sheets: Sequence[int], length: int, read: Reader) -> dict | None:
    """In a submission one sheet short of `length` (or long), the page missing (or
    the sheet extra), found by halves: the first sheet that does not read as its
    position; proposed only if its neighbours' readings agree, and not where the
    default already has it (at the end)."""
    d = len(sheets) - length
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


# --- a person's edits (each returns a new plan) --------------------------------------

def _start(plan: Mapping, k: Any) -> int:
    """The position of submission `k`'s start; ValueError if there is no such submission."""
    at = starts(plan)
    if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < len(at):
        raise ValueError("no such submission")
    return at[k]


def _pos(plan: Mapping, sheet: Any) -> int:
    p = next((p for p, r in enumerate(plan['sheets']) if r['i'] == sheet), None)
    if p is None or isinstance(sheet, bool):
        raise ValueError("no such page")
    return p


def _owner(plan: Mapping, p: int) -> int:
    """The submission whose run holds position `p`."""
    return sum(s <= p for s in starts(plan)) - 1


def _say(plan: dict, *positions: int, start: bool | None = None, drop: bool | None = None) -> None:
    """A person's statement about the sheets at these positions."""
    for p in positions:
        r = plan['sheets'][p]
        r['said'] = True
        if start is not None:
            r.pop('start', None)
            if start:
                r['start'] = True
        if drop is not None:
            r.pop('dropped', None)
            if drop:
                r['dropped'] = True


def settle(plan: dict, k: int) -> None:
    """Submission `k`'s proposed marks become a person's."""
    got = proposals(plan)[k]
    at = {r['i']: p for p, r in enumerate(plan['sheets'])}
    for i in got.get('extra', ()):
        plan['sheets'][at[i]]['extra'] = True
    for i in got.get('missing', ()):
        plan['sheets'][at[i]]['missing'] = plan['sheets'][at[i]].get('missing', 0) + 1


def _edit(fn: Callable[..., None]) -> Callable[..., dict]:
    """A person's edit: on a copy, normalised, `verified` kept only where the submission
    looks as it did when verified (or as it is verified now), every verified
    submission kept whole, then guessed again (stored readings only)."""
    def run(plan: Mapping, *args) -> dict:
        plan = copy.deepcopy(dict(plan))
        before = _seen(plan)
        fn(plan, *args)
        return guess(_whole(_unverify(_normal(plan), before)))
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


@_edit
def confirm(plan: dict, k: int) -> None:
    """A person verifies submission `k`: it is its sheets (`_whole`), and its proposals
    become theirs."""
    p = _start(plan, k)
    settle(plan, k)
    plan['sheets'][p]['verified'] = True


@_edit
def move(plan: dict, k: int, delta: int) -> None:
    """Submission `k`'s first page one kept sheet later (`delta` 1) or earlier (-1):
    a person says it starts there. The sheet it leaves goes to the submission
    before; the first submission has none, so moving its first page says every
    sheet before it is in no submission (and earlier puts back the dropped
    sheet before it)."""
    L, p = plan['sheets'], _start(plan, k)
    kept = [x for x, r in enumerate(L) if not r.get('dropped')]
    if delta > 0:
        q = next((x for x in kept if x > p), None)
    else:
        q = next((x for x in reversed(kept) if x < p), None)
        if q is None:                   # nothing kept before it: the dropped sheet before it
            q = p - 1 if p else None
    if q is None:
        raise ValueError("no more pages that way")
    _say(plan, p, start=False)
    _say(plan, q, start=True, drop=False)
    if k == 0:
        _say(plan, *range(q), start=False, drop=True)


@_edit
def cut(plan: dict, sheet: int) -> None:
    """A person says a submission starts at `sheet`."""
    p = _pos(plan, sheet)
    if plan['sheets'][p].get('dropped'):
        raise ValueError("the page is in no submission")
    _say(plan, p, start=True)


@_edit
def join(plan: dict, k: int) -> None:
    """A person says submission `k` and the one after it are one: no sheet of
    theirs but the first starts a submission."""
    _start(plan, k)
    at = starts(plan) + [len(plan['sheets'])]
    if k + 2 >= len(at):
        raise ValueError("nothing after it to join")
    _say(plan, *[x for x in range(at[k] + 1, at[k + 2]) if not plan['sheets'][x].get('dropped')], start=False)


@_edit
def drop(plan: dict, sheet: int) -> None:
    """Set a sheet aside (a blank or stray page): in no submission. A first page a
    person said is one passes that to the next kept sheet of its run."""
    L, p = plan['sheets'], _pos(plan, sheet)
    nxt = next((x for x in range(p + 1, len(L)) if not L[x].get('dropped')), None)
    if L[p].get('start') and L[p].get('said') and nxt is not None and not L[nxt].get('start'):
        _say(plan, nxt, start=True)
    _say(plan, p, drop=True)


@_edit
def restore(plan: dict, sheet: int) -> None:
    """Put a dropped sheet back, into the submission whose run it is in."""
    _say(plan, _pos(plan, sheet), drop=False)


def _holder(plan: dict, sheet: Any) -> int:
    """The position of a sheet in a submission (its proposals settled: a person is
    marking it), else ValueError."""
    p = _pos(plan, sheet)
    if plan['sheets'][p].get('dropped'):
        raise ValueError("the page is in no submission")
    settle(plan, _owner(plan, p))
    return p


@_edit
def toggle_extra(plan: dict, sheet: int) -> None:
    """Mark a sheet extra (kept, not a printed page), or count it again."""
    r = plan['sheets'][_holder(plan, sheet)]
    r['extra'] = not r.get('extra')


@_edit
def add_missing(plan: dict, sheet: int) -> None:
    """A printed page that was not scanned comes before `sheet`."""
    r = plan['sheets'][_holder(plan, sheet)]
    r['missing'] = r.get('missing', 0) + 1


@_edit
def remove_missing(plan: dict, sheet: int) -> None:
    r = plan['sheets'][_holder(plan, sheet)]
    if not r.get('missing'):
        raise ValueError("no missing page marked there")
    r['missing'] -= 1


@_edit
def shift(plan: dict, sheet: int, delta: int) -> None:
    """Move a kept sheet one place (`delta` -1 or 1) among the kept sheets, over
    any dropped ones. Within its submission it swaps with its neighbour (the
    submission still starts at its first place); at a boundary it crosses into
    the neighbouring submission instead (as its first or last page), and a
    submission it leaves with no sheet is gone."""
    L = plan['sheets']
    p = _holder(plan, sheet)
    kept = [x for x, r in enumerate(L) if not r.get('dropped')]
    j = kept.index(p) + delta
    if not 0 <= j < len(kept):
        raise ValueError("no more pages that way")
    q = kept[j]
    settle(plan, _owner(plan, q))
    if (L[q] if delta > 0 else L[p]).get('start'):          # a boundary: it moves past the sheet
        if delta > 0:
            _say(plan, q, start=False)
            _say(plan, p, start=True)
        else:
            after = kept[j + 2:j + 3]                       # the next kept sheet, if in its run
            if after and not L[after[0]].get('start'):
                _say(plan, after[0], start=True)
            _say(plan, p, start=False)
        return
    a, b = min(p, q), max(p, q)                             # within a submission: the start stays first,
    for key in ('start', 'said'):                           # a person's or a guess as it was
        x, y = L[a].pop(key, None), L[b].pop(key, None)
        L[b].update({key: x} if x else {})
        L[a].update({key: y} if y else {})
    L[a], L[b] = L[b], L[a]


@_edit
def turn(plan: dict, sheets) -> None:
    """These sheets a quarter turn more, each from its own turn. Their readings stay
    what they were when last read (reading pages again reads them as turned), so a
    turn changes nothing else."""
    want = set(sheets)
    for r in plan['sheets']:
        if r['i'] in want:
            want.discard(r['i'])
            r['turn'] = (r.get('turn', 0) + 1) % 4
            if not r['turn']:
                del r['turn']
    if want:
        raise ValueError("no such page")


def accepted(plan: Mapping) -> dict:
    """Accepting: every proposal becomes a person's, and every submission is verified."""
    plan = copy.deepcopy(dict(plan))
    for k, p in enumerate(starts(plan)):
        settle(plan, k)
        plan['sheets'][p]['verified'] = True
    for r in plan['sheets']:
        r['said'] = True
    return plan
