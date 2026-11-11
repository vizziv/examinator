"""Page rendering with a disk cache.

PDFium must not be used from several threads at once, so every call into it
holds one lock. Rendered pages are cached as JPEG files keyed by the PDF's
identity (path, size, modification time), the page and the resolution; crops
are cut from the cache.

A scanned page is usually one full-page image. Such a page is not rendered: the
embedded image is taken as it is, at its own resolution (a JPEG's bytes are
copied untouched). Its placement on the page is not interpreted. Any other page
is rasterized by PDFium at the renderer's `dpi`.
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import threading
from pathlib import Path

from PIL import Image

_PDFIUM_LOCK = threading.Lock()   # PDFium itself is process-global
_FIT = 0.02                       # how closely a scan must cover the page (fraction of its size)


class Renderer:
    def __init__(self, cache_dir: str | Path, dpi: int = 160):
        self.cache_dir = Path(cache_dir)
        self.dpi = dpi
        self._counts: dict[str, int] = {}

    def _key(self, pdf: Path) -> str:
        st = pdf.stat()
        return hashlib.sha1(f'{pdf.resolve()}|{st.st_size}|{st.st_mtime_ns}'.encode()).hexdigest()[:20]

    def page_count(self, pdf: str | Path) -> int:
        pdf = Path(pdf)
        key = self._key(pdf)
        if key not in self._counts:
            import pypdfium2
            with _PDFIUM_LOCK:
                doc = pypdfium2.PdfDocument(str(pdf))
                try:
                    self._counts[key] = len(doc)
                finally:
                    doc.close()
        return self._counts[key]

    def page_path(self, pdf: str | Path, index: int) -> Path:
        """Path of the cached image of one scanned page (0-based), making it if needed."""
        pdf = Path(pdf)
        # 'n' marks the scheme that keeps a scan at its native resolution (older entries were not).
        out = self.cache_dir / self._key(pdf) / f'p{index:03d}-{self.dpi}n.jpg'
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


def scan_image(page) -> Image.Image | bytes | None:
    """The page's one embedded image, or None if the page is not a plain scan.

    A plain scan is a page whose only object is an image covering the page, with
    pixels shaped like the page. The result is RGB or grayscale at the image's
    own resolution, or the embedded JPEG's bytes when they can be used unchanged.
    """
    import pypdfium2.raw as pdfium_c

    if page.get_rotation():
        return None
    objs = list(page.get_objects(max_depth=1))
    if len(objs) != 1 or objs[0].type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
        return None
    obj = objs[0]
    pw, ph = page.get_size()
    l, b, r, t = obj.get_bounds()
    if max(abs(l), abs(r - pw)) > _FIT * pw or max(abs(b), abs(t - ph)) > _FIT * ph:
        return None
    w, h = obj.get_px_size()
    if not w or not h or abs(math.log((w / h) / (pw / ph))) > _FIT:
        return None
    if obj.get_filters() == ['DCTDecode']:
        data = bytes(obj.get_data())
        with Image.open(io.BytesIO(data)) as probe:
            if probe.mode in ('RGB', 'L') and probe.getexif().get(0x0112, 1) == 1:
                return data
    image = obj.get_bitmap().to_pil()
    return image if image.mode in ('RGB', 'L') else image.convert('RGB')
