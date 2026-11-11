"""A workspace: one exam's data on disk.

    workspace/
      pdfs/        ID.pdf per submission, plus one roster file
      exam.sqlite
      cache/       rendered pages
      exports/

The roster is either the scanning service's YAML metadata (file name to a list
of submitters with names and emails) or a CSV with `Submission ID`, `First
Name`, `Last Name`, `Email`. Exactly one must be present.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from PIL import Image

from ..pdf.layout import DEFAULT_PAD, Layout
from ..pdf.render import Renderer
from .defs import Exam, Question, Region
from .store import ANY, Store, Submission


class RosterError(ValueError):
    pass


def _get(d: dict, key: str) -> Any:
    return d.get(key, d.get(':' + key))


def read_roster(pdfs: Path) -> dict[str, tuple[str, str]]:
    """Submission id -> (name, email), from the one roster file in `pdfs`."""
    files = sorted(p for p in pdfs.iterdir() if p.suffix.lower() in ('.yml', '.yaml', '.csv'))
    if len(files) != 1:
        raise RosterError(f"expected exactly one roster file (YAML or CSV) in {pdfs}, found {len(files)}")
    f = files[0]
    out: dict[str, tuple[str, str]] = {}
    if f.suffix.lower() == '.csv':
        with f.open(newline='', encoding='utf-8-sig') as fh:
            for row in csv.DictReader(fh):
                name = f"{row.get('First Name', '').strip()} {row.get('Last Name', '').strip()}".strip()
                out[row['Submission ID'].strip()] = (name, row.get('Email', '').strip())
    else:
        import yaml
        data = yaml.safe_load(f.read_text(encoding='utf-8')) or {}
        for filename, entry in data.items():
            people = _get(entry or {}, 'submitters') or []
            names = "; ".join(str(_get(p, 'name') or "") for p in people)
            emails = "; ".join(str(_get(p, 'email') or "") for p in people)
            out[Path(str(filename)).stem] = (names, emails)
    return out


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

    def sync_roster(self) -> int:
        """Record every PDF in `pdfs/` as a submission, named from the roster."""
        roster = read_roster(self.pdfs)
        subs = []
        for pdf in sorted(self.pdfs.glob('*.pdf')):
            if pdf.is_symlink():
                continue
            name, email = roster.get(pdf.stem, ("", ""))
            subs.append(Submission(pdf.stem, name, email, pdf.name))
        self.store.put_submissions(subs)
        return len(subs)

    # --- settings: exam defaults until the workspace saves its own ---

    @property
    def cover_pages(self) -> int:
        return self.store.setting('cover_pages', self.exam.cover_pages)

    @property
    def scan_window(self) -> tuple[int, int, int, int]:
        return tuple(self.store.setting('scan_window', list(self.exam.scan_window)))

    def set_defaults(self, *, cover_pages: int | None = None, scan_window=None) -> None:
        if cover_pages is not None:
            self.store.set_setting('cover_pages', int(cover_pages))
        if scan_window is not None:
            self.store.set_setting('scan_window', [int(v) for v in scan_window])

    def layout(self, submission: str) -> Layout:
        """The workspace default, with this submission's override on top.
        The override is the artifact (submission, '', 'layout'):
        `{'cover_pages': int | None, 'scan_window': [l, t, r, b] | None}`."""
        o = self.store.get(submission, '', 'layout')
        body = o.body if o else {}
        cover = body.get('cover_pages')
        window = body.get('scan_window')
        return Layout(self.cover_pages if cover is None else cover,
                      tuple(window) if window else self.scan_window)

    def set_layout(self, submission: str, *, cover_pages: int | None, scan_window, author: str | None) -> None:
        if cover_pages is None and not scan_window:
            self.store.delete(submission, '', 'layout')
        else:
            self.store.write(submission, '', 'layout',
                             {'cover_pages': cover_pages, 'scan_window': list(scan_window) if scan_window else None},
                             origin='manual', author=author)

    def selected_pages(self, submission: str, question: Question) -> tuple[tuple[int, ...], str]:
        """The question's printed pages for this submission, and the grader's note.
        The override is the artifact (submission, question, 'pages'): `{'pages': [...], 'note': str}`."""
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
