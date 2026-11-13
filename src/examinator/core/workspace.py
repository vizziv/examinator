"""A workspace: one exam's data on disk.

    workspace/
      sources/     scanned PDFs as added, many submissions each (intake: `core.intake`)
      pdfs/        ID.pdf per submission
      exam.sqlite
      cache/       rendered pages
      exports/

Every submission is named by its pick (`core.identity`), made on the scans
page. For PDFs handed in already split there is one programmatic way in: a file
name that carries a roster ID (`identity.by_file`).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from PIL import Image

from ..pdf.layout import Layout
from ..pdf.render import Renderer
from . import overrides
from .defs import Exam, Field, Question, Region
from .store import Store, Submission

PRINT_AREA = '@print_area'      # its override path


class Workspace:
    def __init__(self, root: str | Path, exam: Exam, *, create: bool = False):
        self.root = Path(root)
        self.exam = exam
        self.pdfs = self.root / 'pdfs'
        if not self.pdfs.is_dir():
            if not create:
                raise FileNotFoundError(f"no pdfs/ folder in {self.root}")
            self.pdfs.mkdir(parents=True, mode=0o700)
        for d in ('cache', 'exports'):
            (self.root / d).mkdir(exist_ok=True, mode=0o700)
        self.store = Store(self.root / 'exam.sqlite')
        self.renderer = Renderer(self.root / 'cache')

    def close(self) -> None:
        self.store.close()

    def sync(self) -> int:
        """Record every PDF in `pdfs/` as a submission, named by its pick."""
        from . import identity
        pdfs = [p for p in sorted(self.pdfs.glob('*.pdf')) if not p.is_symlink()]
        known = {s.id for s in self.store.submissions()}
        self.store.put_submissions([Submission(p.stem, "", "", p.name) for p in pdfs if p.stem not in known])
        identity.by_file(self)
        self.store.put_submissions([identity.submission(self, p.stem, p.name) for p in pdfs])
        return len(pdfs)

    # --- what applies to one submission: `overrides.Layers` resolves it ---
    #
    # Each value is the submission's own override, else its source scan's (every
    # submission intake made from that file), else the exam's, else the package's.

    def layers(self, submission: str | None = None, *, source: str | None = None) -> overrides.Layers:
        return overrides.Layers(self.exam, self.store, submission=submission, source=source)

    def source_of(self, submission: str) -> str | None:
        """The source scan intake made this submission from; None if it was handed in split."""
        m = self.store.made_of(submission)
        return m.source if m else None

    def layout(self, submission: str) -> Layout:
        """This submission's print area."""
        return Layout(tuple(self.layers(submission).value(PRINT_AREA)))

    def source_layout(self, source: str | None) -> Layout:
        """A source scan's sheets, with its print area."""
        return Layout(tuple(self.layers(source=source).value(PRINT_AREA)))

    def selected_pages(self, submission: str, question: Question) -> tuple[int, ...]:
        """The question's pages for this submission: printed pages, and (its own
        choice only) its extra sheets, numbered on from the exam's last page."""
        pages = self.layers(submission).own(overrides.join(question.id, 'pages'))
        if not pages:
            return question.pages
        last = self.exam.pages + self.extras(submission)
        return tuple(p for p in pages if p <= last) or question.pages

    def missing(self, submission: str) -> set[int]:
        """The printed pages this submission's PDF does not have: intake wrote a
        placeholder, or the PDF ends before them. Nothing on them is read."""
        m = self.store.made_of(submission)
        gone = {p for p, i in enumerate(m.pages[:self.exam.pages], 1) if i is None} if m else set()
        try:
            count = self.scan_page_count(submission)
        except Exception:       # no readable PDF: reading it fails anyway
            count = self.exam.pages
        return gone | set(range(count + 1, self.exam.pages + 1))

    def extras(self, submission: str) -> int:
        """How many sheets the PDF has beyond the exam's pages."""
        try:
            return max(0, self.scan_page_count(submission) - self.exam.pages)
        except Exception:
            return 0

    def fields(self, submission: str, question: Question) -> tuple[Field, ...]:
        """The question's fields with this submission's regions."""
        lay = self.layers(submission)
        out = []
        for f in question.fields:
            own = lay.own(overrides.join(question.id, 'fields', f.label, 'region'))
            out.append(f if own is None else dataclasses.replace(f, region=Region(*own)))
        return tuple(out)

    def region(self, submission: str, question: Question, label: str) -> Region | None:
        """A field's region for this submission (None: unset, not read)."""
        return next((f.region for f in self.fields(submission, question) if f.label == label), None)

    # --- pages and crops ---

    def pdf_path(self, submission: str) -> Path:
        s = self.store.submission(submission)
        if s is None:
            raise KeyError(submission)
        path = self.pdfs / s.pdf
        if path.is_symlink() or path.parent != self.pdfs:
            raise KeyError(submission)
        return path

    def scan_page_count(self, submission: str) -> int:
        return self.renderer.page_count(self.pdf_path(submission))

    def scan_page_path(self, submission: str, index: int) -> Path:
        """Cached rendering of one scanned page (0-based index into the PDF)."""
        return self.renderer.page_path(self.pdf_path(submission), index)

    def page_image(self, submission: str, page: int) -> Image.Image:
        """A printed page (or an extra sheet: exam pages + 1, ...) of the submission's PDF."""
        return self.renderer.page(self.pdf_path(submission), page - 1)

    def crop(self, submission: str, region: Region, pad: int | None = None) -> Image.Image:
        pad = self.exam.crop_pad if pad is None else pad
        return self.layout(submission).crop(self.page_image(submission, region.page), region, pad)
