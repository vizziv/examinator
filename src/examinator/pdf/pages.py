"""Writing a submission's PDF from sheets of a source PDF.

An upright sheet is copied as it is (PDFium imports the page; its embedded scan
image is not touched). A sheet that must be turned or mirrored is baked: its
image (the renderer's `scan_image`, at native resolution, or a rasterization
when the page is not a plain scan) is transposed and written as a new page
holding exactly one full-page JPEG (quality 95), the page shaped like the image.
Either way the result is a plain scan, so the renderer's fast path applies to
every page written here, and nothing after intake needs to know about turning.
"""
from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import Sequence

from PIL import Image

from . import render
from .orient import Orient

BAKE_DPI = 200          # rasterizing a page that is not a plain scan
BAKE_QUALITY = 95


def page_count(pdf: str | Path) -> int:
    import pypdfium2
    with render._PDFIUM_LOCK:
        doc = pypdfium2.PdfDocument(str(pdf))
        try:
            return len(doc)
        finally:
            doc.close()


def _image(page) -> Image.Image:
    got = render.scan_image(page)
    if got is None:
        return page.render(scale=BAKE_DPI / 72).to_pil().convert('RGB')
    if isinstance(got, bytes):
        with Image.open(io.BytesIO(got)) as im:
            im.load()
            return im
    return got


def write(src: str | Path, sheets: Sequence[tuple[int, Orient]], out: str | Path) -> None:
    """Write `out` from `sheets` of `src`: (0-based page index, orientation) in order.
    Written to a temporary file first and moved into place."""
    import pypdfium2
    out = Path(out)
    tmp = out.with_name(f'.{out.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    with render._PDFIUM_LOCK:
        doc = pypdfium2.PdfDocument(str(src))
        dst = pypdfium2.PdfDocument.new()
        try:
            for pos, (index, orient) in enumerate(sheets):
                if not 0 <= index < len(doc):
                    raise IndexError(f"page {index + 1} of {len(doc)}")
                if orient.identity:
                    dst.import_pages(doc, [index], index=pos)
                    continue
                page = doc[index]
                w, h = page.get_size()
                image = orient.apply(_image(page))
                if image.mode not in ('RGB', 'L'):
                    image = image.convert('RGB')
                if orient.swaps:
                    w, h = h, w
                # The page takes the image's shape (a scan may sit on a page of another size):
                # the same width, the height that keeps the pixels square.
                h = w * image.height / image.width
                buf = io.BytesIO()
                image.save(buf, 'JPEG', quality=BAKE_QUALITY)
                new = dst.new_page(w, h, index=pos)
                obj = pypdfium2.PdfImage.new(dst)
                obj.load_jpeg(io.BytesIO(buf.getvalue()), inline=False)
                obj.set_matrix(pypdfium2.PdfMatrix().scale(w, h))
                new.insert_obj(obj)
                new.gen_content()
            dst.save(str(tmp))
        finally:
            dst.close()
            doc.close()
    os.replace(tmp, out)
