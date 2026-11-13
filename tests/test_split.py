"""The pure boundary logic of intake: no PDFs, no model."""
import pytest

from examinator.core import split
from examinator.pdf.orient import turn

L = 5        # printed pages
ANCHOR = 1   # the first page with a landmark (0-based): page 2


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
    """A page-number landmark on every printed page from page 2: position, or None on page 1 and strays.
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
    plan = {'length': L, 'anchor': ANCHOR, 'sheets': [{'i': i, **({'start': {}} if i in starts else {})}
                                                       for i in range(len(sheets))]}
    assert split.doubts(plan) == [[], [], ['short']]


def test_nothing_readable_falls_back_to_every_n_and_doubts_every_start():
    plan = split.new_plan(12, L, anchor=ANCHOR, read=lambda i: None)
    assert first_pages(plan) == [0, 5, 10]
    assert split.doubts(plan) == [['start'], ['start'], ['short', 'start']]


def test_missing_first_page_is_flagged_not_hidden():
    sheets = missing(stack(5, 5, 5), 1, 1)             # student 1 lost the page with the landmark
    plan = split.new_plan(len(sheets), L, anchor=ANCHOR, read=title(sheets))
    assert first_pages(plan) == [0, 5, 9]
    assert split.doubts(plan) == [[], ['short', 'start'], []]


def assess(sheets, read, extra=(), missing=(), pages=L):
    return split.assess(split.lay_out(sheets, pages, extra, missing), read, anchor=ANCHOR, extra=extra)


def test_doubts():
    read = {10: 1, 12: 3, 13: 4}
    assert assess([9, 10, 11, 12, 13], read) == []
    assert assess([9, 10, 11, 12], read) == ['short']
    assert assess([9, 10, 11, 12, 13, 14], read) == ['long']
    assert assess([10, 11, 12, 13, 14], read) == ['number']
    assert assess([11, 12, 13, 14, 15], {12: 3}) == ['start']
    assert assess([9, 10, 12, 13, 14], read) == ['number']
    assert assess([0, 1, 2, 3, 4], {}) == []        # never read: no doubt
    # a person's marks settle them: page 3 missing before sheet 12, a stray sheet marked extra
    assert assess([9, 10, 12, 13], read, missing=[12]) == []
    assert assess([9, 10, 99, 11, 12, 13], read, extra={99}) == []
    # sheets per submission beyond the exam's pages: that many trailing extras are expected
    assert split.assess(split.lay_out([1, 2, 3, 4, 5, 6], L), {}, spare=1) == []


# --- a submission: printed pages to sheets ---

def test_lay_out_defaults_extra_and_missing():
    # more sheets than pages: the trailing ones are extra; fewer: the trailing pages are missing
    assert split.lay_out([3, 4, 5], 2) == [(3, 1), (4, 2), (5, None)]
    assert split.lay_out([3, 4], 3) == [(3, 1), (4, 2), (None, 3)]
    # a sheet marked extra is not counted; a missing mark is a page before its sheet (several may be)
    assert split.lay_out([3, 4, 5], 3, extra={4}) == [(3, 1), (4, None), (5, 2), (None, 3)]
    assert split.lay_out([3, 4], 4, missing=[4, 4]) == [(3, 1), (None, 2), (None, 3), (4, 4)]
    # marks beyond the exam's pages mean nothing
    assert split.lay_out([3, 4], 1, missing=[4]) == [(3, 1), (4, None)]
    # the PDF: the printed pages in order (None: a placeholder), then the extras in order
    lay = split.lay_out([3, 4, 5, 6], 4, extra={4}, missing=[6])
    assert split.written(lay) == [3, 5, None, 6, 4]


def test_marking_sheets():
    plan = split.new_plan(10, L)                        # sheets 0-4 and 5-9
    plan = split.toggle_extra(plan, 2)
    assert split.layouts(plan)[0] == [(0, 1), (1, 2), (2, None), (3, 3), (4, 4), (None, 5)]
    assert split.doubts(plan)[0] == ['short']
    plan = split.add_missing(plan, 1)                   # page 2 was not scanned: sheet 1 is page 3
    assert split.written(split.layouts(plan)[0]) == [0, None, 1, 3, 4, 2]
    assert split.doubts(plan)[0] == []
    plan = split.remove_missing(split.toggle_extra(plan, 2), 1)
    assert split.layouts(plan)[0] == [(i, i + 1) for i in range(5)]
    assert plan['sheets'][:5] == [{'i': 0, 'start': {}}, {'i': 1}, {'i': 2}, {'i': 3}, {'i': 4}]
    with pytest.raises(ValueError):
        split.remove_missing(plan, 1)                   # no mark there
    # a dropped sheet is in no submission: nothing to mark
    with pytest.raises(ValueError):
        split.toggle_extra(split.drop(plan, 3), 3)


def test_runs_follow_the_start_marks():
    plan = {'length': 3, 'sheets': [{'i': 2, 'start': {}}, {'i': 0}, {'i': 1, 'dropped': True},
                                    {'i': 3, 'start': {}, 'dropped': True}, {'i': 4}]}
    assert split.runs(plan) == [[2, 0], [4]]
    assert split.span(plan, 1) == [3, 4] and split.dropped(plan) == [1, 3]


def test_moving_a_sheet_in_the_pile():
    plan = split.new_plan(10, L)                        # sheets 0-4 and 5-9
    # within a submission: a swap with its neighbour; a first page swapped later keeps the boundary
    plan = split.shift(plan, 3, -1)
    assert split.runs(plan) == [[0, 1, 3, 2, 4], [5, 6, 7, 8, 9]]
    plan = split.shift(plan, 5, 1)
    assert split.runs(plan) == [[0, 1, 3, 2, 4], [6, 5, 7, 8, 9]] and first_pages(plan) == [0, 6]
    # at a boundary it crosses into the neighbour instead: the boundary moves past it
    plan = split.shift(plan, 4, 1)
    assert split.runs(plan) == [[0, 1, 3, 2], [4, 6, 5, 7, 8, 9]]
    plan = split.shift(plan, 4, -1)
    assert split.runs(plan) == [[0, 1, 3, 2, 4], [6, 5, 7, 8, 9]]
    with pytest.raises(ValueError):
        split.shift(plan, 0, -1)                        # the ends of the pile
    with pytest.raises(ValueError):
        split.shift(split.cut(plan, 9), 9, -1)          # a submission of one page would be empty
    # the marks travel with the sheet; the confirmed start stays where it is
    plan = split.toggle_extra(split.confirm(plan, 1), 7)
    plan = split.shift(split.turn(plan, [7]), 7, -1)
    assert [(r['i'], r.get('extra'), r.get('turn')) for r in plan['sheets'][6:8]] == [(7, True, 1), (5, None, None)]
    assert split.entries(plan)[1]['confirmed'] and first_pages(plan) == [0, 6]


def test_a_reordered_pile_stays_when_a_boundary_moves():
    plan = split.new_plan(15, L)
    plan = split.shift(split.shift(plan, 8, -1), 8, -1)
    assert split.runs(plan)[1] == [5, 8, 6, 7, 9]
    plan = split.move(plan, 1, 1)                       # its first page a page later
    assert split.runs(plan)[:2] == [[0, 1, 2, 3, 4, 5], [8, 6, 7, 9, 10]]
    plan = split.move(plan, 1, -1)
    assert split.runs(plan)[1] == [5, 8, 6, 7, 9]
    plan = split.join(plan, 0)
    assert split.runs(plan)[0] == [0, 1, 2, 3, 4, 5, 8, 6, 7, 9]


def test_the_first_submission_starts_the_pile():
    plan = split.new_plan(10, L)
    plan = split.move(plan, 0, 1)                       # its first page a page later: the one before is dropped
    assert split.runs(plan)[0] == [1, 2, 3, 4, 5] and split.dropped(plan) == [0]
    plan = split.move(plan, 0, -1)                      # and back: put back
    assert split.runs(plan)[0] == [0, 1, 2, 3, 4] and split.dropped(plan) == []
    with pytest.raises(ValueError):
        split.move(plan, 0, -1)
    # a guess finds stray sheets before the first submission and drops them
    sheets = [None] + stack(5, 5)
    plan = split.new_plan(len(sheets), L, anchor=ANCHOR, read=title(sheets))
    assert split.dropped(plan) == [0] and split.runs(plan) == [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]]


def test_turns_are_each_sheets_own():
    plan = split.new_plan(10, L)
    plan = split.turn(plan, [3])
    plan = split.turn(plan, split.span(plan, 0))        # turning a submission turns each of its sheets
    assert split.turns(plan) == {**{i: 0 for i in range(10)}, 0: 1, 1: 1, 2: 1, 3: 2, 4: 1}
    plan = split.move(plan, 1, -1)                      # a sheet moved into another submission keeps its turn
    assert split.runs(plan)[1][0] == 4 and split.turns(plan)[4] == 1 and split.turns(plan)[5] == 0
    plan = split.turn(plan, split.turns(plan))          # the whole scan: every sheet once more
    assert split.turns(plan)[3] == 3 and split.turns(plan)[9] == 1
    plan = split.turn(plan, [3])
    assert 'turn' not in plan['sheets'][3]              # four quarter turns: back to none
    with pytest.raises(ValueError):
        split.turn(plan, [10])


def test_guessing_again_keeps_a_persons_marks_and_order():
    sheets = missing(stack(5, 5, 5), 0, 2)
    n = len(sheets)
    plan = split.new_plan(n, L, anchor=ANCHOR)
    plan = split.shift(plan, 12, -1)
    plan = split.add_missing(split.toggle_extra(split.turn(split.drop(plan, 13), [6]), 7), 9)
    marks = [{k: v for k, v in r.items() if k != 'start'} for r in plan['sheets']]
    plan = split.refill(plan, numbers(sheets))
    assert [{k: v for k, v in r.items() if k != 'start'} for r in plan['sheets']] == marks
    assert first_pages(plan) == [0, 4, 9]


def test_landmarks_propose_the_missing_page_and_the_extra_sheet():
    # student 0 lost page 3; student 1 has a stray sheet after page 3
    sheets = missing(stack(5, 5, 5, stray_after={(1, 2)}), 0, 2)
    n = len(sheets)
    read, calls = counted(numbers(sheets))
    plan = split.new_plan(n, L, anchor=ANCHOR, read=read)
    assert first_pages(plan) == [0, 4, 10]
    assert split.entries(plan)[0]['proposed'] == {'missing': [2]}      # before the sheet that is page 4
    assert split.entries(plan)[1]['proposed'] == {'extra': [7]}        # the stray
    assert split.doubts(plan) == [['missing'], ['extra'], []]
    assert split.written(split.layouts(plan)[0]) == [0, 1, None, 2, 3]
    assert len(set(calls)) < n                          # found by halves, not by reading everything
    # confirming makes them a person's; marking another entry settles its proposal too
    plan = split.confirm(plan, 0)
    assert plan['sheets'][2]['missing'] == 1 and 'proposed' not in split.entries(plan)[0]
    plan = split.toggle_extra(plan, 7)                  # the person says it is not extra after all
    assert not split.marks(plan)[0] and 'proposed' not in split.entries(plan)[1]
    assert split.doubts(plan)[1] == ['long', 'number']


def test_no_proposal_without_agreeing_readings():
    sheets = missing(stack(5, 5), 0, 2)
    plan = split.new_plan(len(sheets), L, anchor=ANCHOR, read=title(sheets))     # only page 2 has a landmark
    assert not any('proposed' in e for e in split.entries(plan))


# --- editing: a person's decisions outrank guesses ---

def first_pages(plan):
    """The sheet at each start mark."""
    return [plan['sheets'][p]['i'] for p in split.starts(plan)]


def test_step_through_confirm_and_move():
    n = 20
    plan = split.new_plan(n, L)
    assert first_pages(plan) == [0, 5, 10, 15] and split.cursor(plan) == 0
    plan = split.confirm(plan, 0)
    plan = split.move(plan, 1, -1)                   # the guess was one page late
    assert first_pages(plan) == [0, 4, 9, 14, 19]            # later guesses follow the moved one
    plan = split.confirm(plan, 1)
    assert split.cursor(plan) == 2
    plan = split.move(plan, 2, 2)
    assert first_pages(plan) == [0, 4, 11, 16]
    plan = split.confirm(plan, 2)
    # going back and moving a confirmed start makes it a guess again, but never moves another confirmed one
    plan = split.move(plan, 1, 1)
    assert first_pages(plan) == [0, 5, 11, 16] and not split.entries(plan)[1].get('confirmed')
    assert split.entries(plan)[2]['confirmed']
    with pytest.raises(ValueError):
        split.move(plan, 1, 6)                       # cannot pass a confirmed start


def test_refill_with_readings_respects_confirmed_starts():
    sheets = missing(stack(5, 5, 5, 5), 2, 3)
    n = len(sheets)
    plan = split.new_plan(n, L, anchor=ANCHOR)
    plan = split.cut(plan, 7)                        # a (wrong) confirmed start
    plan = split.refill(plan, numbers(sheets))
    assert 7 in first_pages(plan) and split.entries(plan)[first_pages(plan).index(7)]['confirmed']
    assert first_pages(plan)[:2] == [0, 5]
    assert plan['checked'] is False and plan['readings']


def test_drop_restore_join_and_ids_survive():
    n = 16
    plan = split.new_plan(n, L)
    for e, id in zip(split.entries(plan), ['s1', 's2', 's3', 's4']):
        e['id'] = id
    plan = split.confirm(plan, 0)
    plan = split.drop(plan, 2)                       # a stray sheet in the first submission
    assert split.runs(plan)[0] == [0, 1, 3, 4, 5] and first_pages(plan)[1] == 6
    plan = split.move(plan, 1, -1)
    assert split.runs(plan)[0] == [0, 1, 3, 4] and first_pages(plan)[1] == 5
    # ids stay with submissions in order; one with nowhere to go is gone (accept then asks about it)
    assert [e.get('id') for e in split.entries(plan)][:3] == ['s1', 's2', 's3']
    assert split.dropped(plan) == [2]
    plan = split.restore(plan, 2)
    assert split.dropped(plan) == []
    plan = split.join(plan, 0)
    assert split.entries(plan)[0]['id'] == 's1' and 's2' not in [e.get('id') for e in split.entries(plan)]


# --- orientation ---

def test_orientation_quarter_turns():
    from PIL import Image
    im = Image.new('L', (3, 2))
    im.putdata(range(6))
    seen = set()
    for a in range(4):
        out = turn(im, a)
        assert out.size == ((2, 3) if a % 2 else (3, 2))
        seen.add(bytes(out.tobytes()) + bytes(out.size))
        for b in range(4):
            assert turn(turn(im, a), b).tobytes() == turn(im, a + b).tobytes(), (a, b)
    assert len(seen) == 4
    # a quarter turn is clockwise: the top-left pixel goes to the top-right
    assert turn(im, 1).getpixel((1, 0)) == 0
