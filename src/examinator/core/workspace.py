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

A workspace has its own definition, `exam`: the package's with the exam-wide
overrides stored here (`refresh`). The package's `Exam` is never changed, and
everything reads the definition through the workspace: for one submission,
through `layers` (its own overrides and its scan's on top).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..pdf.layout import Layout
from ..pdf.render import Renderer
from . import overrides
from .defs import PRINT_AREA, Exam, Region
from .store import Store, Submission


class Workspace:
    def __init__(self, root: str | Path, exam: Exam, *, create: bool = False):
        self.root = Path(root)
        self.exam = exam            # until `refresh` makes the workspace's own
        self.pdfs = self.root / 'pdfs'
        if not self.pdfs.is_dir():
            if not create:
                raise FileNotFoundError(f"no pdfs/ folder in {self.root}")
            self.pdfs.mkdir(parents=True, mode=0o700)
        for d in ('cache', 'exports'):
            (self.root / d).mkdir(exist_ok=True, mode=0o700)
        self.store = Store(self.root / 'exam.sqlite')
        self.renderer = Renderer(self.root / 'cache')
        self.skipped: list[overrides.Skipped] = self.refresh()

    def refresh(self) -> list[overrides.Skipped]:
        """Make this workspace's definition the package's plus the exam-wide overrides
        stored here: part of opening a workspace, and of every exam-wide edit. Those
        that no longer validate are skipped (`skipped`; every scope's:
        `skipped_overrides`). `exam` is replaced: whoever reads it reads it anew."""
        self.exam, self.skipped = overrides.apply(self.exam, {path: o.value for path, o in self.store.overrides().items()})
        return self.skipped

    def skipped_overrides(self) -> list[overrides.Skipped]:
        """Every stored override that does not apply, at every scope (the exam's, each
        scan's, each submission's), each with its scope."""
        out = list(self.skipped)
        for sc in sorted({o.scope for o in self.store.all_overrides() if o.scope}):
            level, id = overrides.parse_scope(sc)
            out += [s for s in self.layers(**{level: id}).check() if s.scope == sc]
        return out

    def close(self) -> None:
        self.store.close()

    def sync(self) -> int:
        """Record every PDF in `pdfs/` as a submission (but not a file an interrupted
        Accept left: `intake.unsettled`), and name by file name those it can (`identity.by_file`)."""
        from . import identity, intake
        stray = intake.unsettled(self)
        pdfs = [p for p in sorted(self.pdfs.glob('*.pdf')) if not p.is_symlink() and p.stem not in stray]
        self.store.put_submissions([Submission(p.stem, "", "", p.name) for p in pdfs])
        identity.by_file(self)
        return len(pdfs)

    # --- what applies to one submission: `overrides.Layers` resolves it ---
    #
    # Each value is the submission's own override, else its source scan's (every
    # submission intake made from that file), else the exam's, else the package's.

    def layers(self, submission: str | None = None, *, source: str | None = None) -> overrides.Layers:
        """The definition for one submission (or a source scan's sheets): `question`
        (its regions: one may sit on the submission's extra sheets, numbered on from
        the exam's last page), `layout`."""
        return overrides.Layers(self, submission=submission, source=source)

    def layout(self, submission: str) -> Layout:
        """This submission's print area."""
        return self.layers(submission).layout

    def source_layout(self, source: str | None) -> Layout:
        """A source scan's sheets, with its print area."""
        return self.layers(source=source).layout

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
