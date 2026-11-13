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
    plan = {'length': L, 'anchor': ANCHOR, 'sheets': [{'i': i, **({'start': True} if i in starts else {})}
                                                       for i in range(len(sheets))]}
    assert split.doubts(plan, L) == [[], [], ['short']]


def test_nothing_readable_falls_back_to_every_n_and_doubts_every_start():
    plan = split.guess(split.new_plan(12, L, anchor=ANCHOR), lambda i: None)
    assert first_pages(plan) == [0, 5, 10]
    assert split.doubts(plan, L) == [['start'], ['start'], ['short', 'start']]


def test_missing_first_page_is_flagged_not_hidden():
    sheets = missing(stack(5, 5, 5), 1, 1)             # student 1 lost the page with the landmark
    plan = split.guess(split.new_plan(len(sheets), L, anchor=ANCHOR), title(sheets))
    assert first_pages(plan) == [0, 5, 9]
    assert split.doubts(plan, L) == [[], ['short', 'start'], []]


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
    assert split.layouts(plan, L)[0] == [(0, 1), (1, 2), (2, None), (3, 3), (4, 4), (None, 5)]
    assert split.doubts(plan, L)[0] == ['short']
    plan = split.add_missing(plan, 1)                   # page 2 was not scanned: sheet 1 is page 3
    assert split.written(split.layouts(plan, L)[0]) == [0, None, 1, 3, 4, 2]
    assert split.doubts(plan, L)[0] == []
    plan = split.remove_missing(split.toggle_extra(plan, 2), 1)
    assert split.layouts(plan, L)[0] == [(i, i + 1) for i in range(5)]
    own = lambda r: {k: v for k, v in r.items() if k not in ('start', 'said')}
    # a person's word stands, also when it is "not": no proposal will be put there
    assert [own(r) for r in plan['sheets'][:3]] == [{'i': 0}, {'i': 1, 'missing': 0}, {'i': 2, 'extra': False}]
    assert not any(r.get('said') for r in plan['sheets'])      # marks say nothing about where submissions start
    with pytest.raises(ValueError):
        split.remove_missing(plan, 1)                   # no mark there
    # a dropped sheet is in no submission: nothing to mark, no submission to start
    with pytest.raises(ValueError):
        split.toggle_extra(split.drop(plan, 3), 3)
    with pytest.raises(ValueError):
        split.cut(split.drop(plan, 3), 3)


def test_a_submission_is_its_kept_sheets():
    plan = split.accepted(split.new_plan(10, 3))        # 0-2, 3-5, 6-8, 9: every start a person's
    plan = split.confirm(plan, 1)
    plan = split.drop(plan, 3)                          # its first page dropped: the next kept sheet starts it
    assert split.runs(plan)[:2] == [[0, 1, 2], [4, 5]] and split.verified(plan)[1]
    assert split.span(plan, 0) == [0, 1, 2, 3] and split.dropped(plan) == [3]     # in the span before, in no run
    plan = split.drop(split.drop(plan, 4), 5)           # all its sheets: it is gone, no empty submission
    assert split.runs(plan) == [[0, 1, 2], [6, 7, 8], [9]]
    plan = split.restore(plan, 4)                       # put back: in the run it is in
    assert split.runs(plan) == [[0, 1, 2, 4], [6, 7, 8], [9]]
    plan = split.drop(split.drop(split.drop(plan, 0), 1), 2)
    assert split.runs(plan)[0] == [4]                   # the first kept sheet starts the first submission


def test_moving_a_sheet_in_the_pile():
    plan = split.accepted(split.new_plan(10, L))        # sheets 0-4 and 5-9, as a person said
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
    # a one-page submission moved out of: it is gone
    one = split.shift(split.cut(plan, 9), 9, -1)
    assert split.runs(one) == split.runs(plan)
    # a move steps over dropped sheets
    hop = split.shift(split.drop(plan, 7), 5, 1)
    assert split.runs(hop)[1] == [6, 8, 5, 9] and split.dropped(hop) == [7]
    # the marks travel with the sheet; the start stays first
    plan = split.toggle_extra(split.confirm(plan, 1), 7)
    plan = split.shift(split.turn(plan, [7]), 7, -1)
    assert [(r['i'], r.get('extra'), r.get('turn')) for r in plan['sheets'][6:8]] == [(7, True, 1), (5, None, None)]
    assert split.verified(plan)[1] and first_pages(plan) == [0, 6]


def test_a_reordered_pile_stays_when_a_boundary_moves():
    plan = split.new_plan(15, L)
    plan = split.shift(split.shift(plan, 8, -1), 8, -1)
    assert split.runs(plan)[1] == [5, 8, 6, 7, 9]
    plan = split.move(plan, 1, 1)                       # its first page a page later
    assert split.runs(plan)[:2] == [[0, 1, 2, 3, 4, 5], [8, 6, 7, 9, 10]]
    plan = split.move(plan, 1, -1)
    assert split.runs(plan)[1][:4] == [5, 8, 6, 7]
    plan = split.join(plan, 0)
    assert split.runs(plan)[0][:10] == [0, 1, 2, 3, 4, 5, 8, 6, 7, 9]


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
    plan = split.guess(split.new_plan(len(sheets), L, anchor=ANCHOR), title(sheets))
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
    plan = split.guess(plan, numbers(sheets))
    assert [{k: v for k, v in r.items() if k != 'start'} for r in plan['sheets']] == marks
    assert first_pages(plan) == [0, 4, 9]


def test_landmarks_propose_the_missing_page_and_the_extra_sheet():
    # student 0 lost page 3; student 1 has a stray sheet after page 3
    sheets = missing(stack(5, 5, 5, stray_after={(1, 2)}), 0, 2)
    n = len(sheets)
    read, calls = counted(numbers(sheets))
    plan = split.guess(split.new_plan(n, L, anchor=ANCHOR), read)
    assert first_pages(plan) == [0, 4, 10]
    assert split.proposals(plan)[:2] == [{'missing': [2]}, {'extra': [7]}]     # before the sheet that is page 4; the stray
    assert split.doubts(plan, L) == [['missing'], ['extra'], []]
    assert split.written(split.layouts(plan, L)[0]) == [0, 1, None, 2, 3]
    assert len(set(calls)) < n                          # found by halves, not by reading everything
    # proposals are computed from the stored readings: the same without the reader
    assert split.proposals(split.guess(plan)) == split.proposals(plan)
    # confirming makes them a person's; marking another submission settles its proposal too
    plan = split.confirm(plan, 0)
    assert plan['sheets'][2]['missing'] == 1 and not split.proposals(plan)[0]
    plan = split.toggle_extra(plan, 7)                  # the person says it is not extra after all
    assert not split.marks(plan)[0] and not split.proposals(plan)[1]
    assert split.doubts(plan, L)[1] == ['long', 'number']
    assert not split.proposals(split.restore(split.drop(plan, 9), 9))[1]      # and it stays not extra


def test_no_proposal_without_agreeing_readings():
    sheets = missing(stack(5, 5), 0, 2)
    plan = split.guess(split.new_plan(len(sheets), L, anchor=ANCHOR), title(sheets))     # only page 2 has a landmark
    assert not any(split.proposals(plan))


def test_a_longer_length_does_not_search_every_submission():
    sheets = [x for s in range(3) for x in [(s, p) for p in range(L)] + [(s, None)]]    # one trailing extra each
    read, calls = counted(lambda i: sheets[i][1] if sheets[i][1] is not None and sheets[i][1] >= ANCHOR else None)
    plan = split.guess(split.new_plan(len(sheets), L + 1, anchor=ANCHOR), read)
    assert first_pages(plan) == [0, 6, 12] and not any(split.proposals(plan))
    assert len(set(calls)) <= 4                         # the boundaries only: no search by halves


# --- editing: a person's decisions outrank guesses ---

def first_pages(plan):
    """The sheet at each start mark."""
    return [plan['sheets'][p]['i'] for p in split.starts(plan)]


def test_step_through_confirm_and_move():
    n = 20
    plan = split.new_plan(n, L)
    assert first_pages(plan) == [0, 5, 10, 15] and split.cursor(plan) == 0
    plan = split.confirm(plan, 0)
    plan = split.move(plan, 1, -1)                   # the guess was one page late: a person's start now
    assert first_pages(plan) == [0, 4, 9, 14, 19]            # later guesses follow the moved one
    assert split.verified(plan)[:3] == [True, False, False] and split.cursor(plan) == 1   # moved, not verified
    plan = split.move(split.move(plan, 2, 1), 2, 1)
    assert first_pages(plan) == [0, 4, 11, 16]
    # going back and moving a start never moves another a person said
    plan = split.move(plan, 1, 1)
    assert first_pages(plan) == [0, 5, 11, 16] and split.verified(plan)[:3] == [True, False, False]
    with pytest.raises(ValueError):
        split.confirm(plan, 4)


def test_guess_never_changes_a_persons_sheets():
    sheets = missing(stack(5, 5, 5, 5), 2, 3)
    n = len(sheets)
    plan = split.new_plan(n, L, anchor=ANCHOR)
    plan = split.cut(plan, 7)                        # a (wrong) start, a person's
    plan = split.guess(plan, numbers(sheets))
    assert 7 in first_pages(plan) and not any(split.verified(plan))
    assert first_pages(plan)[:2] == [0, 5]
    assert plan['readings']
    # a person joined two: a guess does not split them again
    plan = split.join(split.new_plan(n, L), 0)
    assert split.runs(split.guess(plan, numbers(sheets)))[0] == list(range(10))
    # a person put back a sheet a guess dropped: a guess does not drop it again
    strays = [None, None] + stack(5, 5)
    plan = split.guess(split.new_plan(len(strays), L, anchor=ANCHOR), title(strays))
    assert split.dropped(plan) == [0, 1]
    plan = split.restore(plan, 1)
    assert split.dropped(split.guess(plan, title(strays))) == [0]
    plan = split.move(split.guess(split.new_plan(len(strays), L, anchor=ANCHOR), title(strays)), 0, -1)
    assert split.dropped(plan) == [0] and split.runs(plan)[0][0] == 1


def test_which_written_submission_each_run_is():
    runs = [[0, 1, 2, 3], [4, 5, 6, 7]]
    made = {'s1': (0, 1, 2, 3), 's2': (4, 5, 6, 7)}
    assert split.link(runs, made) == (['s1', 's2'], [])
    # a cut: the first part keeps it, the rest is new
    assert split.link([[0, 1], [2, 3], [4, 5, 6, 7]], made) == (['s1', None, 's2'], [])
    # a sheet moved across a boundary either way: both keep their ids
    assert split.link([[0, 1, 2, 3, 4], [5, 6, 7]], made) == (['s1', 's2'], [])
    assert split.link([[0, 1, 2], [3, 4, 5, 6, 7]], made) == (['s1', 's2'], [])
    # the first page moved to the end of the next: the one whose page comes first there keeps that run
    assert split.link([[1, 2, 3], [4, 5, 6, 7, 0]], made) == (['s1', 's2'], [])
    # joined: the first keeps it, the other is to be removed; all dropped: removed
    assert split.link([list(range(8))], made) == (['s1'], ['s2'])
    assert split.link([[0, 1, 2, 3]], made) == (['s1'], ['s2'])
    assert split.link([], {}) == ([], [])


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


def test_a_sheet_moved_across_a_boundary_and_back_verifies_nothing():
    plan = split.new_plan(15, L)                        # 0-4, 5-9, 10-14
    there = split.shift(plan, 4, 1)                     # Shift+D: into the next submission
    assert split.runs(there)[:2] == [[0, 1, 2, 3], [4, 5, 6, 7, 8]]   # and the guesses after it follow
    back = split.shift(there, 4, -1)                    # Shift+A: back
    assert split.runs(back) == split.runs(plan) and not any(split.verified(back))
    # a verified submission stays verified, as the same sheets, through both
    plan = split.confirm(plan, 1)
    back = split.shift(split.shift(plan, 4, 1), 4, -1)
    assert split.runs(back) == split.runs(plan) and split.verified(back) == [False, True, False]


def test_a_correction_cascades_forward_up_to_a_verified_submission():
    plan = split.confirm(split.new_plan(30, L), 4)      # 0, 5, ..., 25; the fifth verified
    plan = split.move(plan, 1, -1)                      # the second starts a page early
    assert first_pages(plan) == [0, 4, 9, 14, 19, 20, 25]       # re-guessed up to the verified one (20-24),
    assert split.runs(plan)[5] == [20, 21, 22, 23, 24] and split.verified(plan) == [False] * 5 + [True, False]
    assert split.doubts(plan, L)[4] == ['short']                # which leaves one sheet over: a doubt


def test_a_sheet_moved_into_the_next_submission_merges_nothing():
    plan = split.shift(split.new_plan(15, L), 9, 1)     # the second's last page into the third
    assert split.runs(plan) == [[0, 1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12, 13], [14]]
