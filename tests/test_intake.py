"""Intake: sources, plans, orientation, accepting into pdfs/. Synthetic material only."""
import json
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageDraw

from examinator import Exam, Landmark, Region, load_exam, synth
from examinator.core import identity, intake, split
from examinator.core.store import Conflict, Made
from examinator.core.workspace import Workspace
from examinator.model.fake import FakeModel
from examinator.pdf import pages as pdf_pages, render
from examinator.pdf.orient import HALF, QUARTER, Orient
from examinator.pdf.render import Renderer

TOY = Path(__file__).resolve().parents[1] / 'exams' / 'toy_exam'


@pytest.fixture(scope='module')
def toy():
    return load_exam(TOY)


@pytest.fixture
def stacked(tmp_path, toy):
    root = tmp_path / 'ws'
    paths = synth.stacks(toy, root)
    ws = Workspace(root, toy, create=True)
    yield ws, paths, synth.fake_model(root)
    ws.close()


def images(pdf):
    """Each page's embedded image streams, as bytes."""
    doc = pdfium.PdfDocument(str(pdf))
    try:
        out = []
        for page in doc:
            objs = [o for o in page.get_objects(max_depth=1) if o.type == pdfium_c.FPDF_PAGEOBJ_IMAGE]
            out.append([bytes(o.get_data()) for o in objs])
        return out
    finally:
        doc.close()


def starts(art):
    return [e['start'] for e in art.body['entries']]


# --- sources ---

def test_adding_the_same_scan_twice_is_recognised(stacked):
    ws, paths, _ = stacked
    a, new = intake.add_source(ws, paths[0])
    b, again = intake.add_source(ws, paths[0])
    assert new and not again and a == b and len(ws.store.sources()) == 1
    assert (ws.root / 'sources' / a.file).read_bytes() == paths[0].read_bytes()
    assert starts(intake.plan(ws, a.id)) == [0, 4, 8, 12, 16, 20]        # every N, no model
    assert not intake.plan(ws, a.id).body['checked']


def test_not_a_pdf_is_refused(tmp_path, toy):
    ws = Workspace(tmp_path / 'ws', toy, create=True)
    bad = tmp_path / 'x.pdf'
    bad.write_bytes(b'not a pdf')
    with pytest.raises(ValueError):
        intake.add_source(ws, bad)
    assert ws.store.sources() == []
    ws.close()


def test_check_with_landmarks_flags_the_awkward_cases(stacked):
    ws, paths, model = stacked
    one, _ = intake.add_source(ws, paths[0])
    art = intake.reguess(ws, one.id, model=model)
    assert starts(art) == [0, 4, 7, 12, 16, 20]
    assert split.doubts(art.body, one.pages) == [[], ['short'], ['long', 'number'], [], ['start'], []]
    calls = len(model.calls)
    intake.reguess(ws, one.id, model=model)
    assert len(model.calls) == calls                 # memoised: a re-run reads nothing again


def test_orientation_source_and_submission(stacked):
    ws, paths, model = stacked
    two, _ = intake.add_source(ws, paths[1])
    art = intake.reguess(ws, two.id, model=model)
    assert all('start' in d for d in split.doubts(art.body, two.pages))   # upside down: nothing reads
    for _ in range(2):
        art = intake.edit(ws, two.id, 'turn_source', expect_rev=art.rev, author='t')
    assert Orient.from_json(art.body['orient']) == HALF and art.body['readings'] == {}
    art = intake.reguess(ws, two.id, model=model)
    assert starts(art) == [0, 4, 8, 12]
    assert split.doubts(art.body, two.pages) == [[], [], [], ['short']]

    one, _ = intake.add_source(ws, paths[0])
    art = intake.reguess(ws, one.id, model=model)
    art = intake.edit(ws, one.id, 'turn', k=4, expect_rev=art.rev, author='t')   # one exam upside down
    assert intake.sheet_orient(art.body, 17) == HALF and intake.sheet_orient(art.body, 21) == Orient()
    art = intake.reguess(ws, one.id, model=model)
    assert split.doubts(art.body, one.pages)[4] == []
    # the submission's own orientation wins over the source's
    art = intake.edit(ws, one.id, 'turn_source', expect_rev=art.rev, author='t')
    assert intake.sheet_orient(art.body, 17) == HALF and intake.sheet_orient(art.body, 21) == QUARTER
    art = intake.edit(ws, one.id, 'upright', k=4, expect_rev=art.rev, author='t')
    assert intake.sheet_orient(art.body, 17) == QUARTER


def test_landmark_crops_come_from_the_oriented_page(tmp_path):
    exam = Exam('mark', pages=1, landmarks=[Landmark('corner', Region(1, 0, 0, 300, 300), ask="corner")])
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    im = Image.new('RGB', (400, 500), 'white')
    ImageDraw.Draw(im).rectangle((0, 0, 120, 150), fill='black')      # dark top-left corner
    pdf = tmp_path / 'one.pdf'
    im.save(pdf, 'PDF', resolution=50.0)

    def reply(call):
        dark = np.asarray(call.images[0].convert('L')).mean() < 128
        return json.dumps({'value': '1' if dark else None})
    model = FakeModel(reply)
    src, _ = intake.add_source(ws, pdf)
    assert intake.reguess(ws, src.id, model=model).body['readings'] == {'0': 0}
    art = intake.plan(ws, src.id)
    art = intake.edit(ws, src.id, 'turn_source', expect_rev=art.rev, author='t')
    intake.edit(ws, src.id, 'turn_source', expect_rev=art.rev, author='t')
    assert intake.reguess(ws, src.id, model=model).body['readings'] == {'0': None}
    ws.close()


# --- accepting ---

def test_accept_copies_pages_untouched_and_the_fast_path_applies(stacked, monkeypatch):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    report = intake.accept(ws, one.id, author='t')
    assert report['new'] == ['s0001', 's0002', 's0003', 's0004', 's0005', 's0006']
    src_images = images(paths[0])
    for k, sid in enumerate(report['new']):
        made = ws.store.made()[sid]
        assert made.pages == tuple(range(4 * k, 4 * k + 4))
        assert images(ws.pdfs / f'{sid}.pdf') == [src_images[i] for i in made.pages]
    taken = []
    real = render.scan_image
    monkeypatch.setattr(render, 'scan_image', lambda *a: taken.append(1) or real(*a))
    Renderer(ws.root / 'cache' / 'check').page(ws.pdfs / 's0002.pdf', 3)
    assert taken
    # downstream: a submission like any other, no roster file needed
    assert [s.id for s in ws.store.submissions()] == report['new']
    assert ws.sync() == 6
    assert intake.state(ws, one.id) == ['made'] * 6
    # no cover sheets in a raw stack: each written submission says so, whatever the workspace default
    ws.set_defaults(cover_pages=1)
    assert ws.layout('s0002').cover_pages == 0 and intake.cover(ws, intake.plan(ws, one.id).body) == 0


def test_a_longer_plan_means_cover_sheets(stacked):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    art = intake.reguess(ws, one.id, length=ws.exam.pages + 1)
    assert intake.cover(ws, art.body) == 1 and art.body['anchor'] == 1
    intake.accept(ws, one.id)
    assert ws.layout('s0001').cover_pages == 1


def test_baked_pages_are_upright_plain_scans(tmp_path):
    exam = Exam('bake', pages=1)
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    im = Image.new('RGB', (400, 500), 'white')
    ImageDraw.Draw(im).rectangle((0, 0, 120, 150), fill='black')
    pdf = tmp_path / 'one.pdf'
    im.save(pdf, 'PDF', resolution=50.0)                              # 8 x 10 inch page
    src, _ = intake.add_source(ws, pdf)
    intake.edit(ws, src.id, 'turn_source', expect_rev=intake.plan(ws, src.id).rev, author='t')
    intake.accept(ws, src.id)
    out = ws.pdfs / 's0001.pdf'
    doc = pdfium.PdfDocument(str(out))
    page = doc[0]
    assert tuple(round(v) for v in page.get_size()) == (720, 576)      # width and height swapped
    assert render.scan_image(page) is not None                        # one full-page image: a plain scan
    doc.close()
    shown = Renderer(ws.root / 'cache' / 'x', dpi=50).page(out, 0).convert('L')
    a = np.asarray(shown)
    assert shown.size == (500, 400)
    assert a[:100, -100:].mean() < 60 and a[-100:, :100].mean() > 200   # dark corner now top right
    ws.close()


def test_writing_a_pdf_that_fails_leaves_no_temporary_file(tmp_path, monkeypatch):
    im = Image.new('RGB', (400, 500), 'white')
    pdf = tmp_path / 'one.pdf'
    im.save(pdf, 'PDF', resolution=50.0)

    def broken(self, dest, **kw):
        Path(dest).write_bytes(b'%PDF-partial')
        raise OSError("disk full")
    monkeypatch.setattr(pdfium.PdfDocument, 'save', broken)
    with pytest.raises(OSError):
        pdf_pages.write(pdf, [(0, QUARTER)], tmp_path / 'out.pdf')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['one.pdf']


def test_reaccept_rewrites_only_what_changed_and_guards_work(stacked):
    ws, paths, model = stacked
    one, _ = intake.add_source(ws, paths[0])
    intake.reguess(ws, one.id, model=model)
    intake.accept(ws, one.id)
    before = {s: (ws.pdfs / f'{s}.pdf').stat().st_mtime_ns for s in ws.store.made()}
    # grading work on the third submission (the one with the stray sheet)
    ws.store.write('s0003', 'parity/B', 'value', {'text': 'even'}, origin='manual', author='t')
    art = intake.plan(ws, one.id)
    art = intake.edit(ws, one.id, 'drop', sheet=9, expect_rev=art.rev, author='t')
    with pytest.raises(intake.Busy) as e:
        intake.accept(ws, one.id)
    assert e.value.submissions == ['s0003']
    assert (ws.pdfs / 's0003.pdf').stat().st_mtime_ns == before['s0003']
    # orientation change of a submission without work: rewritten; nothing else touched
    art = intake.edit(ws, one.id, 'restore', sheet=9, expect_rev=art.rev, author='t')
    intake.edit(ws, one.id, 'turn', k=4, expect_rev=art.rev, author='t')
    assert intake.state(ws, one.id) == ['made', 'made', 'made', 'made', 'changed', 'made']
    report = intake.accept(ws, one.id)
    assert report['rewritten'] == ['s0005'] and report['unchanged'] == 5
    after = {s: (ws.pdfs / f'{s}.pdf').stat().st_mtime_ns for s in ws.store.made()}
    assert [s for s in after if after[s] != before[s]] == ['s0005']
    # forced: the change goes through and the work stays where it was
    intake.edit(ws, one.id, 'drop', sheet=9, expect_rev=intake.plan(ws, one.id).rev, author='t')
    report = intake.accept(ws, one.id, force=True)
    assert report['rewritten'] == ['s0003'] and ws.store.made()['s0003'].pages == (7, 8, 10, 11)
    assert ws.store.get('s0003', 'parity/B', 'value') is not None
    # joining away a submission with no work removes it
    intake.edit(ws, one.id, 'join', k=0, expect_rev=intake.plan(ws, one.id).rev, author='t')
    ws.store.write('s0002', 'parity/B', 'value', {'text': 'odd'}, origin='manual', author='t')
    with pytest.raises(intake.Busy):        # s0002 would go away with its work
        intake.accept(ws, one.id)
    ws.store.delete('s0002', 'parity/B', 'value')
    ws.store.purge_history()
    report = intake.accept(ws, one.id)
    assert report['removed'] == ['s0002'] and not (ws.pdfs / 's0002.pdf').exists()
    assert 's0002' not in {s.id for s in ws.store.submissions()}


def test_ids_are_never_reused(stacked):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    intake.edit(ws, one.id, 'cut', sheet=2, expect_rev=intake.plan(ws, one.id).rev, author='t')
    report = intake.accept(ws, one.id)
    assert report['new'] == ['s0007'] and report['rewritten'] == ['s0001']


# --- who is it, and the roster file rule ---

def test_roster_and_names(stacked):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    assert identity.set_roster(ws, (ws.root / 'roster.csv').read_text()) == 10
    p = identity.people(ws)[2]
    identity.set_who(ws, 's0003', identity.label(p), author='t')
    assert ws.store.submission('s0003').name == "Casey Stubbs"
    assert ws.sync() == 6 and ws.store.submission('s0003').email == p['email']
    assert ws.store.submission('s0003').student == p['id']
    identity.set_who(ws, 's0004', "Someone Unlisted", author='t')
    assert ws.store.submission('s0004').name == "Someone Unlisted"
    identity.set_who(ws, 's0004', "", author='t')
    assert ws.store.submission('s0004').name == ""
    with pytest.raises(ValueError):
        identity.parse_roster("Email\nx@example.invalid\n")
    assert identity.parse_roster("First Name,Last Name\nAda,Lovelace\n")[0]['name'] == "Ada Lovelace"


def test_handed_pdfs_wait_on_names_unless_the_file_name_carries_an_id(tmp_path, toy):
    root = synth.generate(toy, tmp_path / 'ws', synth.load_cases(toy))
    pdfs = sorted((root / 'pdfs').glob('*.pdf'))
    pdfs[0].rename(pdfs[0].with_name('quiz-90000002.pdf'))      # the second person's ID
    pdfs[1].rename(pdfs[1].with_name('90000001_90000003.pdf'))  # two IDs: nobody
    ws = Workspace(root, toy)
    n = ws.sync()
    assert identity.overview(ws)['left'] == n and all(not s.name for s in ws.store.submissions())
    identity.set_roster(ws, (root / 'roster.csv').read_text())
    assert ws.store.submission('quiz-90000002').name == "Synthetic Student 02"
    assert identity.overview(ws)['left'] == n - 1
    # a person's word stands: a cleared pick is not taken from the file name again
    identity.set_who(ws, 'quiz-90000002', "", author='t')
    ws.sync()
    assert ws.store.submission('quiz-90000002').name == "" and identity.overview(ws)['left'] == n
    ws.close()


# --- failures and races ---

def test_failed_landmark_calls_are_counted_not_read_as_nothing(stacked):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    art = intake.reguess(ws, one.id, model=FakeModel(lambda call: None))     # every call fails
    assert art.body['failed'] > 0 and not art.body['checked']
    assert art.body['readings'] == {}                       # failed sheets are unread, not read as None
    assert split.doubts(art.body, one.pages) == [[]] * 6
    assert intake.reguess(ws, one.id).body.get('failed') is None


def test_a_submission_whose_pages_are_all_dropped_is_removed(stacked):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    art = intake.plan(ws, one.id)
    for sheet in range(20, 24):                             # every sheet of the last submission
        art = intake.edit(ws, one.id, 'drop', sheet=sheet, expect_rev=art.rev, author='t')
    assert art.body['entries'][-1]['id'] == 's0006' and intake.removed(ws, one.id) == ['s0006']
    ws.store.write('s0006', 'parity/B', 'value', {'text': 'odd'}, origin='manual', author='t')
    with pytest.raises(intake.Busy) as e:
        intake.accept(ws, one.id)
    assert e.value.submissions == ['s0006']
    ws.store.delete('s0006', 'parity/B', 'value')
    report = intake.accept(ws, one.id)
    assert report['removed'] == ['s0006'] and not (ws.pdfs / 's0006.pdf').exists()
    assert 's0006' not in ws.store.made() and intake.removed(ws, one.id) == []
    assert intake.plan(ws, one.id).body['entries'][-1]['id'] is None
    assert intake.state(ws, one.id)[:5] == ['made'] * 5


def test_a_plan_edit_during_accept_leaves_pdfs_alone(stacked, monkeypatch):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    before = {p.name: p.read_bytes() for p in ws.pdfs.glob('*.pdf')}
    art = intake.edit(ws, one.id, 'turn', k=1, expect_rev=intake.plan(ws, one.id).rev, author='t')
    intake.edit(ws, one.id, 'cut', sheet=2, expect_rev=art.rev, author='t')    # a new submission too
    counter = ws.store.setting('intake_next')
    real = intake.pdf_pages.write

    def write_then_edit(*a):
        real(*a)
        intake.edit(ws, one.id, 'flip', k=0, expect_rev=intake.plan(ws, one.id).rev, author='u')
    monkeypatch.setattr(intake.pdf_pages, 'write', write_then_edit)
    with pytest.raises(Conflict):
        intake.accept(ws, one.id)
    assert {p.name: p.read_bytes() for p in ws.pdfs.glob('*.pdf')} == before
    assert not [p for p in ws.pdfs.iterdir() if p.suffix == '.part']
    assert ws.store.setting('intake_next') == counter


def test_a_page_selection_does_not_hold_a_submission(stacked):
    ws, paths, _ = stacked
    one, _ = intake.add_source(ws, paths[0])
    intake.accept(ws, one.id)
    ws.set_pages('s0003', ws.exam.questions['parity'], [1, 2], "", author='t')
    intake.edit(ws, one.id, 'drop', sheet=9, expect_rev=intake.plan(ws, one.id).rev, author='t')
    assert intake.accept(ws, one.id)['rewritten'] == ['s0003']        # not Busy: settings are not work


def test_orientation_codes():
    for o in (Orient(), HALF, QUARTER, Orient(3, True)):
        assert Orient.from_code(o.code) == o
    assert HALF.code == '20' and Orient(1, True).code == '11'
    for bad in ('40', '0', '002', '0x', None):
        with pytest.raises(ValueError):
            Orient.from_code(bad)


def test_undo_steps_back_one_edit_at_a_time(stacked):
    ws, paths, _ = stacked
    src, _ = intake.add_source(ws, paths[0])
    starts = lambda: [e['start'] for e in intake.plan(ws, src.id).body['entries']]
    step = lambda op, **kw: intake.edit(ws, src.id, op, expect_rev=intake.plan(ws, src.id).rev, author='t', **kw)
    undo = lambda: intake.undo(ws, src.id, expect_rev=intake.plan(ws, src.id).rev)
    first = starts()
    step('move', k=1, delta=1)
    second = starts()
    step('move', k=2, delta=1)
    assert starts() not in (first, second)
    undo()
    assert starts() == second
    undo()                                  # a second undo goes further back; it does not redo
    assert starts() == first
    step('move', k=1, delta=-1)             # a new edit after undoing: undo returns to where it started
    undo()
    assert starts() == first
    while intake.can_undo(ws, src.id):
        undo()
    with pytest.raises(ValueError):
        undo()


# --- the print area: submission, then its scan, then the workspace, then the package ---

def test_print_area_resolves_submission_scan_workspace_package(tmp_path):
    exam = Exam('pa', pages=1, scan_window=(10, 20, 990, 980))
    ws = Workspace(tmp_path / 'ws', exam, create=True)
    try:
        ws.store.set_made(Made('s1', 'src1', (0,), {}, 'now'))
        ws.store.set_made(Made('s2', 'src1', (1,), {}, 'now'))
        win = lambda sid: ws.layout(sid).window
        assert ws.source_of('s1') == 'src1' and ws.source_of('handed') is None
        assert win('s1') == win('handed') == (10, 20, 990, 980)              # the package's
        ws.set_defaults(scan_window=(0, 0, 1000, 1000))
        assert win('s1') == win('handed') == (0, 0, 1000, 1000)              # the workspace's
        ws.set_source_window('src1', (30, 30, 900, 900), author=None, expect_rev=None)
        assert win('s1') == win('s2') == (30, 30, 900, 900)                  # the scan's, for all from it
        assert win('handed') == (0, 0, 1000, 1000)                           # no scan: no such level
        ws.set_layout('s1', cover_pages=1, scan_window=(40, 40, 800, 800), author=None)
        assert win('s1') == (40, 40, 800, 800) and win('s2') == (30, 30, 900, 900)
        assert ws.layout('s1').cover_pages == 1
        # clearing each level falls back to the next
        ws.set_layout('s1', cover_pages=1, scan_window=None, author=None)
        assert win('s1') == (30, 30, 900, 900) and ws.layout('s1').cover_pages == 1
        rev = ws.store.get('src1', '', 'print_area').rev
        with pytest.raises(Conflict):
            ws.set_source_window('src1', None, author=None, expect_rev=rev + 99)
        ws.set_source_window('src1', None, author=None, expect_rev=rev)
        assert ws.source_window('src1') is None and win('s1') == (0, 0, 1000, 1000)
        ws.clear_scan_window()
        assert win('s1') == (10, 20, 990, 980)
    finally:
        ws.close()
