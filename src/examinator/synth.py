"""Synthetic workspaces: fabricated submissions built from an exam's definition.

An exam package may ship `fixtures.py` with `cases() -> list[Case]`. Each case
becomes one fabricated submission: a scan-like PDF (cover pages, the printed
page inset in the print area, answers typed into the field regions), a roster
row, and scripted model replies, so the whole pipeline runs offline. Each
reader writes its own replies (`reader.script(fields, given)`).

`selftest` runs every case end to end with the scripted model: it reads every
question, verifies every value as a grader would, runs every check, and
compares the grades with `Case.expect`. It doubles as a test of every question
and printing of the exam.

Which printing a case is, is a fact about the case: `Case.printed` says what is
printed in each printed field (`core.printed.Printed`), so the placeholder pages
show it and the scripted model reads it; `Case.blank` names the blank PDF of
that printing to draw on.

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

from .core.defs import Exam, Question, Region
from .core.printed import Printed
from .core.runner import Runner
from .core.workspace import Workspace
from .pdf.render import Renderer

PAGE = (850, 1100)      # letter at 100 px/inch
SCRIPT = 'fake_model.json'


@dataclass
class Case:
    name: str                                                    # the submission id
    # question -> {label: text or None (blank)} for the fields its reader reads; or, for a
    # question with one such field (pseudocode, say), the text itself
    answers: Mapping[str, Any] = field(default_factory=dict)
    # question -> {printed field label: what is printed there (a reading, or the value it reads to);
    # None: the model cannot read it}
    printed: Mapping[str, Mapping[str, str | None]] = field(default_factory=dict)
    # grid column -> value, 'blank', or None (must not count: undecided, or waiting for a person)
    expect: Mapping[str, Any] = field(default_factory=dict)
    blank: str | Path | None = None                              # blank exam PDF of this printing, to draw on


def load_cases(exam: Exam) -> list[Case]:
    if exam.path is None:
        raise ValueError("the exam was not loaded from a package")
    return list(importlib.import_module(f'{exam.path.name}.fixtures').cases())


class BadCase(ValueError):
    """A fixture case that does not fit the exam."""


def given(q: Question, body: Any) -> dict[str, str | None]:
    """A case's answers to a question as `{label: text or None}`."""
    if isinstance(body, Mapping):
        return dict(body)
    return {q.written[0].label: body}


def check_cases(exam: Exam, cases: list[Case]) -> None:
    """`BadCase` naming the case and the key for any case that does not fit the exam."""
    columns = [col for q in exam.questions.values() if q.checks for col in q.columns()]
    names = [c.name for c in cases]
    for case in cases:
        def bad(what):
            return BadCase(f"case {case.name!r}: {what}")
        if names.count(case.name) > 1:
            raise bad("two cases have this name")
        for qid, body in case.answers.items():
            q = exam.questions.get(qid)
            if q is None:
                raise bad(f"answers: no question {qid!r} (questions: {list(exam.questions)})")
            written = [f.label for f in q.written]
            if isinstance(body, Mapping):
                unknown = [k for k in body if k not in written]
                if unknown:
                    raise bad(f"answers[{qid!r}]: no written field {unknown[0]!r} (fields: {written})")
            elif body is not None and not isinstance(body, str):
                raise bad(f"answers[{qid!r}]: expected {{label: text or None}}, text, or None")
            elif len(written) != 1:
                raise bad(f"answers[{qid!r}]: expected {{label: text or None}} for the fields {written}")
        for qid, values in case.printed.items():
            q = exam.questions.get(qid)
            if q is None:
                raise bad(f"printed: no question {qid!r}")
            printed = [f.label for f in q.fields if isinstance(f.read, Printed)]
            for label in values:
                if label not in printed:
                    raise bad(f"printed[{qid!r}]: no printed field {label!r} (printed fields: {printed})")
        for col in case.expect:
            if col not in columns:
                raise bad(f"expect: no grid column {col!r} (columns: {columns})")


def _new_root(root: Path) -> None:
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(f"{root} already exists and is not empty: give a new directory")


class _Building:
    """Create `root` for a new synthetic workspace; on failure leave nothing behind."""

    def __init__(self, root: Path):
        _new_root(root)
        self.root, self.existed = root, root.exists()

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.root

    def __exit__(self, kind, *_):
        if kind is not None:
            import shutil
            if self.existed:
                for child in self.root.iterdir():
                    shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
            else:
                shutil.rmtree(self.root, ignore_errors=True)
        return False


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
                for f in q.fields:
                    if f.region is None or f.region.page != p:
                        continue
                    if isinstance(f.read, Printed):
                        l, t, _, _ = _box(f.region)
                        shown = f.read.reading(case.printed.get(q.id, {}).get(f.label)) or ""
                        d.text((l + 40, t + 30), f"{q.title}: {shown}", fill='black', font=_font(16))
                    elif f.read is None:
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
        values = given(q, body)
        for f in q.written:
            text = _text(values.get(f.label))
            if f.region is None or not text:
                continue
            l, t, _, _ = _box(f.region)
            draw = ImageDraw.Draw(pages[f.region.page - 1])
            if '\n' in text:
                draw.multiline_text((l + 40, t + 20), text, fill=(20, 30, 140), font=_font(14))
            else:
                draw.text((l + 12, t + 14), text, fill=(20, 30, 140), font=_font(20))


def _scan(exam: Exam, printed: list[Image.Image]) -> list[Image.Image]:
    """Inset each printed page in the print area and prepend cover pages."""
    w, h = PAGE
    l, t, r, b = exam.print_area
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


def script_for(exam: Exam, case: Case) -> dict[str, str]:
    """Scripted model replies for one case, `'question|step' -> text`: what each
    reader would be told to read the case's answers and printed fields. A
    question or printed field the case does not mention has no reply."""
    script: dict[str, str] = {}
    for qid, q in exam.questions.items():
        for reader, fields in q.groups():
            make = getattr(reader, 'script', None)
            if not callable(make):
                continue
            if all(f.read is not None for f in fields):       # fields with readers of their own
                values = case.printed.get(qid, {})
            elif qid in case.answers:
                values = given(q, case.answers[qid])
            else:
                continue
            for step, reply in make(fields, values).items():
                script[f'{qid}|{step}'] = reply
    return script


def generate(exam: Exam, root: str | Path, cases: list[Case]) -> Path:
    """Create a synthetic workspace at `root`, a new or empty directory. Checks
    the cases first (`check_cases`); a failure leaves no partial workspace."""
    root = Path(root)
    check_cases(exam, cases)
    with _Building(root):
        return _generate(exam, root, cases)


def _generate(exam: Exam, root: Path, cases: list[Case]) -> Path:
    pdfs = root / 'pdfs'
    pdfs.mkdir(mode=0o700)
    renderer = Renderer(root / 'cache' / 'blanks', dpi=100)
    script = {}
    with (root / 'roster.csv').open('w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['Name', 'Student ID', 'Email'])
        for i, case in enumerate(cases, 1):
            printed = _printed_pages(exam, case, renderer)
            _draw_answers(exam, case, printed)
            _draw_identity(exam, printed, {'name': f"Synthetic Student {i:02d}", 'id': f"9{i:07d}",
                                           'email': f"synthetic{i:02d}@example.invalid"})
            scan = _scan(exam, printed)
            scan[0].save(pdfs / f'{case.name}.pdf', 'PDF', save_all=True,
                         append_images=scan[1:], resolution=100.0)
            w.writerow([f"Synthetic Student {i:02d}", f"9{i:07d}", f"synthetic{i:02d}@example.invalid"])
            script[case.name] = script_for(exam, case)
    (root / SCRIPT).write_text(json.dumps(script, indent=1))
    return root


class NoScript(FileNotFoundError):
    """`--fake` on a workspace that `synth` did not make."""


def fake_model(root: str | Path):
    """The scripted model for a synthetic workspace. `NoScript` if `root` has none."""
    from .model.fake import FakeModel
    path = Path(root) / SCRIPT
    if not path.is_file():
        raise NoScript(f"{root} has no scripted model ({SCRIPT}): it was not made by `synth`, "
                       "or `synth` did not finish")
    script = json.loads(path.read_text())

    def reply(call):
        submission, question, step = call.tag
        entry = script.get(submission, {}).get(f'{question}|{step}')
        if isinstance(entry, dict):     # intake: one reply per landmark's or box's question
            return next((v for ask, v in entry.items() if ask in call.prompt), None)
        return entry

    return FakeModel(reply)


def selftest(exam: Exam, root: str | Path, cases: list[Case] | None = None) -> list[str]:
    """Generate, read every question with the scripted model, verify every value
    as a grader would, run every check, and compare with each case's `expect`.
    Returns the mismatches."""
    cases = load_cases(exam) if cases is None else cases
    generate(exam, root, cases)
    ws = Workspace(root, exam)
    ws.sync()
    runner = Runner(exam, ws, model=fake_model(root))
    problems = []
    try:
        for case in cases:
            for qid in exam.questions:
                runner.read(case.name, qid)
            for k, v in runner.values(case.name).items():
                if not v.is_settled and not v.marker:
                    qid, label = k.split('/', 1)
                    runner.verify(case.name, qid, label, author='selftest', expect_rev=v.rev)
            runner.refresh(case.name, questions=list(exam.questions))
            sheet = runner.sheet(case.name)
            for qid, q in exam.questions.items():
                for c in q.checks:
                    col = q.column(c.name)
                    if col not in case.expect:
                        continue
                    st = sheet.status(exam, q, c)
                    want = case.expect[col]
                    got = 'blank' if 'blank' in st.flags else st.value
                    # None: it must not count (undecided, or a proposal still waiting for a person)
                    ok = not st.counts if want is None else (got == want and st.counts)
                    if not ok:
                        problems.append(f"{case.name} {col}: expected {want!r}, got {got!r} "
                                        f"({st.state}{': ' + st.reason if st.reason else ''})")
    finally:
        runner.close()
        ws.close()
    return problems


# --- stacks of scans, for intake ---------------------------------------------------

def stacks(exam: Exam, root: str | Path, cases: list[Case] | None = None) -> list[Path]:
    """Synthetic "big bad" scans for intake, in `root/incoming/` (`root` a new or
    empty directory), with a roster (`root/roster.csv`) and scripted landmark
    readings (`root/fake_model.json`).

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
    cases = load_cases(exam) if cases is None else cases
    root = Path(root)
    check_cases(exam, cases)
    with _Building(root):
        return _stacks(exam, root, cases)


def _stacks(exam: Exam, root: Path, cases: list[Case]) -> list[Path]:
    from .pdf.orient import HALF, IDENTITY
    (root / 'incoming').mkdir(mode=0o700)
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
    script = {}
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
            replies[f'{i}@{up.code}|landmark'] = {
                lm.ask: json.dumps({'quote': "synthetic", 'value': landmark_text(lm, page) if page else None})
                for lm in exam.landmarks}
            if up != IDENTITY:      # shown as scanned, it reads as nothing
                replies[f'{i}@00|landmark'] = {lm.ask: json.dumps({'quote': "", 'value': None})
                                               for lm in exam.landmarks}
            boxes = [f for f in exam.identity if f.region.page == page]
            if boxes:
                said = STACK_READS[k]
                replies[f'{i}@{up.code}|identity'] = {
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
