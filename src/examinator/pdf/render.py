"""Page rendering with a disk cache.

PDFium must not be used from several threads at once, so every call into it
holds one lock. Rendered pages are cached as JPEG files keyed by the PDF's
identity (path, size, modification time), the page and the resolution; crops
are cut from the cache.

A scanned page is one big embedded image and not much else. Such a page is not
rendered: the image is taken as it is, at its own resolution (a JPEG's bytes
are copied untouched when it is already upright). Where the image sits on the
page and how large the page claims to be are ignored, since scanning services
get those wrong; only which way up it is drawn is honoured, and only for
quarter turns and mirror images. Any other page is rasterized by PDFium at the
renderer's `dpi`.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
from pathlib import Path

from PIL import Image

from .orient import CLOCKWISE

_PDFIUM_LOCK = threading.Lock()   # PDFium itself is process-global


class Renderer:
    def __init__(self, cache_dir: str | Path, dpi: int = 160):
        self.cache_dir = Path(cache_dir)
        self.dpi = dpi
        self._counts: dict[str, int] = {}

    def _key(self, pdf: Path) -> str:
        st = pdf.stat()
        return hashlib.sha1(f'{pdf.resolve()}|{st.st_size}|{st.st_mtime_ns}'.encode()).hexdigest()[:20]

    def page_count(self, pdf: str | Path) -> int:
        """`page_count`, remembered while the file is unchanged."""
        pdf = Path(pdf)
        key = self._key(pdf)
        if key not in self._counts:
            self._counts[key] = page_count(pdf)
        return self._counts[key]

    def page_path(self, pdf: str | Path, index: int) -> Path:
        """Path of the cached image of one scanned page (0-based), making it if needed."""
        pdf = Path(pdf)
        # 'i' marks the scheme that takes a scan's own image (entries from older schemes differ).
        out = self.cache_dir / self._key(pdf) / f'p{index:03d}-{self.dpi}i.jpg'
        if out.exists():
            return out
        import pypdfium2
        with _PDFIUM_LOCK:
            if out.exists():
                return out
            doc = pypdfium2.PdfDocument(str(pdf))
            try:
                if not 0 <= index < len(doc):
                    raise IndexError(f"page {index + 1} of {len(doc)}")
                page = doc[index]
                image = scan_image(page)
                if image is None:
                    image = page.render(scale=self.dpi / 72).to_pil().convert('RGB')
            finally:
                doc.close()
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = out.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
            if isinstance(image, bytes):
                tmp.write_bytes(image)
            else:
                image.save(tmp, 'JPEG', quality=88)
            os.replace(tmp, out)
        return out

    def page(self, pdf: str | Path, index: int) -> Image.Image:
        with Image.open(self.page_path(pdf, index)) as im:
            im.load()
            return im


def page_count(pdf: str | Path) -> int:
    """How many pages a PDF has."""
    import pypdfium2
    with _PDFIUM_LOCK:
        doc = pypdfium2.PdfDocument(str(pdf))
        try:
            return len(doc)
        finally:
            doc.close()


def scan_image(page) -> Image.Image | bytes | None:
    """The page's one embedded image, or None if the page is not a plain scan.

    A plain scan is a page with exactly one image, drawn last, over nothing but
    filled shapes (a background), and drawn upright or as a quarter turn or
    mirror image of upright. The result is RGB or grayscale at the image's own
    resolution, the right way up, or the embedded JPEG's bytes when they can be
    used unchanged.
    """
    import pypdfium2.raw as pdfium_c

    objs = list(page.get_objects(max_depth=8))
    if not objs or objs[-1].type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
        return None
    if any(o.type != pdfium_c.FPDF_PAGEOBJ_PATH for o in objs[:-1]):
        return None
    obj = objs[-1]
    a, b, c, d = obj.get_matrix().get()[:4]
    ops = _upright(a, b, c, d)
    if ops is None:
        return None
    ops = ops + _BY_ROTATE[page.get_rotation() % 360]
    if not ops and obj.get_filters() == ['DCTDecode']:
        data = bytes(obj.get_data())
        with Image.open(io.BytesIO(data)) as probe:
            if probe.mode in ('RGB', 'L') and probe.getexif().get(0x0112, 1) == 1:
                return data
    image = obj.get_bitmap().to_pil()
    for op in ops:
        image = image.transpose(op)
    return image if image.mode in ('RGB', 'L') else image.convert('RGB')


_T = Image.Transpose
# The transposes that undo a page's /Rotate (degrees clockwise), as a tuple to append to others.
_BY_ROTATE = {90 * t: (CLOCKWISE[t],) if t else () for t in range(4)}


def _upright(a: float, b: float, c: float, d: float) -> tuple | None:
    """What to do to an image's pixels so they look as the matrix `[a b c d]`
    draws them, or None unless that is a quarter turn or mirror image. Only the
    signs matter: the image's x axis goes to (a, b) and its upward axis to (c, d)."""
    big = max(abs(a), abs(b), abs(c), abs(d))
    if not big:
        return None
    flat = [abs(v) < 1e-3 * big for v in (a, b, c, d)]
    if flat == [False, True, True, False]:          # axes stay put
        return {(True, True): (), (False, True): (_T.FLIP_LEFT_RIGHT,),
                (True, False): (_T.FLIP_TOP_BOTTOM,), (False, False): (_T.ROTATE_180,)}[(a > 0, d > 0)]
    if flat == [True, False, False, True]:          # axes swap
        return {(True, False): (_T.ROTATE_90,), (False, True): (_T.ROTATE_270,),
                (True, True): (_T.TRANSVERSE,), (False, False): (_T.TRANSPOSE,)}[(b > 0, c > 0)]
    return None
