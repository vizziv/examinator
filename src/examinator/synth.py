"""Synthetic workspaces: fabricated submissions built from an exam's definition.

An exam package may ship `fixtures.py` with `cases() -> list[Case]`. Each case
becomes one fabricated submission: a scan-like PDF (cover pages, the printed
page inset in the scan window, answers typed into the field regions), a roster
row, and scripted model replies, so the whole pipeline runs offline.

`selftest` runs every case end to end with the scripted model, verifies each
parse as a grader would, and compares the grades with `Case.expect`. It doubles
as a test of every question and variant of the exam.

Nothing here touches real student data: every byte is derived from the exam
definition, instructor-provided blank PDFs and the cases.
"""
from __future__ import annotations

import csv
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

from .core.defs import Exam, Region
from .core.runner import Runner
from .core.workflow import question_status
from .core.workspace import Workspace
from .pdf.render import Renderer

PAGE = (850, 1100)      # letter at 100 px/inch
SCRIPT = 'fake_model.json'


@dataclass
class Case:
    name: str                                                    # the submission id
    answers: Mapping[str, Any] = field(default_factory=dict)     # question -> parse body
    variants: Mapping[str, str] = field(default_factory=dict)    # question -> printed variant
    expect: Mapping[str, Any] = field(default_factory=dict)      # grid column -> value, 'blank', or None (undecided)
    blank: str | Path | None = None                              # blank exam PDF to draw on
    readings: Mapping[str, str | None] = field(default_factory=dict)   # cue name -> scripted reading override


def load_cases(exam: Exam) -> list[Case]:
    if exam.path is None:
        raise ValueError("the exam was not loaded from a package")
    return list(importlib.import_module(f'{exam.path.name}.fixtures').cases())


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _box(region: Region) -> tuple[int, int, int, int]:
    w, h = PAGE
    return (region.left * w // 1000, region.top * h // 1000, region.right * w // 1000, region.bottom * h // 1000)


def _text(answer: Any) -> str:
    return "" if answer is None else str(answer)


def cue_reading(cue, variant: str | None) -> str | None:
    """A reading that points at `variant`: the first matching key of the cue's map."""
    return next((k for k, v in cue.reads.items() if v == variant), None)


def _printed_pages(exam: Exam, case: Case, renderer: Renderer) -> list[Image.Image]:
    """The printed exam as images: the blank PDF if given, else placeholder pages."""
    pages = []
    for p in range(1, exam.pages + 1):
        if case.blank is not None:
            im = renderer.page(case.blank, p - 1).convert('RGB').resize(PAGE)
        else:
            im = Image.new('RGB', PAGE, 'white')
            d = ImageDraw.Draw(im)
            d.text((40, 30), f"{exam.title}  (synthetic)  page {p}", fill='black', font=_font(18))
            for q in exam.questions.values():
                for cue in q.cues:
                    if cue.region is not None and cue.region.page == p:
                        l, t, _, _ = _box(cue.region)
                        shown = case.readings.get(cue.name) or cue_reading(cue, case.variants.get(q.id)) or ""
                        d.text((l + 40, t + 30), f"{q.title}: {shown}", fill='black', font=_font(16))
                for f in q.fields:
                    if f.region is not None and f.region.page == p:
                        l, t, r, b = _box(f.region)
                        d.rectangle((l, t, r, b), outline='black')
                        d.text((max(l - 60, 5), t + 5), f.label, fill='black', font=_font(16))
            for lm in exam.landmarks:
                text = landmark_text(lm, p)
                if text:
                    l, t, r, b = _box(lm.region)
                    d.text((l + 6, t + 4), text, fill='black', font=_font(max(14, min(30, (b - t) - 8))))
            for f in exam.identity:
                if f.region.page == p:
                    l, t, r, b = _box(f.region)
                    d.text((l, t - 22), f"{f.name}:", fill='black', font=_font(16))
                    d.line((l, b, r, b), fill='black')
        pages.append(im)
    return pages


def landmark_text(lm, page: int) -> str | None:
    """What a landmark shows on a printed page: its reading for that page, if any."""
    if lm.reads:
        return next((k for k, v in lm.reads.items() if v == page), None)
    return str(page) if page >= lm.region.page else None


def _draw_identity(exam: Exam, pages: list[Image.Image], person: Mapping[str, str]) -> None:
    """Write a person into the exam's identity boxes (the box's roster field, else its name)."""
    for f in exam.identity:
        text = person.get(f.roster or f.name) or ""
        if text and 1 <= f.region.page <= len(pages):
            l, t, _, _ = _box(f.region)
            ImageDraw.Draw(pages[f.region.page - 1]).text((l + 14, t + 12), text, fill=(20, 30, 140), font=_font(26))


def _draw_answers(exam: Exam, case: Case, pages: list[Image.Image]) -> None:
    for qid, body in case.answers.items():
        q = exam.questions[qid]
        regions = [f for f in q.fields if f.region is not None]
        if isinstance(body, Mapping):
            for f in regions:
                l, t, _, _ = _box(f.region)
                ImageDraw.Draw(pages[f.region.page - 1]).text(
                    (l + 12, t + 14), _text(body.get(f.label)), fill=(20, 30, 140), font=_font(20))
        elif regions:
            l, t, _, _ = _box(regions[0].region)
            ImageDraw.Draw(pages[regions[0].region.page - 1]).multiline_text(
                (l + 40, t + 20), _text(body), fill=(20, 30, 140), font=_font(14))


def _scan(exam: Exam, printed: list[Image.Image]) -> list[Image.Image]:
    """Inset each printed page in the scan window and prepend cover pages."""
    w, h = PAGE
    l, t, r, b = exam.scan_window
    box = (l * w // 1000, t * h // 1000, r * w // 1000, b * h // 1000)
    out = []
    for _ in range(exam.cover_pages):
        cover = Image.new('RGB', PAGE, 'white')
        ImageDraw.Draw(cover).text((60, 60), "Scan cover page (synthetic)", fill='black', font=_font(24))
        out.append(cover)
    for im in printed:
        page = Image.new('RGB', PAGE, 'white')
        page.paste(im.resize((box[2] - box[0], box[3] - box[1])), box[:2])
        out.append(page)
    return out


def script_for(exam: Exam, case: Case) -> dict[str, Any]:
    """Scripted model replies for one case: `'question|stage' -> text`, and for
    the variant stage a map from each cue's question text to its JSON reply."""
    script: dict[str, Any] = {}
    for qid, q in exam.questions.items():
        if q.cues:
            replies = {}
            for cue in q.cues:
                if cue.fn is not None:
                    continue
                reading = (case.readings[cue.name] if cue.name in case.readings
                           else cue_reading(cue, case.variants.get(qid)))
                replies[cue.ask] = json.dumps({'quote': "synthetic", 'value': reading})
            script[f'{qid}|variant'] = replies
        if qid not in case.answers:
            continue
        body = case.answers[qid]
        if isinstance(body, Mapping):
            script[f'{qid}|transcript'] = "\n".join(
                f"{label}: {'BLANK' if body.get(label) is None else body[label]}" for label in q.labels)
            script[f'{qid}|parse'] = json.dumps({'answers': {label: body.get(label) for label in q.labels}})
        else:
            script[f'{qid}|transcript'] = script[f'{qid}|parse'] = _text(body)
    return script


def generate(exam: Exam, root: str | Path, cases: list[Case]) -> Path:
    """Create a synthetic workspace at `root` (which must not already hold one)."""
    root = Path(root)
    pdfs = root / 'pdfs'
    if pdfs.exists():
        raise FileExistsError(f"{pdfs} already exists")
    pdfs.mkdir(parents=True, mode=0o700)
    renderer = Renderer(root / 'cache' / 'blanks', dpi=100)
    script = {}
    with (pdfs / 'roster.csv').open('w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['Submission ID', 'First Name', 'Last Name', 'Email'])
        for i, case in enumerate(cases, 1):
            printed = _printed_pages(exam, case, renderer)
            _draw_answers(exam, case, printed)
            _draw_identity(exam, printed, {'name': f"Synthetic Student {i:02d}", 'id': f"9{i:07d}",
                                           'email': f"synthetic{i:02d}@example.invalid"})
            scan = _scan(exam, printed)
            scan[0].save(pdfs / f'{case.name}.pdf', 'PDF', save_all=True,
                         append_images=scan[1:], resolution=100.0)
            w.writerow([case.name, "Synthetic", f"Student {i:02d}", f"synthetic{i:02d}@example.invalid"])
            script[case.name] = script_for(exam, case)
    (root / SCRIPT).write_text(json.dumps(script, indent=1))
    return root


def fake_model(root: str | Path):
    """The scripted model for a synthetic workspace."""
    from .model.fake import FakeModel
    script = json.loads((Path(root) / SCRIPT).read_text())

    def reply(call):
        submission, question, stage = call.tag
        entry = script.get(submission, {}).get(f'{question}|{stage}')
        if isinstance(entry, dict):     # the variant stage: one reply per cue question
            return next((v for ask, v in entry.items() if ask in call.prompt), None)
        return entry

    return FakeModel(reply)


def selftest(exam: Exam, root: str | Path, cases: list[Case] | None = None) -> list[str]:
    """Generate, run every stage with the scripted model, verify as a grader
    would, and compare with each case's `expect`. Returns the mismatches."""
    cases = load_cases(exam) if cases is None else cases
    generate(exam, root, cases)
    ws = Workspace(root, exam)
    ws.sync_roster()
    runner = Runner(exam, ws, model=fake_model(root))
    problems = []
    try:
        for case in cases:
            for qid, q in exam.questions.items():
                if not q.stages:        # decided by hand only: nothing to run
                    continue
                runner.run_from(case.name, qid, q.stages[0].name)
                arts = ws.store.for_question(case.name, qid)
                for kind in ('variant', 'parse'):
                    a = arts.get(kind)
                    if a is not None and not a.verified:
                        try:
                            runner.verify(case.name, qid, kind, author='selftest', expect_rev=a.rev)
                        except ValueError:
                            pass        # unresolved variant or review marker: left for "a person"
                runner.recheck(case.name, qid)
                status = question_status(q, ws.store.for_question(case.name, qid))
                for part, st in status.items():
                    col = q.column(part)
                    if col not in case.expect:
                        continue
                    want = case.expect[col]
                    got = 'blank' if 'blank' in st.flags else st.value
                    counts_ok = st.counts if want is not None else not st.counts
                    if got != want or not counts_ok:
                        problems.append(f"{case.name} {col}: expected {want!r}, got {got!r} "
                                        f"({st.state}{': ' + st.reason if st.reason else ''})")
    finally:
        runner.close()
        ws.close()
    return problems


# --- stacks of scans, for intake ---------------------------------------------------

def stacks(exam: Exam, root: str | Path, cases: list[Case] | None = None) -> list[Path]:
    """Synthetic "big bad" scans for intake, in `root/incoming/`, with a roster
    (`root/roster.csv`) and scripted landmark readings (`root/fake_model.json`).

    `stack-1.pdf`: six students, one missing printed page 3, one with a stray
    blank sheet after printed page 2, one put in upside down. `stack-2.pdf`:
    fed in upside down as a whole, three students and the first two sheets of a
    fourth, so its length is not a multiple of the exam's. The model reads a
    landmark, or an identity box, only on a sheet turned the right way up.

    Who is who (`STACK_PEOPLE`, the exam's identity boxes): what the scripted
    model reads is in `STACK_READS`: clean reads, an ID misread by one
    character, a misread name, an ID that belongs to someone else, an empty ID
    box, an unreadable name, and a student not on the roster, which also lists
    one person with no exam."""
    from .pdf.orient import HALF, IDENTITY
    cases = load_cases(exam) if cases is None else cases
    root = Path(root)
    (root / 'incoming').mkdir(parents=True, exist_ok=True, mode=0o700)
    renderer = Renderer(root / 'cache' / 'blanks', dpi=100)
    names = [name for name, _ in STACK_PEOPLE]

    def student(k: int) -> list[tuple[Image.Image, int | None]]:
        case = cases[k % len(cases)]
        printed = _printed_pages(exam, case, renderer)
        _draw_answers(exam, case, printed)
        _draw_identity(exam, printed, {'name': names[k], 'id': STACK_PEOPLE[k][1]})
        sheets = _scan(exam, printed)[exam.cover_pages:]       # a raw stack has no cover sheets
        return list(zip(sheets, range(1, len(sheets) + 1)))

    def without(sheets, printed_page):
        return [x for x in sheets if x[1] != printed_page]

    def stray(sheets, after):
        i = next(j for j, x in enumerate(sheets) if x[1] == after) + 1
        return sheets[:i] + [(Image.new('RGB', PAGE, 'white'), None)] + sheets[i:]

    def turned(sheets, o, k):
        return [(o.apply(im), p, o, k) for im, p in sheets]

    one = (turned(student(0), IDENTITY, 0) + turned(without(student(1), 3), IDENTITY, 1)
           + turned(stray(student(2), 2), IDENTITY, 2) + turned(student(3), IDENTITY, 3)
           + turned(student(4), HALF, 4) + turned(student(5), IDENTITY, 5))
    two = [x for k in (6, 7, 8) for x in turned(student(k), HALF, k)] + turned(student(9), HALF, 9)[:2]
    script = json.loads((root / SCRIPT).read_text()) if (root / SCRIPT).exists() else {}
    out = []
    for name, sheets in (('stack-1.pdf', one), ('stack-2.pdf', two)):
        path = root / 'incoming' / name
        images = [im for im, _, _, _ in sheets]
        images[0].save(path, 'PDF', save_all=True, append_images=images[1:], resolution=100.0)
        import hashlib
        sid = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        replies = {}
        for i, (_, page, o, k) in enumerate(sheets):
            # the model reads a sheet only when it is shown the right way up: undoing `o`
            up = HALF if o == HALF else IDENTITY
            replies[f'{i}@{up.turn}{int(up.flip)}|landmark'] = {
                lm.ask: json.dumps({'quote': "synthetic", 'value': landmark_text(lm, page) if page else None})
                for lm in exam.landmarks}
            if up != IDENTITY:      # shown as scanned, it reads as nothing
                replies[f'{i}@00|landmark'] = {lm.ask: json.dumps({'quote': "", 'value': None})
                                               for lm in exam.landmarks}
            boxes = [f for f in exam.identity if f.region.page == page]
            if boxes:
                said = STACK_READS[k]
                replies[f'{i}@{up.turn}{int(up.flip)}|identity'] = {
                    f.ask: json.dumps({'value': said.get(f.roster or f.name)}) for f in boxes}
                if up != IDENTITY:
                    replies[f'{i}@00|identity'] = {f.ask: json.dumps({'value': None}) for f in boxes}
        script[sid] = replies
        out.append(path)
    (root / SCRIPT).write_text(json.dumps(script, indent=1))
    with (root / 'roster.csv').open('w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['Name', 'Email', 'Student ID'])
        for k, (n, id) in enumerate(STACK_ROSTER, 1):
            w.writerow([n, f"synthetic{k:02d}@example.invalid", id])
    return out


# Who the students in the synthetic stacks are (name, ID), in stack order ...
STACK_PEOPLE = [
    ("Avery Testwell", "90412873"), ("Blake Mockford", "91736250"), ("Casey Stubbs", "92058461"),
    ("Devon Fakely", "93681724"), ("Emery Sampleton", "94207536"), ("Finley Dummond", "95830142"),
    ("Gray Placeholt", "96174385"), ("Harper Fixtura", "97352618"), ("Indigo Exemplar", "98546039"),
    ("Jules Synthe", "99021457")]
# ... what the scripted model reads in their boxes (by roster field) ...
STACK_READS = [
    {'name': "Avery Testwell", 'id': "90412873"},       # clean
    {'name': "Blake Mockford", 'id': "91786250"},       # the ID misread by one character
    {'name': "Kasey Stubs", 'id': "92058461"},          # the name misread
    {'name': "Devon Fakely", 'id': "96174385"},         # the ID is Gray Placeholt's
    {'name': "Emery Sampleton", 'id': "94207536"},      # clean (once the exam is turned upright)
    {'name': "Finley Dummond", 'id': "95830142"},       # not on the roster
    {'name': "Gray Placeholt", 'id': None},             # the ID box left empty
    {'name': None, 'id': "97352618"},                   # the name unreadable
    {'name': "Indigo Exemplar", 'id': "98546039"},
    {'name': "Jules Synthe", 'id': "99021457"}]
# ... and the roster: everyone but Finley Dummond, and one person who sat no exam.
STACK_ROSTER = [p for p in STACK_PEOPLE if p[0] != "Finley Dummond"] + [("Kit Absentia", "90655318")]
