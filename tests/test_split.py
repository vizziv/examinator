"""The pure boundary logic of intake: no PDFs, no model."""
import pytest

from examinator.core import split
from examinator.pdf.orient import ALL, HALF, IDENTITY, MIRROR, QUARTER, Orient

L = 5        # one cover sheet + four printed pages
ANCHOR = 1   # the first position with a landmark


def stack(*lengths, stray_after=None):
    """Sheets as (student, position); `stray_after={(student, position)}` inserts a stray sheet (None)."""
    out = []
    for s, n in enumerate(lengths):
        for p in range(n):
            out.append((s, p))
            if stray_after and (s, p) in stray_after:
                out.append(None)
    return out


def numbers(sheets, skip=()):
    """A page-number landmark on every printed page: position, or None on covers and strays.
    Positions in `skip` (student, position) are missing pages: the remaining sheets keep their numbers."""
    def read(i):
        x = sheets[i]
        return None if x is None or x[1] < ANCHOR else x[1]
    return read


def title(sheets):
    """Only page 1 carries a landmark."""
    def read(i):
        x = sheets[i]
        return ANCHOR if x is not None and x[1] == ANCHOR else None
    return read


def counted(read):
    calls = []

    def f(i):
        calls.append(i)
        return read(i)
    return f, calls


def truth(sheets):
    return [i for i, x in enumerate(sheets) if x is not None and x[1] == 0
            or (x is not None and i == 0)]


def test_plain_proposal_is_every_n():
    assert split.propose(23, 5) == [0, 5, 10, 15, 20]
    assert split.propose(0, 5) == []
    with pytest.raises(ValueError):
        split.propose(10, 0)


@pytest.mark.parametrize('reader', [numbers, title])
def test_clean_stack_reads_one_sheet_per_boundary(reader):
    sheets = stack(5, 5, 5, 5)
    read, calls = counted(reader(sheets))
    assert split.propose(len(sheets), L, read, anchor=ANCHOR) == [0, 5, 10, 15]
    assert len(set(calls)) == 4          # sheet 1 (first start) and one per later boundary


def missing(sheets, student, position):
    return [x for x in sheets if x != (student, position)]


@pytest.mark.parametrize('reader', [numbers, title])
def test_missing_page_moves_the_boundary_back(reader):
    sheets = missing(stack(5, 5, 5), 0, 3)
    assert split.propose(len(sheets), L, reader(sheets), anchor=ANCHOR) == [0, 4, 9]


@pytest.mark.parametrize('reader', [numbers, title])
def test_stray_page_moves_the_boundary_forward(reader):
    sheets = stack(5, 5, 5, stray_after={(1, 2)})
    assert split.propose(len(sheets), L, reader(sheets), anchor=ANCHOR) == [0, 5, 11]


def test_page_number_jumps_straight_to_a_far_boundary():
    sheets = missing(missing(stack(5, 5, 5), 0, 2), 0, 3)   # two pages missing
    read, calls = counted(numbers(sheets))
    assert split.propose(len(sheets), L, read, anchor=ANCHOR) == [0, 3, 8]


def test_leading_stray_sheet_is_left_out():
    sheets = [None] + stack(5, 5)
    assert split.propose(len(sheets), L, title(sheets), anchor=ANCHOR) == [1, 6]


def test_count_not_a_multiple_and_trailing_partial():
    sheets = stack(5, 5, 2)
    starts = split.propose(len(sheets), L, numbers(sheets), anchor=ANCHOR)
    assert starts == [0, 5, 10]
    plan = {'length': L, 'anchor': ANCHOR, 'entries': [{'start': s} for s in starts], 'dropped': []}
    assert split.doubts(plan, len(sheets)) == [[], [], ['short']]


def test_nothing_readable_falls_back_to_every_n_and_doubts_every_start():
    plan = split.new_plan(12, L, anchor=ANCHOR, read=lambda i: None)
    assert [e['start'] for e in plan['entries']] == [0, 5, 10]
    assert split.doubts(plan, 12) == [['start'], ['start'], ['short', 'start']]


def test_missing_first_page_is_flagged_not_hidden():
    sheets = missing(stack(5, 5, 5), 1, 1)             # student 1 lost the page with the landmark
    plan = split.new_plan(len(sheets), L, anchor=ANCHOR, read=title(sheets))
    assert [e['start'] for e in plan['entries']] == [0, 5, 9]
    assert split.doubts(plan, len(sheets)) == [[], ['short', 'start'], []]


def test_doubts():
    read = {10: 1, 12: 3, 13: 4}
    assert split.assess([9, 10, 11, 12, 13], 5, read, 1) == []
    assert split.assess([9, 10, 11, 12], 5, read, 1) == ['short']
    assert split.assess([9, 10, 11, 12, 13, 14], 5, read, 1) == ['long']
    assert split.assess([10, 11, 12, 13, 14], 5, read, 1) == ['number']
    assert split.assess([11, 12, 13, 14, 15], 5, {12: 3}, 1) == ['start']
    assert split.assess([9, 10, 12, 13, 14], 5, read, 1) == ['number']
    assert split.assess([0, 1, 2, 3, 4], 5, {}, 1) == []        # never read: no doubt


# --- editing: a person's decisions outrank guesses ---

def starts(plan):
    return [e['start'] for e in plan['entries']]


def test_step_through_confirm_and_move():
    n = 20
    plan = split.new_plan(n, L)
    assert starts(plan) == [0, 5, 10, 15] and split.cursor(plan) == 0
    plan = split.confirm(plan, 0, n)
    plan = split.move(plan, 1, -1, n)                   # the guess was one page late
    assert starts(plan) == [0, 4, 9, 14, 19]            # later guesses follow the moved one
    plan = split.confirm(plan, 1, n)
    assert split.cursor(plan) == 2
    plan = split.move(plan, 2, 2, n)
    assert starts(plan) == [0, 4, 11, 16]
    plan = split.confirm(plan, 2, n)
    # going back and moving a confirmed start makes it a guess again, but never moves another confirmed one
    plan = split.move(plan, 1, 1, n)
    assert starts(plan) == [0, 5, 11, 16] and not plan['entries'][1]['confirmed']
    assert plan['entries'][2]['confirmed']
    with pytest.raises(ValueError):
        split.move(plan, 1, 6, n)                       # cannot pass a confirmed start


def test_refill_with_readings_respects_confirmed_starts():
    sheets = missing(stack(5, 5, 5, 5), 2, 3)
    n = len(sheets)
    plan = split.new_plan(n, L, anchor=ANCHOR)
    plan = split.cut(plan, 7, n)                        # a (wrong) confirmed start
    plan = split.refill(plan, n, numbers(sheets))
    assert 7 in starts(plan) and plan['entries'][starts(plan).index(7)]['confirmed']
    assert starts(plan)[:2] == [0, 5]
    assert plan['checked'] is False and plan['readings']


def test_drop_restore_join_and_ids_survive():
    n = 16
    plan = split.new_plan(n, L)
    for e, id in zip(plan['entries'], ['s1', 's2', 's3', 's4']):
        e['id'] = id
    plan = split.confirm(plan, 0, n)
    plan = split.drop(plan, 2, n)                       # a stray sheet in the first submission
    assert split.pages(plan, n)[0] == [0, 1, 3, 4, 5] and starts(plan)[1] == 6
    plan = split.move(plan, 1, -1, n)
    assert split.pages(plan, n)[0] == [0, 1, 3, 4] and starts(plan)[1] == 5
    # ids stay with submissions in order; one with nowhere to go is gone (accept then asks about it)
    assert [e['id'] for e in plan['entries']][:3] == ['s1', 's2', 's3']
    assert split.loose(plan, n) == [2]
    plan = split.restore(plan, 2, n)
    assert split.loose(plan, n) == []
    plan = split.join(plan, 0, n)
    assert plan['entries'][0]['id'] == 's1' and 's2' not in [e['id'] for e in plan['entries']]


# --- orientation ---

def test_orientation_compose_and_apply_all_eight():
    from PIL import Image
    im = Image.new('L', (3, 2))
    im.putdata(range(6))
    seen = set()
    for a in ALL:
        out = a.apply(im)
        assert out.size == ((2, 3) if a.swaps else (3, 2))
        seen.add(bytes(out.tobytes()) + bytes(out.size))
        for b in ALL:
            assert b.apply(a.apply(im)).tobytes() == a.then(b).apply(im).tobytes(), (a, b)
    assert len(seen) == 8
    assert QUARTER.then(QUARTER).then(QUARTER).then(QUARTER) == IDENTITY
    assert HALF.then(HALF) == IDENTITY and MIRROR.then(MIRROR) == IDENTITY
    assert Orient.from_json(Orient(3, True).to_json()) == Orient(3, True)
    # a quarter turn is clockwise: the top-left pixel goes to the top-right
    assert QUARTER.apply(im).getpixel((1, 0)) == 0
