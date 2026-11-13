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
from .defs import Exam, Field, Question, Region, check_region, key
from .store import ANY, Store, Submission


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

    # --- settings: exam defaults until the workspace saves its own ---
    #
    # The print area (`scan_window`: where the printed page sits on a scanned sheet,
    # [l, t, r, b] per-mille of the sheet) resolves, most specific first: the
    # submission's own, its source scan's (every submission intake made from that
    # file), the workspace's, the exam package's. Cover pages: the submission's own,
    # else the workspace's, else the package's.

    @property
    def cover_pages(self) -> int:
        return self.store.setting('cover_pages', self.exam.cover_pages)

    @property
    def scan_window(self) -> tuple[int, int, int, int]:
        """The workspace's print area (else the package's)."""
        return tuple(self.store.setting('scan_window', list(self.exam.scan_window)))

    def set_defaults(self, *, cover_pages: int | None = None, scan_window=None) -> None:
        if cover_pages is not None:
            self.store.set_setting('cover_pages', int(cover_pages))
        if scan_window is not None:
            self.store.set_setting('scan_window', [int(v) for v in scan_window])

    def clear_scan_window(self) -> None:
        """The workspace's print area back to the package's."""
        self.store.delete_setting('scan_window')

    def source_of(self, submission: str) -> str | None:
        """The source scan intake made this submission from; None if it was handed in split."""
        m = self.store.made_of(submission)
        return m.source if m else None

    def source_window(self, source: str | None) -> tuple[int, int, int, int] | None:
        """A source scan's own print area, for every submission from it.
        The row (source id, '', 'print_area'): `{'scan_window': [l, t, r, b]}`."""
        o = self.store.get(source, '', 'print_area') if source else None
        w = (o.body or {}).get('scan_window') if o else None
        return tuple(w) if w else None

    def set_source_window(self, source: str, scan_window, *, author: str | None, expect_rev=ANY) -> None:
        """Set (or, with None, clear) a source scan's print area."""
        if not scan_window:
            self.store.delete(source, '', 'print_area', expect_rev=expect_rev)
        else:
            self.store.write(source, '', 'print_area', {'scan_window': [int(v) for v in scan_window]},
                             origin='manual', author=author, expect_rev=expect_rev)

    def window_for_source(self, source: str | None) -> tuple[int, int, int, int]:
        """The print area of a source's sheets: the source's own, else the workspace's."""
        return self.source_window(source) or self.scan_window

    def layout(self, submission: str) -> Layout:
        """The defaults, with this submission's override on top.
        The override is the row (submission, '', 'layout'):
        `{'cover_pages': int | None, 'scan_window': [l, t, r, b] | None}`."""
        o = self.store.get(submission, '', 'layout')
        body = o.body if o else {}
        cover = body.get('cover_pages')
        window = body.get('scan_window')
        return Layout(self.cover_pages if cover is None else cover,
                      tuple(window) if window else self.window_for_source(self.source_of(submission)))

    def set_layout(self, submission: str, *, cover_pages: int | None, scan_window, author: str | None,
                   expect_rev=ANY) -> None:
        if cover_pages is None and not scan_window:
            self.store.delete(submission, '', 'layout', expect_rev=expect_rev)
        else:
            self.store.write(submission, '', 'layout',
                             {'cover_pages': cover_pages, 'scan_window': list(scan_window) if scan_window else None},
                             origin='manual', author=author, expect_rev=expect_rev)

    def selected_pages(self, submission: str, question: Question) -> tuple[tuple[int, ...], str]:
        """The question's printed pages for this submission, and the grader's note.
        The override is the row (submission, question, 'pages'): `{'pages': [...], 'note': str}`."""
        o = self.store.get(submission, question.id, 'pages')
        if o and o.body.get('pages'):
            return tuple(o.body['pages']), o.body.get('note') or ""
        return question.pages, ""

    def set_pages(self, submission: str, question: Question, pages, note: str, *, author: str | None) -> None:
        pages = [int(p) for p in pages or ()]
        if not pages or tuple(pages) == question.pages:
            self.store.delete(submission, question.id, 'pages')
        else:
            if any(not 1 <= p <= self.exam.pages for p in pages):
                raise ValueError("page out of range")
            self.store.write(submission, question.id, 'pages', {'pages': pages, 'note': note},
                             origin='manual', author=author)

    def region(self, submission: str, question: Question, label: str) -> Region | None:
        """A field's region for this submission: its own override, else the exam's
        (which may itself be an exam-wide override, or unset).
        The override is the row (submission, 'question/label', 'region'): `{'region': [p, l, t, r, b]}`."""
        o = self.store.get(submission, key(question.id, label), 'region')
        if o and o.body.get('region'):
            return Region(*o.body['region'])
        f = question.field(label)
        return f.region if f is not None else None

    def set_region(self, submission: str, question: Question, label: str, region: Region | None, *,
                   author: str | None, expect_rev=ANY) -> None:
        """Set (or, with None, clear) a field's region for this submission. Changes no value."""
        k = key(question.id, label)
        if region is None:
            self.store.delete(submission, k, 'region', expect_rev=expect_rev)
        else:
            check_region(region, self.exam.pages, f"{question.id}: field {label!r}")
            self.store.write(submission, k, 'region', {'region': [region.page, region.left, region.top,
                                                                  region.right, region.bottom]},
                             origin='manual', author=author, expect_rev=expect_rev)

    def fields(self, submission: str, question: Question) -> tuple[Field, ...]:
        """The question's fields with this submission's regions."""
        return tuple(dataclasses.replace(f, region=self.region(submission, question, f.label))
                     for f in question.fields)

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

    def page_image(self, submission: str, printed_page: int) -> Image.Image:
        index = self.layout(submission).scan_index(printed_page)
        return self.renderer.page(self.pdf_path(submission), index)

    def crop(self, submission: str, region: Region, pad: int | None = None) -> Image.Image:
        pad = self.exam.crop_pad if pad is None else pad
        return self.layout(submission).crop(self.page_image(submission, region.page), region, pad)
