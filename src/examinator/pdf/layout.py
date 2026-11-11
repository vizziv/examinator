"""Scan layout: from printed-page coordinates to the scanned page.

Exam definitions speak in printed pages and per-mille of the printed page. A
scan usually has prepended cover pages, and shows the printed page reduced and
inset. `Layout` holds both corrections for one submission.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from ..core.defs import Region

DEFAULT_PAD = 12   # per-mille of the printed page, added around every crop


@dataclass(frozen=True)
class Layout:
    cover_pages: int = 0
    window: tuple[int, int, int, int] = (0, 0, 1000, 1000)   # left, top, right, bottom of the
                                                             # printed page, per-mille of the scan

    def scan_index(self, printed_page: int) -> int:
        """0-based index into the scanned PDF of a 1-based printed page."""
        return self.cover_pages + printed_page - 1

    def box(self, region: Region, pad: int = DEFAULT_PAD) -> tuple[float, float, float, float]:
        """The region as fractions (left, top, right, bottom) of the scanned page."""
        wl, wt, wr, wb = self.window

        def x(v):
            return min(1.0, max(0.0, (wl + (wr - wl) * v / 1000) / 1000))

        def y(v):
            return min(1.0, max(0.0, (wt + (wb - wt) * v / 1000) / 1000))

        return (x(region.left - pad), y(region.top - pad), x(region.right + pad), y(region.bottom + pad))

    def crop(self, page: Image.Image, region: Region, pad: int = DEFAULT_PAD) -> Image.Image:
        l, t, r, b = self.box(region, pad)
        w, h = page.size
        return page.crop((round(l * w), round(t * h), max(round(r * w), round(l * w) + 1),
                          max(round(b * h), round(t * h) + 1)))
