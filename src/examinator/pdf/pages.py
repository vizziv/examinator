"""Writing a submission's PDF from sheets of a source PDF.

An upright sheet is copied as it is (PDFium imports the page; its embedded scan
image is not touched). A sheet that must be turned is baked: its
image (the renderer's `scan_image`, at native resolution, or a rasterization
when the page is not a plain scan) is transposed and written as a new page
holding exactly one full-page JPEG (quality 95), the page shaped like the image.
Either way the result is a plain scan, so the renderer's fast path applies to
every page written here, and nothing after intake needs to know about turning.
A page that was not scanned is written as a blank placeholder, marked
"missing page" in small grey letters.
"""
from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from . import render
from .orient import turn

BAKE_DPI = 200          # rasterizing a page that is not a plain scan
BAKE_QUALITY = 95


def _image(page) -> Image.Image:
    got = render.scan_image(page)
    if got is None:
        return page.render(scale=BAKE_DPI / 72).to_pil().convert('RGB')
    if isinstance(got, bytes):
        with Image.open(io.BytesIO(got)) as im:
            im.load()
            return im
    return got


def _missing(w: float, h: float) -> Image.Image:
    """A blank placeholder for a page that was not scanned, shaped `w` by `h`."""
    image = Image.new('L', (850, max(1, round(850 * h / w))), 255)
    ImageDraw.Draw(image).text((40, 30), "missing page", fill=150, font=ImageFont.load_default(24))
    return image


def write(src: str | Path, sheets: Sequence[tuple[int | None, int]], out: str | Path) -> None:
    """Write `out` from `sheets` of `src`: (0-based page index, quarter turns clockwise) in order;
    index None: a placeholder page ("missing page"), shaped like the first sheet.
    Written to a temporary file first and moved into place."""
    import pypdfium2
    out = Path(out)
    tmp = out.with_name(f'.{out.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    with render._PDFIUM_LOCK:
        doc = pypdfium2.PdfDocument(str(src))
        dst = pypdfium2.PdfDocument.new()

        def put(pos: int, image: Image.Image, w: float) -> None:
            """A new page holding exactly `image` as one JPEG, `w` wide, the image's shape."""
            if image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')
            h = w * image.height / image.width
            buf = io.BytesIO()
            image.save(buf, 'JPEG', quality=BAKE_QUALITY)
            new = dst.new_page(w, h, index=pos)
            obj = pypdfium2.PdfImage.new(dst)
            obj.load_jpeg(io.BytesIO(buf.getvalue()), inline=False)
            obj.set_matrix(pypdfium2.PdfMatrix().scale(w, h))
            new.insert_obj(obj)
            new.gen_content()

        try:
            shape = (612.0, 792.0)          # letter, unless a sheet says otherwise
            for index, turns in sheets:
                if index is not None and 0 <= index < len(doc):
                    w, h = doc[index].get_size()
                    shape = (h, w) if turns % 2 else (w, h)
                    break
            for pos, (index, turns) in enumerate(sheets):
                if index is None:
                    put(pos, _missing(*shape), shape[0])
                    continue
                if not 0 <= index < len(doc):
                    raise IndexError(f"page {index + 1} of {len(doc)}")
                if not turns % 4:
                    dst.import_pages(doc, [index], index=pos)
                    continue
                page = doc[index]
                w, h = page.get_size()
                # The page is as wide as the turned page and takes the image's shape (a scan
                # may sit on a page of another size): the height that keeps the pixels square.
                put(pos, turn(_image(page), turns), h if turns % 2 else w)
            dst.save(str(tmp))
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
            dst.close()
            doc.close()
