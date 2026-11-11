"""Page rendering with a disk cache.

PDFium must not be used from several threads at once, so every call into it
holds one lock. Rendered pages are cached as JPEG files keyed by the PDF's
identity (path, size, modification time), the page and the resolution; crops
are cut from the cache.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from PIL import Image

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
        """Path of the cached rendering of one scanned page (0-based), rendering if needed."""
        pdf = Path(pdf)
        out = self.cache_dir / self._key(pdf) / f'p{index:03d}-{self.dpi}.jpg'
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
                image = doc[index].render(scale=self.dpi / 72).to_pil().convert('RGB')
            finally:
                doc.close()
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = out.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
            image.save(tmp, 'JPEG', quality=88)
            os.replace(tmp, out)
        return out

    def page(self, pdf: str | Path, index: int) -> Image.Image:
        with Image.open(self.page_path(pdf, index)) as im:
            im.load()
            return im
